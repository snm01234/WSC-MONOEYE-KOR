#!/usr/bin/env python3
"""
Show what tools/menu_plate_model.py recovers, before anything is written to a ROM.

Per state group: the reconstructed background and a per-pixel agreement map.
Per requested plate: original / background / label mask / label-only, side by side.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image  # noqa: E402

from menu_plate_model import PLATE_H, PLATE_W, Atlas, render_grid  # noqa: E402
from monoeye_rom import find_rom, load_rom  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--plates", default="1,6,7")
    ap.add_argument("--scale", type=int, default=5)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out" / "title_menu_capture" / "model")
    args = ap.parse_args(argv)

    rom = bytes(load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT)))
    at = Atlas(rom)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pal = at.palette

    print(f"canonical group: {at.canonical}  unresolved px: {len(at.canonical_holes)}")
    print("state groups (signature = two leftmost tiles):")
    for name, g in at.groups.items():
        remap = at.remaps[name]
        ident = "identity" if all(k == v for k, v in remap.items()) else (
            " ".join(f"{k:X}>{v:X}" for k, v in sorted(remap.items()) if k != v) or "own-recovery"
        )
        print(
            f"  {name}: {len(g.members):2d} members {g.members}  "
            f"stroke={g.stroke:X} shadow={g.shadow:X} holes={len(g.holes)}  remap={ident}"
        )
        render_grid(g.background, pal, args.scale).save(args.out_dir / f"group_{name}_background.png")

    for idx in [int(s) for s in args.plates.split(",")]:
        tm = at.text_model(idx)
        p = at.plates[idx]
        x0, y0, x1, y1 = tm.bbox
        n = sum(1 for y in range(PLATE_H) for x in range(PLATE_W) if tm.mask[y][x])
        print(
            f"plate {idx:2d} group {tm.group}: label px={n} bbox x{x0}-{x1} y{y0}-{y1} "
            f"({x1 - x0 + 1}x{y1 - y0 + 1}) stroke={tm.stroke:X} shadow={tm.shadow:X} "
            f"shadow_delta={tm.shadow_delta}"
        )
        # Self-check: outside the label, the recovered background must equal the
        # real plate byte-for-byte. A non-zero count means the reconstruction
        # invented pixels and must not be written to a ROM.
        bg = at.groups[tm.group].background
        drift = [
            (x, y)
            for y in range(PLATE_H)
            for x in range(PLATE_W)
            if not tm.mask[y][x] and bg[y][x] != p.grid[y][x]
        ]
        print(f"          background drift outside label: {len(drift)} px {drift[:8]}")
        label_only = [
            [p.grid[y][x] if tm.mask[y][x] else 0 for x in range(PLATE_W)] for y in range(PLATE_H)
        ]
        mask_vis = [[15 if tm.mask[y][x] else 0 for x in range(PLATE_W)] for y in range(PLATE_H)]
        strip = Image.new("RGB", (PLATE_W * args.scale, PLATE_H * args.scale * 4), (255, 0, 255))
        for i, g in enumerate((p.grid, at.groups[tm.group].background, mask_vis, label_only)):
            strip.paste(render_grid(g, pal, args.scale), (0, i * PLATE_H * args.scale))
        strip.save(args.out_dir / f"plate_{idx:02d}_model.png")

    print(f"\n-> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
