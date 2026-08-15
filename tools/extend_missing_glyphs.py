#!/usr/bin/env python3
"""
Bake sheet encode_fail Hangul into pad3 slots and refresh TBL + sticky window.

Also lists remaining non-Hangul misses (should be fixed via normalize_ko_text).
Does not cold-rebuild tip from 8MB — patches the live 16MB tip/work ROM.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_hangul_font import render_compact_glyph  # noqa: E402
from font_pipeline import find_system_font  # noqa: E402
from monoeye_rom import (  # noqa: E402
    COMPACT_FONT_RECORD_SIZE,
    Tbl,
    encode_compact_font_record,
    is_expanded_rom,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from patch_pad3_expansion import (  # noqa: E402
    PAD12_SLOTS,
    bake_overflow_chars,
    install_hooks,
)


def _collect_missing_hangul(
    *,
    sheet: Path,
    tbl: Tbl,
    rom: bytes,
    lo: int,
    hi: int,
) -> Counter:
    st = stock_base(rom)
    lines = json.loads(sheet.read_text(encoding="utf-8"))
    rows = lines["lines"] if isinstance(lines, dict) else lines
    missing: Counter = Counter()
    for L in rows:
        a = int(L["abs"], 16)
        if not (lo <= a <= hi):
            continue
        ko = normalize_ko_text(L.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        if try_encode_ko_text(
            ko, tbl, hangul_marker_code=0xE3DB, hangul_marker_mode="run"
        ) is not None:
            continue
        if not read_encoded_z_safe(rom, st + a):
            continue
        for ch in ko:
            if not ("가" <= ch <= "힣"):
                continue
            try:
                tbl.encode_char(ch)
            except KeyError:
                missing[ch] += 1
    return missing


def _append_tbl_entries(tbl_path: Path, char_to_code: Dict[str, int]) -> int:
    existing = Tbl.load(tbl_path)
    lines = tbl_path.read_text(encoding="utf-8").splitlines()
    added = 0
    for ch, code in sorted(char_to_code.items(), key=lambda kv: kv[1]):
        if ch in existing.char_to_code:
            continue
        lines.append(f"{code:04X}={ch}")
        added += 1
    tbl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--out-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_glyph_work.wsc")
    ap.add_argument("--map", type=Path, default=ROOT / "out/patch/hangul_char_map_pad3.json")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_apply_all.json",
    )
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=0x6040A5)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=0x69FFFF)
    ap.add_argument("--limit", type=int, default=0, help="Max new Hangul (0=all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rom = bytearray(load_rom(args.rom))
    if not is_expanded_rom(rom):
        raise SystemExit("16MiB ROM required")
    tbl = Tbl.load(args.tbl)
    mapping = json.loads(args.map.read_text(encoding="utf-8"))
    pad = mapping.setdefault("padding_store", {})
    base_code = int(pad.get("base_code", "E740"), 16)
    start_slot = int(pad.get("count", 1186))
    font = mapping.get("font") or find_system_font()

    missing = _collect_missing_hangul(
        sheet=args.sheet, tbl=tbl, rom=rom, lo=args.lo, hi=args.hi
    )
    # Prefer high-frequency first
    chars = [ch for ch, _n in missing.most_common()]
    if args.limit > 0:
        chars = chars[: args.limit]

    # Also bake map overflow_chars still missing from tbl
    for ch in mapping.get("overflow_chars") or []:
        if ch not in tbl.char_to_code and ch not in chars and "가" <= ch <= "힣":
            chars.append(ch)

    # Dedup preserve order
    seen: Set[str] = set()
    ordered: List[str] = []
    for ch in chars:
        if ch in seen or ch in tbl.char_to_code:
            continue
        seen.add(ch)
        ordered.append(ch)

    report = {
        "missing_unique": len(missing),
        "to_bake": len(ordered),
        "start_slot": start_slot,
        "top": missing.most_common(20),
    }
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not ordered:
        print(json.dumps({**report, "baked": 0}, ensure_ascii=False, indent=2))
        return 0

    bake = bake_overflow_chars(
        rom,
        mapping,
        font_path=font,
        start_slot=start_slot,
        chars=ordered,
    )
    sticky = int(pad["count"])
    base_index = base_code - 0xDF20
    hook = install_hooks(rom, base_index=base_index, sticky_count=sticky)

    # Reinstall ext3 cave if present (pad3 restore scrub must not leave it broken).
    from patch_3byte_dict_token import DEFAULT_NUM_BANKS, install as install_ext3

    ext3_meta = None
    if rom[stock_base(rom) + 0x7A0736] == 0xEA:
        ext3_meta = install_ext3(rom, force_format=False, num_banks=DEFAULT_NUM_BANKS)

    new_codes = {
        ch: int(mapping["mapping"][ch]["code"], 16) for ch in bake["chars"]
    }
    added_tbl = _append_tbl_entries(args.tbl, new_codes)

    # Drop baked from overflow_chars list
    baked_set = set(bake["chars"])
    mapping["overflow_chars"] = [
        ch for ch in (mapping.get("overflow_chars") or []) if ch not in baked_set
    ]
    mapping["overflow_count"] = len(mapping["overflow_chars"])
    mapping["new_char_count"] = len(
        [c for c in mapping.get("mapping", {}) if "가" <= c <= "힣"]
    )

    cs = update_ws_checksum(rom)
    args.out_rom.parent.mkdir(parents=True, exist_ok=True)
    args.out_rom.write_bytes(rom)
    args.map.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    out = {
        **report,
        "baked": bake["baked"],
        "sticky_count": sticky,
        "tbl_added": added_tbl,
        "hook": hook,
        "ext3_reinstalled": bool(ext3_meta),
        "checksum": f"{cs:04X}",
        "out_rom": str(args.out_rom),
        "sample_codes": {
            ch: f"{new_codes[ch]:04X}" for ch in bake["chars"][:10]
        },
    }
    rep = ROOT / "out/patch/extend_missing_glyphs_report.json"
    rep.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
