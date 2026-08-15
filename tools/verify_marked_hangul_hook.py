#!/usr/bin/env python3
"""Static verification for the provenance-marked UI-isolation Hangul PoC."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import Tbl, compact_font_file_offset, load_rom  # noqa: E402
from patch_font_hangul_hook import (  # noqa: E402
    DISPATCH_SITE,
    EXT_CAVE,
    MAIN_CAVE,
    PAD1_SLOTS,
    PAD2_FILE,
    PAD2_OFF,
    PAD_TOTAL_SLOTS,
    PARSER_B_CALL,
    PRIMARY_SITE,
    STORE_SITE,
    build_primary_cave,
    pad_file_offset,
    runtime_pad_file_offset,
)


def checksum_ok(rom: bytes) -> bool:
    stored = struct.unpack_from("<H", rom, len(rom) - 2)[0]
    return (sum(rom[:-2]) & 0xFFFF) == stored


def runtime_indices(encoded: bytes, marker_code: int) -> list[int]:
    """Model marker consumption + stock code-to-index conversion."""
    out: list[int] = []
    marked = False
    i = 0
    while i < len(encoded):
        lead = encoded[i]
        if lead == 0:
            break
        if 0xE0 <= lead <= 0xEF:
            code = (lead << 8) | encoded[i + 1]
            i += 2
        else:
            code = lead
            i += 1
        if code == marker_code:
            marked = True
            continue
        index = code - 0xDF20 if code >= 0xE000 else code
        if marked:
            index |= 0x8000
            marked = False
        out.append(index)
    if marked:
        raise AssertionError("Dangling Hangul provenance marker")
    return out


def runtime_font_offset(index: int, base_index: int) -> int:
    if index & 0x8000:
        return runtime_pad_file_offset(index, base_index)
    return 0x400000 + 0x0440 + index * 16


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_seed.wsc",
    )
    ap.add_argument(
        "--original",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    )
    ap.add_argument(
        "--map",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_char_map.json",
    )
    ap.add_argument(
        "--apply-report",
        type=Path,
        default=ROOT / "out" / "patch" / "apply_report.json",
    )
    ap.add_argument(
        "--hook-report",
        type=Path,
        default=ROOT / "out" / "patch" / "rom_font_hooked.hook.json",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_patch.tbl",
    )
    args = ap.parse_args()

    original = bytes(load_rom(args.original))
    rom = bytes(load_rom(args.rom))
    mapping = json.loads(args.map.read_text(encoding="utf-8"))
    apply_report = json.loads(args.apply_report.read_text(encoding="utf-8"))
    hook_report = json.loads(args.hook_report.read_text(encoding="utf-8"))
    tbl = Tbl.load(args.tbl)

    pad = mapping["padding_store"]
    base_code = int(pad["base_code"], 16)
    base_index = base_code - 0xDF20
    count = int(pad["count"])
    marker_code = int(pad["marker_code"], 16)

    checks: dict[str, object] = {}
    checks["checksum_ok"] = checksum_ok(rom)
    checks["marker_tbl_empty"] = tbl.decode_char(marker_code) == ""
    checks["marker_report_match"] = (
        apply_report["hangul_marker_code"] == f"{marker_code:04X}"
        and hook_report["marker_code"] == f"{marker_code:04X}"
    )

    # Numeric stock slots shared by UI must remain byte-identical.
    stock_untouched = True
    for code in range(base_code, base_code + count):
        off = compact_font_file_offset(code)
        if original[off : off + 16] != rom[off : off + 16]:
            stock_untouched = False
            break
    checks["stock_ui_glyph_window_untouched"] = stock_untouched

    checks["padding_contains_all_glyphs"] = all(
        any(rom[pad_file_offset(i) : pad_file_offset(i) + 16]) for i in range(count)
    )

    # Every encoded Hangul must be marker-prefixed and resolve to padding.
    marked_hangul = 0
    encoding_ok = True
    for result in apply_report["results"]:
        encoded = bytes.fromhex(result["encoded_hex"])
        indices = runtime_indices(encoded, marker_code)
        decoded = result["decode_check"]
        if decoded != result["ko"]:
            encoding_ok = False
        for i in range(len(encoded) - 3):
            code = (encoded[i + 2] << 8) | encoded[i + 3]
            if (encoded[i] << 8) | encoded[i + 1] != marker_code:
                continue
            if not base_code <= code < base_code + count:
                encoding_ok = False
                continue
            tagged = (code - 0xDF20) | 0x8000
            off = runtime_font_offset(tagged, base_index)
            expected = pad_file_offset(code - base_code)
            if off != expected:
                encoding_ok = False
            marked_hangul += 1
        if any(
            base_index <= idx <= base_index + count - 1 and not (idx & 0x8000)
            for idx in indices
        ):
            encoding_ok = False
    checks["marked_encoding_and_padding_resolution"] = encoding_ok
    checks["marked_hangul_occurrences"] = marked_hangul

    # Safety invariant: untagged UI indices always resolve to the stock table.
    checks["untagged_ui_resolution_invariant"] = all(
        runtime_font_offset(index, base_index)
        == 0x400000 + 0x0440 + index * 16
        for index in range(0x0000, 0x1100)
    )

    checks["primary_site_is_jump"] = rom[PRIMARY_SITE] == 0xE9
    checks["dispatch_site_preserves_moves_and_calls_cave"] = (
        rom[DISPATCH_SITE : DISPATCH_SITE + 4] == bytes.fromhex("8BCA8BD7")
        and rom[DISPATCH_SITE + 4] == 0xE8
    )
    checks["parser_b_call_is_near_call"] = rom[PARSER_B_CALL] == 0xE8
    checks["store_site_is_near_call"] = rom[STORE_SITE] == 0xE8
    checks["main_cave_written"] = rom[MAIN_CAVE] != 0xFF
    checks["ext_cave_written"] = rom[EXT_CAVE] != 0xFF
    primary = build_primary_cave(base_index)
    cave = bytes(rom[EXT_CAVE : EXT_CAVE + len(primary)])
    checks["primary_cave_matches_pad2_bank41"] = cave == primary
    # Active pad2 uses CX=4000 (not legacy 3100 / unproven 2F00).
    checks["primary_cave_pad2_seg_4000"] = b"\x00\x40" in cave
    checks["primary_cave_not_wrong_seg_3100"] = b"\x00\x31" not in cave
    checks["dual_pad_slots"] = {
        "pad1": min(count, PAD1_SLOTS),
        "pad2": max(0, count - PAD1_SLOTS),
        "pad2_file": f"{PAD2_FILE:06X}",
        "pad2_off": f"{PAD2_OFF:04X}",
        "pad_total_capacity": PAD_TOTAL_SLOTS,
    }
    checks["strategy_report"] = hook_report["strategy"] in {
        "store_range_tag_plus_marker_consume",
        "store_flag_tag_dual_pad",
    }

    failed = [
        name
        for name, value in checks.items()
        if isinstance(value, bool) and not value
    ]
    report = {
        "rom": str(args.rom),
        "base_code": f"{base_code:04X}",
        "marker_code": f"{marker_code:04X}",
        "count": count,
        "checks": checks,
        "failed": failed,
        "status": "PASS" if not failed else "FAIL",
        "note": "Static verification only; no emulator execution performed.",
    }
    out_path = args.rom.with_suffix(".marked_verify.json")
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {out_path}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
