#!/usr/bin/env python3
r"""
Find where an on-screen 8x8 block is stored in ROM, independent of palette.

The title-menu hunt got its answer by rendering a suspected region and reading it.
That only works once you already suspect the region. This does the reverse: take a
capture, take one 8x8 block of it, and ask which ROM tiles could have produced it.

Matching is **palette-permutation invariant**. A tile is canonicalised by relabelling
its 64 values in order of first appearance, so a screen block (RGB) and a ROM tile
(4bpp index / 2bpp planar) compare directly without knowing the runtime palette.
That is what made the earlier raw-byte comparison in ``analyze_title_graphics.py``
useless: 843 hits, nearly all flat background. Here a block is only reported when it
has enough distinct colours to be selective, and the hit count is printed so a
non-unique answer is visible rather than assumed.

Usage::

    # every text-like block of a capture, 4bpp, 32-byte aligned
    python tools/find_screen_tile_in_rom.py out/title_trace/intermission.png

    # one block, wider search
    python tools/find_screen_tile_in_rom.py capture.png --col 6 --row 2 --stride 4
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image  # noqa: E402

from monoeye_rom import find_rom, load_rom  # noqa: E402


def canon(values) -> tuple:
    """Relabel by order of first appearance -> palette-independent signature."""
    seen: dict = {}
    out = []
    for v in values:
        if v not in seen:
            seen[v] = len(seen)
        out.append(seen[v])
    return tuple(out)


def screen_tile(img: Image.Image, col: int, row: int) -> list:
    px = img.load()
    return [px[col * 8 + x, row * 8 + y] for y in range(8) for x in range(8)]


def rom_tile_4bpp(rom: bytes, off: int) -> list:
    out = []
    for y in range(8):
        for x in range(4):
            b = rom[off + y * 4 + x]
            out.append(b >> 4)
            out.append(b & 0x0F)
    return out


def rom_tile_2bpp(rom: bytes, off: int) -> list:
    out = []
    for y in range(8):
        p0, p1 = rom[off + y * 2], rom[off + y * 2 + 1]
        for x in range(8):
            bit = 7 - x
            out.append(((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1))
    return out


def build_index(rom: bytes, fmt: str, stride: int) -> dict:
    reader = rom_tile_4bpp if fmt == "4bpp" else rom_tile_2bpp
    size = 32 if fmt == "4bpp" else 16
    index: dict = collections.defaultdict(list)
    for off in range(0, len(rom) - size, stride):
        index[canon(reader(rom, off))].append(off)
    return index


def build_tiles(rom: bytes, fmt: str, stride: int, min_ink: int) -> list:
    """Flat tiles for the overlay matcher, skipping near-empty ones."""
    reader = rom_tile_4bpp if fmt == "4bpp" else rom_tile_2bpp
    size = 32 if fmt == "4bpp" else 16
    out = []
    for off in range(0, len(rom) - size, stride):
        vals = reader(rom, off)
        if sum(1 for v in vals if v != 0) >= min_ink:
            out.append((off, vals))
    return out


def overlay_match(romvals, scr) -> bool:
    """Does this tile explain the block when index 0 is transparent?

    The intermission labels are drawn on an overlay layer: the glyph tile carries
    the character over index 0, and index-0 pixels show whatever background tile
    is underneath. So only the non-zero indices are constrained -- each must map
    to one screen colour, and different indices must map to different colours.
    """
    fwd: dict = {}
    used: set = set()
    for rv, sv in zip(romvals, scr):
        if rv == 0:
            continue
        prev = fwd.get(rv)
        if prev is None:
            if sv in used:
                return False
            fwd[rv] = sv
            used.add(sv)
        elif prev != sv:
            return False
    return len(fwd) >= 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=Path)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--format", choices=("4bpp", "2bpp"), default="4bpp")
    ap.add_argument("--stride", type=int, default=32, help="ROM scan stride in bytes")
    ap.add_argument("--col", type=int, default=None, help="single block column")
    ap.add_argument("--row", type=int, default=None, help="single block row")
    ap.add_argument(
        "--min-colors",
        type=int,
        default=4,
        help="skip blocks with fewer distinct colours (flat background matches everything)",
    )
    ap.add_argument("--max-hits", type=int, default=8, help="skip blocks with more hits than this")
    ap.add_argument(
        "--offset",
        default="auto",
        metavar="DX,DY",
        help="pixel offset of the 8x8 grid in the capture. 'auto' searches 0..7 in "
        "both axes, which is needed for window grabs: out/title_trace/intermission.png "
        "is 223x146, not the native 224x144, and aligns at dx=0 dy=2",
    )
    ap.add_argument("--map", type=Path, default=None, help="write the block -> ROM tile map as JSON")
    ap.add_argument(
        "--match",
        choices=("exact", "overlay"),
        default="exact",
        help="exact: the tile alone must explain the whole block (background layer). "
        "overlay: only the tile's non-zero indices are constrained, index 0 shows "
        "through (the layer the intermission labels are drawn on)",
    )
    ap.add_argument(
        "--min-ink",
        type=int,
        default=8,
        help="overlay mode: skip tiles with fewer non-zero pixels",
    )
    ap.add_argument(
        "--search-lo", type=lambda s: int(s, 16), default=None, help="restrict the ROM scan"
    )
    ap.add_argument("--search-hi", type=lambda s: int(s, 16), default=None)
    args = ap.parse_args(argv)

    img = Image.open(args.capture).convert("RGB")
    rom = bytes(load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT)))
    lo = args.search_lo or 0
    hi = args.search_hi or len(rom)
    window = rom[lo:hi]
    print(f"capture {img.width}x{img.height}")
    print(f"indexing ROM {lo:06X}-{hi:06X} as {args.format}, stride {args.stride}, match {args.match} ...")
    if args.match == "exact":
        index = build_index(window, args.format, args.stride)
        print(f"{len(index)} distinct tile signatures")
        tiles = None
    else:
        tiles = build_tiles(window, args.format, args.stride, args.min_ink)
        index = None
        print(f"{len(tiles)} candidate tiles with >= {args.min_ink} ink pixels")

    def locate(dx: int, dy: int):
        px = img.load()
        cols, rows = (img.width - dx) // 8, (img.height - dy) // 8
        out = []
        for r in range(rows):
            for c in range(cols):
                vals = [px[dx + c * 8 + x, dy + r * 8 + y] for y in range(8) for x in range(8)]
                if len(set(vals)) < args.min_colors:
                    continue
                if index is not None:
                    hits = [lo + h for h in index.get(canon(vals), [])]
                else:
                    hits = [lo + off for off, tv in tiles if overlay_match(tv, vals)]
                if not hits or len(hits) > args.max_hits:
                    continue
                out.append({"col": c, "row": r, "colors": len(set(vals)), "hits": hits})
        return out

    if args.offset == "auto":
        scored = []
        for dy in range(8):
            for dx in range(8):
                scored.append((len(locate(dx, dy)), dx, dy))
        scored.sort(reverse=True)
        for n, dx, dy in scored[:3]:
            print(f"  offset dx={dx} dy={dy}: {n} located")
        _, dx, dy = scored[0]
    else:
        dx, dy = (int(v) for v in args.offset.split(","))
    print(f"using offset dx={dx} dy={dy}")

    blocks = (
        [b for b in locate(dx, dy) if b["col"] == args.col and b["row"] == args.row]
        if args.col is not None and args.row is not None
        else locate(dx, dy)
    )
    for b in blocks:
        where = " ".join(f"{h:06X}" for h in b["hits"][:8])
        print(f"  block col={b['col']:3d} row={b['row']:3d} colors={b['colors']:2d} "
              f"hits={len(b['hits'])}  {where}")
    print(f"\n{len(blocks)} block(s) located")

    banks = collections.Counter(b["hits"][0] >> 16 for b in blocks)
    if banks:
        print("first-hit banks: " + ", ".join(f"{k:02X}={v}" for k, v in sorted(banks.items())))

    if args.map:
        import json

        args.map.parent.mkdir(parents=True, exist_ok=True)
        args.map.write_text(
            json.dumps(
                {
                    "capture": str(args.capture),
                    "format": args.format,
                    "stride": args.stride,
                    "offset": [dx, dy],
                    "blocks": [
                        {**b, "hits": [f"{h:06X}" for h in b["hits"]]} for b in blocks
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"map -> {args.map}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
