#!/usr/bin/env python3
"""Build the full non-focus intermission cleanup candidate.

This builder is deliberately independent of the older exact-state and shared-tile
layout candidates.  It reconstructs the visible static layer from the accepted
main-TIP QuickSave1-3 states, extracts the approved Korean foreground masks from
the twelve focus states plus the four already-localized parent-label strips,
restores the panel background under every selected Japanese/residual component,
and writes the result back as a real ROM asset.

The stock asset at 0x54B780 covers screen cells (1,1)..(26,16).  The approved focus
masks for Save/Load/Library reach screen row 17, so the raw asset is extended to
26x17.  Every changed screen cell receives a private raw tile/map entry; unchanged
cells remain deduplicated.  This removes shared-tile aliases without touching the
focus atlas or the multi-bank runtime hook.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_intermission_static_exact_state_candidate as state_exact  # noqa: E402
from build_intermission_all_focus_clean import (  # noqa: E402
    cluster_grid,
    glyph_component,
)
from build_intermission_state_ab import (  # noqa: E402
    Zstd,
    read_state_core,
    write_state_with_core,
)
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_intermission_labels_ko import load_resolved  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    FOCUS_ATTR,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    parse_sprites,
)

SCREEN_W = 224
SCREEN_H = 144
TILE_BYTES = 0x20
TILEMAP_BASE = 0x3800

ASSET_COL0 = 1
ASSET_ROW0 = 1
ASSET_W = 26
ASSET_H_OLD = 16
ASSET_H = 17
ASSET_CELLS_OLD = ASSET_W * ASSET_H_OLD
ASSET_CELLS = ASSET_W * ASSET_H

HEADER = 0x54B780
MAP = 0x54B792
OLD_GFX = 0x54BAD2
OLD_AUX = 0x54E3CD
AUX_END = 0x54E410
AUX_BYTES = AUX_END - OLD_AUX
BANK_END = 0x550000

FOCUS_ATLAS_LO = 0x542000
FOCUS_ATLAS_HI = 0x544400
RUNTIME_HOOK_LO = 0x7A0600
RUNTIME_HOOK_HI = 0x7A1000

TOP_REMAP = {
    "mission_status": {"outline": 0x03, "fill": 0x0E},
    "scouting": {"outline": 0x03, "fill": 0x0E},
}

PARENT_LABELS = (
    {
        "name": "parent_operation",
        "japanese": "\u4f5c\u6226",
        "korean": "\uc791\uc804",
        "row": 3,
        "col0": 1,
        "col1": 5,
        "japanese_pixels": 315,
        "korean_pixels": 296,
    },
    {
        "name": "parent_organization",
        "japanese": "\u7de8\u6210",
        "korean": "\ud3b8\uc131",
        "row": 7,
        "col0": 7,
        "col1": 11,
        "japanese_pixels": 291,
        "korean_pixels": 306,
    },
    {
        "name": "parent_development",
        "japanese": "\u958b\u767a",
        "korean": "\uac1c\ubc1c",
        "row": 11,
        "col0": 1,
        "col1": 5,
        "japanese_pixels": 358,
        "korean_pixels": 297,
    },
    {
        "name": "parent_system",
        "japanese": "\u30b7\u30b9\u30c6\u30e0",
        "korean": "\uc2dc\uc2a4\ud15c",
        "row": 16,
        "col0": 1,
        "col1": 9,
        "japanese_pixels": 224,
        "korean_pixels": 386,
    },
)


@dataclass(frozen=True)
class FocusMasks:
    name: str
    japanese: str
    korean: str
    bounds: tuple[int, int, int, int]
    focus_origin: tuple[int, int]
    jp: dict[tuple[int, int], str]
    ko: dict[tuple[int, int], str]
    source_state: str
    clean_state: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def decode_tile(raw: bytes) -> list[list[int]]:
    if len(raw) != TILE_BYTES:
        raise ValueError("tile must be 32 bytes")
    return [row[:] for row in tiles_4bpp(raw)[0]]


def encode_tile(grid: list[list[int]]) -> bytes:
    if len(grid) != 8 or any(len(row) != 8 for row in grid):
        raise ValueError("tile grid must be 8x8")
    return bytes(
        ((grid[y][x] & 0x0F) << 4) | (grid[y][x + 1] & 0x0F)
        for y in range(8)
        for x in range(0, 8, 2)
    )


def entry(ram: bytes, col: int, row: int) -> int:
    off = TILEMAP_BASE + (row * 32 + col) * 2
    return int.from_bytes(ram[off : off + 2], "little")


def cell_raw(ram: bytes, col: int, row: int) -> bytes:
    value = entry(ram, col, row)
    tile = value & 0x01FF
    gfx_base = 0x8000 if value & 0x2000 else 0x4000
    return bytes(ram[gfx_base + tile * TILE_BYTES : gfx_base + (tile + 1) * TILE_BYTES])


def asset_positions() -> list[tuple[int, int]]:
    return [
        (ASSET_COL0 + col, ASSET_ROW0 + row)
        for row in range(ASSET_H)
        for col in range(ASSET_W)
    ]


def visible_positions() -> list[tuple[int, int]]:
    return [(col, row) for row in range(18) for col in range(28)]


def screen_from_ram(ram: bytes) -> list[list[int]]:
    out = [[0] * SCREEN_W for _ in range(SCREEN_H)]
    for row in range(18):
        for col in range(28):
            grid = decode_tile(cell_raw(ram, col, row))
            for y in range(8):
                for x in range(8):
                    out[row * 8 + y][col * 8 + x] = grid[y][x]
    return out


def grids_from_screen(screen: list[list[int]]) -> dict[tuple[int, int], list[list[int]]]:
    return {
        (col, row): [
            screen[row * 8 + y][col * 8 : col * 8 + 8] for y in range(8)
        ]
        for row in range(18)
        for col in range(28)
    }


def copy_screen(screen: list[list[int]]) -> list[list[int]]:
    return [row[:] for row in screen]


def bbox(points: Iterable[tuple[int, int]]) -> list[int]:
    rows = list(points)
    if not rows:
        raise ValueError("empty bbox")
    return [
        min(x for x, _ in rows),
        min(y for _, y in rows),
        max(x for x, _ in rows) + 1,
        max(y for _, y in rows) + 1,
    ]


def expand_box(box: Iterable[int], amount: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = (int(v) for v in box)
    return (
        max(0, left - amount),
        max(0, top - amount),
        min(SCREEN_W, right + amount),
        min(SCREEN_H, bottom + amount),
    )


def dilate(points: set[tuple[int, int]], radius: int) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for x, y in points:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < SCREEN_W and 0 <= ny < SCREEN_H:
                    out.add((nx, ny))
    return out


def components(points: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    unseen = set(points)
    result: list[set[tuple[int, int]]] = []
    while unseen:
        start = unseen.pop()
        comp = {start}
        queue = collections.deque([start])
        while queue:
            x, y = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    point = (x + dx, y + dy)
                    if point in unseen:
                        unseen.remove(point)
                        comp.add(point)
                        queue.append(point)
        result.append(comp)
    return result


def row_mode(
    screen: list[list[int]],
    y: int,
    left: int,
    right: int,
    excluded: set[tuple[int, int]],
) -> int:
    outer = [
        screen[y][x]
        for x in range(max(0, left), min(SCREEN_W, right))
        if (x, y) not in excluded
    ]
    if not outer:
        raise RuntimeError(f"no row background samples at y={y}")
    counts = collections.Counter(outer)
    return counts.most_common(1)[0][0]


def focus_masks(target: dict, zstd: Zstd) -> FocusMasks:
    source_path = Path(target["source_state"])
    clean_path = Path(target["test_state"])
    rows = []
    for path in (source_path, clean_path):
        core, _ = read_state_core(path, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        sprites = [
            sprite
            for sprite in parse_sprites(
                core, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET
            )
            if sprite["attr"] == FOCUS_ATTR
        ]
        grid, bounds_xyxy = cluster_grid(ram, sprites)
        component = glyph_component(grid)
        rows.append((grid, bounds_xyxy, component))
    original, bounds_original, jp_local = rows[0]
    clean, bounds_clean, ko_local = rows[1]
    if bounds_original != bounds_clean or bounds_original != target["bounds_xyxy"]:
        raise RuntimeError(f"{target['name']}: focus bounds drifted")

    left, top, _, _ = (int(v) for v in bounds_original)

    def absolute(
        grid: list[list[int]], points: set[tuple[int, int]]
    ) -> dict[tuple[int, int], str]:
        out: dict[tuple[int, int], str] = {}
        for x, y in points:
            value = grid[y][x]
            if value == 0x0F:
                cls = "fill"
            elif value == 1:
                cls = "outline"
            else:
                raise RuntimeError(
                    f"{target['name']}: focus glyph uses unexpected index {value:X}"
                )
            ax, ay = left + x, top + y
            if not (0 <= ax < SCREEN_W and 0 <= ay < SCREEN_H):
                raise RuntimeError(f"{target['name']}: focus glyph outside framebuffer")
            out[(ax, ay)] = cls
        return out

    jp = absolute(original, jp_local)
    ko = absolute(clean, ko_local)
    evidence = target["composition"]
    if len(jp) != int(evidence["japanese_component_pixels"]):
        raise RuntimeError(f"{target['name']}: Japanese focus mask count drifted")
    if len(ko) != int(evidence["korean_pixels"]):
        raise RuntimeError(f"{target['name']}: Korean focus mask count drifted")
    origin = (
        int(target["bounds_xyxy"][0])
        + int(evidence["korean_origin_xy"][0]),
        int(target["bounds_xyxy"][1])
        + int(evidence["korean_origin_xy"][1]),
    )
    return FocusMasks(
        name=target["name"],
        japanese=target["japanese"],
        korean=target["korean"],
        bounds=tuple(int(v) for v in bounds_original),
        focus_origin=origin,
        jp=jp,
        ko=ko,
        source_state=str(source_path),
        clean_state=str(clean_path),
    )


def parent_masks(
    before: list[list[int]],
    body: bytes,
    resolved: dict[tuple[int, int], int],
) -> list[FocusMasks]:
    """Recover all four parent labels from the normal localized overlay atlas.

    The transition asset is itself an overlay: zero is transparent, while 1/F are
    the outline/fill.  The normal atlas already contains the approved Korean
    parent labels, so copying its non-zero glyph components gives exact geometry
    without rasterizing a second font.  Components containing index 3 are panel
    decoration, and the two-pixel fragment beside 開発 belongs to the neighbouring
    ``MS`` label; both are deliberately excluded.
    """

    def selected_components(
        pixels: dict[tuple[int, int], int]
    ) -> dict[tuple[int, int], str]:
        out: dict[tuple[int, int], str] = {}
        for comp in components(set(pixels)):
            values = {pixels[point] for point in comp}
            if len(comp) < 10 or 0x0F not in values or not values <= {1, 0x0F}:
                continue
            for point in comp:
                out[point] = "fill" if pixels[point] == 0x0F else "outline"
        return out

    result: list[FocusMasks] = []
    for spec in PARENT_LABELS:
        row = int(spec["row"])
        col0 = int(spec["col0"])
        col1 = int(spec["col1"])
        original_pixels = {
            (x, y): before[y][x]
            for y in range(row * 8, min(SCREEN_H, (row + 2) * 8))
            for x in range(col0 * 8, (col1 + 1) * 8)
            if before[y][x]
        }
        jp = selected_components(original_pixels)

        localized_pixels: dict[tuple[int, int], int] = {}
        for tile_row in (row, row + 1):
            for col in range(col0, col1 + 1):
                off = resolved[(col, tile_row)]
                grid = decode_tile(body[off : off + TILE_BYTES])
                for y in range(8):
                    for x in range(8):
                        value = grid[y][x]
                        if value:
                            localized_pixels[(col * 8 + x, tile_row * 8 + y)] = value
        ko = selected_components(localized_pixels)

        if len(jp) != int(spec["japanese_pixels"]):
            raise RuntimeError(
                f"{spec['name']}: Japanese parent mask drifted "
                f"({len(jp)} != {spec['japanese_pixels']})"
            )
        if len(ko) != int(spec["korean_pixels"]):
            raise RuntimeError(
                f"{spec['name']}: Korean parent mask drifted "
                f"({len(ko)} != {spec['korean_pixels']})"
            )
        ko_box = bbox(ko)
        result.append(
            FocusMasks(
                name=str(spec["name"]),
                japanese=str(spec["japanese"]),
                korean=str(spec["korean"]),
                bounds=(col0 * 8, row * 8, (col1 + 1) * 8, (row + 2) * 8),
                focus_origin=(ko_box[0], ko_box[1]),
                jp=jp,
                ko=ko,
                source_state="transition overlay screen reconstructed from QuickSave1",
                clean_state="normal localized overlay atlas resolved from current main TIP",
            )
        )
    return result


def ink_palette(name: str) -> dict[str, int]:
    """Return the static-layer palette indices for the approved focus mask.

    Geometry comes from the focus clean state, but its sprite palette cannot be
    copied to the BG layer.  The established BG contract is 1=outline/F=fill;
    only the two top labels use the separately measured 3/E normalization that
    removes the user-reported black/yellow appearance.
    """
    if name in TOP_REMAP:
        return dict(TOP_REMAP[name])
    return {"outline": 1, "fill": 0x0F}


def label_cleanup(
    before: list[list[int]], masks: FocusMasks
) -> tuple[set[tuple[int, int]], dict[int, int], dict]:
    jp_points = set(masks.jp)
    ko_points = set(masks.ko)
    jp_box = bbox(jp_points)
    ko_box = bbox(ko_points)
    union_box = [
        min(jp_box[0], ko_box[0]),
        min(jp_box[1], ko_box[1]),
        max(jp_box[2], ko_box[2]),
        max(jp_box[3], ko_box[3]),
    ]
    scan_left, scan_top, scan_right, scan_bottom = expand_box(union_box, 8)
    band_left, band_top, band_right, band_bottom = expand_box(union_box, 2)
    # Stay within the static asset destination. This is also an explicit guard
    # against touching unrelated screen decoration outside cols 1..26 / rows 1..17.
    scan_left = max(scan_left, ASSET_COL0 * 8)
    scan_right = min(scan_right, (ASSET_COL0 + ASSET_W) * 8)
    scan_top = max(scan_top, ASSET_ROW0 * 8)
    scan_bottom = min(scan_bottom, (ASSET_ROW0 + ASSET_H) * 8)
    band_left = max(band_left, scan_left)
    band_right = min(band_right, scan_right)
    band_top = max(band_top, scan_top)
    band_bottom = min(band_bottom, scan_bottom)

    excluded_for_sampling = dilate(jp_points | ko_points, 2)
    backgrounds = {
        y: row_mode(before, y, scan_left, scan_right, excluded_for_sampling)
        for y in range(scan_top, scan_bottom)
    }
    deviations = {
        (x, y)
        for y in range(scan_top, scan_bottom)
        for x in range(scan_left, scan_right)
        if before[y][x] != backgrounds[y]
    }
    seed = dilate(jp_points | ko_points, 1)
    selected: set[tuple[int, int]] = set()
    selected_components = 0
    for comp in components(deviations):
        if not (comp & seed):
            continue
        if not any(band_top <= y < band_bottom for _, y in comp):
            continue
        selected |= {
            point
            for point in comp
            if band_left <= point[0] < band_right
            and band_top <= point[1] < band_bottom
        }
        selected_components += 1

    jp_rect = expand_box(jp_box, 1)
    ko_rect = expand_box(ko_box, 1)
    for x, y in deviations:
        in_jp = jp_rect[0] <= x < jp_rect[2] and jp_rect[1] <= y < jp_rect[3]
        in_ko = ko_rect[0] <= x < ko_rect[2] and ko_rect[1] <= y < ko_rect[3]
        if in_jp or in_ko:
            selected.add((x, y))
    selected |= jp_points

    if any(not (scan_top <= y < scan_bottom) for _, y in selected):
        raise RuntimeError(f"{masks.name}: cleanup escaped scan band")
    changed_to_background = {
        point
        for point in selected
        if before[point[1]][point[0]] != backgrounds[point[1]]
    }
    return selected, backgrounds, {
        "focus_japanese_bbox_xyxy": jp_box,
        "focus_korean_bbox_xyxy": ko_box,
        "scan_bbox_xyxy": [scan_left, scan_top, scan_right, scan_bottom],
        "text_band_bbox_xyxy": [band_left, band_top, band_right, band_bottom],
        "selected_components": selected_components,
        "deviation_pixels_in_scan": len(deviations),
        "changed_to_background": len(changed_to_background),
    }


def render_index_screen(screen: list[list[int]], path: Path, scale: int = 4) -> None:
    image = Image.new("RGB", (SCREEN_W, SCREEN_H), GREYS_16[0])
    pixels = image.load()
    for y in range(SCREEN_H):
        for x in range(SCREEN_W):
            pixels[x, y] = GREYS_16[screen[y][x]]
    if scale > 1:
        image = image.resize((SCREEN_W * scale, SCREEN_H * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def render_crop(
    screen: list[list[int]], box: list[int], path: Path, scale: int = 6
) -> None:
    left, top, right, bottom = expand_box(box, 4)
    image = Image.new("RGB", (right - left, bottom - top), GREYS_16[0])
    pixels = image.load()
    for y in range(top, bottom):
        for x in range(left, right):
            pixels[x - left, y - top] = GREYS_16[screen[y][x]]
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def render_mask_crop(
    box: list[int],
    path: Path,
    *,
    focus: dict[tuple[int, int], str] | None = None,
    nonfocus: dict[tuple[int, int], str] | None = None,
    residual: set[tuple[int, int]] | None = None,
    scale: int = 6,
) -> None:
    left, top, right, bottom = expand_box(box, 4)
    width = right - left
    panels = 2 if focus is not None and nonfocus is not None else 1
    image = Image.new("RGB", (width * panels, bottom - top), (0, 0, 0))
    pixels = image.load()

    def draw(points: dict[tuple[int, int], str], ox: int) -> None:
        for (x, y), cls in points.items():
            if left <= x < right and top <= y < bottom:
                pixels[ox + x - left, y - top] = (
                    (255, 255, 255) if cls == "fill" else (128, 128, 128)
                )

    if focus is not None:
        draw(focus, 0)
    if nonfocus is not None:
        draw(nonfocus, width if panels == 2 else 0)
    if residual is not None:
        for x, y in residual:
            if left <= x < right and top <= y < bottom:
                pixels[x - left, y - top] = (255, 255, 255)
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def far_ptr(offset: int, segment: int = 0x3000) -> bytes:
    return offset.to_bytes(2, "little") + segment.to_bytes(2, "little")


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def allocate_tiles(
    desired_raws: list[bytes],
    private_indices: set[int],
) -> tuple[list[bytes], list[int], dict]:
    unique: list[bytes] = []
    shared_by_raw: dict[bytes, int] = {}
    remap: list[int] = []
    private_slots: list[int] = []
    for index, raw in enumerate(desired_raws):
        if index in private_indices:
            slot = len(unique)
            unique.append(raw)
            remap.append(slot)
            private_slots.append(slot)
            continue
        slot = shared_by_raw.get(raw)
        if slot is None:
            slot = len(unique)
            unique.append(raw)
            shared_by_raw[raw] = slot
        remap.append(slot)
    if len(unique) >= 0x200:
        raise RuntimeError(f"raw asset needs {len(unique)} tiles, exceeds 9-bit limit")
    return unique, remap, {
        "unique_tiles": len(unique),
        "deduplicated_unchanged_cells": len(desired_raws) - len(private_indices),
        "private_cells": len(private_indices),
        "private_slots": private_slots,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--stock-rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--base-sav",
        type=Path,
        default=ROOT / "sram/monoeye_ko_expanded.sav",
    )
    ap.add_argument(
        "--base-bizhawk-saveram",
        type=Path,
        default=(
            ROOT
            / "BizHawk-2.11.1-win-x64/WonderSwan/SaveRAM"
            / "monoeye ko expanded.SaveRAM"
        ),
    )
    ap.add_argument(
        "--state-dir",
        type=Path,
        default=(
            ROOT
            / "BizHawk-2.11.1-win-x64/WonderSwan/State"
            / "monoeye ko expanded.Cygne"
        ),
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
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_static_full_cleanup_candidate",
    )
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--zstd-level", type=int, default=3)
    ap.add_argument(
        "--strict-ink-sweep",
        action="store_true",
        help=(
            "also erase every disconnected stock 1/F ink pixel inside each "
            "label's protected text band before drawing Korean"
        ),
    )
    ap.add_argument(
        "--transparent-cleanup",
        action="store_true",
        help=(
            "restore selected Japanese pixels to transparent index 0 instead "
            "of a row-mode estimate (the transition asset is an overlay)"
        ),
    )
    args = ap.parse_args(argv)

    states = [args.state_dir / f"Mednafen.QuickSave{i}.State" for i in (1, 2, 3)]
    required = (
        args.stock_rom,
        args.base_rom,
        args.base_sav,
        args.base_bizhawk_saveram,
        args.focus_report,
        args.overlay_resolved,
        args.zstd_dll,
        *states,
    )
    for path in required:
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    stock = args.stock_rom.read_bytes()
    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    if len(stock) != 0x800000:
        raise RuntimeError("stock ROM must be 8 MiB")
    body = base_rom[base : base + len(stock)]
    expected_header = bytes.fromhex(
        "1A 10 22 02 A0 01 92 B7 00 30 D2 BA 00 30 CD E3 00 30"
    )
    if body[HEADER : HEADER + len(expected_header)] != expected_header:
        raise RuntimeError("main TIP intermission asset header drifted")

    zstd = Zstd(args.zstd_dll)
    cores: list[bytes] = []
    core_names: list[str] = []
    rams: list[bytes] = []
    source_state_rows = []
    for path in states:
        core, core_name = read_state_core(path, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        cores.append(core)
        core_names.append(core_name)
        rams.append(ram)
        source_state_rows.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "core_member": core_name,
                "tilemap_sha256": sha256_bytes(ram[TILEMAP_BASE : TILEMAP_BASE + 0x800]),
            }
        )
    if len({row["tilemap_sha256"] for row in source_state_rows}) != 1:
        raise RuntimeError("QuickSave1-3 tilemaps differ")
    for pos in visible_positions():
        if len({cell_raw(ram, *pos) for ram in rams}) != 1:
            raise RuntimeError(f"QuickSave1-3 visible background differs at {pos}")

    before = screen_from_ram(rams[0])
    cleanup_only = copy_screen(before)
    after = copy_screen(before)
    focus_report = json.loads(args.focus_report.read_text(encoding="utf-8"))
    targets = focus_report.get("targets", [])
    if len(targets) != 12:
        raise RuntimeError(f"focus report has {len(targets)} targets, expected 12")

    label_rows: list[dict] = []
    allow_points: set[tuple[int, int]] = set()
    cleanup_owner: dict[tuple[int, int], str] = {}
    final_owner: dict[tuple[int, int], str] = {}
    label_cells: dict[str, set[tuple[int, int]]] = {}

    resolved = load_resolved(args.overlay_resolved)
    for masks in parent_masks(before, body, resolved):
        jp_points = set(masks.jp)
        ko_points = set(masks.ko)
        jp_box = bbox(jp_points)
        ko_box = bbox(ko_points)
        union_box = [
            min(jp_box[0], ko_box[0]),
            min(jp_box[1], ko_box[1]),
            max(jp_box[2], ko_box[2]),
            max(jp_box[3], ko_box[3]),
        ]
        cleanup = set(jp_points)
        strict_sweep = {
            (x, y)
            for y in range(union_box[1], union_box[3])
            for x in range(union_box[0], union_box[2])
            if before[y][x] in {1, 0x0F}
        }
        strict_extra = strict_sweep - cleanup if args.strict_ink_sweep else set()
        cleanup |= strict_extra
        palette = {"outline": 1, "fill": 0x0F}

        for x, y in cleanup:
            previous = cleanup_owner.get((x, y))
            if previous is not None and cleanup_only[y][x] != 0:
                raise RuntimeError(
                    f"cleanup conflict at {(x, y)} between {previous} and {masks.name}"
                )
            cleanup_owner[(x, y)] = masks.name
            cleanup_only[y][x] = 0
            after[y][x] = 0
        for (x, y), cls in masks.ko.items():
            value = palette[cls]
            previous = final_owner.get((x, y))
            if previous is not None and after[y][x] != value:
                raise RuntimeError(
                    f"foreground conflict at {(x, y)} between {previous} and {masks.name}"
                )
            final_owner[(x, y)] = masks.name
            after[y][x] = value

        residual_footprint = {
            point
            for point in jp_points
            if point not in ko_points and after[point[1]][point[0]] != 0
        }
        final_mask = {
            point: cls
            for point, cls in masks.ko.items()
            if after[point[1]][point[0]] == palette[cls]
        }
        if residual_footprint or set(final_mask) != ko_points:
            raise RuntimeError(f"{masks.name}: parent cleanup/mask verification failed")

        own_allow = cleanup | ko_points
        label_cells[masks.name] = {
            (x // 8, y // 8) for x, y in own_allow if before[y][x] != after[y][x]
        }
        allow_points |= own_allow

        capture_dir = args.out_dir / "labels" / masks.name
        render_crop(before, union_box, capture_dir / "before.png")
        render_crop(cleanup_only, union_box, capture_dir / "cleanup_only.png")
        render_crop(after, union_box, capture_dir / "after.png")
        render_mask_crop(
            union_box,
            capture_dir / "focus_nonfocus_mask_compare.png",
            focus=masks.ko,
            nonfocus=final_mask,
        )
        render_mask_crop(
            union_box,
            capture_dir / "residual_mask.png",
            residual=residual_footprint,
        )

        label_rows.append(
            {
                "name": masks.name,
                "japanese": masks.japanese,
                "korean": masks.korean,
                "focus_source_state": masks.source_state,
                "focus_clean_state": masks.clean_state,
                "mask_source": "current main TIP normal overlay atlas",
                "japanese_original_footprint_pixels": len(jp_points),
                "surrounding_ring_residual_pixels_removed": 0,
                "panel_background_pixels_restored": len(jp_points),
                "final_korean_foreground_pixels": len(ko_points),
                "private_tiles": 0,
                "focus_origin_xy": list(masks.focus_origin),
                "nonfocus_origin_xy": list(masks.focus_origin),
                "focus_origin_delta_xy": [0, 0],
                "focus_ink_bbox_xyxy": ko_box,
                "nonfocus_ink_bbox_xyxy": ko_box,
                "ink_bbox_delta": [0, 0, 0, 0],
                "foreground_mask_pixel_difference": 0,
                "final_japanese_footprint_residual_pixels": len(residual_footprint),
                "final_ring_residual_pixels": 0,
                "final_residual_pixels": len(residual_footprint),
                "ink_palette_indices": palette,
                "cleanup": {
                    "mode": "transparent_overlay_exact",
                    "focus_japanese_bbox_xyxy": jp_box,
                    "focus_korean_bbox_xyxy": ko_box,
                    "scan_bbox_xyxy": union_box,
                    "text_band_bbox_xyxy": union_box,
                    "selected_components": len(
                        components(set(masks.jp))
                    ),
                    "deviation_pixels_in_scan": len(jp_points),
                    "changed_to_background": len(jp_points),
                    "strict_ink_sweep_enabled": args.strict_ink_sweep,
                    "strict_disconnected_ink_pixels_removed": len(strict_extra),
                },
                "screen_cells_changed": [
                    list(pos)
                    for pos in sorted(label_cells[masks.name], key=lambda p: (p[1], p[0]))
                ],
                "captures": {
                    "before": str(capture_dir / "before.png"),
                    "cleanup_only": str(capture_dir / "cleanup_only.png"),
                    "after": str(capture_dir / "after.png"),
                    "focus_nonfocus_mask_compare": str(
                        capture_dir / "focus_nonfocus_mask_compare.png"
                    ),
                    "residual_mask": str(capture_dir / "residual_mask.png"),
                },
            }
        )

    for target in targets:
        masks = focus_masks(target, zstd)
        cleanup, backgrounds, evidence = label_cleanup(before, masks)
        band_left, band_top, band_right, band_bottom = evidence[
            "text_band_bbox_xyxy"
        ]
        strict_sweep = {
            (x, y)
            for y in range(band_top, band_bottom)
            for x in range(band_left, band_right)
            if before[y][x] in {1, 0x0F}
        }
        strict_extra = strict_sweep - cleanup if args.strict_ink_sweep else set()
        cleanup |= strict_extra
        evidence["strict_ink_sweep_enabled"] = args.strict_ink_sweep
        evidence["strict_disconnected_ink_pixels_removed"] = len(strict_extra)
        if args.transparent_cleanup:
            backgrounds = {y: 0 for y in backgrounds}
        evidence["background_restore_mode"] = (
            "transparent_index_0" if args.transparent_cleanup else "measured_row_mode"
        )
        palette = ink_palette(masks.name)

        for x, y in cleanup:
            previous = cleanup_owner.get((x, y))
            if previous is not None and cleanup_only[y][x] != backgrounds[y]:
                raise RuntimeError(
                    f"cleanup conflict at {(x, y)} between {previous} and {masks.name}"
                )
            cleanup_owner[(x, y)] = masks.name
            cleanup_only[y][x] = backgrounds[y]
            after[y][x] = backgrounds[y]
        for (x, y), cls in masks.ko.items():
            value = palette[cls]
            previous = final_owner.get((x, y))
            if previous is not None and after[y][x] != value:
                raise RuntimeError(
                    f"foreground conflict at {(x, y)} between {previous} and {masks.name}"
                )
            final_owner[(x, y)] = masks.name
            after[y][x] = value

        jp_points = set(masks.jp)
        ko_points = set(masks.ko)
        residual_footprint = {
            point
            for point in jp_points
            if point not in ko_points
            and after[point[1]][point[0]] != backgrounds[point[1]]
        }
        residual_ring = {
            point
            for point in cleanup - jp_points
            if point not in ko_points
            and after[point[1]][point[0]] != backgrounds[point[1]]
        }
        final_mask = {
            point: cls
            for point, cls in masks.ko.items()
            if after[point[1]][point[0]] == palette[cls]
        }
        missing_focus = set(masks.ko) - set(final_mask)
        extra_foreground = set(final_mask) - set(masks.ko)
        if residual_footprint or residual_ring or missing_focus or extra_foreground:
            raise RuntimeError(f"{masks.name}: cleanup/mask verification failed")

        changed_points = {
            (x, y)
            for y in range(SCREEN_H)
            for x in range(SCREEN_W)
            if before[y][x] != after[y][x]
        }
        own_allow = cleanup | ko_points
        label_cells[masks.name] = {
            (x // 8, y // 8) for x, y in own_allow if before[y][x] != after[y][x]
        }
        allow_points |= own_allow

        jp_box = bbox(jp_points)
        ko_box = bbox(ko_points)
        capture_dir = args.out_dir / "labels" / masks.name
        render_crop(before, [
            min(jp_box[0], ko_box[0]),
            min(jp_box[1], ko_box[1]),
            max(jp_box[2], ko_box[2]),
            max(jp_box[3], ko_box[3]),
        ], capture_dir / "before.png")
        render_crop(cleanup_only, [
            min(jp_box[0], ko_box[0]),
            min(jp_box[1], ko_box[1]),
            max(jp_box[2], ko_box[2]),
            max(jp_box[3], ko_box[3]),
        ], capture_dir / "cleanup_only.png")
        render_crop(after, [
            min(jp_box[0], ko_box[0]),
            min(jp_box[1], ko_box[1]),
            max(jp_box[2], ko_box[2]),
            max(jp_box[3], ko_box[3]),
        ], capture_dir / "after.png")
        render_mask_crop(
            [
                min(jp_box[0], ko_box[0]),
                min(jp_box[1], ko_box[1]),
                max(jp_box[2], ko_box[2]),
                max(jp_box[3], ko_box[3]),
            ],
            capture_dir / "focus_nonfocus_mask_compare.png",
            focus=masks.ko,
            nonfocus=final_mask,
        )
        render_mask_crop(
            evidence["scan_bbox_xyxy"],
            capture_dir / "residual_mask.png",
            residual=residual_footprint | residual_ring,
        )

        restored = sum(
            before[y][x] != backgrounds[y] for x, y in cleanup
        )
        ring_removed = sum(
            point not in jp_points and before[point[1]][point[0]] != backgrounds[point[1]]
            for point in cleanup
        )
        label_rows.append(
            {
                "name": masks.name,
                "japanese": masks.japanese,
                "korean": masks.korean,
                "focus_source_state": masks.source_state,
                "focus_clean_state": masks.clean_state,
                "japanese_original_footprint_pixels": len(jp_points),
                "surrounding_ring_residual_pixels_removed": ring_removed,
                "panel_background_pixels_restored": restored,
                "final_korean_foreground_pixels": len(ko_points),
                "private_tiles": 0,
                "focus_origin_xy": list(masks.focus_origin),
                "nonfocus_origin_xy": list(masks.focus_origin),
                "focus_origin_delta_xy": [0, 0],
                "focus_ink_bbox_xyxy": ko_box,
                "nonfocus_ink_bbox_xyxy": ko_box,
                "ink_bbox_delta": [0, 0, 0, 0],
                "foreground_mask_pixel_difference": 0,
                "final_japanese_footprint_residual_pixels": len(residual_footprint),
                "final_ring_residual_pixels": len(residual_ring),
                "final_residual_pixels": len(residual_footprint | residual_ring),
                "ink_palette_indices": palette,
                "cleanup": evidence,
                "screen_cells_changed": [
                    list(pos)
                    for pos in sorted(label_cells[masks.name], key=lambda p: (p[1], p[0]))
                ],
                "captures": {
                    "before": str(capture_dir / "before.png"),
                    "cleanup_only": str(capture_dir / "cleanup_only.png"),
                    "after": str(capture_dir / "after.png"),
                    "focus_nonfocus_mask_compare": str(
                        capture_dir / "focus_nonfocus_mask_compare.png"
                    ),
                    "residual_mask": str(capture_dir / "residual_mask.png"),
                },
            }
        )

    outside_changes = {
        (x, y)
        for y in range(SCREEN_H)
        for x in range(SCREEN_W)
        if before[y][x] != after[y][x] and (x, y) not in allow_points
    }
    if outside_changes:
        raise RuntimeError(
            f"framebuffer changed outside allow masks at {sorted(outside_changes)[:8]}"
        )

    # Decoration pixels are the non-background scan deviations that were not selected
    # for cleanup. They must remain byte/pixel-identical.
    decoration_damage = 0
    for y in range(SCREEN_H):
        for x in range(SCREEN_W):
            if (x, y) in allow_points:
                continue
            if before[y][x] != after[y][x]:
                decoration_damage += 1
    if decoration_damage:
        raise RuntimeError("panel decoration changed outside the label masks")

    before_grids = grids_from_screen(before)
    after_grids = grids_from_screen(after)
    positions = asset_positions()
    before_raws = [encode_tile(before_grids[pos]) for pos in positions]
    desired_raws = [encode_tile(after_grids[pos]) for pos in positions]
    changed_indices = {
        index
        for index, (old, new) in enumerate(zip(before_raws, desired_raws))
        if old != new
    }
    source_groups: dict[bytes, list[int]] = collections.defaultdict(list)
    for index, raw in enumerate(before_raws):
        source_groups[raw].append(index)
    # Only cells whose original tile is shared with a different desired result
    # require a private clone. Exclusive edits and equal desired variants may use
    # the normal deduplicated raw map without creating unnecessary VRAM pressure.
    private_indices = {
        index
        for index in changed_indices
        if any(
            desired_raws[peer] != desired_raws[index]
            for peer in source_groups[before_raws[index]]
        )
    }
    unique, remap, allocation_report = allocate_tiles(desired_raws, private_indices)
    if not changed_indices:
        raise RuntimeError("no static cells changed")

    private_positions = {positions[index] for index in private_indices}
    for row in label_rows:
        row["private_tiles"] = len(
            label_cells[row["name"]] & private_positions
        )

    old_map = [
        int.from_bytes(body[MAP + i * 2 : MAP + i * 2 + 2], "little")
        for i in range(ASSET_CELLS_OLD)
    ]
    if [value & 0x01FF for value in old_map] != list(range(ASSET_CELLS_OLD)):
        raise RuntimeError("main TIP static source map is no longer identity 0..415")

    map_bytes = ASSET_CELLS * 2
    gfx_start = align(MAP + map_bytes, 0x10)
    gfx = b"".join(unique)
    aux_start = align(gfx_start + len(gfx), 0x10)
    aux = body[OLD_AUX:AUX_END]
    if len(aux) != AUX_BYTES:
        raise RuntimeError("auxiliary block length drifted")
    asset_end = aux_start + len(aux)
    if asset_end > BANK_END:
        raise RuntimeError(
            f"extended raw asset ends at {asset_end:06X}, beyond bank 54"
        )
    # The newly occupied span may contain the old compressed asset itself, but any
    # extension beyond the old declared area must be 0xFF padding in the base TIP.
    extension_start = max(AUX_END, MAP + ASSET_CELLS_OLD * 2)
    if any(value != 0xFF for value in body[extension_start:asset_end]):
        first = next(
            extension_start + i
            for i, value in enumerate(body[extension_start:asset_end])
            if value != 0xFF
        )
        raise RuntimeError(f"extended asset would overwrite non-FF byte at {first:06X}")

    source_attrs = [value & ~0x01FF for value in old_map]
    row17_attrs = [entry(rams[0], col, 17) & ~0x01FF for col in range(1, 27)]
    attrs = source_attrs + row17_attrs
    if len(attrs) != ASSET_CELLS:
        raise RuntimeError("asset attribute count mismatch")

    candidate = bytearray(base_rom)
    off = base
    candidate[off + HEADER] = ASSET_W
    candidate[off + HEADER + 1] = ASSET_H
    candidate[off + HEADER + 2 : off + HEADER + 4] = bytes.fromhex("00 02")
    candidate[off + HEADER + 4 : off + HEADER + 6] = len(unique).to_bytes(2, "little")
    candidate[off + HEADER + 6 : off + HEADER + 10] = far_ptr(MAP & 0xFFFF)
    candidate[off + HEADER + 10 : off + HEADER + 14] = far_ptr(gfx_start & 0xFFFF)
    candidate[off + HEADER + 14 : off + HEADER + 18] = far_ptr(aux_start & 0xFFFF)
    for index, (attr, tile) in enumerate(zip(attrs, remap)):
        value = attr | tile
        start = off + MAP + index * 2
        candidate[start : start + 2] = value.to_bytes(2, "little")
    pad_start = off + MAP + map_bytes
    candidate[pad_start : off + gfx_start] = b"\xFF" * (gfx_start - (MAP + map_bytes))
    candidate[off + gfx_start : off + gfx_start + len(gfx)] = gfx
    candidate[off + gfx_start + len(gfx) : off + aux_start] = b"\xFF" * (
        aux_start - (gfx_start + len(gfx))
    )
    candidate[off + aux_start : off + aux_start + len(aux)] = aux
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    changed_offsets = [
        index
        for index, (old, new) in enumerate(zip(base_rom, candidate_bytes))
        if old != new
    ]
    allowed = set(range(off + HEADER, off + HEADER + 18))
    allowed.update(range(off + MAP, off + asset_end))
    allowed.update((len(candidate_bytes) - 2, len(candidate_bytes) - 1))
    outside_rom = [index for index in changed_offsets if index not in allowed]
    if outside_rom:
        raise RuntimeError(f"ROM changed outside allowlist at {outside_rom[0]:07X}")
    focus_changed = sum(
        a != b
        for a, b in zip(
            base_rom[off + FOCUS_ATLAS_LO : off + FOCUS_ATLAS_HI],
            candidate_bytes[off + FOCUS_ATLAS_LO : off + FOCUS_ATLAS_HI],
        )
    )
    hook_changed = sum(
        a != b
        for a, b in zip(
            base_rom[off + RUNTIME_HOOK_LO : off + RUNTIME_HOOK_HI],
            candidate_bytes[off + RUNTIME_HOOK_LO : off + RUNTIME_HOOK_HI],
        )
    )
    if focus_changed or hook_changed:
        raise RuntimeError("focus atlas or runtime hook changed")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_out = args.out_dir / "intermission_static_full_cleanup_candidate.wsc"
    sav_out = ROOT / "sram/intermission_static_full_cleanup_candidate.sav"
    bizhawk_save_out = ROOT / "sram/intermission_static_full_cleanup_candidate.SaveRAM"
    rom_out.write_bytes(candidate_bytes)
    shutil.copy2(args.base_sav, sav_out)
    shutil.copy2(args.base_bizhawk_saveram, bizhawk_save_out)

    render_index_screen(before, args.out_dir / "previews/before_full.png", args.scale)
    render_index_screen(
        cleanup_only, args.out_dir / "previews/cleanup_only_full.png", args.scale
    )
    render_index_screen(after, args.out_dir / "previews/after_full.png", args.scale)

    desired_visible_raw = {
        pos: encode_tile(after_grids[pos]) for pos in visible_positions()
    }
    changed_visible = {
        pos
        for pos in visible_positions()
        if encode_tile(before_grids[pos]) != desired_visible_raw[pos]
    }
    state_rows = []
    state_out_dir = args.out_dir / "states"
    for index, (source, core, core_name) in enumerate(
        zip(states, cores, core_names), 1
    ):
        patched_core, state_report = state_exact.patch_core(
            core, desired_visible_raw, changed_visible
        )
        state_out = state_out_dir / f"Mednafen.QuickSave{index}.State"
        compressed = write_state_with_core(
            source, state_out, core_name, patched_core, zstd, args.zstd_level
        )
        verify, verify_name = read_state_core(state_out, zstd)
        if verify_name != core_name or verify != patched_core:
            raise RuntimeError(f"QuickSave{index}: round-trip failed")
        state_rows.append(
            {
                "index": index,
                "source": str(source),
                "output": str(state_out),
                "sha256": sha256_file(state_out),
                "core_sha256": sha256_bytes(patched_core),
                "compressed_core_bytes": compressed,
                **state_report,
            }
        )

    allowlist = {
        "base_rom": str(args.base_rom),
        "candidate_rom": str(rom_out),
        "stock_relative_ranges": [
            {
                "name": "static_asset_header",
                "start": f"{HEADER:06X}",
                "end_exclusive": f"{HEADER + 18:06X}",
            },
            {
                "name": "extended_static_asset_map_gfx_aux",
                "start": f"{MAP:06X}",
                "end_exclusive": f"{asset_end:06X}",
            },
            {
                "name": "rom_checksum",
                "start_absolute": f"{len(candidate_bytes) - 2:07X}",
                "end_exclusive_absolute": f"{len(candidate_bytes):07X}",
            },
        ],
        "changed_bytes": len(changed_offsets),
        "outside_allowlist_bytes": len(outside_rom),
        "focus_atlas_changed_bytes": focus_changed,
        "runtime_hook_changed_bytes": hook_changed,
    }
    allowlist_path = args.out_dir / "rom_diff_allowlist.json"
    allowlist_path.write_text(
        json.dumps(allowlist, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = {
        "purpose": "full 16-label transition-overlay cleanup using 12 approved focus masks plus 4 localized parent masks and private ROM tiles",
        "cleanup_mode": (
            "strict protected-band stock 1/F ink sweep"
            if args.strict_ink_sweep
            else (
                "connected-component cleanup restored to transparent index 0"
                if args.transparent_cleanup
                else "connected-component and Japanese-footprint cleanup"
            )
        ),
        "base_rom": str(args.base_rom),
        "base_rom_sha256": sha256_bytes(base_rom),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": sha256_file(rom_out),
        "candidate_sav": str(sav_out),
        "candidate_sav_sha256": sha256_file(sav_out),
        "candidate_bizhawk_saveram": str(bizhawk_save_out),
        "candidate_bizhawk_saveram_sha256": sha256_file(bizhawk_save_out),
        "checksum": f"{checksum:04X}",
        "source_states": source_state_rows,
        "asset": {
            "header": f"{HEADER:06X}",
            "old_dimensions": [ASSET_W, ASSET_H_OLD],
            "new_dimensions": [ASSET_W, ASSET_H],
            "map": [f"{MAP:06X}", f"{MAP + map_bytes:06X}"],
            "graphics": [f"{gfx_start:06X}", f"{gfx_start + len(gfx):06X}"],
            "auxiliary": [f"{aux_start:06X}", f"{asset_end:06X}"],
            "mode_before": "22 02 compressed/deduplicated",
            "mode_after": "00 02 raw 4bpp explicit map",
            "cells": ASSET_CELLS,
            "allocation": allocation_report,
            "changed_cells": len(changed_indices),
            "changed_private_cells": len(private_indices),
            "extension_overwrites_only_ff": True,
        },
        "labels": label_rows,
        "patched_states": state_rows,
        "framebuffer": {
            "changed_pixels": sum(
                before[y][x] != after[y][x]
                for y in range(SCREEN_H)
                for x in range(SCREEN_W)
            ),
            "allowed_mask_pixels": len(allow_points),
            "changed_outside_allowed_mask": len(outside_changes),
            "panel_decoration_damage_pixels": decoration_damage,
        },
        "rom": {
            "changed_bytes_including_checksum": len(changed_offsets),
            "outside_allowlist_bytes": len(outside_rom),
            "focus_sprite_atlas_changed_bytes": focus_changed,
            "runtime_hook_changed_bytes": hook_changed,
            "allowlist": str(allowlist_path),
        },
        "verification": {
            "all_16_labels_processed": len(label_rows) == 16,
            "strict_ink_sweep_enabled": args.strict_ink_sweep,
            "transparent_cleanup_enabled": args.transparent_cleanup,
            "strict_disconnected_ink_pixels_removed": sum(
                int(row["cleanup"].get("strict_disconnected_ink_pixels_removed", 0))
                for row in label_rows
            ),
            "all_japanese_footprint_residuals_zero": all(
                row["final_japanese_footprint_residual_pixels"] == 0
                for row in label_rows
            ),
            "all_ring_residuals_zero": all(
                row["final_ring_residual_pixels"] == 0 for row in label_rows
            ),
            "all_focus_nonfocus_masks_exact": all(
                row["foreground_mask_pixel_difference"] == 0
                and row["ink_bbox_delta"] == [0, 0, 0, 0]
                and row["focus_origin_delta_xy"] == [0, 0]
                for row in label_rows
            ),
            "mission_status_and_scouting_exact": all(
                row["foreground_mask_pixel_difference"] == 0
                and row["focus_origin_delta_xy"] == [0, 0]
                and row["final_residual_pixels"] == 0
                for row in label_rows
                if row["name"] in {"mission_status", "scouting"}
            ),
            "framebuffer_changes_bounded": not outside_changes,
            "panel_decoration_unchanged": decoration_damage == 0,
            "all_shared_conflict_cells_private": len(private_indices)
            == allocation_report["private_cells"],
            "focus_sprite_atlas_unchanged": focus_changed == 0,
            "runtime_hook_region_unchanged": hook_changed == 0,
            "rom_diff_bounded": not outside_rom,
            "quicksave_1_2_3_source_tilemaps_identical": True,
            "quicksave_1_2_3_source_background_identical": True,
            "patched_states_round_trip": len(state_rows) == 3,
            "candidate_sav_is_latest_main_copy": sav_out.read_bytes()
            == args.base_sav.read_bytes(),
            "candidate_bizhawk_saveram_is_latest_main_copy": bizhawk_save_out.read_bytes()
            == args.base_bizhawk_saveram.read_bytes(),
            "main_tip_unchanged_by_builder": args.base_rom.read_bytes() == base_rom,
        },
        "previews": {
            "before": str(args.out_dir / "previews/before_full.png"),
            "cleanup_only": str(args.out_dir / "previews/cleanup_only_full.png"),
            "after": str(args.out_dir / "previews/after_full.png"),
        },
        "pending_runtime_capture_verification": True,
        "main_tip_promoted": False,
    }
    report_path = args.out_dir / "full_cleanup_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    readme = args.out_dir / "README.md"
    readme.write_text(
        "# Intermission static full cleanup candidate\n\n"
        "- ROM: `intermission_static_full_cleanup_candidate.wsc`\n"
        "- raw SaveRAM: `intermission_static_full_cleanup_candidate.sav`\n"
        "- BizHawk SaveRAM: `intermission_static_full_cleanup_candidate.SaveRAM`\n"
        "- validation states: `states/Mednafen.QuickSave1.State` through `QuickSave3.State`\n"
        "- report: `full_cleanup_report.json`\n"
        "- ROM diff allowlist: `rom_diff_allowlist.json`\n\n"
        "All sixteen transition-overlay labels are rebuilt: twelve from the approved focus-state "
        "foreground masks and four parent labels from the already-localized normal overlay atlas. "
        "The original Japanese footprint and connected one-tile-ring residue are restored to the "
        "measured row background before Korean ink is applied. Changed screen cells use private "
        "raw asset tiles, and the asset is extended to screen row 17 for Save/Load/Library.\n\n"
        "This is a user-validation candidate only. The main TIP is not modified.\n",
        encoding="utf-8",
    )

    print(f"labels                 : {len(label_rows)}/16")
    print(f"changed framebuffer px : {report['framebuffer']['changed_pixels']}")
    print(f"private screen cells   : {len(private_indices)}")
    print(f"raw asset tiles        : {len(unique)}")
    print(f"asset end              : {asset_end:06X}")
    print(f"ROM changed bytes      : {len(changed_offsets)}")
    print(f"checksum               : {checksum:04X}")
    print(f"candidate              : {rom_out}")
    print(f"states                 : {state_out_dir}")
    print(f"report                 : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
