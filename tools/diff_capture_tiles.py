#!/usr/bin/env python3
r"""
Locate *where* two captures differ, in 8x8 screen-tile units.

An MD5 mismatch only says "something moved". The gate this project set for the
title menu is stronger: mutating one ROM tile must change **one** on-screen tile.
That needs the changed pixels localised, so this reports the changed 8x8 blocks
of the 224x144 WonderSwan framebuffer and writes a highlighted overlay.

Usage::

    python tools/diff_capture_tiles.py baseline.png candidate.png --overlay out.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops


def block_diff(a: Image.Image, b: Image.Image, block: int = 8):
    if a.size != b.size:
        raise SystemExit(f"size mismatch {a.size} vs {b.size}")
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    px = diff.load()
    w, h = diff.size
    blocks = []
    total_px = 0
    for by in range(0, h, block):
        for bx in range(0, w, block):
            n = 0
            for y in range(by, min(by + block, h)):
                for x in range(bx, min(bx + block, w)):
                    if px[x, y] != (0, 0, 0):
                        n += 1
            if n:
                blocks.append({"col": bx // block, "row": by // block, "px": n})
                total_px += n
    return blocks, total_px, diff.size


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline", type=Path)
    ap.add_argument("candidate", type=Path)
    ap.add_argument("--block", type=int, default=8)
    ap.add_argument("--overlay", type=Path, default=None)
    ap.add_argument("--scale", type=int, default=3)
    args = ap.parse_args(argv)

    a = Image.open(args.baseline)
    b = Image.open(args.candidate)
    blocks, total, size = block_diff(a, b, args.block)

    print(f"framebuffer {size[0]}x{size[1]}  block={args.block}")
    print(f"changed blocks: {len(blocks)}   changed pixels: {total}")
    if blocks:
        cols = [x["col"] for x in blocks]
        rows = [x["row"] for x in blocks]
        print(f"bounding box: col {min(cols)}-{max(cols)}  row {min(rows)}-{max(rows)}")
        for x in blocks[:64]:
            print(f"  block col={x['col']:3d} row={x['row']:3d}  px={x['px']}")
        if len(blocks) > 64:
            print(f"  ... {len(blocks) - 64} more")

    if args.overlay and blocks:
        out = b.convert("RGB").copy()
        px = out.load()
        for blk in blocks:
            x0, y0 = blk["col"] * args.block, blk["row"] * args.block
            for i in range(args.block):
                for (x, y) in (
                    (x0 + i, y0),
                    (x0 + i, y0 + args.block - 1),
                    (x0, y0 + i),
                    (x0 + args.block - 1, y0 + i),
                ):
                    if 0 <= x < out.width and 0 <= y < out.height:
                        px[x, y] = (255, 0, 255)
        if args.scale > 1:
            out = out.resize((out.width * args.scale, out.height * args.scale), Image.NEAREST)
        args.overlay.parent.mkdir(parents=True, exist_ok=True)
        out.save(args.overlay)
        print(f"overlay -> {args.overlay}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
