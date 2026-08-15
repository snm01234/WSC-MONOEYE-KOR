#!/usr/bin/env python3
"""Statically identify the standalone ID-effect label 盾 and render its glyph.

This is intentionally read-only: it compares the stock ROM and current main TIP,
records the effect-name string context, and renders the compact-font record used
by the ordinary text path.  It does not patch either ROM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    compact_font_file_offset,
    decode_compact_font_record,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "data" / "monoeye.tbl"
OUT = ROOT / "out" / "patch" / "id_command_shield_static_analysis"

SHIELD_CODE = 0xE2F7
SHIELD_RAW = bytes.fromhex("E2F700")
SHIELD_LOGICAL = 0x67EBA1
SHIELD_DESCRIPTOR_LOGICAL = 0x67E7B4
SHIELD_DESCRIPTOR_RAW = bytes.fromhex("00A1EBE7")
CONTEXT_START = 0x67EA80
CONTEXT_END = 0x67ED20


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def hexoff(value: int) -> str:
    return f"{value:06X}"


def find_all(data: bytes | bytearray, needle: bytes) -> list[int]:
    hits: list[int] = []
    pos = 0
    while True:
        pos = data.find(needle, pos)
        if pos < 0:
            return hits
        hits.append(pos)
        pos += 1


def parse_zstrings(
    rom: bytes | bytearray,
    tbl: Tbl,
    dictionary: Dictionary,
    logical_start: int,
    logical_end: int,
) -> list[dict]:
    base = stock_base(rom)
    cursor = base + logical_start
    end = base + logical_end
    rows: list[dict] = []
    while cursor < end:
        while cursor < end and rom[cursor] == 0:
            cursor += 1
        if cursor >= end:
            break
        result = read_encoded_z_safe(rom, cursor, min(256, end - cursor))
        if result is None:
            cursor += 1
            continue
        payload, terminator = result
        # Reject obvious binary runs. This table is composed of short labels.
        text = dictionary.expand(payload, tbl)
        if payload and len(payload) <= 64 and "<BADDICT:" not in text:
            rows.append(
                {
                    "logical_start": hexoff(cursor - base),
                    "physical_start": hexoff(cursor),
                    "logical_end_inclusive": hexoff(terminator - base),
                    "raw_hex": payload.hex().upper(),
                    "text": text,
                    "contains_shield": SHIELD_RAW[:-1] in payload,
                }
            )
        cursor = terminator + 1
    return rows


def render_glyph(pixels: list[list[int]], scale: int = 12) -> Image.Image:
    # Transparent/background plus the three stock intensities.
    palette = [(248, 248, 248), (174, 174, 174), (92, 92, 92), (16, 16, 16)]
    image = Image.new("RGB", (8, 8), palette[0])
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            image.putpixel((x, y), palette[value & 3])
    return image.resize((8 * scale, 8 * scale), Image.Resampling.NEAREST)


def render_comparison(
    stock_record: bytes,
    main_record: bytes,
    stock_offset: int,
    main_offset: int,
    out_path: Path,
) -> None:
    scale = 12
    glyph_size = 8 * scale
    canvas = Image.new("RGB", (glyph_size * 2 + 48, glyph_size + 62), "white")
    draw = ImageDraw.Draw(canvas)
    canvas.paste(render_glyph(decode_compact_font_record(stock_record), scale), (12, 42))
    canvas.paste(
        render_glyph(decode_compact_font_record(main_record), scale),
        (glyph_size + 36, 42),
    )
    draw.text((12, 8), f"stock 盾  @{stock_offset:06X}", fill="black")
    draw.text((glyph_size + 36, 8), f"main 盾  @{main_offset:06X}", fill="black")
    draw.text((12, 25), "8x8 compact glyph (nearest-neighbor x12)", fill="black")
    canvas.save(out_path)


def pointer_patterns(data: bytes | bytearray, logical: int) -> dict[str, list[str]]:
    off16 = logical & 0xFFFF
    bank = (logical >> 16) & 0xFF
    patterns = {
        "le16_offset": off16.to_bytes(2, "little"),
        "le16_then_bank": off16.to_bytes(2, "little") + bytes([bank]),
        "bank_then_le16": bytes([bank]) + off16.to_bytes(2, "little"),
    }
    return {
        name: [hexoff(hit) for hit in find_all(data, pattern)]
        for name, pattern in patterns.items()
    }


def effect_descriptor_context(
    rom: bytes | bytearray, tbl: Tbl, dictionary: Dictionary
) -> list[dict]:
    """Decode the alternating data/name far descriptors around the shield row."""
    base = stock_base(rom)
    rows: list[dict] = []
    # Each effect row is two 4-byte descriptors: data first, display name second.
    for pair_start in range(0x67E7A8, 0x67E7C8, 8):
        data_desc = bytes(rom[base + pair_start : base + pair_start + 4])
        name_desc = bytes(rom[base + pair_start + 4 : base + pair_start + 8])
        data_target = data_desc[1] | (data_desc[2] << 8)
        name_target = name_desc[1] | (name_desc[2] << 8)
        name_bank = name_desc[3] & 0x7F
        name_logical = (name_bank << 16) | name_target
        result = read_encoded_z_safe(rom, base + name_logical, 64)
        payload = b"" if result is None else result[0]
        rows.append(
            {
                "pair_descriptor_logical": f"{pair_start:06X}-{pair_start + 7:06X}",
                "data_descriptor_hex": data_desc.hex().upper(),
                "data_target_runtime": f"{data_desc[3]:02X}:{data_target:04X}",
                "name_descriptor_logical": f"{pair_start + 4:06X}",
                "name_descriptor_hex": name_desc.hex().upper(),
                "name_target_runtime": f"{name_desc[3]:02X}:{name_target:04X}",
                "name_target_logical": f"{name_logical:06X}",
                "name_raw_hex": payload.hex().upper(),
                "name_text": dictionary.expand(payload, tbl),
                "is_shield_row": pair_start + 4 == SHIELD_DESCRIPTOR_LOGICAL,
            }
        )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", type=Path, default=STOCK)
    ap.add_argument("--main", type=Path, default=MAIN)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    tbl = Tbl.load(TBL_PATH)

    stock = load_rom(args.stock)
    stock_base_value = stock_base(stock)
    stock_dictionary = Dictionary(stock)
    stock_string_physical = stock_base_value + SHIELD_LOGICAL
    stock_glyph_offset = compact_font_file_offset(SHIELD_CODE)
    stock_record = bytes(stock[stock_glyph_offset : stock_glyph_offset + 16])
    context = parse_zstrings(
        stock, tbl, stock_dictionary, CONTEXT_START, CONTEXT_END
    )
    descriptor_context = effect_descriptor_context(stock, tbl, stock_dictionary)

    main_rom = load_rom(args.main)
    main_base_value = stock_base(main_rom)
    main_string_physical = main_base_value + SHIELD_LOGICAL
    main_glyph_offset = compact_font_file_offset(SHIELD_CODE)
    main_record = bytes(main_rom[main_glyph_offset : main_glyph_offset + 16])
    main_descriptor_physical = main_base_value + SHIELD_DESCRIPTOR_LOGICAL

    preview = args.out / "shield_compact_glyph_stock_vs_main.png"
    render_comparison(
        stock_record, main_record, stock_glyph_offset, main_glyph_offset, preview
    )

    bank4c_inventory = (
        ROOT
        / "out"
        / "patch"
        / "id_command_residual_static_analysis"
        / "residual_plaque_inventory.json"
    )
    inventory_doc = json.loads(bank4c_inventory.read_text(encoding="utf-8"))
    inventory_text = json.dumps(inventory_doc, ensure_ascii=False)

    stock_code_hits = find_all(stock, SHIELD_RAW[:-1])
    main_code_hits = find_all(main_rom, SHIELD_RAW[:-1])
    report = {
        "generated_by": "tools/analyze_id_command_shield_glyph.py",
        "mode": "read-only static analysis",
        "inputs": {
            "stock": str(args.stock.relative_to(ROOT)),
            "stock_size": len(stock),
            "stock_sha256": sha256(stock),
            "main": str(args.main.relative_to(ROOT)),
            "main_size": len(main_rom),
            "main_sha256": sha256(main_rom),
        },
        "standalone_shield_effect_name": {
            "text": "盾",
            "tbl_code": "E2F7",
            "logical_string_range": "67EBA1-67EBA3",
            "stock_physical_string_range": (
                f"{stock_string_physical:06X}-{stock_string_physical + 2:06X}"
            ),
            "main_physical_string_range": (
                f"{main_string_physical:06X}-{main_string_physical + 2:06X}"
            ),
            "expected_raw": SHIELD_RAW.hex().upper(),
            "stock_raw": bytes(stock[stock_string_physical : stock_string_physical + 3])
            .hex()
            .upper(),
            "main_raw": bytes(main_rom[main_string_physical : main_string_physical + 3])
            .hex()
            .upper(),
            "stock_main_byte_exact": (
                stock[stock_string_physical : stock_string_physical + 3]
                == main_rom[main_string_physical : main_string_physical + 3]
            ),
            "exact_E2F700_hits_stock": [hexoff(x) for x in find_all(stock, SHIELD_RAW)],
            "exact_E2F700_hits_main": [hexoff(x) for x in find_all(main_rom, SHIELD_RAW)],
            "all_E2F7_hits_stock": [hexoff(x) for x in stock_code_hits],
            "all_E2F7_hits_main": [hexoff(x) for x in main_code_hits],
            "stock_pointer_pattern_hits": pointer_patterns(stock, SHIELD_LOGICAL),
            "effect_name_far_descriptor": {
                "logical_range": "67E7B4-67E7B7",
                "stock_physical_range": "67E7B4-67E7B7",
                "main_physical_range": (
                    f"{main_descriptor_physical:06X}-{main_descriptor_physical + 3:06X}"
                ),
                "expected_hex": SHIELD_DESCRIPTOR_RAW.hex().upper(),
                "stock_hex": bytes(
                    stock[
                        SHIELD_DESCRIPTOR_LOGICAL : SHIELD_DESCRIPTOR_LOGICAL + 4
                    ]
                )
                .hex()
                .upper(),
                "main_hex": bytes(
                    main_rom[
                        main_descriptor_physical : main_descriptor_physical + 4
                    ]
                )
                .hex()
                .upper(),
                "runtime_target": "E7:EBA1",
                "logical_target": "67EBA1",
                "exact_descriptor_hits_stock": [
                    hexoff(x) for x in find_all(stock, SHIELD_DESCRIPTOR_RAW)
                ],
            },
        },
        "compact_font_glyph": {
            "code": "E2F7",
            "glyph_index": f"{SHIELD_CODE - 0xDF20:03X}",
            "stock_physical_record_range": (
                f"{stock_glyph_offset:06X}-{stock_glyph_offset + 15:06X}"
            ),
            "main_physical_record_range": (
                f"{main_glyph_offset:06X}-{main_glyph_offset + 15:06X}"
            ),
            "stock_record_hex": stock_record.hex().upper(),
            "main_record_hex": main_record.hex().upper(),
            "stock_main_byte_exact": stock_record == main_record,
            "preview": str(preview.relative_to(ROOT)),
        },
        "effect_name_table_context": context,
        "effect_descriptor_context": descriptor_context,
        "bank4c_precomposed_plaque_check": {
            "inventory": str(bank4c_inventory.relative_to(ROOT)),
            "inventory_mentions_literal_shield": "盾" in inventory_text,
            "original_plus_residual_inventory_count": int(
                inventory_doc.get("summary", {}).get("expanded_static_inventory", 0)
            ),
            "residual_inventory_entry_count": len(inventory_doc.get("residuals", [])),
            "shield_badge_logical_range": "4C4BB4-4C4CB3",
            "shield_badge_storage": "32x16 body plus shared right cap",
            "shield_badge_text": "盾!",
            "conclusion": (
                "A separate precomposed 盾! plaque body exists at 4C4BB4. "
                "This coexists with the zero-terminated standalone 盾 effect-name "
                "string at 67EBA1 and its compact-font glyph."
            ),
        },
        "conclusion": {
            "shield_output_exists": True,
            "classification": "two sources: standalone effect-name glyph and precomposed 盾! plaque",
            "bank4c_shield_plaque_exists": True,
            "current_main_still_japanese": True,
            "confidence": "high",
        },
    }
    report_path = args.out / "shield_static_analysis.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    print(f"Wrote {report_path}")
    print(f"Wrote {preview}")


if __name__ == "__main__":
    main()
