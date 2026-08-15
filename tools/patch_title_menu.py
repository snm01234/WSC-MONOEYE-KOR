#!/usr/bin/env python3
"""Preserve a disproven title-menu patch experiment.

The following matching strings exist in a null-terminated table in bank 75:
  75:B7A4  PUSH START BUTTON
  75:B7C5  ニュ－ゲ－ム
  75:B7CD  コンティニュ－
  75:B7D5  オプション

BizHawk testing proved that changing this table and the corresponding compact
glyphs does NOT change the visible initial title/menu. The table is not the
runtime source for that screen. Keep this tool only to reproduce the failed
hypothesis documented in docs/TITLE_MENU_FAILED_EXPERIMENT.md; do not use its
output as a playable or distributable patch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_hangul_font import render_compact_glyph, render_preview  # noqa: E402
from font_pipeline import find_system_font  # noqa: E402
from monoeye_rom import (  # noqa: E402
    compact_font_file_offset,
    encode_compact_font_record,
    find_rom,
    load_rom,
    update_ws_checksum,
)


TITLE_STRINGS = {
    0x75B7A4: bytes.fromhex(
        "E2 CC E2 9E E1 69 E3 8A E6 2F E1 69 E2 06 E1 C0 "
        "E4 83 E2 06 E6 68 E2 9E E2 06 E2 06 E1 66 E1 80"
    ),
    0x75B7C5: bytes.fromhex("60 78 10 E0 CA 10 3F"),
    0x75B7CD: bytes.fromhex("5E 1A 4A 46 60 78 10"),
    0x75B7D5: bytes.fromhex("2C 6C 2D E0 BF 1A"),
}

# Reuse glyphs that are already rendered on the title screen. This avoids the
# unsafe E740 UI-slot overwrite and makes the PoC independent of loader hooks.
HANGUL_CODE_MAP = {
    "시": 0xE2CC,  # Ｐ
    "작": 0xE29E,  # Ｕ
    "버": 0xE169,  # Ｓ
    "튼": 0xE38A,  # Ｈ
    "새": 0xE206,  # Ｔ
    "게": 0xE1C0,  # Ａ
    "임": 0xE483,  # Ｒ
    "계": 0xE668,  # Ｂ
    "속": 0xE166,  # Ｏ
    "설": 0xE180,  # Ｎ
    "정": 0xE0CA,  # ゲ
}

MENU_TEXT = {
    0x75B7A4: "시작 버튼",
    0x75B7C5: "새 게임",
    # The stock record is seven bytes, so use the natural compact label.
    0x75B7CD: "계속",
    0x75B7D5: "설정",
}


def encode_title_text(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        if ch == " ":
            out.append(0x01)
        else:
            code = HANGUL_CODE_MAP[ch]
            out.extend(code.to_bytes(2, "big") if code > 0xFF else bytes([code]))
    return bytes(out)


def patch_fixed_record(
    rom: bytearray, offset: int, expected: bytes, replacement: bytes
) -> dict:
    actual = bytes(rom[offset : offset + len(expected)])
    terminator = rom[offset + len(expected)]
    if actual != expected or terminator != 0:
        raise RuntimeError(
            f"Unexpected title record at {offset:06X}: "
            f"{actual.hex(' ')} term={terminator:02X}"
        )
    if len(replacement) > len(expected):
        raise RuntimeError(
            f"Replacement at {offset:06X} is too long: "
            f"{len(replacement)} > {len(expected)}"
        )
    padded = replacement + b"\x00" * (len(expected) - len(replacement))
    rom[offset : offset + len(expected)] = padded
    return {
        "offset": f"{offset:06X}",
        "stock_len": len(expected),
        "replacement_len": len(replacement),
        "replacement_hex": replacement.hex(" "),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_title_menu.wsc",
    )
    args = ap.parse_args()
    source = args.rom or find_rom(ROOT)
    rom = bytearray(load_rom(source))

    font_path = find_system_font()
    if not font_path:
        raise SystemExit("No Korean system font found")

    glyph_report = []
    preview = Image.new("RGB", (80 * len(HANGUL_CODE_MAP), 80), "white")
    for column, (char, code) in enumerate(HANGUL_CODE_MAP.items()):
        pixels = render_compact_glyph(char, font_path)
        record = encode_compact_font_record(pixels)
        offset = compact_font_file_offset(code)
        old = bytes(rom[offset : offset + 16])
        rom[offset : offset + 16] = record
        preview.paste(render_preview(pixels), (column * 80, 0))
        glyph_report.append(
            {
                "char": char,
                "code": f"{code:04X}",
                "font_offset": f"{offset:06X}",
                "old": old.hex(" "),
                "new": record.hex(" "),
            }
        )

    string_report = []
    for offset, stock in TITLE_STRINGS.items():
        replacement = encode_title_text(MENU_TEXT[offset])
        item = patch_fixed_record(rom, offset, stock, replacement)
        item["text"] = MENU_TEXT[offset]
        string_report.append(item)

    checksum = update_ws_checksum(rom)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(rom)
    preview_path = args.out.with_suffix(".glyphs.png")
    preview.save(preview_path)
    report_path = args.out.with_suffix(".json")
    report_path.write_text(
        json.dumps(
            {
                "source": str(source),
                "output": str(args.out),
                "strategy": "DISPROVEN title-local stock glyph reassignment",
                "title_table": "75:B7A4-75:B7DB",
                "checksum": f"{checksum:04X}",
                "strings": string_report,
                "glyphs": glyph_report,
                "known_scope": (
                    "Known failed experiment: visible initial title/menu remains "
                    "unchanged. Reassigned stock glyphs can corrupt later text."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.out}")
    print(f"Wrote {preview_path}")
    print(f"Wrote {report_path}")
    for item in string_report:
        print(f"  @{item['offset']} {item['text']} ({item['replacement_hex']})")


if __name__ == "__main__":
    main()
