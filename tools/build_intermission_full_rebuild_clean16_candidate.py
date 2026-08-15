#!/usr/bin/env python3
"""Rebuild the complete 16-label intermission overlay from a clean base.

Unlike the earlier footprint/ring cleanup, this builder clears each protected
text core completely to transparent index 0 and redraws the approved Korean
mask.  The 26x17 overlay is serialized with one unique map slot per screen cell.
Focus sprites use bank-0 tile IDs 0x110..0x133; those numeric slots may be used
only by bank-1 background cells and are excluded from every bank-0 BG mapping.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_intermission_static_full_cleanup_candidate as common  # noqa: E402
from build_intermission_state_ab import Zstd, read_state_core, write_state_with_core  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_intermission_labels_ko import (  # noqa: E402
    FONT,
    Rasteriser,
    draw_strip,
    load_resolved,
)
from trace_intermission_focus_sprites import (  # noqa: E402
    FOCUS_ATTR,
    NEXT_SPRITE_COUNT_CORE_OFFSET,
    NEXT_SPRITE_TABLE_CORE_OFFSET,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    parse_sprites,
)


EXPECTED_MAIN_SHA = "3b0a07f82d97a90055957dc310b6a9dc713c4d4c6aa4c75586b286e255412da9"
FOCUS_RESERVED_START = 0x110
FOCUS_RESERVED_END = 0x134
FOCUS_ATLAS_LO = 0x542000
FOCUS_ATLAS_HI = 0x544400
CONFIRM_ATLAS_LO = 0x547CFC
CONFIRM_ATLAS_HI = 0x549A1C
RUNTIME_HOOK_LO = 0x7A0600
RUNTIME_HOOK_HI = 0x7A1000
CANONICAL_FONT_SIZE = 13
CANONICAL_LETTER_SPACING = 1
PARENT_ERASE_WINDOWS = {
    "parent_operation": (8, 22, 48, 40),
    "parent_organization": (56, 50, 96, 70),
    "parent_development": (8, 87, 48, 104),
    "parent_system": (8, 124, 72, 142),
}
MS_PRESERVE_BOX = (12, 69, 47, 85)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def inverse_orient(grid: list[list[int]], entry: int) -> list[list[int]]:
    """Convert an oriented screen grid back to raw tile storage order."""
    out = [[0] * 8 for _ in range(8)]
    for y in range(8):
        sy = 7 - y if entry & 0x8000 else y
        for x in range(8):
            sx = 7 - x if entry & 0x4000 else x
            out[sy][sx] = grid[y][x]
    return out


def decode_rebuilt_asset(
    body: bytes,
    gfx_start: int,
    slot_count: int,
) -> list[list[int]]:
    screen = [[0] * common.SCREEN_W for _ in range(common.SCREEN_H)]
    for index, (col, row) in enumerate(common.asset_positions()):
        map_off = common.MAP + index * 2
        value = int.from_bytes(body[map_off : map_off + 2], "little")
        slot = value & 0x1FF
        if slot >= slot_count:
            raise RuntimeError(f"asset map references slot {slot:03X} >= {slot_count:03X}")
        raw = body[gfx_start + slot * common.TILE_BYTES : gfx_start + (slot + 1) * common.TILE_BYTES]
        grid = common.decode_tile(raw)
        for y in range(8):
            sy = 7 - y if value & 0x8000 else y
            for x in range(8):
                sx = 7 - x if value & 0x4000 else x
                screen[row * 8 + y][col * 8 + x] = grid[sy][sx]
    return screen


def focus_tiles_from_core(core: bytes) -> set[int]:
    """Return focus-atlas tile IDs referenced by either live sprite table."""
    return {
        int(sprite["tile"])
        for table_offset, count_offset in (
            (SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET),
            (NEXT_SPRITE_TABLE_CORE_OFFSET, NEXT_SPRITE_COUNT_CORE_OFFSET),
        )
        for sprite in parse_sprites(core, table_offset, count_offset)
        if int(sprite["attr"]) == FOCUS_ATTR
    }


def canonical_parent_masks() -> dict[str, dict[tuple[int, int], str]]:
    """Rasterize the four parent labels with the focus-atlas typography."""
    rasteriser = Rasteriser(FONT, CANONICAL_FONT_SIZE)
    result: dict[str, dict[tuple[int, int], str]] = {}
    for spec in common.PARENT_LABELS:
        col0 = int(spec["col0"])
        col1 = int(spec["col1"])
        row = int(spec["row"])
        width = (col1 - col0 + 1) * 8
        grid = draw_strip(
            str(spec["korean"]),
            width,
            rasteriser,
            0x0F,
            1,
            CANONICAL_LETTER_SPACING,
        )
        mask: dict[tuple[int, int], str] = {}
        for y, values in enumerate(grid):
            for x, value in enumerate(values):
                if value == 0:
                    continue
                if value not in {1, 0x0F}:
                    raise RuntimeError(
                        f"{spec['name']}: canonical parent uses unexpected index {value:X}"
                    )
                mask[(col0 * 8 + x, row * 8 + y)] = (
                    "fill" if value == 0x0F else "outline"
                )
        result[str(spec["name"])] = mask
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc"
    )
    ap.add_argument(
        "--base-sav", type=Path, default=ROOT / "sram/monoeye_ko_expanded.sav"
    )
    ap.add_argument(
        "--state-dir",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/WonderSwan/State/monoeye ko expanded.Cygne",
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
        default=ROOT / "out/patch/intermission_full_rebuild_clean16_candidate",
    )
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--zstd-level", type=int, default=3)
    args = ap.parse_args()

    states = [args.state_dir / f"Mednafen.QuickSave{i}.State" for i in (1, 2, 3)]
    for path in (
        args.base_rom,
        args.base_sav,
        args.focus_report,
        args.overlay_resolved,
        args.zstd_dll,
        *states,
    ):
        if not path.is_file():
            raise SystemExit(f"missing: {path}")

    base_rom = args.base_rom.read_bytes()
    if sha256_bytes(base_rom) != EXPECTED_MAIN_SHA:
        raise RuntimeError("base TIP hash drifted")
    base = stock_base(base_rom)
    body = base_rom[base : base + 0x800000]
    header = body[common.HEADER : common.HEADER + 18]
    if header[:4] != bytes.fromhex("1A 11 00 02"):
        raise RuntimeError(f"current raw overlay header drifted: {header.hex()}")
    current_aux_start = 0x540000 + int.from_bytes(header[14:16], "little")
    if header[16:18] != bytes.fromhex("00 30"):
        raise RuntimeError("current auxiliary pointer segment drifted")
    current_aux = body[current_aux_start : current_aux_start + common.AUX_BYTES]
    if len(current_aux) != common.AUX_BYTES:
        raise RuntimeError("current auxiliary block is truncated")

    zstd = Zstd(args.zstd_dll)
    cores: list[bytes] = []
    core_names: list[str] = []
    rams: list[bytes] = []
    source_rows = []
    for path in states:
        core, core_name = read_state_core(path, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        cores.append(core)
        core_names.append(core_name)
        rams.append(ram)
        source_rows.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "tilemap_sha256": sha256_bytes(
                    ram[common.TILEMAP_BASE : common.TILEMAP_BASE + 0x800]
                ),
            }
        )
    if len({row["tilemap_sha256"] for row in source_rows}) != 1:
        raise RuntimeError("QuickSave1-3 tilemaps differ")
    for pos in common.visible_positions():
        if len({common.cell_raw(ram, *pos) for ram in rams}) != 1:
            raise RuntimeError(f"QuickSave1-3 visible background differs at {pos}")

    # The source overlay cells have no H/V flips. Keep the assertion explicit so
    # a future state cannot silently invalidate screen-space composition.
    flipped_asset_cells = [
        pos
        for pos in common.asset_positions()
        if common.entry(rams[0], *pos) & 0xC000
    ]
    if flipped_asset_cells:
        raise RuntimeError(f"asset contains flipped source cells: {flipped_asset_cells[:8]}")

    original = common.screen_from_ram(rams[0])
    clean_base = common.copy_screen(original)
    rebuilt = common.copy_screen(original)
    focus_report = json.loads(args.focus_report.read_text(encoding="utf-8"))
    targets = focus_report.get("targets") or []
    if len(targets) != 12:
        raise RuntimeError("focus report must contain twelve targets")
    reserved = set(range(FOCUS_RESERVED_START, FOCUS_RESERVED_END))
    observed_focus_tiles: set[int] = set()
    focus_tile_sources = []
    for target in targets:
        for field in ("source_state", "test_state"):
            path = Path(target[field])
            if not path.is_file():
                raise RuntimeError(f"{target['name']}: missing {field}: {path}")
            core, _ = read_state_core(path, zstd)
            tiles = focus_tiles_from_core(core)
            if not tiles:
                raise RuntimeError(f"{target['name']}: {field} has no focus sprites")
            outside_reserved = tiles - reserved
            if outside_reserved:
                formatted = ", ".join(f"{tile:03X}" for tile in sorted(outside_reserved))
                raise RuntimeError(
                    f"{target['name']}: {field} references focus tiles outside "
                    f"{FOCUS_RESERVED_START:03X}-{FOCUS_RESERVED_END - 1:03X}: {formatted}"
                )
            observed_focus_tiles |= tiles
            focus_tile_sources.append(
                {
                    "label": target["name"],
                    "kind": field,
                    "path": str(path),
                    "tiles": [f"{tile:03X}" for tile in sorted(tiles)],
                }
            )
    if observed_focus_tiles != reserved:
        missing = reserved - observed_focus_tiles
        raise RuntimeError(
            "focus state evidence does not cover the complete reserved range; "
            f"missing: {[f'{tile:03X}' for tile in sorted(missing)]}"
        )
    masks = common.parent_masks(
        original, body, load_resolved(args.overlay_resolved)
    ) + [common.focus_masks(target, zstd) for target in targets]
    if len(masks) != 16:
        raise RuntimeError("sixteen-mask contract failed")
    canonical_parents = canonical_parent_masks()
    parent_names = [mask.name for mask in masks[: len(common.PARENT_LABELS)]]
    if parent_names != list(canonical_parents):
        raise RuntimeError("parent mask ordering contract drifted")
    parent_mask_mismatches = {
        mask.name: len(set(mask.ko.items()) ^ set(canonical_parents[mask.name].items()))
        for mask in masks[: len(common.PARENT_LABELS)]
        if mask.ko != canonical_parents[mask.name]
    }
    if parent_mask_mismatches:
        raise RuntimeError(
            f"parent masks differ from canonical 13px focus typography: "
            f"{parent_mask_mismatches}"
        )

    rectangles: list[tuple[int, int, int, int]] = []
    label_rows = []
    cleared_points: set[tuple[int, int]] = set()
    korean_points: set[tuple[int, int]] = set()
    for mask in masks:
        jp_box = common.bbox(mask.jp)
        ko_box = common.bbox(mask.ko)
        rect = PARENT_ERASE_WINDOWS.get(
            mask.name,
            (
                min(jp_box[0], ko_box[0]),
                min(jp_box[1], ko_box[1]),
                max(jp_box[2], ko_box[2]),
                max(jp_box[3], ko_box[3]),
            ),
        )
        if any(
            not (rect[0] <= x < rect[2] and rect[1] <= y < rect[3])
            for x, y in set(mask.jp) | set(mask.ko)
        ):
            raise RuntimeError(f"{mask.name}: approved erase window misses mask pixels")
        if not (
            common.ASSET_COL0 * 8 <= rect[0] < rect[2] <= (common.ASSET_COL0 + common.ASSET_W) * 8
            and common.ASSET_ROW0 * 8 <= rect[1] < rect[3] <= (common.ASSET_ROW0 + common.ASSET_H) * 8
        ):
            raise RuntimeError(f"{mask.name}: text core escaped overlay bounds")
        if any(intersects(rect, previous) for previous in rectangles):
            raise RuntimeError(f"{mask.name}: protected text cores overlap")
        rectangles.append(rect)

        rect_points = {
            (x, y)
            for y in range(rect[1], rect[3])
            for x in range(rect[0], rect[2])
        }
        jp_points = set(mask.jp)
        ko_points = set(mask.ko)
        nonzero_before = {point for point in rect_points if original[point[1]][point[0]] != 0}
        non_jp_nonzero = nonzero_before - jp_points
        for x, y in rect_points:
            clean_base[y][x] = 0
            rebuilt[y][x] = 0
        if any(clean_base[y][x] != 0 for x, y in rect_points):
            raise RuntimeError(f"{mask.name}: clean text core is not transparent")

        palette = common.ink_palette(mask.name)
        for (x, y), cls in mask.ko.items():
            rebuilt[y][x] = palette[cls]
        final_nonzero = {
            point for point in rect_points if rebuilt[point[1]][point[0]] != 0
        }
        if final_nonzero != ko_points:
            raise RuntimeError(f"{mask.name}: rebuilt foreground differs from Korean mask")
        if any(rebuilt[y][x] != palette[cls] for (x, y), cls in mask.ko.items()):
            raise RuntimeError(f"{mask.name}: rebuilt Korean palette mismatch")
        japanese_only_residual = {
            point
            for point in jp_points - ko_points
            if rebuilt[point[1]][point[0]] != 0
        }
        if japanese_only_residual:
            raise RuntimeError(
                f"{mask.name}: Japanese-only pixels survived rebuild: "
                f"{sorted(japanese_only_residual)[:8]}"
            )

        cleared_points |= rect_points
        korean_points |= ko_points
        label_rows.append(
            {
                "name": mask.name,
                "japanese": mask.japanese,
                "korean": mask.korean,
                "clean_core_bbox_xyxy": list(rect),
                "core_pixels": len(rect_points),
                "source_japanese_mask_pixels": len(jp_points),
                "source_nonzero_pixels_in_core": len(nonzero_before),
                "source_nonzero_outside_extracted_jp_mask_removed": len(
                    non_jp_nonzero
                ),
                "final_korean_pixels": len(ko_points),
                "final_non_korean_pixels_in_erase_window": len(
                    final_nonzero - ko_points
                ),
                "final_japanese_only_residual_pixels": len(
                    japanese_only_residual
                ),
                "erase_window_source": (
                    "approved_full_parent_japanese_footprint"
                    if mask.name in PARENT_ERASE_WINDOWS
                    else "focus_japanese_korean_union"
                ),
                "ink_palette_indices": palette,
                "typography_contract": (
                    "canonical_galmuri11_13px_spacing1"
                    if mask.name in canonical_parents
                    else "exact_approved_focus_state_mask"
                ),
                "focus_origin_xy": list(mask.focus_origin),
                "focus_source_state": mask.source_state,
                "focus_clean_state": mask.clean_state,
            }
        )

    ms_points = {
        (x, y)
        for y in range(MS_PRESERVE_BOX[1], MS_PRESERVE_BOX[3])
        for x in range(MS_PRESERVE_BOX[0], MS_PRESERVE_BOX[2])
    }
    ms_clean_mismatch = {
        point
        for point in ms_points
        if clean_base[point[1]][point[0]] != original[point[1]][point[0]]
    }
    ms_rebuilt_mismatch = {
        point
        for point in ms_points
        if rebuilt[point[1]][point[0]] != original[point[1]][point[0]]
    }
    if ms_clean_mismatch or ms_rebuilt_mismatch:
        raise RuntimeError(
            f"MS label preservation failed: clean={len(ms_clean_mismatch)} "
            f"rebuilt={len(ms_rebuilt_mismatch)}"
        )

    outside_changes = {
        (x, y)
        for y in range(common.SCREEN_H)
        for x in range(common.SCREEN_W)
        if original[y][x] != rebuilt[y][x] and (x, y) not in cleared_points
    }
    if outside_changes:
        raise RuntimeError(f"rebuild changed pixels outside text cores: {sorted(outside_changes)[:8]}")

    # One unique background map slot per overlay cell. Focus sprites live in
    # WSRAM/VRAM bank 0, so 0x110..0x133 are excluded from bank-0 BG cells. The
    # same numeric IDs are safe for bank-1 BG cells (different physical tile RAM).
    positions = common.asset_positions()
    if len(positions) != common.ASSET_CELLS:
        raise RuntimeError("asset position count drifted")
    attrs = [common.entry(rams[0], *pos) & ~0x1FF for pos in positions]
    bank0_indexes = [index for index, attr in enumerate(attrs) if not (attr & 0x2000)]
    bank1_indexes = [index for index, attr in enumerate(attrs) if attr & 0x2000]
    compact_slots = list(range(common.ASSET_CELLS))
    bank0_slots = [slot for slot in compact_slots if slot not in reserved][
        : len(bank0_indexes)
    ]
    if len(bank0_slots) != len(bank0_indexes):
        raise RuntimeError("not enough focus-safe bank-0 slots")
    used_bank0 = set(bank0_slots)
    bank1_slots = [slot for slot in compact_slots if slot not in used_bank0]
    if len(bank1_slots) != len(bank1_indexes):
        raise RuntimeError("bank-aware compact slot allocation failed")
    assigned_slots = [-1] * len(positions)
    for index, slot in zip(bank0_indexes, bank0_slots):
        assigned_slots[index] = slot
    for index, slot in zip(bank1_indexes, bank1_slots):
        assigned_slots[index] = slot
    if set(assigned_slots) != set(compact_slots):
        raise RuntimeError("cell-private assignment is not the contiguous slot range")
    bank0_focus_collisions = {
        slot
        for attr, slot in zip(attrs, assigned_slots)
        if not (attr & 0x2000) and slot in reserved
    }
    if bank0_focus_collisions:
        raise RuntimeError("bank-0 background map references focus sprite slots")
    bank1_focus_numbers = {
        slot
        for attr, slot in zip(attrs, assigned_slots)
        if attr & 0x2000 and slot in reserved
    }
    if bank1_focus_numbers != reserved:
        raise RuntimeError("focus-reserved numeric slots are not all assigned to bank-1 BG cells")
    slot_count = max(assigned_slots) + 1
    if slot_count >= 0x200:
        raise RuntimeError("raw overlay slot count exceeds 9-bit tile IDs")

    rebuilt_grids = common.grids_from_screen(rebuilt)
    slot_raws = [bytes(common.TILE_BYTES) for _ in range(slot_count)]
    for pos, attr, slot in zip(positions, attrs, assigned_slots):
        raw_grid = inverse_orient(rebuilt_grids[pos], attr)
        slot_raws[slot] = common.encode_tile(raw_grid)

    map_bytes = common.ASSET_CELLS * 2
    gfx_start = common.align(common.MAP + map_bytes, 0x10)
    gfx = b"".join(slot_raws)
    aux_start = common.align(gfx_start + len(gfx), 0x10)
    asset_end = aux_start + len(current_aux)
    if asset_end > common.BANK_END:
        raise RuntimeError(f"rebuilt overlay exceeds bank 54 at {asset_end:06X}")
    current_asset_end = current_aux_start + len(current_aux)
    extension = body[current_asset_end:asset_end]
    if any(value != 0xFF for value in extension):
        first = next(i for i, value in enumerate(extension) if value != 0xFF)
        raise RuntimeError(f"rebuilt overlay extension overwrites data at {current_asset_end + first:06X}")

    candidate = bytearray(base_rom)
    off = base
    candidate[off + common.HEADER : off + asset_end] = b"\xFF" * (asset_end - common.HEADER)
    new_header = bytearray(18)
    new_header[0] = common.ASSET_W
    new_header[1] = common.ASSET_H
    new_header[2:4] = bytes.fromhex("00 02")
    new_header[4:6] = slot_count.to_bytes(2, "little")
    new_header[6:10] = common.far_ptr(common.MAP & 0xFFFF)
    new_header[10:14] = common.far_ptr(gfx_start & 0xFFFF)
    new_header[14:18] = common.far_ptr(aux_start & 0xFFFF)
    candidate[off + common.HEADER : off + common.HEADER + 18] = new_header
    for index, (attr, slot) in enumerate(zip(attrs, assigned_slots)):
        value = attr | slot
        start = off + common.MAP + index * 2
        candidate[start : start + 2] = value.to_bytes(2, "little")
    candidate[off + gfx_start : off + gfx_start + len(gfx)] = gfx
    candidate[off + aux_start : off + asset_end] = current_aux
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_body = candidate_bytes[base : base + 0x800000]

    decoded = decode_rebuilt_asset(candidate_body, gfx_start, slot_count)
    decoded_mismatch = {
        (x, y)
        for col, row in positions
        for y in range(row * 8, row * 8 + 8)
        for x in range(col * 8, col * 8 + 8)
        if decoded[y][x] != rebuilt[y][x]
    }
    if decoded_mismatch:
        raise RuntimeError(f"serialized overlay differs from rebuilt screen: {sorted(decoded_mismatch)[:8]}")

    map_values = [
        int.from_bytes(candidate_body[common.MAP + i * 2 : common.MAP + i * 2 + 2], "little")
        for i in range(common.ASSET_CELLS)
    ]
    map_slots = [value & 0x1FF for value in map_values]
    if set(map_slots) != set(range(common.ASSET_CELLS)):
        raise RuntimeError("serialized map does not use contiguous slots 0..441 exactly once")
    serialized_attr_mismatches = [
        index
        for index, (expected, value) in enumerate(zip(attrs, map_values))
        if value & ~0x1FF != expected
    ]
    if serialized_attr_mismatches:
        raise RuntimeError(
            f"serialized map attribute drift at cell {serialized_attr_mismatches[0]}"
        )
    serialized_bank0_focus_collisions = {
        value & 0x1FF
        for value in map_values
        if not (value & 0x2000) and (value & 0x1FF) in reserved
    }
    if serialized_bank0_focus_collisions:
        raise RuntimeError("serialized bank-0 map collides with focus sprite slots")
    serialized_bank1_focus_numbers = {
        value & 0x1FF
        for value in map_values
        if value & 0x2000 and (value & 0x1FF) in reserved
    }
    if serialized_bank1_focus_numbers != reserved:
        raise RuntimeError(
            "serialized map does not place every focus-reserved number in bank 1"
        )

    changed_offsets = [
        index
        for index, pair in enumerate(zip(base_rom, candidate_bytes))
        if pair[0] != pair[1]
    ]
    allowed = set(range(off + common.HEADER, off + asset_end))
    allowed.update((len(candidate_bytes) - 2, len(candidate_bytes) - 1))
    outside_rom = [index for index in changed_offsets if index not in allowed]
    if outside_rom:
        raise RuntimeError(f"candidate changed outside overlay/checksum at {outside_rom[0]:08X}")
    checksums_valid = (sum(candidate_bytes[:-2]) & 0xFFFF) == int.from_bytes(candidate_bytes[-2:], "little")
    if not checksums_valid:
        raise RuntimeError("WonderSwan checksum mismatch")

    preserved_ranges = {
        "normal_focus_atlas": (FOCUS_ATLAS_LO, FOCUS_ATLAS_HI),
        "confirmation_focus_atlas": (CONFIRM_ATLAS_LO, CONFIRM_ATLAS_HI),
        "runtime_hook": (RUNTIME_HOOK_LO, RUNTIME_HOOK_HI),
    }
    preserved = {}
    for name, (start, end) in preserved_ranges.items():
        changed = sum(
            left != right
            for left, right in zip(
                base_rom[off + start : off + end],
                candidate_bytes[off + start : off + end],
            )
        )
        preserved[name] = {"range": [f"{start:06X}", f"{end:06X}"], "changed_bytes": changed}
        if changed:
            raise RuntimeError(f"{name} changed by full rebuild")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_out = args.out_dir / "intermission_full_rebuild_clean16_candidate.wsc"
    sav_out = ROOT / "sram/intermission_full_rebuild_clean16_candidate.sav"
    rom_out.write_bytes(candidate_bytes)
    shutil.copy2(args.base_sav, sav_out)

    preview_dir = args.out_dir / "previews"
    common.render_index_screen(original, preview_dir / "01_basic_japanese.png", args.scale)
    common.render_index_screen(clean_base, preview_dir / "02_clean_base_no_text.png", args.scale)
    common.render_index_screen(rebuilt, preview_dir / "03_rebuilt_korean16.png", args.scale)
    for row, mask in zip(label_rows, masks):
        crop_dir = args.out_dir / "labels" / mask.name
        common.render_crop(original, row["clean_core_bbox_xyxy"], crop_dir / "before.png")
        common.render_crop(clean_base, row["clean_core_bbox_xyxy"], crop_dir / "clean_base.png")
        common.render_crop(rebuilt, row["clean_core_bbox_xyxy"], crop_dir / "rebuilt.png")

    # Save-state visual fixtures are validation evidence only; the ROM remains the
    # deliverable and the live main SaveRAM is copied without modification.
    state_rows = []
    desired_raw: dict[tuple[int, int], bytes] = {}
    changed_visible: set[tuple[int, int]] = set()
    rebuilt_grids_all = common.grids_from_screen(rebuilt)
    for pos in common.visible_positions():
        if pos in set(positions):
            attr = common.entry(rams[0], *pos)
            raw = common.encode_tile(inverse_orient(rebuilt_grids_all[pos], attr))
        else:
            raw = common.cell_raw(rams[0], *pos)
        desired_raw[pos] = raw
        if raw != common.cell_raw(rams[0], *pos):
            changed_visible.add(pos)
    state_dir = args.out_dir / "states"
    for index, (source, core, core_name) in enumerate(zip(states, cores, core_names), 1):
        patched_core, state_report = common.state_exact.patch_core(
            core, desired_raw, changed_visible
        )
        state_out = state_dir / f"Mednafen.QuickSave{index}.State"
        compressed = write_state_with_core(
            source, state_out, core_name, patched_core, zstd, args.zstd_level
        )
        verify, verify_name = read_state_core(state_out, zstd)
        if verify_name != core_name or verify != patched_core:
            raise RuntimeError(f"QuickSave{index}: patched state round-trip failed")
        state_rows.append(
            {
                "index": index,
                "source": str(source),
                "output": str(state_out),
                "sha256": sha256_file(state_out),
                "compressed_core_bytes": compressed,
                **state_report,
            }
        )

    report = {
        "purpose": "from-scratch sixteen-label rebuild on the 0x54B780 transition overlay with cell-private background slots",
        "scope": {
            "rebuilt_resource": "full-screen transition and natural-entry overlay",
            "rom_range": [f"{common.HEADER:06X}", f"{asset_end:06X}"],
            "steady_state_static_bg_atlas_rewritten": False,
            "steady_state_static_bg_note": (
                "The separate 0x544xxx steady-state parent/leaf BG atlases are "
                "preserved; this candidate validates the transition overlay only."
            ),
        },
        "base_tip": str(args.base_rom),
        "base_tip_sha256": sha256_bytes(base_rom),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": sha256_file(rom_out),
        "candidate_sav": str(sav_out),
        "candidate_sav_sha256": sha256_file(sav_out),
        "checksum": f"{checksum:04X}",
        "source_states": source_rows,
        "typography": {
            "font": str(FONT),
            "font_sha256": sha256_file(FONT),
            "font_size": CANONICAL_FONT_SIZE,
            "letter_spacing": CANONICAL_LETTER_SPACING,
            "parent_masks_match_canonical_focus_typography": not parent_mask_mismatches,
            "leaf_masks_source": "approved localized focus sprite states",
        },
        "focus_tile_evidence": {
            "expected_bank": 0,
            "expected_range": [
                f"{FOCUS_RESERVED_START:03X}",
                f"{FOCUS_RESERVED_END - 1:03X}",
            ],
            "observed_tiles": [
                f"{tile:03X}" for tile in sorted(observed_focus_tiles)
            ],
            "sources": focus_tile_sources,
        },
        "asset": {
            "header": f"{common.HEADER:06X}",
            "dimensions": [common.ASSET_W, common.ASSET_H],
            "map": [f"{common.MAP:06X}", f"{common.MAP + map_bytes:06X}"],
            "graphics": [f"{gfx_start:06X}", f"{gfx_start + len(gfx):06X}"],
            "auxiliary": [f"{aux_start:06X}", f"{asset_end:06X}"],
            "slot_count": slot_count,
            "slot_number_range": ["000", f"{slot_count - 1:03X}"],
            "slot_numbers_contiguous": set(map_slots) == set(range(slot_count)),
            "screen_cells": common.ASSET_CELLS,
            "unique_map_slots": len(set(map_slots)),
            "shared_map_slots": common.ASSET_CELLS - len(set(map_slots)),
            "focus_bank0_reserved_slots": [f"{FOCUS_RESERVED_START:03X}", f"{FOCUS_RESERVED_END - 1:03X}"],
            "focus_reserved_slot_count": len(reserved),
            "bank0_background_cells": len(bank0_indexes),
            "bank1_background_cells": len(bank1_indexes),
            "bank0_references_to_focus_reserved_slots": len(serialized_bank0_focus_collisions),
            "bank1_references_to_same_numeric_slots": len(
                serialized_bank1_focus_numbers
            ),
            "unused_slot_ids_below_0x200": 0x200 - slot_count,
            "serialized_attribute_mismatches": len(serialized_attr_mismatches),
            "extension_overwrites_only_ff": True,
            "auxiliary_sha256": sha256_bytes(current_aux),
        },
        "labels": label_rows,
        "framebuffer": {
            "clean_core_pixels": len(cleared_points),
            "final_korean_mask_pixels": len(korean_points),
            "changed_pixels": sum(
                original[y][x] != rebuilt[y][x]
                for y in range(common.SCREEN_H)
                for x in range(common.SCREEN_W)
            ),
            "changes_outside_clean_cores": len(outside_changes),
            "serialized_asset_mismatch_pixels": len(decoded_mismatch),
            "ms_preserve_box_xyxy": list(MS_PRESERVE_BOX),
            "ms_clean_mismatch_pixels": len(ms_clean_mismatch),
            "ms_rebuilt_mismatch_pixels": len(ms_rebuilt_mismatch),
        },
        "rom": {
            "changed_bytes_including_checksum": len(changed_offsets),
            "outside_allowlist_bytes": len(outside_rom),
            "preserved_ranges": preserved,
        },
        "patched_states": state_rows,
        "verification": {
            "all_16_labels_rebuilt": len(label_rows) == 16,
            "all_text_cores_cleared_to_transparent_before_redraw": True,
            "all_final_foregrounds_exactly_match_focus_masks": True,
            "all_parent_masks_match_canonical_focus_typography": not parent_mask_mismatches,
            "all_japanese_only_pixels_removed": all(
                row["final_japanese_only_residual_pixels"] == 0
                for row in label_rows
            ),
            "all_non_korean_pixels_zero_in_erase_windows": all(
                row["final_non_korean_pixels_in_erase_window"] == 0
                for row in label_rows
            ),
            "connected_ms_label_unchanged": not ms_clean_mismatch
            and not ms_rebuilt_mismatch,
            "all_442_background_cells_have_private_slots": len(set(map_slots)) == common.ASSET_CELLS,
            "map_uses_contiguous_slots_0_through_441_once": set(map_slots)
            == set(range(common.ASSET_CELLS)),
            "zero_bank0_background_references_to_focus_reserved_slots": not serialized_bank0_focus_collisions,
            "all_focus_reserved_numbers_assigned_only_to_bank1": serialized_bank1_focus_numbers
            == reserved,
            "focus_state_evidence_covers_reserved_range": observed_focus_tiles
            == reserved,
            "serialized_map_attributes_preserved": not serialized_attr_mismatches,
            "normal_focus_atlas_unchanged": preserved["normal_focus_atlas"]["changed_bytes"] == 0,
            "confirmation_focus_atlas_unchanged": preserved["confirmation_focus_atlas"]["changed_bytes"] == 0,
            "runtime_hook_unchanged": preserved["runtime_hook"]["changed_bytes"] == 0,
            "serialized_asset_exact": not decoded_mismatch,
            "changes_bounded_to_overlay_and_checksum": not outside_rom,
            "checksum_valid": checksums_valid,
            "patched_states_round_trip": len(state_rows) == 3,
            "matching_sav_is_current_copy": sav_out.read_bytes() == args.base_sav.read_bytes(),
            "main_tip_unchanged_by_builder": args.base_rom.read_bytes() == base_rom,
        },
        "previews": {
            "basic_japanese": str(preview_dir / "01_basic_japanese.png"),
            "clean_base_no_text": str(preview_dir / "02_clean_base_no_text.png"),
            "rebuilt_korean16": str(preview_dir / "03_rebuilt_korean16.png"),
        },
        "main_tip_promoted": False,
        "user_validation_status": "pending",
    }
    report_path = args.out_dir / "full_rebuild_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    readme = args.out_dir / "README.md"
    readme.write_text(
        "# 인터미션 16개 요소 완전 재구축 테스트 후보\n\n"
        "- ROM: `intermission_full_rebuild_clean16_candidate.wsc`\n"
        "- 같은 이름 SaveRAM: `intermission_full_rebuild_clean16_candidate.sav`\n"
        f"- ROM SHA-256: `{sha256_file(rom_out).upper()}`\n"
        f"- WonderSwan checksum: `{checksum:04X}`\n\n"
        "ROM 0x54B780 전환·자연 진입 오버레이의 16개 보호 글자 코어를 전부 투명 0으로 "
        "비운 뒤, "
        "승인된 focus 한글 마스크를 처음부터 다시 배치했다. 전체화면 442개 셀은 각각 "
        "독립 타일 슬롯을 사용한다. focus 스프라이트가 사용하는 bank-0 0x110..0x133은 "
        "bank-0 배경 맵에서 제외하고, 같은 숫자는 물리 메모리가 분리된 bank-1에만 "
        "배정한다. 일반 focus/확인 강조 아틀라스는 메인 TIP과 "
        "바이트 동일하게 보존한다.\n\n"
        "주의: 평상시 정지 화면이 사용하는 별도 0x544xxx 상위 4개/하위 12개 BG 아틀라스는 "
        "이번 후보가 다시 쓰지 않는다. 따라서 이 후보의 검증 범위는 전환 오버레이이며, "
        "평상시 정지 비-focus 화면의 재구축은 별도 작업이다. 이 ROM은 실기 검증용이며 "
        "메인 TIP에는 반영하지 않았다.\n",
        encoding="utf-8",
    )

    print(f"labels                 : {len(label_rows)}/16")
    print(f"clean core pixels      : {len(cleared_points)}")
    print(f"private BG map slots   : {len(set(map_slots))}/{common.ASSET_CELLS}")
    print(f"focus bank0 reserved   : {FOCUS_RESERVED_START:03X}-{FOCUS_RESERVED_END - 1:03X}")
    print(f"slot count             : {slot_count}")
    print(f"asset end              : {asset_end:06X}")
    print(f"ROM changed bytes      : {len(changed_offsets)}")
    print(f"checksum               : {checksum:04X}")
    print(f"candidate              : {rom_out}")
    print(f"report                 : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
