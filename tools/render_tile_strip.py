#!/usr/bin/env python3
r"""
Render a 16 px tall run of tiles: top row from ``--lo``, bottom row ``--delta`` away.

The intermission screen (bank 54) is a tile atlas plus a tilemap, not a flat
bitmap, so a label's upper and lower halves are not adjacent in ROM. Measured
against ``out/title_trace/intermission.png``, the offset from a tile to the tile
below it on screen is constant within a band (0x2C0 for the top label row, 0x380
for the bottom system row, 0x1C0 in the minimap). This renders a band so the
addresses can be confirmed by reading the result instead of trusting the fit.

Usage::

    python tools/render_tile_strip.py --lo 546D40 --delta 380 --count 28
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image  # noqa: E402

from monoeye_rom import find_rom, load_rom  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402


def strip(rom: bytes, lo: int, delta: int, count: int, palette=GREYS_16) -> Image.Image:
    img = Image.new("RGB", (count * 8, 16))
    px = img.load()
    for c in range(count):
        for half, base in ((0, lo), (1, lo + delta)):
            off = base + c * 0x20
            if off < 0 or off + 32 > len(rom):
                continue
            t = tiles_4bpp(rom[off : off + 32])[0]
            for y in range(8):
                for x in range(8):
                    px[c * 8 + x, half * 8 + y] = palette[t[y][x]]
    return img


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--lo", type=lambda s: int(s, 16), required=True)
    ap.add_argument("--delta", type=lambda s: int(s, 16), required=True)
    ap.add_argument("--count", type=int, default=28)
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument(
        "--ruler",
        type=int,
        default=0,
        metavar="N",
        help="draw a magenta line every N cells so cell indices can be read off "
        "(use 2 for 16 px glyph cells)",
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    rom = bytes(load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT)))
    img = strip(rom, args.lo, args.delta, args.count)
    img = img.resize((img.width * args.scale, img.height * args.scale), Image.NEAREST)
    if args.ruler:
        px = img.load()
        for c in range(0, args.count + 1, args.ruler):
            x = min(c * 8 * args.scale, img.width - 1)
            for y in range(img.height):
                px[x, y] = (255, 0, 255)
    out = args.out or (
        ROOT / "out" / "title_menu_capture" / "tiles" / f"strip_{args.lo:06X}_d{args.delta:X}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"{args.count} cells from {args.lo:06X}, delta 0x{args.delta:X} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
