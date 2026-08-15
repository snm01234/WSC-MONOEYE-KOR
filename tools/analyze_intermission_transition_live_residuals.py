#!/usr/bin/env python3
"""Compare a captured transition BG layer with the exact sixteen Korean masks."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_intermission_static_full_cleanup_candidate as full  # noqa: E402
from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base  # noqa: E402
from patch_intermission_labels_ko import load_resolved  # noqa: E402
from render_bank_tiles import tiles_4bpp  # noqa: E402
from trace_intermission_focus_sprites import WSRAM_BYTES, WSRAM_CORE_OFFSET  # noqa: E402


def oriented_screen(ram: bytes, tilemap_base: int = 0x3800) -> list[list[int]]:
    out = [[0] * 224 for _ in range(144)]
    for row in range(18):
        for col in range(28):
            off = tilemap_base + (row * 32 + col) * 2
            entry = int.from_bytes(ram[off : off + 2], "little")
            tile = entry & 0x1FF
            gfx = 0x8000 if entry & 0x2000 else 0x4000
            grid = tiles_4bpp(ram[gfx + tile * 0x20 : gfx + (tile + 1) * 0x20])[0]
            for y in range(8):
                sy = 7 - y if entry & 0x8000 else y
                for x in range(8):
                    sx = 7 - x if entry & 0x4000 else x
                    out[row * 8 + y][col * 8 + x] = grid[sy][sx]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument(
        "--source-state",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/WonderSwan/State/monoeye ko expanded.Cygne/Mednafen.QuickSave1.State",
    )
    ap.add_argument(
        "--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc"
    )
    ap.add_argument(
        "--focus-report",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json",
    )
    ap.add_argument(
        "--overlay-resolved",
        type=Path,
        default=ROOT / "out/title_menu_capture/intermission_overlay_resolved.json",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    zstd = Zstd(args.zstd_dll)
    source_core, _ = read_state_core(args.source_state, zstd)
    source_ram = source_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    before = full.screen_from_ram(source_ram)
    live_core, _ = read_state_core(args.state, zstd)
    live_ram = live_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    live = oriented_screen(live_ram)

    rom = args.rom.read_bytes()
    base = stock_base(rom)
    body = rom[base : base + 0x800000]
    masks = full.parent_masks(before, body, load_resolved(args.overlay_resolved))
    focus = json.loads(args.focus_report.read_text(encoding="utf-8"))["targets"]
    masks += [full.focus_masks(row, zstd) for row in focus]

    rows = []
    all_extras: set[tuple[int, int]] = set()
    for mask in masks:
        jp_box = full.bbox(mask.jp)
        ko_box = full.bbox(mask.ko)
        safe = (
            min(jp_box[0], ko_box[0]),
            min(jp_box[1], ko_box[1]),
            max(jp_box[2], ko_box[2]),
            max(jp_box[3], ko_box[3]),
        )
        expected_palette = full.ink_palette(mask.name)
        expected = {
            point: expected_palette[cls] for point, cls in mask.ko.items()
        }
        residual_indices = {1, 0x0F}
        extras = {
            (x, y)
            for y in range(safe[1], safe[3])
            for x in range(safe[0], safe[2])
            if live[y][x] in residual_indices and (x, y) not in expected
        }
        missing = {
            point
            for point, value in expected.items()
            if live[point[1]][point[0]] != value
        }
        all_extras |= extras
        rows.append(
            {
                "name": mask.name,
                "safe_core_bbox_xyxy": list(safe),
                "unexpected_stock_ink_pixels": len(extras),
                "unexpected_stock_ink_points": [list(point) for point in sorted(extras, key=lambda p: (p[1], p[0]))],
                "missing_expected_korean_pixels": len(missing),
            }
        )

    payload = {
        "purpose": "captured BG layer versus exact Korean mask comparison",
        "state": str(args.state),
        "labels": rows,
        "unexpected_stock_ink_pixels_total": len(all_extras),
        "labels_with_unexpected_stock_ink": [row["name"] for row in rows if row["unexpected_stock_ink_pixels"]],
        "all_expected_korean_masks_present": all(row["missing_expected_korean_pixels"] == 0 for row in rows),
        "interpretation": (
            "Any remaining dot inside a Korean mask is part of the approved Korean raster, not stock Japanese residue."
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
