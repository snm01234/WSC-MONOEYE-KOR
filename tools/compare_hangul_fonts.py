#!/usr/bin/env python3
"""
Compare candidate Hangul fonts inside the game's 8x8 glyph cell.

READ-ONLY with respect to the ROM; only PNG previews and a JSON report are written.

The constraint is the record format, not the screen. The renderer at ROM ``7A:0403``
computes ``record = 40:0440 + index * 16`` and each record is an **8x8 2bpp** bitmap
that the game pixel-doubles to 16x16 on screen. So the source resolution is 8x8 no
matter how large the text looks, and a font designed for a taller em cell has to be
downscaled into 8x8 before it ever reaches the ROM.

For each font this measures, over a sample of Hangul syllables:

``ink``          set pixels out of 64 — too low is faint, too high is a blob
``rows_used``    distinct occupied rows; a syllable with batchim needs the bottom row
``blank``        syllables that render empty at this size
``collisions``   pairs of *different* syllables whose 8x8 bitmaps come out identical,
                 which is the concrete readability failure: 느 and 드 becoming the
                 same glyph
``preview``      PNG grid per font for eyeballing

A font is only usable if it is designed for an 8px (or smaller) em cell. Galmuri7 is;
Galmuri9 and Galmuri11 are not, and the numbers below show what happens to them.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

DEFAULT_OUT = ROOT / "out/patch/font_compare.json"
PREVIEW_DIR = ROOT / "out/patch/font_preview"

CELL = 8

# Syllables that stress the 8x8 cell: batchim, double batchim, similar shapes.
SAMPLE = (
    "가나다라마바사아자차카타파하"      # 받침 없는 기본형
    "느드르므브스으즈"                  # 가로획만 — 8x8에서 서로 뭉개지기 쉬움
    "각갇갈갉감갑갓강"                  # 받침 · 겹받침
    "월일년전투기체함대유닛출격이동공격"  # 실제 UI·전투 어휘
    "한글확인"
)

CANDIDATES: Tuple[Tuple[str, Path], ...] = (
    ("Galmuri7", ROOT / "assets/fonts/Galmuri7.ttf"),
    ("Galmuri7Bitmap", ROOT / "assets/fonts/Galmuri7Bitmap.ttf"),
    ("Galmuri9", ROOT / "assets/fonts/galmuri_tmp/Galmuri9.ttf"),
    ("Galmuri11", ROOT / "assets/fonts/galmuri_tmp/Galmuri11.ttf"),
    ("Galmuri11Condensed", ROOT / "assets/fonts/galmuri_tmp/Galmuri11-Condensed.ttf"),
    ("GalmuriMono7", ROOT / "assets/fonts/galmuri_tmp/GalmuriMono7.ttf"),
    ("GalmuriMono9", ROOT / "assets/fonts/galmuri_tmp/GalmuriMono9.ttf"),
    ("GalmuriMono11", ROOT / "assets/fonts/galmuri_tmp/GalmuriMono11.ttf"),
)


def render_native(ch: str, path: Path, size: int) -> List[List[int]] | None:
    """Draw at ``size`` px directly into the 8x8 cell (no downscale)."""
    try:
        font = ImageFont.truetype(str(path), size=size)
    except Exception:
        return None
    img = Image.new("L", (CELL, CELL), 0)
    draw = ImageDraw.Draw(img)
    try:
        bbox = draw.textbbox((0, 0), ch, font=font)
    except Exception:
        return None
    draw.text((-bbox[0], -bbox[1]), ch, fill=255, font=font)
    return [
        [1 if img.getpixel((x, y)) >= 128 else 0 for x in range(CELL)]
        for y in range(CELL)
    ]


def render_downscaled(ch: str, path: Path, big: int = 64) -> List[List[int]] | None:
    """Draw large, then box-downscale into 8x8 — the only way to fit a tall font."""
    try:
        font = ImageFont.truetype(str(path), size=int(big * 0.78))
    except Exception:
        return None
    img = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(img)
    try:
        bbox = draw.textbbox((0, 0), ch, font=font)
    except Exception:
        return None
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((big - w) // 2 - bbox[0], (big - h) // 2 - bbox[1]), ch, fill=255, font=font)
    small = img.resize((CELL, CELL), Image.Resampling.BOX)
    peak = max(small.getpixel((x, y)) for y in range(CELL) for x in range(CELL))
    if peak <= 0:
        return [[0] * CELL for _ in range(CELL)]
    thr = max(8, int(peak * 0.32))
    return [
        [1 if small.getpixel((x, y)) >= thr else 0 for x in range(CELL)]
        for y in range(CELL)
    ]


def stats(bitmaps: Dict[str, List[List[int]]]) -> dict:
    inks = []
    rows = []
    blank = []
    seen: Dict[str, str] = {}
    collisions: List[List[str]] = []
    for ch, px in bitmaps.items():
        ink = sum(sum(r) for r in px)
        inks.append(ink)
        rows.append(sum(1 for r in px if any(r)))
        if ink == 0:
            blank.append(ch)
        key = "".join(str(v) for r in px for v in r)
        if key in seen and seen[key] != ch:
            collisions.append([seen[key], ch])
        else:
            seen.setdefault(key, ch)
    n = len(bitmaps) or 1
    return {
        "chars": len(bitmaps),
        "ink_mean": round(sum(inks) / n, 1),
        "ink_min": min(inks) if inks else 0,
        "ink_max": max(inks) if inks else 0,
        "rows_mean": round(sum(rows) / n, 2),
        "blank": blank,
        "blank_count": len(blank),
        "collisions": collisions[:20],
        "collision_count": len(collisions),
    }


def preview(bitmaps: Dict[str, List[List[int]]], dest: Path, scale: int = 8) -> None:
    cols = 16
    rows = (len(bitmaps) + cols - 1) // cols
    img = Image.new("L", (cols * CELL * scale, rows * CELL * scale), 255)
    for i, px in enumerate(bitmaps.values()):
        cx, cy = (i % cols) * CELL * scale, (i // cols) * CELL * scale
        for y in range(CELL):
            for x in range(CELL):
                if px[y][x]:
                    for dy in range(scale):
                        for dx in range(scale):
                            img.putpixel((cx + x * scale + dx, cy + y * scale + dy), 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--preview-dir", type=Path, default=PREVIEW_DIR)
    args = ap.parse_args(argv)

    sample = list(dict.fromkeys(SAMPLE))
    results: List[dict] = []
    for name, path in CANDIDATES:
        if not path.exists():
            results.append({"font": name, "path": str(path), "available": False})
            continue
        entry: dict = {"font": name, "path": str(path), "available": True, "modes": {}}
        for mode, fn in (
            ("native8", lambda ch, p: render_native(ch, p, CELL)),
            ("native11", lambda ch, p: render_native(ch, p, 11)),
            ("downscaled", render_downscaled),
        ):
            bitmaps = {}
            for ch in sample:
                px = fn(ch, path)
                if px is not None:
                    bitmaps[ch] = px
            if not bitmaps:
                continue
            entry["modes"][mode] = stats(bitmaps)
            preview(bitmaps, args.preview_dir / f"{name}_{mode}.png")
        results.append(entry)

    report = {
        "generated_by": "tools/compare_hangul_fonts.py",
        "read_only": True,
        "constraint": "renderer 7A:0403 → record = 40:0440 + index*16, each record "
        "is an 8x8 2bpp bitmap pixel-doubled to 16x16 on screen. The ROM never "
        "stores more than 8x8 per glyph.",
        "cell": f"{CELL}x{CELL}",
        "sample_chars": "".join(sample),
        "preview_dir": str(args.preview_dir),
        "fonts": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"cell {CELL}x{CELL}, sample {len(sample)} syllables\n")
    hdr = f"{'font':20s} {'mode':11s} {'ink':>5} {'rows':>5} {'blank':>6} {'dup':>5}"
    print(hdr)
    for e in results:
        if not e.get("available"):
            print(f"{e['font']:20s} (missing)")
            continue
        for mode, s in e["modes"].items():
            print(
                f"{e['font']:20s} {mode:11s} {s['ink_mean']:>5} {s['rows_mean']:>5} "
                f"{s['blank_count']:>6} {s['collision_count']:>5}"
            )
    print(f"\npreviews → {args.preview_dir}")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
