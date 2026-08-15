#!/usr/bin/env python3
"""Verify the safe default-intermission A/B framebuffer against actual WSRAM slots.

The A/B builder records the exact ``Core.bin`` slot used by every changed bank-54
ROM tile.  This verifier therefore does not depend on an ambiguous screen-to-ROM
content match.  It derives the expected framebuffer mask from:

* the serialized 32x32 BG tilemap at WSRAM 0x3800;
* the exact changed WSRAM graphics slots from the A/B report;
* the real A/B tile nibbles in those slots; and
* the current sprite table, used only to explain expected BG pixels hidden by the
  active focus plate.

A valid result has no observed framebuffer change outside the exact BG mask.  Any
expected but unobserved pixel must be covered by an opaque sprite pixel.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from render_bank_tiles import tiles_4bpp  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    NEXT_SPRITE_COUNT_CORE_OFFSET,
    NEXT_SPRITE_TABLE_CORE_OFFSET,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    parse_sprites,
)

SCREEN_W = 224
SCREEN_H = 144
TILEMAP_W = 32
VISIBLE_COLS = SCREEN_W // 8
VISIBLE_ROWS = SCREEN_H // 8
TILEMAP_BASE = 0x3800
TILE_BYTES = 0x20


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_path(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def tile_wsram_offset(entry: int) -> int:
    tile = entry & 0x01FF
    bank = 0x8000 if entry & 0x2000 else 0x4000
    return bank + tile * TILE_BYTES


def sprite_coverage(core: bytes, ram: bytes, table: int, count: int) -> set[tuple[int, int]]:
    coverage: set[tuple[int, int]] = set()
    for sprite in parse_sprites(core, table, count):
        off = sprite["wsram_offset"]
        tile = tiles_4bpp(ram[off : off + TILE_BYTES])[0]
        for dy in range(8):
            sy = 7 - dy if sprite["flip_v"] else dy
            for dx in range(8):
                sx = 7 - dx if sprite["flip_h"] else dx
                if tile[sy][sx] == 0:
                    continue
                x = sprite["x"] + dx
                y = sprite["y"] + dy
                if 0 <= x < SCREEN_W and 0 <= y < SCREEN_H:
                    coverage.add((x, y))
    return coverage


def save_mask(path: Path, points: set[tuple[int, int]]) -> None:
    image = Image.new("RGB", (SCREEN_W, SCREEN_H), (0, 0, 0))
    pixels = image.load()
    for x, y in points:
        pixels[x, y] = (255, 255, 255)
    image.save(path)


def save_diff(path: Path, image_a: Image.Image, image_b: Image.Image) -> None:
    out = Image.new("RGB", (SCREEN_W, SCREEN_H), (0, 0, 0))
    pixels = out.load()
    a = image_a.load()
    b = image_b.load()
    for y in range(SCREEN_H):
        for x in range(SCREEN_W):
            if a[x, y] != b[x, y]:
                pixels[x, y] = b[x, y]
    out.save(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--build-report",
        type=Path,
        default=ROOT
        / "out/patch/intermission_static_focus_matched_safe/static_focus_matched_safe_report.json",
    )
    ap.add_argument(
        "--state-report",
        type=Path,
        default=ROOT
        / "out/patch/intermission_static_focus_matched_safe/state_ab/intermission_state_ab_report.json",
    )
    ap.add_argument(
        "--capture-a",
        type=Path,
        default=ROOT
        / "out/patch/intermission_static_focus_matched_safe/captures/static_focus_A_s00.png",
    )
    ap.add_argument(
        "--capture-b",
        type=Path,
        default=ROOT
        / "out/patch/intermission_static_focus_matched_safe/captures/static_focus_B_s00.png",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    args = ap.parse_args(argv)

    for path in (
        args.build_report,
        args.state_report,
        args.capture_a,
        args.capture_b,
        args.zstd_dll,
    ):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    build = json.loads(args.build_report.read_text(encoding="utf-8"))
    state = json.loads(args.state_report.read_text(encoding="utf-8"))
    state_base = args.state_report.parents[4]
    state_a_path = resolve_path(state["a"]["state"], state_base)
    state_b_path = resolve_path(state["b"]["state"], state_base)
    for path in (state_a_path, state_b_path):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    zstd = Zstd(args.zstd_dll)
    core_a, member_a = read_state_core(state_a_path, zstd)
    core_b, member_b = read_state_core(state_b_path, zstd)
    if member_a != member_b:
        raise RuntimeError(f"A/B core members differ: {member_a} != {member_b}")
    ram_a = core_a[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    ram_b = core_b[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]

    slot_to_rom: dict[int, str] = {}
    for row in state["replacements"]:
        slot = int(row["core_offset"], 16) - WSRAM_CORE_OFFSET
        if slot < 0 or slot + TILE_BYTES > WSRAM_BYTES:
            raise RuntimeError(f"replacement outside WSRAM: {row}")
        if slot in slot_to_rom:
            raise RuntimeError(f"duplicate WSRAM replacement slot: {slot:04X}")
        slot_to_rom[slot] = row["rom_tile"]

    actual_positions_by_rom: dict[str, list[list[int]]] = collections.defaultdict(list)
    slot_positions: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for row in range(VISIBLE_ROWS):
        for col in range(VISIBLE_COLS):
            map_off = TILEMAP_BASE + (row * TILEMAP_W + col) * 2
            entry = int.from_bytes(ram_a[map_off : map_off + 2], "little")
            slot = tile_wsram_offset(entry)
            if slot not in slot_to_rom:
                continue
            slot_positions[slot].append((col, row))
            actual_positions_by_rom[slot_to_rom[slot]].append([col, row])

    unreferenced_slots = sorted(set(slot_to_rom) - set(slot_positions))
    multiply_referenced_slots = {
        f"{slot:04X}": [list(pos) for pos in positions]
        for slot, positions in slot_positions.items()
        if len(positions) != 1
    }

    claimed_positions = {
        row["rom"]: sorted(row["screen_positions"], key=lambda p: (p[1], p[0]))
        for row in build["changed_tiles"]
    }
    mapping_mismatches = []
    for rom_tile in sorted(set(claimed_positions) | set(actual_positions_by_rom)):
        claimed = claimed_positions.get(rom_tile, [])
        actual = sorted(actual_positions_by_rom.get(rom_tile, []), key=lambda p: (p[1], p[0]))
        if claimed != actual:
            mapping_mismatches.append(
                {"rom": rom_tile, "claimed": claimed, "actual": actual}
            )

    expected: set[tuple[int, int]] = set()
    expected_by_screen_tile: dict[tuple[int, int], set[tuple[int, int]]] = {}
    expected_by_rom: dict[str, set[tuple[int, int]]] = collections.defaultdict(set)
    for slot, positions in slot_positions.items():
        before = tiles_4bpp(ram_a[slot : slot + TILE_BYTES])[0]
        after = tiles_4bpp(ram_b[slot : slot + TILE_BYTES])[0]
        if before == after:
            raise RuntimeError(f"declared replacement did not change WSRAM slot {slot:04X}")
        rom_tile = slot_to_rom[slot]
        for col, row in positions:
            tile_points = {
                (col * 8 + x, row * 8 + y)
                for y in range(8)
                for x in range(8)
                if before[y][x] != after[y][x]
            }
            expected |= tile_points
            expected_by_screen_tile[(col, row)] = tile_points
            expected_by_rom[rom_tile] |= tile_points

    current_sprites = parse_sprites(
        core_a, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET
    )
    next_sprites = parse_sprites(
        core_a, NEXT_SPRITE_TABLE_CORE_OFFSET, NEXT_SPRITE_COUNT_CORE_OFFSET
    )
    changed_sprite_refs = [
        {
            "table": table_name,
            "index": sprite["index"],
            "x": sprite["x"],
            "y": sprite["y"],
            "wsram_offset": f"{sprite['wsram_offset']:04X}",
            "rom_tile": slot_to_rom[sprite["wsram_offset"]],
        }
        for table_name, sprites in (("current", current_sprites), ("next", next_sprites))
        for sprite in sprites
        if sprite["wsram_offset"] in slot_to_rom
    ]
    current_coverage = sprite_coverage(
        core_a, ram_a, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET
    )
    next_coverage = sprite_coverage(
        core_a, ram_a, NEXT_SPRITE_TABLE_CORE_OFFSET, NEXT_SPRITE_COUNT_CORE_OFFSET
    )
    sprite_coverage_union = current_coverage | next_coverage

    image_a = Image.open(args.capture_a).convert("RGB")
    image_b = Image.open(args.capture_b).convert("RGB")
    if image_a.size != (SCREEN_W, SCREEN_H) or image_b.size != (SCREEN_W, SCREEN_H):
        raise RuntimeError(f"unexpected capture size: A={image_a.size} B={image_b.size}")
    pixels_a = image_a.load()
    pixels_b = image_b.load()
    observed = {
        (x, y)
        for y in range(SCREEN_H)
        for x in range(SCREEN_W)
        if pixels_a[x, y] != pixels_b[x, y]
    }

    outside = observed - expected
    hidden = expected - observed
    hidden_by_sprite = hidden & sprite_coverage_union
    hidden_without_sprite = hidden - sprite_coverage_union

    target_positions: dict[str, set[tuple[int, int]]] = {}
    cleanup = {row["name"]: row for row in build["cleanup"]}
    for target in build["targets"]:
        positions = {tuple(pos) for pos in target["screen_tiles_touched"]}
        for row in cleanup[target["name"]]["exclusive_tiles_cleared"]:
            positions.add(tuple(row["screen"]))
        target_positions[target["name"]] = positions

    target_rows = []
    for target in build["targets"]:
        positions = target_positions[target["name"]]
        target_expected = set().union(
            *(expected_by_screen_tile.get(pos, set()) for pos in positions)
        )
        target_observed = target_expected & observed
        target_hidden = target_expected - observed
        target_rows.append(
            {
                "name": target["name"],
                "korean": target["korean"],
                "screen_tiles": [
                    list(pos) for pos in sorted(positions, key=lambda p: (p[1], p[0]))
                ],
                "expected_changed_pixels": len(target_expected),
                "observed_changed_pixels": len(target_observed),
                "hidden_changed_pixels": len(target_hidden),
                "hidden_pixels_covered_by_sprite": len(
                    target_hidden & sprite_coverage_union
                ),
                "all_hidden_explained_by_sprite": not (
                    target_hidden - sprite_coverage_union
                ),
            }
        )

    captures_dir = args.capture_b.parent
    expected_mask_path = captures_dir / "static_focus_expected_mask.png"
    observed_mask_path = captures_dir / "static_focus_observed_mask.png"
    hidden_mask_path = captures_dir / "static_focus_hidden_by_sprite_mask.png"
    diff_path = captures_dir / "static_focus_visible_diff.png"
    save_mask(expected_mask_path, expected)
    save_mask(observed_mask_path, observed)
    save_mask(hidden_mask_path, hidden)
    save_diff(diff_path, image_a, image_b)

    verification = {
        "capture_dimensions_exact": True,
        "a_b_framebuffers_differ": image_a.tobytes() != image_b.tobytes(),
        "replacement_count_matches_build_tile_count": (
            len(slot_to_rom) == len(build["changed_tiles"])
        ),
        "every_changed_slot_has_one_visible_bg_reference": (
            not unreferenced_slots and not multiply_referenced_slots
        ),
        "runtime_bg_mapping_matches_builder_claims": not mapping_mismatches,
        "changed_slots_not_referenced_by_sprites": not changed_sprite_refs,
        "no_framebuffer_diff_outside_exact_bg_mask": not outside,
        "all_unobserved_expected_pixels_covered_by_sprite": not hidden_without_sprite,
        "at_least_one_static_pixel_visible": bool(observed & expected),
        "all_targets_have_expected_static_changes": all(
            row["expected_changed_pixels"] > 0 for row in target_rows
        ),
    }
    result = {
        "build_report": str(args.build_report),
        "state_report": str(args.state_report),
        "capture_a": str(args.capture_a),
        "capture_a_sha256": digest(args.capture_a),
        "capture_b": str(args.capture_b),
        "capture_b_sha256": digest(args.capture_b),
        "core_member": member_a,
        "replacement_slots": len(slot_to_rom),
        "visible_bg_references": sum(len(rows) for rows in slot_positions.values()),
        "unreferenced_slots": [f"{slot:04X}" for slot in unreferenced_slots],
        "multiply_referenced_slots": multiply_referenced_slots,
        "mapping_mismatches": mapping_mismatches,
        "changed_sprite_references": changed_sprite_refs,
        "expected_static_changed_pixels": len(expected),
        "observed_framebuffer_changed_pixels": len(observed),
        "observed_inside_expected_static_mask": len(observed & expected),
        "observed_outside_expected_static_mask": len(outside),
        "expected_pixels_hidden_by_sprite": len(hidden_by_sprite),
        "expected_pixels_unexplained": len(hidden_without_sprite),
        "visible_coverage_ratio": len(observed & expected) / len(expected),
        "outside_examples": [
            list(point) for point in sorted(outside, key=lambda p: (p[1], p[0]))[:32]
        ],
        "unexplained_examples": [
            list(point)
            for point in sorted(hidden_without_sprite, key=lambda p: (p[1], p[0]))[:32]
        ],
        "sprite_coverage": {
            "current_pixels": len(current_coverage),
            "next_pixels": len(next_coverage),
            "union_pixels": len(sprite_coverage_union),
        },
        "targets": target_rows,
        "artifacts": {
            "expected_mask": str(expected_mask_path),
            "observed_mask": str(observed_mask_path),
            "hidden_by_sprite_mask": str(hidden_mask_path),
            "visible_diff": str(diff_path),
        },
        "verification": verification,
    }
    out = args.build_report.parent / "capture_verification.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"replacement slots : {len(slot_to_rom)}")
    print(f"expected pixels   : {len(expected)}")
    print(f"observed pixels   : {len(observed)}")
    print(f"outside mask      : {len(outside)}")
    print(f"sprite-hidden     : {len(hidden_by_sprite)}")
    print(f"unexplained       : {len(hidden_without_sprite)}")
    print(f"mapping mismatch  : {len(mapping_mismatches)}")
    for row in target_rows:
        print(
            f"{row['name']:18s} expected={row['expected_changed_pixels']:4d} "
            f"observed={row['observed_changed_pixels']:4d} "
            f"hidden={row['hidden_changed_pixels']:4d}"
        )
    print(f"report            : {out}")

    if not all(verification.values()):
        raise RuntimeError(
            "capture verification failed: "
            + json.dumps(verification, ensure_ascii=False)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
