#!/usr/bin/env python3
"""Inventory ID-command/battle plaque graphics missed by the original 24 scan.

The first inventory searched for one exact transparency mask.  That was useful
for finding the measured 48x16 family, but it excluded badges with a different
width, border, tone, or glyph spill.  This analyzer records the additional raw
bank-4C blocks exposed by gap reconstruction.  It is strictly read-only with
respect to both the stock ROM and the current main TIP.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
OLD_SPEC = ROOT / "data/id_command_plaque_translations_ko.json"
OUT_DIR = ROOT / "out/patch/id_command_residual_static_analysis"
REPORT = OUT_DIR / "residual_plaque_inventory.json"
STOCK_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
STOCK_SIZE = 8_388_608
TIP_SIZE = 16_777_216
TILE_BYTES = 32


RESIDUALS = [
    {
        "logical": 0x4C4A74,
        "width": 40,
        "storage": "body_40x16_plus_shared_right_cap",
        "display_width": 48,
        "jp": "封印!",
        "ko_suggestion": "봉인!",
        "category": "id_command_result_badge",
        "confidence": "user_runtime_capture_proven",
    },
    {
        "logical": 0x4C4BB4,
        "width": 32,
        "storage": "body_32x16_plus_shared_right_cap",
        "display_width": 40,
        "jp": "盾!",
        "ko_suggestion": "방패!",
        "category": "id_command_result_badge",
        "confidence": "user_reading_plus_clean_4x2_tile_boundary",
    },
    {
        "logical": 0x4C50F4,
        "width": 40,
        "storage": "body_40x16_plus_shared_right_cap",
        "display_width": 48,
        "jp": "必中!",
        "ko_suggestion": "필중!",
        "category": "id_command_result_badge",
        "confidence": "high_static_glyph_and_stream_boundary",
    },
    {
        "logical": 0x4C53B4,
        "width": 40,
        "storage": "body_40x16_plus_shared_right_cap",
        "display_width": 48,
        "jp": "回避!",
        "ko_suggestion": "회피!",
        "category": "battle_result_badge",
        "confidence": "screen_proven_capture_match",
    },
    {
        "logical": 0x4CBEAA,
        "width": 48,
        "storage": "full_48x16",
        "display_width": 48,
        "jp": "↓移動",
        "ko_suggestion": "↓이동",
        "category": "id_command_stat_plaque",
        "confidence": "high_static_glyph_and_contiguous_12_tile_slot",
    },
    {
        "logical": 0x4CC32A,
        "width": 40,
        "storage": "sparse_40x16_insert_shared_mid_column",
        "display_width": 48,
        "jp": "追撃!",
        "ko_suggestion": "추격!",
        "category": "battle_result_badge",
        "confidence": "stock_main_runtime_tile_comparison_proven",
        "shared_display_column": 3,
        "shared_top_logical": "4CB80A",
        "shared_bottom_logical": "4CB8AA",
    },
    {
        "logical": 0x4CE86A,
        "width": 48,
        "storage": "full_48x16",
        "display_width": 48,
        "jp": "貫通!",
        "ko_suggestion": "관통!",
        "category": "battle_result_badge",
        "confidence": "high_static_glyph_and_contiguous_12_tile_slot",
    },
    {
        "logical": 0x4CE9EA,
        "width": 32,
        "storage": "body_32x16_plus_shared_both_caps",
        "display_width": 48,
        "jp": "先制",
        "ko_suggestion": "선제",
        "category": "battle_action_badge",
        "confidence": "clean_4x2_tile_boundary_plus_shared_cap_match",
    },
]


LIVE_PALETTE = {
    0x0: (0, 0, 0),
    0xA: (80, 136, 80),
    0xB: (170, 255, 187),
    0xC: (68, 255, 68),
    0xD: (0, 204, 0),
    0xE: (0, 68, 0),
    0xF: (255, 255, 255),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def decode_grid(raw: bytes, width: int) -> list[list[int]]:
    columns = width // 8
    expected = columns * 2 * TILE_BYTES
    if len(raw) != expected:
        raise ValueError(f"bad {width}x16 payload: {len(raw)} != {expected}")
    pixels = [[0] * width for _ in range(16)]
    for tile_index in range(columns * 2):
        tile = raw[tile_index * TILE_BYTES : (tile_index + 1) * TILE_BYTES]
        ox = (tile_index % columns) * 8
        oy = (tile_index // columns) * 8
        for y in range(8):
            for pair in range(4):
                value = tile[y * 4 + pair]
                pixels[oy + y][ox + pair * 2] = value >> 4
                pixels[oy + y][ox + pair * 2 + 1] = value & 0xF
    return pixels


def decode_tile8(raw: bytes) -> list[list[int]]:
    if len(raw) != TILE_BYTES:
        raise ValueError(f"bad 8x8 tile payload: {len(raw)}")
    pixels = [[0] * 8 for _ in range(8)]
    for y in range(8):
        for pair in range(4):
            value = raw[y * 4 + pair]
            pixels[y][pair * 2] = value >> 4
            pixels[y][pair * 2 + 1] = value & 0xF
    return pixels


def render_pixels(pixels: list[list[int]], mode: str, scale: int = 8) -> Image.Image:
    width = len(pixels[0])
    image = Image.new("RGB", (width, 16))
    target = image.load()
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            if mode == "ink_mask":
                target[x, y] = (0, 0, 0) if value in {0, 0xA, 0xE} else (255, 255, 255)
            else:
                target[x, y] = LIVE_PALETTE.get(value, (value * 17,) * 3)
    return image.resize((width * scale, 16 * scale), Image.Resampling.NEAREST)


def render_sheet(rows: list[dict[str, Any]], path: Path, mode: str) -> None:
    scale = 8
    visual_width = 48 * scale
    row_height = 16 * scale + 18
    sheet = Image.new("RGB", (visual_width, row_height * len(rows)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, row in enumerate(rows):
        image = render_pixels(row["pixels"], mode, scale)
        y = index * row_height
        sheet.paste(image, (0, y))
        draw.text(
            (2, y + 16 * scale + 2),
            f"{row['logical']:06X} {row['width']}x16 {row['category']}",
            fill=(255, 255, 255),
            font=font,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def all_hits(data: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    start = 0
    while True:
        hit = data.find(needle, start)
        if hit < 0:
            return hits
        hits.append(hit)
        start = hit + 1


def old_intervals(spec: dict[str, Any]) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for row in spec.get("plaques") or []:
        logical = int(row["logical"], 16)
        size = 320 if row["storage"] == "body_plus_shared_cap" else 384
        intervals.append((logical, logical + size))
    if len(intervals) != 24:
        raise RuntimeError(f"old inventory drift: {len(intervals)}")
    return intervals


def overlaps(start: int, end: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start < old_end and end > old_start for old_start, old_end in intervals)


def main() -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    stock = STOCK.read_bytes()
    tip = TIP.read_bytes()
    before_stock = sha256(stock)
    before_tip = sha256(tip)
    if len(stock) != STOCK_SIZE or before_stock != STOCK_SHA:
        raise RuntimeError("stock ROM drift")
    if len(tip) != TIP_SIZE:
        raise RuntimeError("main TIP size drift")
    stock_base = len(tip) - len(stock)
    if stock_base != 0x800000:
        raise RuntimeError(f"unexpected stock base: {stock_base:#x}")

    spec = json.loads(OLD_SPEC.read_text(encoding="utf-8"))
    prior = old_intervals(spec)
    bright_full = decode_grid(stock[0x4C4654 : 0x4C4654 + 384], 48)
    shared_left_cap = [line[0:8] for line in bright_full]
    shared_right_cap = [line[40:48] for line in bright_full]
    pursuit_shared_top = decode_tile8(stock[0x4CB80A : 0x4CB80A + 32])
    pursuit_shared_bottom = decode_tile8(stock[0x4CB8AA : 0x4CB8AA + 32])
    rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for source in RESIDUALS:
        logical = int(source["logical"])
        width = int(source["width"])
        size = (width // 8) * 2 * TILE_BYTES
        physical = stock_base + logical
        raw = stock[logical : logical + size]
        current = tip[physical : physical + size]
        body_pixels = decode_grid(raw, width)
        uses_shared_right_cap = "plus_shared_right_cap" in source["storage"]
        uses_shared_both_caps = "plus_shared_both_caps" in source["storage"]
        uses_sparse_mid = source["storage"] == "sparse_40x16_insert_shared_mid_column"
        if uses_sparse_mid:
            pixels = []
            for y in range(16):
                shared = pursuit_shared_top[y] if y < 8 else pursuit_shared_bottom[y - 8]
                private_cols = [body_pixels[y][x : x + 8] for x in range(0, 40, 8)]
                pixels.append(
                    private_cols[0] + private_cols[1] + private_cols[2]
                    + shared
                    + private_cols[3] + private_cols[4]
                )
        elif uses_shared_both_caps:
            pixels = [
                shared_left_cap[y] + body_pixels[y] + shared_right_cap[y]
                for y in range(16)
            ]
        elif uses_shared_right_cap:
            pixels = [body_pixels[y] + shared_right_cap[y] for y in range(16)]
        else:
            pixels = body_pixels
        right_edge_pixel_diff_vs_success = sum(
            body_pixels[y][-8:][x] != shared_right_cap[y][x]
            for y in range(16)
            for x in range(8)
        )
        embedded_left_cap_pixel_diff_vs_shared = None
        interval_overlap = overlaps(logical, logical + size, prior)
        row = {
            **source,
            "size": size,
            "tiles": size // TILE_BYTES,
            "pixels": pixels,
        }
        rows.append(row)
        manifest.append(
            {
                "logical": f"{logical:06X}-{logical + size - 1:06X}",
                "physical_current_tip": f"{physical:06X}-{physical + size - 1:06X}",
                "stored_width": width,
                "display_width": int(source["display_width"]),
                "storage": source["storage"],
                "height": 16,
                "bytes": size,
                "tiles": size // TILE_BYTES,
                "jp": source["jp"],
                "ko_suggestion": source["ko_suggestion"],
                "category": source["category"],
                "confidence": source["confidence"],
                **(
                    {"runtime_caveat": source["runtime_caveat"]}
                    if source.get("runtime_caveat")
                    else {}
                ),
                "sha256": sha256(raw),
                "palette_indices": sorted({value for line in pixels for value in line}),
                "current_tip_stock_exact": current == raw,
                "overlaps_original_24": interval_overlap,
                "exact_raw_hits_in_stock": [f"{hit:06X}" for hit in all_hits(stock, raw)],
                "right_edge_pixel_diff_vs_success": right_edge_pixel_diff_vs_success,
                **(
                    {"shared_right_cap_appended": True}
                    if uses_shared_right_cap
                    else {}
                ),
                **(
                    {"shared_left_and_right_caps_composed": True}
                    if uses_shared_both_caps
                    else {}
                ),
                **(
                    {
                        "shared_mid_column_inserted": True,
                        "shared_display_column": 3,
                        "shared_top_logical": "4CB80A",
                        "shared_bottom_logical": "4CB8AA",
                        "runtime_display_geometry": "48x16",
                    }
                    if uses_sparse_mid
                    else {}
                ),
                **(
                    {
                        "embedded_left_cap_pixel_diff_vs_shared":
                            embedded_left_cap_pixel_diff_vs_shared
                    }
                    if embedded_left_cap_pixel_diff_vs_shared is not None
                    else {}
                ),
            }
        )

    live_sheet = OUT_DIR / "all_8_residuals_live_palette.png"
    ink_sheet = OUT_DIR / "all_8_residuals_ink_mask.png"
    render_sheet(rows, live_sheet, "live_palette")
    render_sheet(rows, ink_sheet, "ink_mask")

    checks = {
        "stock_hash_bound": before_stock == STOCK_SHA,
        "tip_size_valid": len(tip) == TIP_SIZE,
        "stock_base_is_800000": stock_base == 0x800000,
        "original_inventory_is_24": len(prior) == 24,
        "residual_inventory_is_8": len(manifest) == 8,
        "all_residuals_disjoint_from_original_24": all(
            not row["overlaps_original_24"] for row in manifest
        ),
        "all_residuals_current_tip_stock_exact": all(
            row["current_tip_stock_exact"] for row in manifest
        ),
        "all_residual_raw_blocks_unique": all(
            row["exact_raw_hits_in_stock"] == [row["logical"].split("-")[0]]
            for row in manifest
        ),
        "all_right_cap_compositions_have_expected_display_width": all(
            row["display_width"] == row["stored_width"] + 8
            for row in manifest
            if "plus_shared_right_cap" in row["storage"]
        ),
        "both_cap_composition_has_expected_display_width": all(
            row["display_width"] == row["stored_width"] + 16
            for row in manifest
            if "plus_shared_both_caps" in row["storage"]
        ),
        "pursuit_sparse_mid_column_runtime_model": (
            next(row for row in manifest if row["logical"].startswith("4CC32A"))
            .get("shared_mid_column_inserted") is True
            and next(row for row in manifest if row["logical"].startswith("4CC32A"))
            .get("runtime_display_geometry") == "48x16"
        ),
        "pursuit_right_edge_matches_success": next(
            row for row in manifest if row["logical"].startswith("4CC32A")
        )["right_edge_pixel_diff_vs_success"] == 0,
        "stock_unchanged": sha256(STOCK.read_bytes()) == before_stock,
        "main_tip_unchanged": sha256(TIP.read_bytes()) == before_tip,
    }
    if not all(checks.values()):
        raise RuntimeError(f"analysis check failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_id_command_residual_plaques.py",
        "read_only": True,
        "summary": {
            "original_inventory": 24,
            "additional_residual_graphics": 8,
            "expanded_static_inventory": 32,
            "additional_full_48x16": 2,
            "additional_full_40x16": 0,
            "additional_sparse_40x16_insert_shared_mid_column": 1,
            "additional_body_40x16_plus_shared_right_cap": 3,
            "additional_body_32x16_plus_shared_right_cap": 1,
            "additional_body_32x16_plus_shared_both_caps": 1,
            "current_tip_untranslated_additional_blocks": 8,
        },
        "why_original_scan_missed_them": (
            "The old scan required the exact zero/transparency mask of one measured plaque. "
            "Different stored widths, shared-cap composition, border masks, and tone variants failed "
            "the predicate even though their raw pixels are valid battle labels."
        ),
        "stock": identity(STOCK, stock),
        "current_tip": {**identity(TIP, tip), "stock_base": f"{stock_base:06X}"},
        "residuals": manifest,
        "coverage": {
            "small_gap_reconstruction": [
                "4C47D4-4C48F3: nine icon/cap tiles; no text plaque",
                "4C4A74-4C5233: 封印! 40x16 body, 盾! 32x16 body, intervening icon/effect tiles, 必中! 40x16 body",
                "4C53B4-4C54F3: 回避!",
                "4C58F4-4C5913: one shared cap tile",
                "4CB50A-4CB629: nine icon/cap tiles; no text plaque",
                "4CBEAA-4CC029: ↓移動",
                "4CC32A-4CC529: 追撃! followed by six icon/effect tiles",
                "4CE86A-4CE9E9: 貫通!",
                "4CE9EA-4CEAE9: 先制 32x16 text body between external left/right cap pairs; bytes from 4CEAEA are not part of this plaque",
            ],
            "large_gap_atlases": [
                "out/patch/id_command_residual_static_analysis/large_gap_atlases/4C0000_4C44D3_4bpp_c16.png",
                "out/patch/id_command_residual_static_analysis/large_gap_atlases/4C5FD4_4CB389_4bpp_c16.png",
                "out/patch/id_command_residual_static_analysis/large_gap_atlases/4CC6AA_4CE569_4bpp_c16.png",
                "out/patch/id_command_residual_static_analysis/large_gap_atlases/4CEAEA_4CFFFF_4bpp_c16.png",
            ],
            "large_gap_classification": (
                "battle effects, unit/portrait graphics, data/blank tail; no further plaque-like "
                "large Japanese glyph blocks observed"
            ),
            "scope_limit": (
                "This is exhaustive for raw packed-4bpp plaque-like graphics in stock bank 4C. "
                "The separate 8x16 glyph renderer used by クリティカル!/Iフィールド is not "
                "part of this plaque inventory."
            ),
        },
        "previews": {
            "live_palette": str(live_sheet.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "ink_mask": str(ink_sheet.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        },
        "composition_basis": {
            "shared_left_cap": {
                "top_tile_logical": "4C44D4",
                "bottom_tile_logical": "4C4594",
                "placement": "prepended before stored body",
            },
            "shared_right_cap": {
                "top_tile_logical": "4C46F4",
                "bottom_tile_logical": "4C47B4",
                "placement": "appended after result-badge bodies that do not embed a right edge",
            },
            "pursuit": (
                "user stock/main 6x captures prove a 48x16 sparse composition: stored "
                "4CC32A-4CC469 supplies display columns 0,1,2,4,5 while display column 3 "
                "reuses 4CB80A/4CB8AA from ↑電撃. Localizing ↑電撃 therefore changed only "
                "the shared 撃 column in the otherwise stock-exact 追撃 plaque."
            ),
            "preemptive": (
                "32x16 先制 text body; neither outer column is an edge template, "
                "so the shared left and right caps are composed externally"
            ),
        },
        "checks": checks,
        "next_safe_step": (
            "Build the corrected eight-plaque test ROM from current main TIP; for 追撃 patch "
            "only its ten private tiles and require the already-localized shared column to "
            "match the target 추격! layout byte/pixel-exact."
        ),
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
