#!/usr/bin/env python3
r"""
Render a ROM range as WonderSwan tiles so graphics candidates can be read by eye.

The title-menu hunt narrowed the initial menu's source to ``72:0000-17FF`` by
zeroing slices and watching the screenshot hash change, but nobody has yet said
*what* those 6 KB are. This renders the range in each plausible WonderSwan tile
format and writes one PNG per format, upscaled, so the katakana button plates are
either visible or provably absent.

Formats
    ``4bpp``  packed 4bpp, 8x8 tile = 32 bytes, 2 pixels per byte (WSC)
    ``2bpp``  planar 2bpp, 8x8 tile = 16 bytes, 2 bitplane bytes per row (WS/WSC)

Usage::

    python tools/render_bank_tiles.py --lo 720000 --hi 7217FF --cols 16 --scale 6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image  # noqa: E402

from monoeye_rom import find_rom, load_rom  # noqa: E402

GREYS_16 = [(i * 17, i * 17, i * 17) for i in range(16)]
GREYS_4 = [(0, 0, 0), (85, 85, 85), (170, 170, 170), (255, 255, 255)]


def tiles_4bpp(data: bytes) -> list[list[list[int]]]:
    """Packed 4bpp: 32 bytes per 8x8 tile, high nibble is the left pixel."""
    out = []
    for t in range(len(data) // 32):
        blk = data[t * 32 : t * 32 + 32]
        rows = []
        for y in range(8):
            row = []
            for x in range(4):
                b = blk[y * 4 + x]
                row.append(b >> 4)
                row.append(b & 0x0F)
            rows.append(row)
        out.append(rows)
    return out


def tiles_2bpp(data: bytes) -> list[list[list[int]]]:
    """Planar 2bpp: 16 bytes per 8x8 tile, two plane bytes per row."""
    out = []
    for t in range(len(data) // 16):
        blk = data[t * 16 : t * 16 + 16]
        rows = []
        for y in range(8):
            p0, p1 = blk[y * 2], blk[y * 2 + 1]
            row = []
            for x in range(8):
                bit = 7 - x
                row.append(((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1))
            rows.append(row)
        out.append(rows)
    return out


def render(tiles, palette, cols: int, scale: int) -> Image.Image:
    rows = (len(tiles) + cols - 1) // cols
    img = Image.new("RGB", (cols * 8, rows * 8), (255, 0, 255))
    px = img.load()
    for i, tile in enumerate(tiles):
        ox, oy = (i % cols) * 8, (i // cols) * 8
        for y in range(8):
            for x in range(8):
                px[ox + x, oy + y] = palette[tile[y][x]]
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    return img


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--lo", type=lambda s: int(s, 16), default=0x720000)
    ap.add_argument("--hi", type=lambda s: int(s, 16), default=0x7217FF)
    ap.add_argument("--cols", type=int, default=16)
    ap.add_argument("--scale", type=int, default=6)
    ap.add_argument("--formats", default="4bpp,2bpp")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out" / "title_menu_capture" / "tiles")
    args = ap.parse_args(argv)

    rom = load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT))
    data = bytes(rom[args.lo : args.hi + 1])
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for fmt in [f.strip() for f in args.formats.split(",") if f.strip()]:
        if fmt == "4bpp":
            tiles, palette = tiles_4bpp(data), GREYS_16
        elif fmt == "2bpp":
            tiles, palette = tiles_2bpp(data), GREYS_4
        else:
            raise SystemExit(f"unknown format: {fmt}")
        img = render(tiles, palette, args.cols, args.scale)
        out = args.out_dir / f"{args.lo:06X}_{args.hi:06X}_{fmt}_c{args.cols}.png"
        img.save(out)
        print(f"{fmt}: {len(tiles)} tiles -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
