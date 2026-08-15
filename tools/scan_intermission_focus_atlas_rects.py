#!/usr/bin/env python3
"""Match static Japanese label masks against rectangular bank-54 focus assets.

The focus atlas uses 32-byte packed-4bpp tiles at file offsets congruent to 6
modulo 32, so ordinary bank-aligned tile viewers miss its boundaries.  This
scanner tries compact 3/4-row rectangles at every tile in the atlas stream and
scores how completely each known static label mask is embedded in the candidate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from render_bank_tiles import tiles_4bpp  # noqa: E402


ATLAS_LO = 0x542806
ATLAS_HI = 0x544406
TILE_BYTES = 0x20
LABELS = [
    ("mission_status", 1, 7, 16),
    ("scouting", 1, 18, 21),
    ("advance", 1, 22, 26),
    ("operation", 3, 1, 5),
    ("supply", 5, 13, 16),
    ("list", 5, 18, 21),
    ("assignment", 5, 23, 26),
    ("organization", 7, 7, 11),
    ("development", 11, 1, 5),
    ("development_plan", 12, 7, 16),
    ("remodel", 12, 18, 21),
    ("disassemble", 12, 23, 26),
    ("system", 16, 1, 9),
    ("save", 16, 12, 17),
    ("load", 16, 18, 22),
    ("library", 16, 24, 27),
]


def load_masks(rom: bytes, resolved_path: Path) -> list[dict]:
    resolved_json = json.loads(resolved_path.read_text(encoding="utf-8"))
    resolved = {
        (int(col), int(row)): int(value, 16) - 0x800000
        for row, info in resolved_json["rows"].items()
        for col, value in info["resolved"].items()
    }
    masks = []
    for name, row, left, right in LABELS:
        width = (right - left + 1) * 8
        points = []
        known_tiles = 0
        missing_tiles = []
        for sy in range(2):
            for col in range(left, right + 1):
                off = resolved.get((col, row + sy))
                if off is None:
                    missing_tiles.append([col, row + sy])
                    continue
                known_tiles += 1
                tile = tiles_4bpp(rom[off : off + TILE_BYTES])[0]
                for y in range(8):
                    for x in range(8):
                        value = tile[y][x]
                        if value:
                            points.append(((col - left) * 8 + x, sy * 8 + y, value))
        masks.append(
            {
                "name": name,
                "row": row,
                "cols": [left, right],
                "width": width,
                "height": 16,
                "points": points,
                "known_tiles": known_tiles,
                "missing_tiles": missing_tiles,
            }
        )
    return masks


def candidate_grid(stream: list[list[list[int]]], start: int, cols: int, rows: int):
    grid = [[0] * (cols * 8) for _ in range(rows * 8)]
    for i in range(cols * rows):
        tile = stream[start + i]
        ox, oy = (i % cols) * 8, (i // cols) * 8
        for y in range(8):
            for x in range(8):
                grid[oy + y][ox + x] = tile[y][x]
    return grid


def best_embedding(grid, mask: dict) -> tuple[int, int, int]:
    if mask["width"] > len(grid[0]) or mask["height"] > len(grid):
        return 0, 0, 0
    best = (0, 0, 0)
    for oy in range(len(grid) - mask["height"] + 1):
        for ox in range(len(grid[0]) - mask["width"] + 1):
            exact = sum(
                grid[oy + y][ox + x] == value
                for x, y, value in mask["points"]
            )
            if exact > best[0]:
                best = (exact, ox, oy)
    return best


def scan_dimension(stream: np.ndarray, mask: dict, cols: int, rows: int) -> list[dict]:
    """Vectorize all candidate starts for one rectangle/alignment."""
    width, height = cols * 8, rows * 8
    if mask["width"] > width or mask["height"] > height:
        return []
    starts = np.arange(0, len(stream) - cols * rows + 1, dtype=np.int32)
    points = np.asarray(mask["points"], dtype=np.int16)
    mx, my, expected = points[:, 0], points[:, 1], points[:, 2]
    scored: list[dict] = []
    for oy in range(height - mask["height"] + 1):
        for ox in range(width - mask["width"] + 1):
            x = mx + ox
            y = my + oy
            tile_rel = (y // 8) * cols + (x // 8)
            px, py = x % 8, y % 8
            actual = stream[
                starts[:, None] + tile_rel[None, :], py[None, :], px[None, :]
            ]
            exacts = np.count_nonzero(actual == expected[None, :], axis=1)
            # Keep the best few starts for each alignment; the global sort below
            # removes duplicates and makes this much faster than Python pixels.
            take = min(8, len(starts))
            indices = np.argpartition(exacts, -take)[-take:]
            for index in indices.tolist():
                exact = int(exacts[index])
                scored.append(
                    {
                        "ratio": exact / len(expected) if len(expected) else 0.0,
                        "exact": exact,
                        "total": int(len(expected)),
                        "source": f"{ATLAS_LO + int(starts[index]) * TILE_BYTES:06X}",
                        "cols": cols,
                        "rows": rows,
                        "align_xy": [ox, oy],
                    }
                )
    return scored


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    )
    ap.add_argument(
        "--resolved",
        type=Path,
        default=ROOT / "out/title_menu_capture/intermission_overlay_resolved.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out/patch/intermission_focus_trace/atlas_rect_scan.json",
    )
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)

    rom = args.rom.read_bytes()
    raw = rom[ATLAS_LO:ATLAS_HI]
    stream_list = tiles_4bpp(raw)
    stream = np.asarray(stream_list, dtype=np.uint8)
    masks = load_masks(rom, args.resolved)
    results = []
    for mask in masks:
        scored = []
        total = len(mask["points"])
        label_cols = (mask["width"] + 7) // 8
        for rows in (3, 4):
            for cols in sorted({label_cols + 1, label_cols + 2}):
                scored.extend(scan_dimension(stream, mask, cols, rows))
        scored.sort(key=lambda row: (row["ratio"], row["exact"]), reverse=True)
        result = {
            key: value
            for key, value in mask.items()
            if key != "points"
        }
        result["ink_points"] = total
        result["best"] = scored[: args.top]
        results.append(result)
        best = scored[0]
        print(
            f"{mask['name']:18s} {best['ratio']:.4f} {best['exact']}/{best['total']} "
            f"{best['source']} {best['cols']}x{best['rows']} at {best['align_xy']}"
        )

    report = {
        "atlas": [f"{ATLAS_LO:06X}", f"{ATLAS_HI:06X}"],
        "tile_residue_mod_32": ATLAS_LO % 32,
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
