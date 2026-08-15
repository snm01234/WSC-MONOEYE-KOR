#!/usr/bin/env python3
"""Render the bank-54 confirmation-focus atlas with tile coordinates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402

START = 0x547CFC


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rows", type=int, default=22)
    ap.add_argument("--scale", type=int, default=7)
    args = ap.parse_args()

    rom = args.rom.read_bytes()
    base = stock_base(rom)
    body = rom[base : base + 0x800000]
    tiles = tiles_4bpp(body[START : START + 12 * args.rows * 0x20])
    margin_x, margin_y = 22, 13
    image = Image.new(
        "RGB", (margin_x + 12 * 8 * args.scale, margin_y + args.rows * 8 * args.scale), (30, 30, 30)
    )
    draw = ImageDraw.Draw(image)
    for index, tile in enumerate(tiles):
        col, row = index % 12, index // 12
        ox = margin_x + col * 8 * args.scale
        oy = margin_y + row * 8 * args.scale
        cell = Image.new("RGB", (8, 8))
        pixels = cell.load()
        for y in range(8):
            for x in range(8):
                pixels[x, y] = GREYS_16[tile[y][x]]
        image.paste(cell.resize((8 * args.scale, 8 * args.scale), Image.NEAREST), (ox, oy))
    for col in range(13):
        x = margin_x + col * 8 * args.scale
        draw.line((x, margin_y, x, image.height), fill=(255, 0, 0))
        if col < 12:
            draw.text((x + 2, 0), str(col), fill=(255, 255, 0))
    for row in range(args.rows + 1):
        y = margin_y + row * 8 * args.scale
        draw.line((margin_x, y, image.width, y), fill=(255, 0, 0))
        if row < args.rows:
            draw.text((0, y + 2), str(row), fill=(255, 255, 0))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
