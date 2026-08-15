#!/usr/bin/env python3
"""Render known characters under plausible WSC packed-font layouts."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import load_rom  # noqa: E402

FONT_BASE = 0x400000
FONT_START = 0x40
STRIDE = 128


def packed_pixels(data: bytes, low_first: bool) -> list[int]:
    pixels: list[int] = []
    for byte in data:
        pair = (byte & 0xF, byte >> 4) if low_first else (byte >> 4, byte & 0xF)
        pixels.extend(pair)
    return pixels


def linear_16x16(data: bytes, low_first: bool) -> list[list[int]]:
    pixels = packed_pixels(data, low_first)
    return [pixels[y * 16 : (y + 1) * 16] for y in range(16)]


def tile_8x8(data: bytes, low_first: bool) -> list[list[int]]:
    pixels = packed_pixels(data, low_first)
    return [pixels[y * 8 : (y + 1) * 8] for y in range(8)]


def tiled_16x16(
    data: bytes, low_first: bool, order: tuple[tuple[int, int], ...]
) -> list[list[int]]:
    canvas = [[0] * 16 for _ in range(16)]
    for index, (oy, ox) in enumerate(order):
        tile = tile_8x8(data[index * 32 : (index + 1) * 32], low_first)
        for y in range(8):
            for x in range(8):
                canvas[oy + y][ox + x] = tile[y][x]
    return canvas


def image(canvas: list[list[int]], scale: int = 6) -> Image.Image:
    # Treat the most common value as transparent/background and every other value as ink.
    flat = [value for row in canvas for value in row]
    background = max(set(flat), key=flat.count)
    img = Image.new("RGB", (16, 16), "white")
    for y, row in enumerate(canvas):
        for x, value in enumerate(row):
            if value != background:
                # Preserve intensity enough to distinguish antialias/palette values.
                shade = max(0, 160 - value * 8)
                img.putpixel((x, y), (shade, shade, shade))
    return img.resize((16 * scale, 16 * scale), Image.Resampling.NEAREST)


def main() -> None:
    rom = load_rom()
    out = ROOT / "out" / "patch" / "font_format_probe"
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
    }
    layouts = {
        "linear_hi": lambda raw: linear_16x16(raw, False),
        "linear_lo": lambda raw: linear_16x16(raw, True),
        "tiles_row_hi": lambda raw: tiled_16x16(
            raw, False, ((0, 0), (0, 8), (8, 0), (8, 8))
        ),
        "tiles_col_hi": lambda raw: tiled_16x16(
            raw, False, ((0, 0), (8, 0), (0, 8), (8, 8))
        ),
    }

    sheet = Image.new("RGB", (len(known) * 96, len(layouts) * 96), "white")
    for column, (code, name) in enumerate(known.items()):
        offset = FONT_BASE + FONT_START + code * STRIDE
        raw = bytes(rom[offset : offset + STRIDE])
        for row, (layout_name, decoder) in enumerate(layouts.items()):
            rendered = image(decoder(raw))
            sheet.paste(rendered, (column * 96, row * 96))
            rendered.save(out / f"{code:02X}_{name}_{layout_name}.png")
    sheet.save(out / "comparison.png")

    # Raw 8x8 packed-tile atlas, which tests whether bank 40 is a regular tile sheet
    # rather than one contiguous 128-byte record per character.
    atlas = Image.new("RGB", (32 * 32, 16 * 32), "white")
    for index in range(32 * 16):
        offset = FONT_BASE + index * 32
        tile = tile_8x8(bytes(rom[offset : offset + 32]), low_first=False)
        flat = [value for row in tile for value in row]
        background = max(set(flat), key=flat.count)
        rendered = Image.new("RGB", (8, 8), "white")
        for y, row_values in enumerate(tile):
            for x, value in enumerate(row_values):
                if value != background:
                    rendered.putpixel((x, y), (0, 0, 0))
        atlas.paste(
            rendered.resize((32, 32), Image.Resampling.NEAREST),
            ((index % 32) * 32, (index // 32) * 32),
        )
    atlas.save(out / "packed_tile_atlas.png")
    print(f"Wrote {out / 'comparison.png'}")


if __name__ == "__main__":
    main()
