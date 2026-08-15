#!/usr/bin/env python3
r"""
Render a ROM range as 16x16 glyphs assembled from four 8x8 4bpp tiles.

The intermission labels (bank 54) are 16x16 characters built from four tiles:
top-left at ``T``, top-right at ``T+0x20``, and the lower halves one screen row
away at ``T+delta`` and ``T+delta+0x20``. ``delta`` is constant inside a band but
differs between bands because the atlas is deduplicated per screen row (0x2A0 for
the top label row, 0x380 for the bottom system row).

Replacing a glyph needs only the four addresses, not the tilemap: the game keeps
pointing at the same tiles, so overwriting them changes the character wherever it
appears. This renders every candidate so the addresses can be confirmed by reading
the sheet rather than by trusting a heuristic -- the seam-continuity score was
tried and rejected, it picks 0x2C0 for the band that visibly needs 0x2A0.

Usage::

    python tools/render_glyph_sheet.py --lo 544000 --hi 548000 --delta 2A0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageDraw  # noqa: E402

from monoeye_rom import find_rom, load_rom  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402


def glyph(rom: bytes, top: int, delta: int) -> tuple[Image.Image, int]:
    """16x16 image plus its ink pixel count (ink = index != 0, the transparent one)."""
    img = Image.new("RGB", (16, 16))
    px = img.load()
    ink = 0
    for oy, base in ((0, top), (8, top + delta)):
        for ox, off in ((0, base), (8, base + 0x20)):
            if off < 0 or off + 32 > len(rom):
                continue
            t = tiles_4bpp(rom[off : off + 32])[0]
            for y in range(8):
                for x in range(8):
                    v = t[y][x]
                    if v != 0:
                        ink += 1
                    px[ox + x, oy + y] = GREYS_16[v]
    return img, ink


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--lo", type=lambda s: int(s, 16), default=0x544000)
    ap.add_argument("--hi", type=lambda s: int(s, 16), default=0x548000)
    ap.add_argument("--delta", type=lambda s: int(s, 16), required=True)
    ap.add_argument("--step", type=lambda s: int(s, 16), default=0x40, help="glyph pitch in ROM")
    ap.add_argument("--cols", type=int, default=24)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--min-ink", type=int, default=20, help="skip near-empty candidates")
    ap.add_argument("--max-ink", type=int, default=230, help="skip near-solid candidates")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    rom = bytes(load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT)))
    cells = []
    for top in range(args.lo, args.hi, args.step):
        img, ink = glyph(rom, top, args.delta)
        if args.min_ink <= ink <= args.max_ink:
            cells.append((top, img))
    if not cells:
        print("no candidates in range")
        return 1

    cols = args.cols
    rows = (len(cells) + cols - 1) // cols
    cell = 16 * args.scale
    label_h = 8
    sheet = Image.new("RGB", (cols * cell, rows * (cell + label_h)), (24, 24, 40))
    draw = ImageDraw.Draw(sheet)
    for i, (top, img) in enumerate(cells):
        cx, cy = (i % cols) * cell, (i // cols) * (cell + label_h)
        sheet.paste(img.resize((cell, cell), Image.NEAREST), (cx, cy))
        draw.text((cx + 1, cy + cell - 1), f"{top & 0xFFFF:04X}", fill=(180, 180, 255))

    out = args.out or (
        ROOT / "out" / "title_menu_capture" / "tiles"
        / f"glyphs_{args.lo:06X}_{args.hi:06X}_d{args.delta:X}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"{len(cells)} candidate glyphs (delta 0x{args.delta:X}, step 0x{args.step:X}) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
