#!/usr/bin/env python3
"""Locate title-screen copyright tiles and compare Hangul fonts for menu plates.

Read-only. Does not write the main TIP or SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from render_bank_tiles import tiles_4bpp  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
STATE = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state27")
SHOT = Path(
    r"C:\Users\Administrator\.cursor\projects\d-monoeye\assets"
    r"\c__Users_Administrator_AppData_Roaming_Cursor_User_workspaceStorage"
    r"_3b9c44c66ddd6841e2e32ad3c754a9de_images"
    r"_monoeye_ko_expanded.state27-2f9254f4-2937-43cc-a27e-7f04fe0ffe2a.png"
)
FONT_DIR = ROOT / "assets/fonts"
OUT = ROOT / "out/patch/title_menu_font_copyright_diag"
HELPER = ROOT / "out/patch/_analyze_beetle_status_vram.py"

NEEDLES = (
    b"BANDAI",
    b"2002",
    bytes.fromhex("916E92CA"),  # 創通 Shift-JIS
    "サンライズ".encode("shift_jis"),
    "エージェンシー".encode("shift_jis"),
)

LABELS = ("새 게임", "계속", "설정", "통신 모드", "유닛 교환")
VARIANTS = (
    ("Galmuri11.ttf", 11),
    ("Galmuri11-Bold.ttf", 11),
    ("Galmuri11-Bold.ttf", 12),
    ("Galmuri11-Condensed.ttf", 11),
    ("Galmuri11Bitmap-Regular-2.40.3.ttf", 11),
    ("Galmuri11Bitmap-Regular-2.40.3.ttf", 16),
    ("Galmuri11Bitmap-Bold-2.40.3.ttf", 16),
    ("Galmuri9Bitmap-Regular-2.40.3.ttf", 12),
    ("Galmuri7.ttf", 8),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_helper():
    import importlib.util

    spec = importlib.util.spec_from_file_location("beetle_vram", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def list_fonts() -> list[dict]:
    out = []
    if not FONT_DIR.is_dir():
        return out
    for path in sorted(FONT_DIR.rglob("*")):
        if path.is_file():
            out.append(
                {
                    "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "size": path.stat().st_size,
                    "sha256": sha256(path.read_bytes())[:16],
                }
            )
    return out


def find_needles(rom: bytes) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for needle in NEEDLES:
        key = needle.decode("ascii", "backslashreplace")
        found = []
        start = 0
        while True:
            pos = rom.find(needle, start)
            if pos < 0:
                break
            found.append(f"{pos:06X}")
            start = pos + 1
            if len(found) >= 20:
                break
        hits[key] = found
    return hits


def measure_font(path: Path, size: int, texts: tuple[str, ...] = LABELS) -> dict:
    font = ImageFont.truetype(str(path), size=size)
    probe = Image.new("L", (size * 4, size * 4), 0)
    draw = ImageDraw.Draw(probe)
    vref_top = draw.textbbox((0, 0), "갱", font=font)[1]
    glyphs = {}
    for text in texts + ("계", "속", "새", "게", "임"):
        w = max(1, int(round(font.getlength(text))))
        h = size + 4
        img = Image.new("L", (max(w + 4, 8), h), 0)
        ImageDraw.Draw(img).text((0, -vref_top), text, fill=255, font=font)
        px = img.load()
        ink = [
            (x, y)
            for y in range(img.height)
            for x in range(img.width)
            if px[x, y] >= 128
        ]
        if ink:
            xs = [p[0] for p in ink]
            ys = [p[1] for p in ink]
            bbox = [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
        else:
            bbox = [0, 0, 0, 0]
        glyphs[text] = {
            "advance": w,
            "ink_px": len(ink),
            "bbox": bbox,
            "height": bbox[3] - bbox[1],
            "width": bbox[2] - bbox[0],
        }
    return {
        "font": path.name,
        "size": size,
        "exists": True,
        "vref_top": vref_top,
        "glyphs": glyphs,
        "계속_fits_80x16": glyphs["계속"]["advance"] + 1 <= 80
        and glyphs["계속"]["height"] + 1 <= 16,
        "유닛_교환_width": glyphs["유닛 교환"]["advance"] if "유닛 교환" in glyphs else None,
    }


def render_compare(font_paths: list[tuple[Path, int]], out_dir: Path) -> None:
    rows = []
    cell_w, cell_h = 220, 28
    for path, size in font_paths:
        font = ImageFont.truetype(str(path), size=size)
        row = Image.new("RGB", (cell_w * 3, cell_h), (20, 40, 80))
        d = ImageDraw.Draw(row)
        for i, text in enumerate(("새 게임", "계속", "설정")):
            d.text((8 + i * cell_w, 4), f"{text}", fill=(255, 255, 255), font=font)
            d.text((8 + i * cell_w, cell_h - 10), f"{path.name} {size}", fill=(180, 200, 220))
        rows.append(row)
    sheet = Image.new("RGB", (cell_w * 3, cell_h * len(rows) + 4), (0, 0, 0))
    for i, row in enumerate(rows):
        sheet.paste(row, (0, i * cell_h))
    sheet.resize((sheet.width * 4, sheet.height * 4), Image.Resampling.NEAREST).save(
        out_dir / "menu_font_compare_x4.png"
    )
    sheet.save(out_dir / "menu_font_compare.png")


def match_blob(rom: bytes, blob: bytes, limit: int = 16) -> list[int]:
    hits = []
    start = 0
    while True:
        pos = rom.find(blob, start)
        if pos < 0:
            return hits
        hits.append(pos)
        start = pos + 1
        if len(hits) >= limit:
            return hits


def analyze_state(helper, stock: bytes, tip: bytes) -> dict:
    ram, gfx = helper.parse_beetle_ram(STATE)
    pals = helper.wsc_palettes(ram)
    loc = gfx["FGBGLoc"][0]
    bg_map = (loc & 7) << 11
    fg_map = ((loc >> 4) & 7) << 11
    bg_img = Image.new("RGB", (224, 144), (255, 0, 255))
    fg_img = Image.new("RGB", (224, 144), (0, 0, 0))
    rows = []
    for layer_name, map_off, img in (("bg", bg_map, bg_img), ("fg", fg_map, fg_img)):
        layer_rows = []
        for row in range(18):
            cells = []
            for col in range(28):
                word = struct.unpack_from("<H", ram, map_off + (row * 32 + col) * 2)[0]
                e = helper.parse_entry(word)
                blob = helper.tile_bytes(ram, e["tile"], e["bank"])
                grid = tiles_4bpp(blob)[0]
                helper.paste_grid(
                    img, grid, pals[e["palette"]], col * 8, row * 8, e["flip_h"], e["flip_v"]
                )
                stock_hits = match_blob(stock, blob)
                tip_hits = match_blob(tip, blob)
                cells.append(
                    {
                        "col": col,
                        "tile": f"{e['tile']:03X}",
                        "palette": e["palette"],
                        "bank": f"{e['bank']:04X}",
                        "flip_h": e["flip_h"],
                        "flip_v": e["flip_v"],
                        "entry": f"{e['raw']:04X}",
                        "ink": sum(1 for v in blob if v),
                        "sha16": hashlib.sha256(blob).hexdigest()[:16],
                        "stock_hits": [f"{h:06X}" for h in stock_hits[:8]],
                        "stock_aligned32": [f"{h:06X}" for h in stock_hits if h % 32 == 0][:8],
                        "tip_hits": [f"{h:06X}" for h in tip_hits[:4]],
                    }
                )
            layer_rows.append({"row": row, "cells": cells})
        rows.append({"layer": layer_name, "map": f"{map_off:04X}", "rows": layer_rows})
        img.resize((224 * 3, 144 * 3), Image.Resampling.NEAREST).save(
            OUT / f"state27_{layer_name}_x3.png"
        )
        img.save(OUT / f"state27_{layer_name}_native.png")
        img.crop((0, 128, 224, 144)).resize((224 * 4, 16 * 4), Image.Resampling.NEAREST).save(
            OUT / f"state27_{layer_name}_bottom_x4.png"
        )
    return {
        "gfx": {
            "DispControl": f"{gfx['DispControl'][0]:02X}",
            "FGBGLoc": f"{loc:02X}",
            "VideoMode": f"{gfx['VideoMode'][0]:02X}",
            "BGXScroll": gfx["BGXScroll"][0],
            "BGYScroll": gfx["BGYScroll"][0],
            "FGXScroll": gfx["FGXScroll"][0],
            "FGYScroll": gfx["FGYScroll"][0],
            "bg_map": f"{bg_map:04X}",
            "fg_map": f"{fg_map:04X}",
        },
        "layers": rows,
    }


def cluster_hits(cells: list[dict]) -> list[dict]:
    """Find contiguous ROM runs among aligned stock hits on one map row."""
    clusters = []
    for cell in cells:
        aligned = [int(h, 16) for h in cell["stock_aligned32"]]
        clusters.append(aligned)
    return clusters


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    fonts = list_fonts()
    stock = STOCK.read_bytes() if STOCK.is_file() else b""
    tip = TIP.read_bytes() if TIP.is_file() else b""
    report: dict = {
        "tip": {
            "path": str(TIP),
            "exists": TIP.is_file(),
            "size": TIP.stat().st_size if TIP.is_file() else 0,
            "sha256": sha256(tip) if tip else None,
        },
        "stock_needles": find_needles(stock) if stock else {},
        "tip_needles": find_needles(tip) if tip else {},
        "fonts": fonts,
        "font_metrics": [],
        "state": str(STATE),
        "state_exists": STATE.is_file(),
        "shot": {
            "path": str(SHOT),
            "exists": SHOT.is_file(),
            "size": list(Image.open(SHOT).size) if SHOT.is_file() else None,
        },
    }

    available = []
    for name, size in VARIANTS:
        matches = [f for f in fonts if f["path"].endswith(name)]
        if not matches:
            report["font_metrics"].append({"font": name, "size": size, "exists": False})
            continue
        path = ROOT / matches[0]["path"]
        try:
            metrics = measure_font(path, size)
        except OSError as exc:
            report["font_metrics"].append(
                {
                    "font": name,
                    "size": size,
                    "exists": True,
                    "error": str(exc),
                }
            )
            continue
        report["font_metrics"].append(metrics)
        available.append((path, size))
    if available:
        render_compare(available, OUT)

    if STATE.is_file():
        helper = load_helper()
        report["vram"] = analyze_state(helper, stock, tip)
        # Focus on likely copyright rows 16-17 (y=128-143).
        for layer in report["vram"]["layers"]:
            for row in layer["rows"]:
                if row["row"] < 15:
                    continue
                aligned = []
                for cell in row["cells"]:
                    if cell["ink"] == 0:
                        continue
                    aligned.extend(int(h, 16) for h in cell["stock_aligned32"])
                unique = sorted(set(aligned))
                runs = []
                if unique:
                    start = prev = unique[0]
                    for off in unique[1:]:
                        if off == prev + 32:
                            prev = off
                            continue
                        runs.append([f"{start:06X}", f"{prev:06X}", (prev - start) // 32 + 1])
                        start = prev = off
                    runs.append([f"{start:06X}", f"{prev:06X}", (prev - start) // 32 + 1])
                row["aligned_runs"] = runs

    out_json = OUT / "report.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"fonts: {len(fonts)}")
    for item in report["font_metrics"]:
        if not item.get("exists"):
            print(f"  missing {item['font']} @{item['size']}")
            continue
        g = item["glyphs"]["계속"]
        wide = item["glyphs"].get("유닛 교환", {})
        print(
            f"  {item['font']} @{item['size']}: 계속 {g['width']}x{g['height']} "
            f"ink={g['ink_px']}  유닛교환_adv={wide.get('advance')} "
            f"계={item['glyphs']['계']['width']}x{item['glyphs']['계']['height']}"
        )
    if report.get("vram"):
        for layer in report["vram"]["layers"]:
            for row in layer["rows"]:
                if row["row"] < 15:
                    continue
                print(
                    f"  {layer['layer']} row {row['row']} runs={row.get('aligned_runs')}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
