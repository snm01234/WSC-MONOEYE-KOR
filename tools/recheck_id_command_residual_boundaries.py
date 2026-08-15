#!/usr/bin/env python3
"""Render sliding tile-aligned plaque windows around disputed bank-4C starts."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_id_command_plaques_ko_candidate import decode_grid  # noqa: E402

STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/id_command_residual_static_analysis/boundary_recheck"
LIVE = {
    0x0: (0, 0, 0),
    0xA: (80, 136, 80),
    0xB: (170, 255, 187),
    0xC: (68, 255, 68),
    0xD: (0, 204, 0),
    0xE: (0, 68, 0),
    0xF: (255, 255, 255),
}
REGIONS = {
    "early_cluster": (0x4C49F4, 0x4C5333),
    "movement_cluster": (0x4CBDCA, 0x4CC0E9),
    "pursuit_cluster": (0x4CC24A, 0x4CC5A9),
    "penetrate_counter_cluster": (0x4CE78A, 0x4CEC09),
}


def render_pixels(pixels: list[list[int]], scale: int = 4) -> Image.Image:
    width = len(pixels[0])
    image = Image.new("RGB", (width, 16), "black")
    dst = image.load()
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            dst[x, y] = LIVE.get(value, (value * 17,) * 3)
    return image.resize((width * scale, 16 * scale), Image.Resampling.NEAREST)


def sheet_for_width(data: bytes, lo: int, hi: int, columns: int) -> Image.Image:
    block_size = columns * 2 * 32
    starts = list(range(lo, hi - block_size + 2, 0x20))
    scale = 4
    cell_w = 48 * scale
    cell_h = 16 * scale + 16
    grid_columns = 4
    grid_rows = (len(starts) + grid_columns - 1) // grid_columns
    sheet = Image.new("RGB", (cell_w * grid_columns, cell_h * grid_rows), (22, 22, 22))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, start in enumerate(starts):
        raw = data[start : start + block_size]
        pixels = decode_grid(raw, columns, 2)
        image = render_pixels(pixels, scale)
        ox = (index % grid_columns) * cell_w
        oy = (index // grid_columns) * cell_h
        sheet.paste(image, (ox, oy))
        draw.text(
            (ox + 2, oy + 16 * scale + 1),
            f"{start:06X} {columns * 8}x16",
            fill="white",
            font=font,
        )
    return sheet


def main() -> int:
    data = STOCK.read_bytes()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (lo, hi) in REGIONS.items():
        for columns in (3, 4, 5, 6):
            sheet = sheet_for_width(data, lo, hi, columns)
            path = OUT / f"{name}_sliding_{columns * 8}x16.png"
            sheet.save(path)
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
