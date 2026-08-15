#!/usr/bin/env python3
"""Independent audit for the from-scratch 16-label intermission rebuild."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_intermission_static_full_cleanup_candidate as common  # noqa: E402
from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base  # noqa: E402
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

PATCH = ROOT / "out/patch"
OUT = PATCH / "intermission_full_rebuild_clean16_candidate"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAV = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = OUT / "intermission_full_rebuild_clean16_candidate.wsc"
CANDIDATE_SAV = ROOT / "sram/intermission_full_rebuild_clean16_candidate.sav"
BUILD_REPORT = OUT / "full_rebuild_report.json"
AUDIT_REPORT = OUT / "independent_audit.json"

EXPECTED_TIP_SHA = "3b0a07f82d97a90055957dc310b6a9dc713c4d4c6aa4c75586b286e255412da9"
EXPECTED_CANDIDATE_SHA = "2dcfb253b0488182ce061df7b4396918564e6049c31ecdce0d1a9f2a4dd834d7"
FOCUS_RESERVED = set(range(0x110, 0x134))
SLOT_COUNT = 442
GFX_START = 0x54BB10
AUX_START = 0x54F250
ASSET_END = 0x54F293
CANONICAL_FONT_SIZE = 13
CANONICAL_LETTER_SPACING = 1
PARENT_ERASE_WINDOWS = {
    "parent_operation": (8, 22, 48, 40),
    "parent_organization": (56, 50, 96, 70),
    "parent_development": (8, 87, 48, 104),
    "parent_system": (8, 124, 72, 142),
}
MS_PRESERVE_BOX = (12, 69, 47, 85)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ident(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def inverse_orient(grid: list[list[int]], entry: int) -> list[list[int]]:
    out = [[0] * 8 for _ in range(8)]
    for y in range(8):
        sy = 7 - y if entry & 0x8000 else y
        for x in range(8):
            sx = 7 - x if entry & 0x4000 else x
            out[sy][sx] = grid[y][x]
    return out


def decode_asset(body: bytes) -> tuple[list[list[int]], list[int]]:
    screen = [[0] * common.SCREEN_W for _ in range(common.SCREEN_H)]
    values = []
    for index, (col, row) in enumerate(common.asset_positions()):
        off = common.MAP + index * 2
        value = int.from_bytes(body[off : off + 2], "little")
        values.append(value)
        slot = value & 0x1FF
        raw = body[GFX_START + slot * common.TILE_BYTES : GFX_START + (slot + 1) * common.TILE_BYTES]
        grid = common.decode_tile(raw)
        for y in range(8):
            sy = 7 - y if value & 0x8000 else y
            for x in range(8):
                sx = 7 - x if value & 0x4000 else x
                screen[row * 8 + y][col * 8 + x] = grid[sy][sx]
    return screen, values


def focus_tiles_from_core(core: bytes) -> set[int]:
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
    """Independently rasterize the four non-focus parent labels."""
    rasteriser = Rasteriser(FONT, CANONICAL_FONT_SIZE)
    result: dict[str, dict[tuple[int, int], str]] = {}
    for spec in common.PARENT_LABELS:
        col0 = int(spec["col0"])
        col1 = int(spec["col1"])
        row = int(spec["row"])
        grid = draw_strip(
            str(spec["korean"]),
            (col1 - col0 + 1) * 8,
            rasteriser,
            0x0F,
            1,
            CANONICAL_LETTER_SPACING,
        )
        result[str(spec["name"])] = {
            (col0 * 8 + x, row * 8 + y): (
                "fill" if value == 0x0F else "outline"
            )
            for y, values in enumerate(grid)
            for x, value in enumerate(values)
            if value != 0
        }
        unexpected = {
            value for values in grid for value in values if value not in {0, 1, 0x0F}
        }
        if unexpected:
            raise RuntimeError(
                f"{spec['name']}: unexpected canonical palette {sorted(unexpected)}"
            )
    return result


def main() -> int:
    for path in (TIP, TIP_SAV, CANDIDATE, CANDIDATE_SAV, BUILD_REPORT):
        if not path.is_file():
            raise RuntimeError(f"missing: {path}")
    if sha(TIP) != EXPECTED_TIP_SHA or sha(CANDIDATE) != EXPECTED_CANDIDATE_SHA:
        raise RuntimeError("ROM hash binding drifted")

    tip = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    base = stock_base(candidate)
    tip_body = tip[base : base + 0x800000]
    body = candidate[base : base + 0x800000]
    expected_header = bytes.fromhex(
        "1A 11 00 02 BA 01 92 B7 00 30 10 BB 00 30 50 F2 00 30"
    )
    header_ok = body[common.HEADER : common.HEADER + 18] == expected_header

    changed = [i for i, pair in enumerate(zip(tip, candidate)) if pair[0] != pair[1]]
    outside = [
        i
        for i in changed
        if not (base + common.HEADER <= i < base + ASSET_END or i >= len(candidate) - 2)
    ]
    checksum = int.from_bytes(candidate[-2:], "little")

    decoded, map_values = decode_asset(body)
    slots = [value & 0x1FF for value in map_values]
    bank0_focus = {
        value & 0x1FF
        for value in map_values
        if not (value & 0x2000) and (value & 0x1FF) in FOCUS_RESERVED
    }
    bank1_focus_numbers = {
        value & 0x1FF
        for value in map_values
        if value & 0x2000 and (value & 0x1FF) in FOCUS_RESERVED
    }

    state = ROOT / "BizHawk-2.11.1-win-x64/WonderSwan/State/monoeye ko expanded.Cygne/Mednafen.QuickSave1.State"
    zstd_dll = ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll"
    focus_report = PATCH / "intermission_all_focus_clean/all_focus_clean_report.json"
    overlay_resolved = ROOT / "out/title_menu_capture/intermission_overlay_resolved.json"
    zstd = Zstd(zstd_dll)
    core, _ = read_state_core(state, zstd)
    ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    original = common.screen_from_ram(ram)
    focus_targets = json.loads(focus_report.read_text(encoding="utf-8"))["targets"]
    observed_focus_tiles = set()
    for target in focus_targets:
        for field in ("source_state", "test_state"):
            focus_core, _ = read_state_core(Path(target[field]), zstd)
            observed_focus_tiles |= focus_tiles_from_core(focus_core)
    masks = common.parent_masks(
        original, tip_body, load_resolved(overlay_resolved)
    ) + [
        common.focus_masks(row, zstd)
        for row in focus_targets
    ]
    canonical_parents = canonical_parent_masks()
    parent_names = [mask.name for mask in masks[: len(common.PARENT_LABELS)]]
    if parent_names != list(canonical_parents):
        raise RuntimeError(
            f"parent mask ordering contract drifted: {parent_names}"
        )
    parent_mask_mismatches = {
        mask.name: len(set(mask.ko.items()) ^ set(canonical_parents[mask.name].items()))
        for mask in masks[: len(common.PARENT_LABELS)]
        if mask.ko != canonical_parents[mask.name]
    }

    expected = common.copy_screen(original)
    all_cores: set[tuple[int, int]] = set()
    label_checks = []
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
        points = {
            (x, y)
            for y in range(rect[1], rect[3])
            for x in range(rect[0], rect[2])
        }
        for x, y in points:
            expected[y][x] = 0
        palette = common.ink_palette(mask.name)
        for (x, y), cls in mask.ko.items():
            expected[y][x] = palette[cls]
        actual_nonzero = {point for point in points if decoded[point[1]][point[0]] != 0}
        expected_nonzero = set(mask.ko)
        unexpected_non_korean = actual_nonzero - expected_nonzero
        japanese_only_residual = {
            point
            for point in set(mask.jp) - expected_nonzero
            if decoded[point[1]][point[0]] != 0
        }
        exact = actual_nonzero == expected_nonzero and all(
            decoded[y][x] == palette[cls] for (x, y), cls in mask.ko.items()
        )
        label_checks.append(
            {
                "name": mask.name,
                "core_bbox_xyxy": list(rect),
                "expected_korean_pixels": len(expected_nonzero),
                "actual_nonzero_pixels": len(actual_nonzero),
                "unexpected_non_korean_pixels": len(unexpected_non_korean),
                "japanese_only_residual_pixels": len(japanese_only_residual),
                "erase_window_source": (
                    "approved_full_parent_japanese_footprint"
                    if mask.name in PARENT_ERASE_WINDOWS
                    else "focus_japanese_korean_union"
                ),
                "typography_contract": (
                    "canonical_galmuri11_13px_spacing1"
                    if mask.name in canonical_parents
                    else "exact_approved_focus_state_mask"
                ),
                "exact": exact,
            }
        )
        all_cores |= points

    asset_positions = set(common.asset_positions())
    source_attrs = [
        common.entry(ram, col, row) & ~0x1FF
        for col, row in common.asset_positions()
    ]
    serialized_attr_mismatches = [
        index
        for index, (expected, value) in enumerate(zip(source_attrs, map_values))
        if value & ~0x1FF != expected
    ]
    screen_mismatch = {
        (x, y)
        for col, row in asset_positions
        for y in range(row * 8, row * 8 + 8)
        for x in range(col * 8, col * 8 + 8)
        if decoded[y][x] != expected[y][x]
    }
    outside_core_mismatch = {
        point for point in screen_mismatch if point not in all_cores
    }
    ms_points = {
        (x, y)
        for y in range(MS_PRESERVE_BOX[1], MS_PRESERVE_BOX[3])
        for x in range(MS_PRESERVE_BOX[0], MS_PRESERVE_BOX[2])
    }
    ms_mismatch = {
        point
        for point in ms_points
        if decoded[point[1]][point[0]] != original[point[1]][point[0]]
    }

    current_aux = tip_body[0x54E790:0x54E7D3]
    rebuilt_aux = body[AUX_START:ASSET_END]
    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    report_checks = report.get("verification") or {}
    required_report_checks = (
        "all_16_labels_rebuilt",
        "all_text_cores_cleared_to_transparent_before_redraw",
        "all_final_foregrounds_exactly_match_focus_masks",
        "all_parent_masks_match_canonical_focus_typography",
        "all_japanese_only_pixels_removed",
        "all_non_korean_pixels_zero_in_erase_windows",
        "connected_ms_label_unchanged",
        "all_442_background_cells_have_private_slots",
        "map_uses_contiguous_slots_0_through_441_once",
        "zero_bank0_background_references_to_focus_reserved_slots",
        "all_focus_reserved_numbers_assigned_only_to_bank1",
        "focus_state_evidence_covers_reserved_range",
        "serialized_map_attributes_preserved",
        "normal_focus_atlas_unchanged",
        "confirmation_focus_atlas_unchanged",
        "runtime_hook_unchanged",
        "serialized_asset_exact",
        "changes_bounded_to_overlay_and_checksum",
        "checksum_valid",
        "patched_states_round_trip",
        "matching_sav_is_current_copy",
        "main_tip_unchanged_by_builder",
    )

    checks = {
        "tip_hash_bound": sha(TIP) == EXPECTED_TIP_SHA,
        "candidate_hash_bound": sha(CANDIDATE) == EXPECTED_CANDIDATE_SHA,
        "header_exact": header_ok,
        "checksum_valid": (sum(candidate[:-2]) & 0xFFFF) == checksum,
        "changed_bytes_exact": len(changed) == 11014,
        "changes_bounded_to_overlay_and_checksum": not outside,
        "map_uses_all_442_slots_once": len(slots) == 442 and set(slots) == set(range(442)),
        "bank0_focus_slot_collisions_zero": not bank0_focus,
        "bank1_uses_all_36_same_numeric_slots_safely": bank1_focus_numbers == FOCUS_RESERVED,
        "focus_state_evidence_covers_110_through_133": observed_focus_tiles
        == FOCUS_RESERVED,
        "serialized_map_attributes_match_source_state": not serialized_attr_mismatches,
        "all_16_korean_masks_exact": len(label_checks) == 16 and all(row["exact"] for row in label_checks),
        "all_parent_masks_match_canonical_13px_typography": not parent_mask_mismatches,
        "all_non_korean_pixels_zero_in_16_cores": all(
            row["unexpected_non_korean_pixels"] == 0 for row in label_checks
        ),
        "all_japanese_only_pixels_removed": all(
            row["japanese_only_residual_pixels"] == 0 for row in label_checks
        ),
        "connected_ms_label_preserved": not ms_mismatch,
        "serialized_screen_exact": not screen_mismatch,
        "no_changes_outside_text_cores": not outside_core_mismatch,
        "auxiliary_block_preserved": rebuilt_aux == current_aux,
        "normal_focus_atlas_preserved": tip_body[0x542000:0x544400] == body[0x542000:0x544400],
        "confirmation_focus_atlas_preserved": tip_body[0x547CFC:0x549A1C] == body[0x547CFC:0x549A1C],
        "runtime_hook_preserved": tip_body[0x7A0600:0x7A1000] == body[0x7A0600:0x7A1000],
        "matching_sav_is_current_main_copy": CANDIDATE_SAV.read_bytes() == TIP_SAV.read_bytes(),
        "builder_report_bound_to_candidate": str(report.get("candidate_rom_sha256") or "").lower() == EXPECTED_CANDIDATE_SHA,
        "builder_report_all_checks_pass": all(report_checks.get(name) is True for name in required_report_checks),
    }
    if not all(checks.values()):
        raise RuntimeError(f"independent audit failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/audit_intermission_full_rebuild_clean16_candidate.py",
        "ok": True,
        "published": False,
        "base_tip": ident(TIP),
        "candidate": ident(CANDIDATE),
        "matching_sav": ident(CANDIDATE_SAV),
        "wonder_swan_checksum": f"{checksum:04X}",
        "changed_bytes_including_checksum": len(changed),
        "outside_allowlist_bytes": len(outside),
        "slot_contract": {
            "map_cells": len(map_values),
            "unique_slots": len(set(slots)),
            "shared_slots": len(slots) - len(set(slots)),
            "bank0_focus_reserved_range": ["110", "133"],
            "bank0_collisions": len(bank0_focus),
            "bank1_same_numeric_slots": len(bank1_focus_numbers),
            "focus_state_observed_tiles": [
                f"{tile:03X}" for tile in sorted(observed_focus_tiles)
            ],
            "serialized_attribute_mismatches": len(serialized_attr_mismatches),
        },
        "typography_contract": {
            "font": str(FONT.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "font_sha256": sha(FONT),
            "font_size": CANONICAL_FONT_SIZE,
            "letter_spacing": CANONICAL_LETTER_SPACING,
            "parent_mask_mismatches": parent_mask_mismatches,
            "leaf_source": "approved localized focus sprite states",
        },
        "erase_contract": {
            "parent_windows_xyxy": {
                name: list(box) for name, box in PARENT_ERASE_WINDOWS.items()
            },
            "ms_preserve_box_xyxy": list(MS_PRESERVE_BOX),
            "ms_mismatch_pixels": len(ms_mismatch),
        },
        "label_checks": label_checks,
        "checks": checks,
    }
    AUDIT_REPORT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
