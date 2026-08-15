#!/usr/bin/env python3
"""Dump the title-screen copyright 224x16 strip and palette histogram."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_id_command_plaques_ko_candidate import decode_grid, encode_grid  # noqa: E402
from monoeye_rom import stock_base  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SHOT = Path(
    r"C:\Users\Administrator\.cursor\projects\d-monoeye\assets"
    r"\c__Users_Administrator_AppData_Roaming_Cursor_User_workspaceStorage"
    r"_3b9c44c66ddd6841e2e32ad3c754a9de_images"
    r"_monoeye_ko_expanded.state27-2f9254f4-2937-43cc-a27e-7f04fe0ffe2a.png"
)
HELPER = ROOT / "out/patch/_analyze_beetle_status_vram.py"
STATE = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state27")
OUT = ROOT / "out/patch/title_menu_font_copyright_diag"

LOGICAL = 0x5519DC
TILES = 56
COLS, ROWS = 28, 2
BLOB = TILES * 32


def load_helper():
    import importlib.util

    spec = importlib.util.spec_from_file_location("beetle_vram", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render_grid(pixels, pal, scale: int) -> Image.Image:
    img = Image.new("RGB", (len(pixels[0]), len(pixels)))
    px = img.load()
    for y, row in enumerate(pixels):
        for x, v in enumerate(row):
            px[x, y] = pal.get(v, (v * 17,) * 3)
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tip = TIP.read_bytes()
    stock = STOCK.read_bytes()
    sb = stock_base(tip)
    blob = bytes(tip[sb + LOGICAL : sb + LOGICAL + BLOB])
    stock_blob = stock[LOGICAL : LOGICAL + BLOB]
    print(f"logical {LOGICAL:06X}-{LOGICAL + BLOB - 1:06X} bytes={BLOB}")
    print(f"tip==stock blob: {blob == stock_blob}")
    print(f"unique in tip: {tip.count(blob)}  stock: {stock.count(stock_blob)}")
    first = blob[:32]
    print(f"first-tile hits tip: {tip.count(first)} stock: {stock.count(first)}")

    pixels = decode_grid(blob, COLS, ROWS)
    counts = Counter(v for row in pixels for v in row)
    print("index histogram:", dict(sorted(counts.items())))

    helper = load_helper()
    ram, gfx = helper.parse_beetle_ram(STATE)
    pals = helper.wsc_palettes(ram)
    pal6 = {i: pals[6][i] for i in range(16)}
    print("palette 6:", pal6)

    render_grid(pixels, pal6, 4).save(OUT / "copyright_rom_x4.png")
    render_grid(pixels, pal6, 1).save(OUT / "copyright_rom_native.png")

    # Column ink occupancy to find JP vs EN split.
    cols = []
    for x in range(224):
        used = sorted({pixels[y][x] for y in range(16)})
        ink = sum(1 for y in range(16) if pixels[y][x] != 0)
        cols.append({"x": x, "indices": used, "ink": ink})
    # Find runs of empty vs ink
    runs = []
    start = 0
    empty = cols[0]["ink"] == 0
    for x in range(1, 224):
        now = cols[x]["ink"] == 0
        if now != empty:
            runs.append({"empty": empty, "x0": start, "x1": x, "w": x - start})
            start = x
            empty = now
    runs.append({"empty": empty, "x0": start, "x1": 224, "w": 224 - start})
    print("empty/ink runs:", runs)

    shot = Image.open(SHOT).convert("RGB")
    bottom = shot.crop((0, 128, 224, 144))
    bottom.resize((224 * 4, 16 * 4), Image.NEAREST).save(OUT / "copyright_shot_x4.png")
    bottom.save(OUT / "copyright_shot_native.png")

    report = {
        "logical": f"{LOGICAL:06X}",
        "end": f"{LOGICAL + BLOB - 1:06X}",
        "bytes": BLOB,
        "tip_equals_stock": blob == stock_blob,
        "blob_sha256": hashlib.sha256(blob).hexdigest(),
        "index_histogram": {f"{k:X}": v for k, v in sorted(counts.items())},
        "palette6_rgb": {f"{k:X}": list(v) for k, v in pal6.items()},
        "runs": runs,
    }
    (OUT / "copyright_strip.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
