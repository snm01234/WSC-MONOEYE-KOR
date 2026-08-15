#!/usr/bin/env python3
"""Render the confirmed compact 16-byte font records from segment 40."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import load_rom  # noqa: E402

FONT_BASE = 0x400000
FONT_TABLE = 0x440
RECORD_SIZE = 16


def code_to_index(code: int) -> int:
    return code if code < 0xE000 else code - 0xDF20


def decode_record(record: bytes, low_first: bool = True) -> list[list[int]]:
    """16 bytes = 8 rows × 8 packed 2bpp logical pixels."""
    pixels: list[int] = []
    for byte in record:
        shifts = (0, 2, 4, 6) if low_first else (6, 4, 2, 0)
        pixels.extend((byte >> shift) & 3 for shift in shifts)
    return [pixels[row * 8 : (row + 1) * 8] for row in range(8)]


def render(canvas: list[list[int]], scale: int = 10) -> Image.Image:
    # Palette 0 is normally transparent; use direct luminance for all four values.
    palette = (255, 170, 85, 0)
    image = Image.new("L", (8, 8), 255)
    for y, row in enumerate(canvas):
        for x, value in enumerate(row):
            image.putpixel((x, y), palette[value])
    return image.resize((8 * scale, 8 * scale), Image.Resampling.NEAREST).convert("RGB")


def main() -> None:
    rom = load_rom()
    out = ROOT / "out" / "patch" / "compact_font_probe"
    out.mkdir(parents=True, exist_ok=True)
    known = {
        0x01: "space",
        0x02: "ellipsis",
        0x03: "exclamation",
        0x04: "hiragana_i",
        0x1D: "question",
        0x39: "kanji_sen",
        0x80: "kanji_ki",
        0x82: "long_mark",
        0xE000: "kanji_jo",
        0xE03F: "katakana_chi",
        0xE100: "kanji_higashi",
        0xE73D: "kanji_kan",
    }
    sheet = Image.new("RGB", (len(known) * 80, 160), "white")
    for column, (code, name) in enumerate(known.items()):
        index = code_to_index(code)
        offset = FONT_BASE + FONT_TABLE + index * RECORD_SIZE
        record = bytes(rom[offset : offset + RECORD_SIZE])
        for row, low_first in enumerate((True, False)):
            image = render(decode_record(record, low_first))
            sheet.paste(image, (column * 80, row * 80))
            image.save(out / f"{code:04X}_{name}_{'lo' if low_first else 'hi'}.png")
        print(
            f"{code:04X} index={index:03X} offset={offset:06X} "
            f"values={Counter(decode_record(record, True)[0])}"
        )
    sheet.save(out / "comparison.png")
    print(f"Wrote {out / 'comparison.png'}")


if __name__ == "__main__":
    main()
