#!/usr/bin/env python3
"""Render candidate WonderSwan tilemap pages from an intermission save state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402
from trace_intermission_focus_sprites import WSRAM_BYTES, WSRAM_CORE_OFFSET  # noqa: E402


def render_map(ram: bytes, base: int) -> Image.Image:
    image = Image.new("RGB", (224, 144))
    pixels = image.load()
    for row in range(18):
        for col in range(28):
            off = base + (row * 32 + col) * 2
            entry = int.from_bytes(ram[off : off + 2], "little")
            tile = entry & 0x1FF
            gfx = 0x8000 if entry & 0x2000 else 0x4000
            grid = tiles_4bpp(ram[gfx + tile * 0x20 : gfx + (tile + 1) * 0x20])[0]
            flip_h = bool(entry & 0x4000)
            flip_v = bool(entry & 0x8000)
            for y in range(8):
                sy = 7 - y if flip_v else y
                for x in range(8):
                    sx = 7 - x if flip_h else x
                    pixels[col * 8 + x, row * 8 + y] = GREYS_16[grid[sy][sx]]
    return image


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--state",
        type=Path,
        default=(
            ROOT
            / "BizHawk-2.11.1-win-x64/WonderSwan/State"
            / "monoeye ko expanded.Cygne/Mednafen.QuickSave1.State"
        ),
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_confirm_atlas_trace/tilemap_layers",
    )
    args = ap.parse_args()

    core, _ = read_state_core(args.state, Zstd(args.zstd_dll))
    ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for base in range(0, 0x4000, 0x800):
        path = args.out_dir / f"tilemap_{base:04X}.png"
        render_map(ram, base).save(path)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
