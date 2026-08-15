#!/usr/bin/env python3
"""
Spill-rewrite selected bank-5F dictionary slots that are exact proper nouns.

Does not touch the dialogue sheet. Shared tokens mean Japanese lines that
reference these slots will show Hangul names — intentional for this PoC.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from hangul_marker import resolve_marker  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import encode_ko_text, normalize_ko_text, try_encode_ko_text  # noqa: E402


def _file_abs(rom: bytes | bytearray, logical_abs: int) -> int:
    return stock_base(rom) + logical_abs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=None,
        help="ROM used only to match JP dict text (default: --rom). "
        "Use original/unpatched when --rom already has KO proper nouns.",
    )
    ap.add_argument(
        "--names",
        type=Path,
        default=ROOT / "data" / "proper_nouns_ko.json",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_patch_pad3.tbl",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data" / "translations_seed_hook96.json",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out" / "patch" / "proper_nouns_report.json",
    )
    ap.add_argument(
        "--allow-seed-fail",
        action="store_true",
        help="Do not exit(1) when opening-seed expand drifts "
        "(expected for shared UI/proper-noun slots).",
    )
    args = ap.parse_args()

    spec = json.loads(args.names.read_text(encoding="utf-8"))
    marker = resolve_marker(spec.get("marker"), source=str(args.names.name))
    rom = bytearray(load_rom(args.rom))
    match_rom = load_rom(args.base_rom) if args.base_rom else bytes(rom)
    tbl = Tbl.load(args.tbl)
    d_match = Dictionary(match_rom)

    jp_to_ko = {
        row["jp"]: normalize_ko_text(row["ko"])
        for row in spec["entries"]
        if row.get("jp") and row.get("ko")
    }

    # Exact expand_index match only (avoid substring false hits).
    # Match JP against base ROM to find indices, but ONLY rewrite if tip ROM
    # still holds that exact JP — never overwrite slots already reused for
    # dialogue KO (steal / opening_dedicated / stock reclaim).
    d_tip = Dictionary(rom)
    slot_payload: dict[int, bytes] = {}
    applied: list[dict] = []
    missing_jp: list[str] = []
    encode_fail: list[str] = []
    skipped_tip_reused: list[dict] = []

    index_by_jp: dict[str, list[int]] = {}
    for idx in range(d_match.count):
        plain = d_match.expand_index(idx, tbl)
        if plain in jp_to_ko:
            index_by_jp.setdefault(plain, []).append(idx)

    for jp, ko in jp_to_ko.items():
        idxs = index_by_jp.get(jp) or []
        if not idxs:
            missing_jp.append(jp)
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            encode_fail.append(jp)
            continue
        for idx in idxs:
            tip_plain = d_tip.expand_index(idx, tbl)
            if tip_plain == ko:
                # Already localized on tip — leave alone.
                continue
            if tip_plain != jp:
                skipped_tip_reused.append(
                    {
                        "index": f"{idx:04X}",
                        "jp": jp,
                        "tip": tip_plain[:60],
                        "ko": ko,
                    }
                )
                continue
            slot_payload[idx] = enc
            applied.append(
                {
                    "index": f"{idx:04X}",
                    "jp": jp,
                    "ko": ko,
                    "bytes": len(enc),
                }
            )

    if not slot_payload:
        report = {
            "marker": f"{marker:04X}",
            "slots_written": 0,
            "unique_jp": 0,
            "decode_fail": 0,
            "seed_fail": 0,
            "missing_jp": missing_jp,
            "encode_fail": encode_fail,
            "skipped_tip_reused": skipped_tip_reused[:40],
            "skipped_tip_reused_count": len(skipped_tip_reused),
            "applied": [],
            "checksum": f"{update_ws_checksum(rom):04X}",
            "notes": ["No JP slots left to write (already KO or tip-reused)."],
        }
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"Proper nouns SKIP | already done / no JP left | "
            f"reused_skip={len(skipped_tip_reused)} missing={len(missing_jp)}"
        )
        return

    # Unit/UI names intentionally share name75/aux consumers.
    write_dictionary_slots_spill(rom, slot_payload, allow_aux_consumers=True)

    # Prefer extended dictionary when meta exists (opening may use FF tokens).
    meta_path = ROOT / "out" / "patch" / "ext_dictionary_meta.json"
    d2: Dictionary
    if meta_path.exists():
        try:
            from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: WPS433

            d2 = make_dictionary(rom, load_ext_meta(meta_path))
        except Exception:
            d2 = Dictionary(rom)
    else:
        d2 = Dictionary(rom)

    decode_fail = 0
    for row in applied:
        idx = int(row["index"], 16)
        got = d2.expand_index(idx, tbl)
        ok = got == row["ko"]
        row["ok"] = ok
        if not ok:
            decode_fail += 1
            row["decode"] = got

    seed_fail = 0
    seed_fail_samples: list[dict] = []
    for row in json.loads(args.seed.read_text(encoding="utf-8"))["lines"]:
        abs_off = int(row["abs"], 16)
        ko = normalize_ko_text(row["ko"])
        try:
            body = split_prefix_body(
                read_encoded_z(rom, _file_abs(rom, abs_off))[0]
            )[1]
        except Exception:
            seed_fail += 1
            continue
        exp = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        exp_each = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
        )
        got = d2.expand(body, tbl).rstrip("　 \t")
        exp_plain = d2.expand(exp, tbl).rstrip("　 \t")
        exp_each_plain = d2.expand(exp_each, tbl).rstrip("　 \t")
        if got != exp_plain and got != exp_each_plain:
            seed_fail += 1
            if len(seed_fail_samples) < 8:
                seed_fail_samples.append(
                    {"abs": f"{abs_off:06X}", "expect": ko, "got": got}
                )
    report = {
        "marker": f"{marker:04X}",
        "slots_written": len(slot_payload),
        "unique_jp": len({r["jp"] for r in applied}),
        "decode_fail": decode_fail,
        "seed_fail": seed_fail,
        "missing_jp": missing_jp,
        "encode_fail": encode_fail,
        "skipped_tip_reused": skipped_tip_reused[:40],
        "skipped_tip_reused_count": len(skipped_tip_reused),
        "seed_fail_samples": seed_fail_samples,
        "applied": applied,
        "checksum": f"{update_ws_checksum(rom):04X}",
        "notes": [
            "Shared dict tokens: JP dialogue using these slots shows Hangul names.",
            "Skip slots whose tip payload is no longer the base JP (dialogue reuse).",
            "Title/menu UI strings are NOT in bank 5F — separate source hunt required.",
        ],
    }
    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Proper nouns OK | slots={len(slot_payload)} unique={report['unique_jp']} "
        f"decode_fail={decode_fail} seed_fail={seed_fail} checksum={report['checksum']}"
    )
    for row in applied:
        print(f"  [{row['index']}] {row['jp']} -> {row['ko']}")
    if missing_jp:
        print("missing:", ", ".join(missing_jp))
    print(f"Wrote {args.out_rom}")
    print(f"Wrote {args.out_report}")
    if decode_fail or encode_fail or (seed_fail and not args.allow_seed_fail):
        sys.exit(1)


if __name__ == "__main__":
    main()
