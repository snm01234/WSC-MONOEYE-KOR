#!/usr/bin/env python3
r"""
Extract the intermission label characters: which four ROM tiles form each glyph.

Input is the resolved tilemap (``resolve_tilemap.py``) built from a native 224x144
intermission capture. Two facts make the extraction mechanical:

* The label characters are drawn on an **overlay layer**: the tile carries the
  glyph over palette index 0, which shows the plate underneath. So a glyph tile is
  identified by content -- a healthy amount of index 0 plus a moderate amount of
  ink -- with no reference to the tilemap.
* A character is **16x16**, i.e. a 2x2 block of those tiles.

Replacing a character needs only its four addresses; the tilemap keeps pointing at
the same tiles, so the change follows the character to every screen that uses it.

Output: ``out/title_menu_capture/intermission_labels.json`` plus a contact sheet of
every recovered 16x16 character, so the addresses can be confirmed by reading the
sheet rather than trusting the classifier.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageDraw  # noqa: E402

from monoeye_rom import find_rom, load_rom  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402

DEFAULT_MAP = ROOT / "out" / "title_menu_capture" / "intermission_overlay_resolved.json"


def tile_stats(rom: bytes, off: int) -> tuple[int, int, int]:
    """(transparent px, ink px, distinct ink indices)"""
    t = tiles_4bpp(rom[off : off + 32])[0]
    flat = [v for row in t for v in row]
    zero = sum(1 for v in flat if v == 0)
    return zero, 64 - zero, len(set(v for v in flat if v != 0))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--map", type=Path, default=DEFAULT_MAP)
    ap.add_argument("--cols", type=int, default=28)
    ap.add_argument("--rows", type=int, default=18)
    ap.add_argument("--min-transparent", type=int, default=8)
    ap.add_argument("--min-ink", type=int, default=8)
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "title_menu_capture" / "intermission_labels.json")
    args = ap.parse_args(argv)

    rom = bytes(load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT)))
    data = json.loads(args.map.read_text(encoding="utf-8"))
    resolved: dict[tuple[int, int], int] = {}
    for r, info in data["rows"].items():
        for c, off in info["resolved"].items():
            resolved[(int(c), int(r))] = int(off, 16)

    glyph: dict[tuple[int, int], int] = {}
    for (c, r), off in resolved.items():
        zero, ink, n = tile_stats(rom, off)
        if zero >= args.min_transparent and ink >= args.min_ink and n >= 2:
            glyph[(c, r)] = off

    print(f"{len(resolved)} resolved blocks, {len(glyph)} look like overlay glyph tiles")
    print("glyph-tile map ('#' = glyph, 'o' = resolved but not glyph):")
    print("    " + "".join(str(c % 10) for c in range(args.cols)))
    for r in range(args.rows):
        line = "".join(
            "#" if (c, r) in glyph else ("o" if (c, r) in resolved else ".")
            for c in range(args.cols)
        )
        print(f"{r:3d} {line}")

    # 2x2 grouping: walk each row pair, take glyph columns two at a time
    cells = []
    for r in range(args.rows - 1):
        c = 0
        while c < args.cols - 1:
            quad = [(c, r), (c + 1, r), (c, r + 1), (c + 1, r + 1)]
            if all(q in glyph for q in quad):
                cells.append(
                    {
                        "col": c,
                        "row": r,
                        "tiles": [f"{glyph[q]:06X}" for q in quad],
                        "tiles_int": [glyph[q] for q in quad],
                    }
                )
                c += 2
            else:
                c += 1

    print(f"\n{len(cells)} 16x16 character cells recovered")
    for cell in cells:
        print(f"  col {cell['col']:2d} row {cell['row']:2d}  " + " ".join(cell["tiles"]))

    if cells:
        cols = 16
        rows = (len(cells) + cols - 1) // cols
        cw = 16 * args.scale
        lab = 9
        sheet = Image.new("RGB", (cols * cw, rows * (cw + lab)), (20, 20, 36))
        draw = ImageDraw.Draw(sheet)
        for i, cell in enumerate(cells):
            img = Image.new("RGB", (16, 16))
            px = img.load()
            for k, (ox, oy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
                t = tiles_4bpp(rom[cell["tiles_int"][k] : cell["tiles_int"][k] + 32])[0]
                for y in range(8):
                    for x in range(8):
                        px[ox + x, oy + y] = GREYS_16[t[y][x]]
            bx, by = (i % cols) * cw, (i // cols) * (cw + lab)
            sheet.paste(img.resize((cw, cw), Image.NEAREST), (bx, by))
            draw.text((bx + 1, by + cw - 1), f"c{cell['col']}r{cell['row']}", fill=(170, 170, 255))
        p = args.out.with_name("intermission_chars.png")
        sheet.save(p)
        print(f"contact sheet -> {p}")

    args.out.write_text(
        json.dumps(
            {
                "rom": str(args.rom or find_rom(ROOT)),
                "map": str(args.map),
                "cells": [{k: v for k, v in c.items() if k != "tiles_int"} for c in cells],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
