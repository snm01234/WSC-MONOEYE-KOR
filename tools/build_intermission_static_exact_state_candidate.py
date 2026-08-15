#!/usr/bin/env python3
"""Build a new test ROM plus exact QuickSave1-3 intermission states.

The ROM is the current shared-tile-safe static candidate.  The matching states use
private WSRAM tile slots for every changed background cell, so all twelve labels can
be inspected at the exact approved focus-atlas coordinates without Japanese residue
or cross-label tile aliases.
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

import build_intermission_static_exact_nodedup_candidate as exact  # noqa: E402
from build_intermission_state_ab import Zstd, read_state_core, write_state_with_core  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    NEXT_SPRITE_COUNT_CORE_OFFSET,
    NEXT_SPRITE_TABLE_CORE_OFFSET,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    parse_sprites,
)

TILE_BYTES = 0x20
TILEMAP_BASE = 0x3800
VISIBLE = [(col, row) for row in range(18) for col in range(28)]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def entry(ram: bytes, col: int, row: int) -> int:
    off = TILEMAP_BASE + (row * 32 + col) * 2
    return int.from_bytes(ram[off : off + 2], "little")


def raw_for(ram: bytes, col: int, row: int) -> bytes:
    value = entry(ram, col, row)
    tile = value & 0x01FF
    base = 0x8000 if value & 0x2000 else 0x4000
    return bytes(ram[base + tile * TILE_BYTES : base + (tile + 1) * TILE_BYTES])


def sprite_tiles(core: bytes) -> set[int]:
    result: set[int] = set()
    for table, count in (
        (SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET),
        (NEXT_SPRITE_TABLE_CORE_OFFSET, NEXT_SPRITE_COUNT_CORE_OFFSET),
    ):
        result.update(row["tile"] for row in parse_sprites(core, table, count))
    return result


def patch_core(
    core: bytes,
    desired_raw: dict[tuple[int, int], bytes],
    changed: set[tuple[int, int]],
) -> tuple[bytes, dict]:
    ram_before = bytes(core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES])
    ram = bytearray(ram_before)
    sprites = sprite_tiles(core)

    used_all = {0: set(), 1: set()}
    forbidden = {0: set(), 1: set()}
    changed_ids = {0: set(), 1: set()}
    for row in range(32):
        for col in range(32):
            value = entry(ram_before, col, row)
            bank = 1 if value & 0x2000 else 0
            tile = value & 0x01FF
            used_all[bank].add(tile)
            if (col, row) in changed:
                changed_ids[bank].add(tile)
            else:
                forbidden[bank].add(tile)
    forbidden[0] |= sprites

    allocation: dict[int, dict[bytes, int]] = {0: {}, 1: {}}
    bank_rows = []
    for bank in (0, 1):
        raws = []
        for pos in sorted(changed, key=lambda p: (p[1], p[0])):
            if (1 if entry(ram_before, *pos) & 0x2000 else 0) == bank:
                raw = desired_raw[pos]
                if raw not in raws:
                    raws.append(raw)
        reusable = sorted(changed_ids[bank] - forbidden[bank])
        free = sorted(set(range(0x200)) - used_all[bank] - (sprites if bank == 0 else set()))
        pool = reusable + [tile for tile in free if tile not in reusable]
        if len(pool) < len(raws):
            raise RuntimeError(
                f"bank {bank}: exact state needs {len(raws)} slots, only {len(pool)} are safe"
            )
        for raw, tile in zip(raws, pool):
            allocation[bank][raw] = tile
            gfx_base = 0x8000 if bank else 0x4000
            ram[gfx_base + tile * TILE_BYTES : gfx_base + (tile + 1) * TILE_BYTES] = raw
        bank_rows.append(
            {
                "bank": bank,
                "unique_changed_tiles": len(raws),
                "reusable_changed_slots": len(reusable),
                "free_slots": len(free),
                "allocated_tiles": [f"{tile:03X}" for tile in pool[: len(raws)]],
                "protected_sprite_tiles": len(sprites) if bank == 0 else 0,
            }
        )

    for pos in changed:
        old = entry(ram_before, *pos)
        bank = 1 if old & 0x2000 else 0
        tile = allocation[bank][desired_raw[pos]]
        new = (old & ~0x01FF) | tile
        off = TILEMAP_BASE + (pos[1] * 32 + pos[0]) * 2
        ram[off : off + 2] = new.to_bytes(2, "little")

    for pos in VISIBLE:
        expected = desired_raw[pos]
        actual = raw_for(ram, *pos)
        if actual != expected:
            raise RuntimeError(f"patched state visible pixel mismatch at {pos}")

    for tile in sprites:
        start = 0x4000 + tile * TILE_BYTES
        if ram[start : start + TILE_BYTES] != ram_before[start : start + TILE_BYTES]:
            raise RuntimeError(f"focus sprite tile {tile:03X} was overwritten")

    patched = bytearray(core)
    patched[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES] = ram
    return bytes(patched), {
        "changed_screen_cells": len(changed),
        "tilemap_entry_changes": sum(
            entry(ram_before, *pos) != entry(ram, *pos) for pos in changed
        ),
        "banks": bank_rows,
        "sprite_tiles_preserved": len(sprites),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out/patch/intermission_static_focus_matched_safe/intermission_static_focus_matched_safe_candidate.wsc",
    )
    ap.add_argument(
        "--sav",
        type=Path,
        default=ROOT / "sram/intermission_static_focus_matched_safe_candidate.sav",
    )
    ap.add_argument(
        "--state-dir",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/WonderSwan/State/monoeye ko expanded.Cygne",
    )
    ap.add_argument("--zstd-dll", type=Path, default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll")
    ap.add_argument("--labels", type=Path, default=ROOT / "data/intermission_labels_ko.json")
    ap.add_argument(
        "--focus-report",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_static_exact_clean_color_candidate",
    )
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--zstd-level", type=int, default=3)
    args = ap.parse_args(argv)

    states = [args.state_dir / f"Mednafen.QuickSave{i}.State" for i in (1, 2, 3)]
    for path in (args.rom, args.sav, args.zstd_dll, args.labels, args.focus_report, *states):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    zstd = Zstd(args.zstd_dll)
    cores = []
    core_names = []
    rams = []
    for path in states:
        core, name = read_state_core(path, zstd)
        cores.append(core)
        core_names.append(name)
        rams.append(core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES])

    tilemap_hashes = {
        sha256_bytes(ram[TILEMAP_BASE : TILEMAP_BASE + 0x800]) for ram in rams
    }
    if len(tilemap_hashes) != 1:
        raise RuntimeError("QuickSave1-3 tilemaps differ")
    for pos in VISIBLE:
        if len({raw_for(ram, *pos) for ram in rams}) != 1:
            raise RuntimeError(f"QuickSave1-3 static pixels differ at {pos}")

    before_grids = exact.visible_grids(rams[0])
    after_grids, targets, cleanup = exact.build_desired_grids(
        before_grids,
        args.labels,
        args.focus_report,
        enforce_asset_bounds=False,
    )
    # Non-focus BG palette 0 renders the canonical glyph indices 1/F as
    # black/yellow.  The focus plates use a different sprite palette, so those
    # colours are not present there.  For the two user-reported top labels,
    # remap only the already-clean Korean mask to BG indices 3/E, which render as
    # dark blue outline + pale cyan fill.  The cleanup report proves these cells
    # contain no non-Korean nonzero pixels after reconstruction.
    cleanup_by_name = {row["name"]: row for row in cleanup}
    ink_remap_cells: set[tuple[int, int]] = set()
    for name in ("mission_status", "scouting"):
        row = cleanup_by_name[name]
        ink_remap_cells.update(tuple(pos) for pos in row["original_tiles"])
        ink_remap_cells.update(tuple(pos) for pos in row["ring_tiles"])
    remapped_pixels = {"outline_1_to_3": 0, "fill_F_to_E": 0}
    for pos in ink_remap_cells:
        grid = after_grids[pos]
        for y in range(8):
            for x in range(8):
                if grid[y][x] == 1:
                    grid[y][x] = 3
                    remapped_pixels["outline_1_to_3"] += 1
                elif grid[y][x] == 0x0F:
                    grid[y][x] = 0x0E
                    remapped_pixels["fill_F_to_E"] += 1

    desired_raw = {pos: exact.encode_tile(after_grids[pos]) for pos in VISIBLE}
    changed = {
        pos for pos in VISIBLE if desired_raw[pos] != exact.encode_tile(before_grids[pos])
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_out = args.out_dir / "intermission_static_exact_clean_color_candidate.wsc"
    sav_out = ROOT / "sram/intermission_static_exact_clean_color_candidate.sav"
    shutil.copy2(args.rom, rom_out)
    shutil.copy2(args.sav, sav_out)

    previews = args.out_dir / "previews"
    exact.render_screen(before_grids, previews / "before_main_tip_state1.png", args.scale)
    exact.render_screen(after_grids, previews / "after_exact_focus_coordinates.png", args.scale)

    state_rows = []
    state_out_dir = args.out_dir / "states"
    for index, (source, core, core_name) in enumerate(zip(states, cores, core_names), 1):
        patched_core, row = patch_core(core, desired_raw, changed)
        output = state_out_dir / f"Mednafen.QuickSave{index}.State"
        compressed = write_state_with_core(
            source, output, core_name, patched_core, zstd, args.zstd_level
        )
        verify, verify_name = read_state_core(output, zstd)
        if verify_name != core_name or verify != patched_core:
            raise RuntimeError(f"QuickSave{index} round-trip failed")
        state_rows.append(
            {
                "index": index,
                "source": str(source),
                "output": str(output),
                "output_sha256": sha256_file(output),
                "core_sha256": sha256_bytes(patched_core),
                "compressed_core_bytes": compressed,
                **row,
            }
        )

    report = {
        "purpose": "exact-focus-coordinate QuickSave1-3 backgrounds with mission/scouting black-yellow BG ink removed",
        "rom": str(rom_out),
        "rom_sha256": sha256_file(rom_out),
        "sav": str(sav_out),
        "sav_sha256": sha256_file(sav_out),
        "source_rom": str(args.rom),
        "source_rom_sha256": sha256_file(args.rom),
        "rom_is_shared_tile_safe_candidate": True,
        "state_background": {
            "exact_focus_coordinates": True,
            "font_size": 13,
            "labels": len(targets),
            "changed_visible_cells": len(changed),
            "japanese_label_ink_cleared_before_redraw": True,
            "mission_scouting_bg_ink_indices": {"outline": 3, "fill": 14},
            "mission_scouting_bg_ink_pixels_remapped": remapped_pixels,
            "mission_scouting_black_yellow_indices_removed": True,
        },
        "targets": targets,
        "cleanup": cleanup,
        "states": state_rows,
        "verification": {
            "quicksave_1_2_3_tilemaps_identical": True,
            "all_visible_background_pixels_identical_across_sources": True,
            "all_12_labels_exact": len(targets) == 12,
            "all_patched_states_round_trip": True,
            "all_visible_cells_match_exact_target": True,
            "focus_sprite_tiles_preserved": True,
            "source_main_tip_unchanged": True,
        },
        "previews": {
            "before": str(previews / "before_main_tip_state1.png"),
            "after": str(previews / "after_exact_focus_coordinates.png"),
        },
        "note": "Load the supplied matching QuickSave1-3 files when inspecting this test ROM; savestates restore serialized VRAM.",
    }
    report_path = args.out_dir / "exact_state_candidate_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    readme = args.out_dir / "README.md"
    readme.write_text(
        "# Exact intermission static test candidate\n\n"
        "- ROM: `intermission_static_exact_clean_color_candidate.wsc`\n"
        "- SaveRAM: `intermission_static_exact_clean_color_candidate.sav`\n"
        "- Matching states: `states/Mednafen.QuickSave1.State` through `QuickSave3.State`\n"
        "- All twelve leaf labels use the focus atlas wording, 13px font and exact coordinates.\n"
        "- Mission/status and scouting use BG ink 3/E, removing black/yellow 1/F colours.\n"
        "- The states use private background tile slots and preserve the focus-sprite slots.\n"
        "- Load the supplied states, not the old main-TIP states, because a savestate restores VRAM.\n",
        encoding="utf-8",
    )

    print(f"labels exact       : {len(targets)}/12")
    print(f"changed cells      : {len(changed)}")
    print(f"states built       : {len(state_rows)}")
    print(f"candidate ROM      : {rom_out}")
    print(f"matching states    : {state_out_dir}")
    print(f"report             : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
