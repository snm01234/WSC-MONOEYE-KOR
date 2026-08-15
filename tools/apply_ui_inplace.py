#!/usr/bin/env python3
"""
Size-preserving inplace patch for fixed-address UI strings.

Verifies current JP (via Dictionary.expand) before write.
Does not move pointers. Skips if encoded KO longer than original nbytes.

"Size-preserving" means the record's **terminator does not move**: a shorter
Korean payload is padded to the original length with ``0x01`` (ideographic space)
before the NUL, not followed by NUL filler. See :data:`PAD_BYTE` for the bug that
rule fixes. A write whose terminator still ends up somewhere else is rolled back
to Japanese and reported as ``terminator_moved``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hangul_marker import resolve_marker  # noqa: E402
from monoeye_rom import (  # noqa: E402
    DICT_DATA_START,
    DICT_PTR_END,
    SEG_DICT,
    Dictionary,
    Tbl,
    find_rom,
    is_expanded_rom,
    load_rom,
    read_encoded_z,
    stock_base,
    update_ws_checksum,
)

#: Ideographic space. Padding a shortened record with this keeps the zstring
#: terminator at its ORIGINAL offset.
#:
#: The previous behaviour ("write KO + NUL, leave the rest 0x00") moved the
#: terminator forward and turned each surplus byte into a phantom empty record.
#: Bank 75 holds a back-to-back NUL-terminated UI label table at 0x75B690+, so
#: 75B6A6 / 75B7C5 / 75B7CD / 75BA40 inserted three phantoms and shifted every
#: following entry — the unit strengthen screen then drew the neighbouring
#: single-code icon record (75B6C8 / 75B6CB) and could land on 75B716
#: 'ＭＡＰ<E62F>ＳＥＬＥＣＴ' in a two-cell field. Same rule as
#: apply_safe_unit.padded_token_payload and apply_name75_ko.
PAD_BYTE = 0x01

#: Dictionary phrase data: a trailing ideographic space here would be pasted into
#: every string that composes the phrase (the documented fragment-composition
#: hazard), so shortened records in this range keep 0x00 filler. They are
#: pointer-addressed, so a moved terminator is harmless there.
NO_PAD_RANGE = (
    (SEG_DICT << 16) | DICT_DATA_START,
    (SEG_DICT << 16) | DICT_PTR_END,
)


def pad_byte_for(logical: int) -> int:
    lo, hi = NO_PAD_RANGE
    return 0x00 if lo <= logical <= hi else PAD_BYTE
from normalize_ko_text import encode_ko_text, normalize_ko_text, try_encode_ko_text  # noqa: E402


def _file_abs(rom: bytes | bytearray, logical_abs: int) -> int:
    """Logical bank abs → file offset (16 MiB tip uses stock_base)."""
    if is_expanded_rom(rom) or stock_base(rom):
        return stock_base(rom) + logical_abs
    return logical_abs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--out-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--base-rom", type=Path, default=None, help="JP verify source (default original)")
    ap.add_argument("--strings", type=Path, default=ROOT / "data/ui_inplace_ko.json")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument("--out-report", type=Path, default=ROOT / "out/patch/ui_inplace_report.json")
    args = ap.parse_args()

    spec = json.loads(args.strings.read_text(encoding="utf-8"))
    marker = resolve_marker(spec.get("marker"), source=str(args.strings.name))
    rom = bytearray(load_rom(args.rom))
    base = load_rom(args.base_rom) if args.base_rom else load_rom(find_rom(ROOT))
    tbl = Tbl.load(args.tbl)
    d_base = Dictionary(base)
    d_rom = Dictionary(rom)

    # Prefer ext dict for expand if present on patch rom
    meta_path = ROOT / "out/patch/ext_dictionary_meta.json"
    if meta_path.exists():
        try:
            from apply_ext_dict_unit import load_ext_meta, make_dictionary

            d_rom = make_dictionary(rom, load_ext_meta(meta_path))
            d_base = make_dictionary(base, load_ext_meta(meta_path))
        except Exception:
            pass

    applied = []
    skipped = []
    for row in spec["lines"]:
        logical = int(row["abs"], 16)
        jp_expect = row["jp"]
        ko = normalize_ko_text(row["ko"])
        base_off = _file_abs(base, logical)
        tip_off = _file_abs(rom, logical)
        try:
            raw, _ = read_encoded_z(base, base_off)
        except Exception:
            skipped.append({"abs": row["abs"], "reason": "empty"})
            continue
        if not raw:
            skipped.append({"abs": row["abs"], "reason": "empty"})
            continue
        got_jp = d_base.expand(raw, tbl)
        if got_jp != jp_expect:
            skipped.append(
                {
                    "abs": row["abs"],
                    "reason": "jp_mismatch",
                    "expect": jp_expect,
                    "got": got_jp,
                }
            )
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            skipped.append({"abs": row["abs"], "reason": "encode_fail", "ko": ko})
            continue
        # zstring: payload + 00 terminator must fit in original record span
        need = len(enc) + 1
        if need > len(raw) + 1:
            skipped.append(
                {
                    "abs": row["abs"],
                    "reason": "too_long",
                    "jp_bytes": len(raw),
                    "ko_bytes": len(enc),
                    "jp": jp_expect,
                    "ko": ko,
                }
            )
            continue
        # Pad to the original payload length, THEN terminate, so the terminator
        # lands on exactly the byte it occupied before (see PAD_BYTE).
        span = len(raw) + 1
        pad = pad_byte_for(logical)
        patch = enc + bytes([pad]) * (len(raw) - len(enc)) + b"\x00"
        assert len(patch) == span, (len(patch), span)
        rom[tip_off : tip_off + span] = patch
        got_back = read_encoded_z(rom, tip_off)
        if len(got_back[0]) != len(raw):
            skipped.append(
                {
                    "abs": row["abs"],
                    "reason": "terminator_moved",
                    "jp_bytes": len(raw),
                    "written_bytes": len(got_back[0]),
                }
            )
            rom[tip_off : tip_off + span] = raw + b"\x00"
            continue
        check = d_rom.expand(got_back[0], tbl)
        applied.append(
            {
                "abs": row["abs"],
                "jp": jp_expect,
                "ko": ko,
                "jp_bytes": len(raw),
                "ko_bytes": len(enc),
                "pad_byte": f"{pad:02X}",
                "pad_bytes": len(raw) - len(enc),
                "ok": check.rstrip("\u3000 ") == ko.rstrip("\u3000 "),
                "decode": check if check.rstrip("\u3000 ") != ko.rstrip("\u3000 ") else None,
            }
        )

    report = {
        "marker": f"{marker:04X}",
        "applied": len(applied),
        "skipped": len(skipped),
        "decode_fail": sum(1 for r in applied if not r["ok"]),
        "applied_rows": applied,
        "skipped_rows": skipped,
        "checksum": f"{update_ws_checksum(rom):04X}",
    }
    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"UI inplace OK | applied={report['applied']} skipped={report['skipped']} "
        f"decode_fail={report['decode_fail']} checksum={report['checksum']}"
    )
    for r in applied[:20]:
        print(f"  @{r['abs']} {r['jp']} -> {r['ko']} ({r['jp_bytes']}->{r['ko_bytes']})")
    if len(applied) > 20:
        print(f"  ... +{len(applied)-20} more")
    reasons = {}
    for s in skipped:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
    print("skipped:", reasons)
    print(f"Wrote {args.out_rom}")
    if report["decode_fail"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
