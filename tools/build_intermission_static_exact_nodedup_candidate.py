#!/usr/bin/env python3
"""Build an exact default-intermission Korean candidate without shared-tile aliases.

The three accepted main-TIP savestates already contain the complete 26x16 static
intermission background.  Their tilemap and every referenced background tile are
byte-identical; only non-background focus-sprite VRAM differs.  This builder uses
QuickSave1 as the pixel source and QuickSave2/3 as independent equality checks.

The stock asset is a compressed 26x16 source whose loader deduplicates equal tiles.
That is why unrelated Korean syllables can share one live tile.  The candidate
converts only this asset to the game's raw 4bpp mode, writes an explicit deduplicated
map after the Korean labels are rendered, and relocates its 67-byte auxiliary block
into the existing FF padding.  Exact focus-atlas wording, 13px font and origins are
used for all twelve leaf labels.

Matching QuickSave1/2/3 states are also emitted.  Their serialized WSRAM tilemaps
and graphics are rewritten so the candidate can be inspected immediately without
requiring a fresh intermission entry.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core, write_state_with_core  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_intermission_labels_ko import FONT, Rasteriser, draw_strip  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402
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
TILE_BYTES = 0x20
TILEMAP_BASE = 0x3800
ASSET_COL0 = 1
ASSET_ROW0 = 1
ASSET_W = 26
ASSET_H = 16
ASSET_CELLS = ASSET_W * ASSET_H

HEADER = 0x54B780
MAP = 0x54B792
GFX = 0x54BAD2
OLD_AUX = 0x54E3CD
AUX_END = 0x54E410
AUX_BYTES = AUX_END - OLD_AUX


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


def state_entry(ram: bytes, col: int, row: int) -> int:
    off = TILEMAP_BASE + (row * 32 + col) * 2
    return int.from_bytes(ram[off : off + 2], "little")


def state_cell_raw(ram: bytes, col: int, row: int) -> bytes:
    entry = state_entry(ram, col, row)
    tile = entry & 0x01FF
    gfx_base = 0x8000 if entry & 0x2000 else 0x4000
    return bytes(ram[gfx_base + tile * TILE_BYTES : gfx_base + (tile + 1) * TILE_BYTES])


def asset_positions() -> list[tuple[int, int]]:
    return [
        (ASSET_COL0 + col, ASSET_ROW0 + row)
        for row in range(ASSET_H)
        for col in range(ASSET_W)
    ]


def render_screen(
    full_grids: dict[tuple[int, int], list[list[int]]], path: Path, scale: int
) -> None:
    image = Image.new("RGB", (SCREEN_W, SCREEN_H), GREYS_16[0])
    pixels = image.load()
    for (col, row), grid in full_grids.items():
        if not (0 <= col < 28 and 0 <= row < 18):
            continue
        for y in range(8):
            for x in range(8):
                pixels[col * 8 + x, row * 8 + y] = GREYS_16[grid[y][x]]
    if scale > 1:
        image = image.resize((SCREEN_W * scale, SCREEN_H * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def far_ptr(offset: int, segment: int = 0x3000) -> bytes:
    return offset.to_bytes(2, "little") + segment.to_bytes(2, "little")


def dedupe_tiles(raws: list[bytes]) -> tuple[list[bytes], list[int]]:
    unique: list[bytes] = []
    index_by_raw: dict[bytes, int] = {}
    remap: list[int] = []
    for raw in raws:
        index = index_by_raw.get(raw)
        if index is None:
            index = len(unique)
            if index >= 0x200:
                raise RuntimeError("deduplicated asset exceeds 9-bit tile indices")
            index_by_raw[raw] = index
            unique.append(raw)
        remap.append(index)
    return unique, remap


def visible_grids(ram: bytes) -> dict[tuple[int, int], list[list[int]]]:
    return {
        (col, row): decode_tile(state_cell_raw(ram, col, row))
        for row in range(18)
        for col in range(28)
    }


def build_desired_grids(
    source_grids: dict[tuple[int, int], list[list[int]]],
    labels_path: Path,
    focus_report_path: Path,
    *,
    enforce_asset_bounds: bool = True,
) -> tuple[dict[tuple[int, int], list[list[int]]], list[dict], list[dict]]:
    grids = {
        pos: [row[:] for row in grid]
        for pos, grid in source_grids.items()
    }
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    entries = {row["jp"]: row for row in labels["labels"]}
    focus = json.loads(focus_report_path.read_text(encoding="utf-8"))
    targets = focus["targets"]
    if len(targets) != 12:
        raise RuntimeError(f"focus report has {len(targets)} targets, expected 12")

    cleanup_rows: list[dict] = []
    all_cleanup: set[tuple[int, int]] = set()
    original_by_name: dict[str, set[tuple[int, int]]] = {}
    for target in targets:
        entry = entries[target["japanese"]]
        original = {
            (col, row)
            for col in range(int(entry["from"]), int(entry["to"]) + 1)
            for row in (int(entry["row"]), int(entry["row"]) + 1)
        }
        original_by_name[target["name"]] = original
        all_cleanup |= original

        ring: set[tuple[int, int]] = set()
        for col in range(int(entry["from"]) - 1, int(entry["to"]) + 2):
            for row in range(int(entry["row"]) - 1, int(entry["row"]) + 3):
                pos = (col, row)
                if pos in original or pos not in grids:
                    continue
                ink = [value for line in grids[pos] for value in line if value]
                if ink and set(ink) <= {1, 0x0F} and len(ink) <= 64:
                    ring.add(pos)
        all_cleanup |= ring
        cleanup_rows.append(
            {
                "name": target["name"],
                "original_tiles": [list(pos) for pos in sorted(original, key=lambda p: (p[1], p[0]))],
                "ring_tiles": [list(pos) for pos in sorted(ring, key=lambda p: (p[1], p[0]))],
            }
        )

    for pos in all_cleanup:
        grid = grids[pos]
        for y in range(8):
            for x in range(8):
                if grid[y][x] in (1, 0x0F):
                    grid[y][x] = 0

    ras = Rasteriser(FONT, 13)
    target_rows: list[dict] = []
    for target in targets:
        origin = (
            int(target["bounds_xyxy"][0])
            + int(target["composition"]["korean_origin_xy"][0]),
            int(target["bounds_xyxy"][1])
            + int(target["composition"]["korean_origin_xy"][1]),
        )
        strip = draw_strip(
            target["korean"],
            int(target["strip_width"]),
            ras,
            0x0F,
            1,
            1,
        )
        points = [
            (origin[0] + x, origin[1] + y, value)
            for y, row in enumerate(strip)
            for x, value in enumerate(row)
            if value
        ]
        touched: set[tuple[int, int]] = set()
        for x, y, value in points:
            if not (0 <= x < SCREEN_W and 0 <= y < SCREEN_H):
                raise RuntimeError(f"{target['name']}: exact focus origin leaves the screen")
            pos = (x // 8, y // 8)
            if pos not in grids:
                raise RuntimeError(f"{target['name']}: target cell is outside visible screen: {pos}")
            if enforce_asset_bounds and not (
                ASSET_COL0 <= pos[0] < ASSET_COL0 + ASSET_W
                and ASSET_ROW0 <= pos[1] < ASSET_ROW0 + ASSET_H
            ):
                raise RuntimeError(f"{target['name']}: target cell is outside 26x16 asset: {pos}")
            grid = grids[pos]
            tx, ty = x % 8, y % 8
            if grid[ty][tx] not in (0, 1, 0x0F):
                raise RuntimeError(
                    f"{target['name']}: exact draw overlaps plate artwork at ({x},{y}) index={grid[ty][tx]:X}"
                )
            grid[ty][tx] = value
            touched.add(pos)
        target_rows.append(
            {
                "name": target["name"],
                "korean": target["korean"],
                "font_size": 13,
                "exact_strip_origin_xy": list(origin),
                "strip_width": int(target["strip_width"]),
                "screen_tiles_touched": [list(pos) for pos in sorted(touched, key=lambda p: (p[1], p[0]))],
            }
        )
    return grids, target_rows, cleanup_rows


def sprite_ids(core: bytes) -> set[int]:
    out: set[int] = set()
    for table, count in (
        (SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET),
        (NEXT_SPRITE_TABLE_CORE_OFFSET, NEXT_SPRITE_COUNT_CORE_OFFSET),
    ):
        out.update(row["tile"] for row in parse_sprites(core, table, count))
    return out


def patch_state_core(
    core: bytes,
    desired_raws: list[bytes],
) -> tuple[bytes, dict]:
    ram = bytearray(core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES])
    positions = asset_positions()
    old_entries = [state_entry(ram, col, row) for col, row in positions]
    desired_by_bank: dict[int, list[bytes]] = {0: [], 1: []}
    for entry, raw in zip(old_entries, desired_raws):
        desired_by_bank[1 if entry & 0x2000 else 0].append(raw)

    inner_positions = set(positions)
    used_all: dict[int, set[int]] = {0: set(), 1: set()}
    used_outside: dict[int, set[int]] = {0: set(), 1: set()}
    used_inner: dict[int, set[int]] = {0: set(), 1: set()}
    for row in range(32):
        for col in range(32):
            entry = state_entry(ram, col, row)
            bank = 1 if entry & 0x2000 else 0
            tile = entry & 0x01FF
            used_all[bank].add(tile)
            if (col, row) in inner_positions:
                used_inner[bank].add(tile)
            else:
                used_outside[bank].add(tile)

    sprites = sprite_ids(core)
    allocation: dict[int, dict[bytes, int]] = {0: {}, 1: {}}
    allocation_rows = []
    for bank in (0, 1):
        unique = list(dict.fromkeys(desired_by_bank[bank]))
        forbidden = set(used_outside[bank])
        if bank == 0:
            forbidden |= sprites
        reusable = sorted(used_inner[bank] - forbidden)
        free = sorted(set(range(0x200)) - used_all[bank] - (sprites if bank == 0 else set()))
        pool = reusable + [tile for tile in free if tile not in reusable]
        if len(pool) < len(unique):
            raise RuntimeError(
                f"state bank {bank}: need {len(unique)} tile slots, have {len(pool)} safe slots"
            )
        for raw, tile in zip(unique, pool):
            allocation[bank][raw] = tile
            gfx_base = 0x8000 if bank else 0x4000
            ram[gfx_base + tile * TILE_BYTES : gfx_base + (tile + 1) * TILE_BYTES] = raw
        allocation_rows.append(
            {
                "bank": bank,
                "unique_desired_tiles": len(unique),
                "reused_inner_slots": len(reusable),
                "free_slots_available": len(free),
                "allocated_first": f"{min(pool[:len(unique)]):03X}" if unique else None,
                "allocated_last": f"{max(pool[:len(unique)]):03X}" if unique else None,
                "sprite_slots_protected": len(sprites) if bank == 0 else 0,
            }
        )

    for (col, row), entry, raw in zip(positions, old_entries, desired_raws):
        bank = 1 if entry & 0x2000 else 0
        tile = allocation[bank][raw]
        new_entry = (entry & ~0x01FF) | tile
        off = TILEMAP_BASE + (row * 32 + col) * 2
        ram[off : off + 2] = new_entry.to_bytes(2, "little")

    new_core = bytearray(core)
    new_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES] = ram
    return bytes(new_core), {
        "banks": allocation_rows,
        "tilemap_entries_changed": sum(
            state_entry(core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES], col, row)
            != state_entry(ram, col, row)
            for col, row in positions
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock-rom", type=Path, default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc")
    ap.add_argument("--base-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--base-sav", type=Path, default=ROOT / "sram/monoeye_ko_expanded.sav")
    ap.add_argument("--state-dir", type=Path, default=ROOT / "BizHawk-2.11.1-win-x64/WonderSwan/State/monoeye ko expanded.Cygne")
    ap.add_argument("--zstd-dll", type=Path, default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll")
    ap.add_argument("--labels", type=Path, default=ROOT / "data/intermission_labels_ko.json")
    ap.add_argument("--focus-report", type=Path, default=ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out/patch/intermission_static_exact_nodedup")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--zstd-level", type=int, default=3)
    args = ap.parse_args(argv)

    states = [args.state_dir / f"Mednafen.QuickSave{i}.State" for i in (1, 2, 3)]
    for path in (args.stock_rom, args.base_rom, args.base_sav, args.zstd_dll, args.labels, args.focus_report, *states):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    stock = args.stock_rom.read_bytes()
    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    if len(stock) != 0x800000:
        raise RuntimeError("stock ROM must be 8 MiB")
    body = base_rom[base : base + len(stock)]
    expected_header = bytes.fromhex("1A 10 22 02 A0 01 92 B7 00 30 D2 BA 00 30 CD E3 00 30")
    if body[HEADER : HEADER + 18] != expected_header:
        raise RuntimeError("main TIP intermission asset header drifted")
    source_map = [
        int.from_bytes(body[MAP + i * 2 : MAP + i * 2 + 2], "little")
        for i in range(ASSET_CELLS)
    ]
    if [value & 0x01FF for value in source_map] != list(range(ASSET_CELLS)):
        raise RuntimeError("main TIP intermission source map is no longer 0..415")

    zstd = Zstd(args.zstd_dll)
    state_rows = []
    cores: list[bytes] = []
    rams: list[bytes] = []
    core_names: list[str] = []
    for path in states:
        core, core_name = read_state_core(path, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        cores.append(core)
        rams.append(ram)
        core_names.append(core_name)
        state_rows.append(
            {
                "source": str(path),
                "core_member": core_name,
                "tilemap_sha256": sha256_bytes(ram[TILEMAP_BASE : TILEMAP_BASE + 0x800]),
            }
        )
    if len({row["tilemap_sha256"] for row in state_rows}) != 1:
        raise RuntimeError("QuickSave1/2/3 static tilemaps differ")
    positions = asset_positions()
    for pos in positions:
        raws = {state_cell_raw(ram, *pos) for ram in rams}
        if len(raws) != 1:
            raise RuntimeError(f"QuickSave1/2/3 background pixels differ at {pos}")

    before_grids = visible_grids(rams[0])
    desired_grids, target_rows, cleanup_rows = build_desired_grids(
        before_grids, args.labels, args.focus_report
    )
    desired_raws = [encode_tile(desired_grids[pos]) for pos in positions]
    unique, remap = dedupe_tiles(desired_raws)
    gfx = b"".join(unique)
    new_aux = GFX + len(gfx)
    aux = body[OLD_AUX:AUX_END]
    if len(aux) != AUX_BYTES:
        raise RuntimeError("auxiliary block length drifted")
    if new_aux + len(aux) > 0x550000:
        raise RuntimeError("raw asset exceeds bank 54")
    extension = body[AUX_END : new_aux + len(aux)]
    if any(value != 0xFF for value in extension):
        raise RuntimeError("raw asset extension would overwrite non-FF data")

    candidate = bytearray(base_rom)
    off = base
    candidate[off + HEADER + 2 : off + HEADER + 4] = bytes.fromhex("00 02")
    candidate[off + HEADER + 4 : off + HEADER + 6] = len(unique).to_bytes(2, "little")
    candidate[off + HEADER + 10 : off + HEADER + 14] = far_ptr(GFX & 0xFFFF)
    candidate[off + HEADER + 14 : off + HEADER + 18] = far_ptr(new_aux & 0xFFFF)
    for i, (old, index) in enumerate(zip(source_map, remap)):
        value = (old & ~0x01FF) | index
        candidate[off + MAP + i * 2 : off + MAP + i * 2 + 2] = value.to_bytes(2, "little")
    candidate[off + GFX : off + GFX + len(gfx)] = gfx
    candidate[off + new_aux : off + new_aux + len(aux)] = aux
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_out = args.out_dir / "intermission_static_exact_nodedup_candidate.wsc"
    sav_out = ROOT / "sram/intermission_static_exact_nodedup_candidate.sav"
    rom_out.write_bytes(candidate_bytes)
    shutil.copy2(args.base_sav, sav_out)

    preview_dir = args.out_dir / "previews"
    render_screen(before_grids, preview_dir / "before_main_tip_state1.png", args.scale)
    render_screen(desired_grids, preview_dir / "after_exact_static.png", args.scale)

    patched_state_rows = []
    state_out_dir = args.out_dir / "states"
    for index, (path, core, core_name) in enumerate(zip(states, cores, core_names), 1):
        patched_core, patch_report = patch_state_core(core, desired_raws)
        state_out = state_out_dir / f"Mednafen.QuickSave{index}.State"
        compressed = write_state_with_core(
            path, state_out, core_name, patched_core, zstd, args.zstd_level
        )
        verify_core, verify_name = read_state_core(state_out, zstd)
        if verify_name != core_name or verify_core != patched_core:
            raise RuntimeError(f"QuickSave{index} patched state failed round-trip")
        verify_ram = verify_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        for pos, desired in zip(positions, desired_raws):
            if state_cell_raw(verify_ram, *pos) != desired:
                raise RuntimeError(f"QuickSave{index} pixel verification failed at {pos}")
        patched_state_rows.append(
            {
                "index": index,
                "source": str(path),
                "output": str(state_out),
                "output_sha256": sha256_file(state_out),
                "core_sha256": sha256_bytes(patched_core),
                "compressed_core_bytes": compressed,
                **patch_report,
            }
        )

    changed_offsets = [
        i for i, (old, new) in enumerate(zip(base_rom, candidate_bytes)) if old != new
    ]
    allowed = set(range(base + HEADER, base + HEADER + 18))
    allowed.update(range(base + MAP, base + MAP + ASSET_CELLS * 2))
    allowed.update(range(base + GFX, base + new_aux + len(aux)))
    allowed.update((len(candidate_bytes) - 2, len(candidate_bytes) - 1))
    outside = [i for i in changed_offsets if i not in allowed]
    if outside:
        raise RuntimeError(f"candidate changed outside exact asset allowlist at {outside[0]:07X}")

    report = {
        "purpose": "exact 12-label Korean default intermission background with explicit non-aliased raw tile map",
        "base_rom": str(args.base_rom),
        "base_rom_sha256": sha256_bytes(base_rom),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": sha256_file(rom_out),
        "candidate_sav": str(sav_out),
        "candidate_sav_sha256": sha256_file(sav_out),
        "checksum": f"{checksum:04X}",
        "source_states": state_rows,
        "asset": {
            "header": f"{HEADER:06X}",
            "map": [f"{MAP:06X}", f"{MAP + ASSET_CELLS * 2:06X}"],
            "graphics": [f"{GFX:06X}", f"{GFX + len(gfx):06X}"],
            "auxiliary_old": [f"{OLD_AUX:06X}", f"{AUX_END:06X}"],
            "auxiliary_new": [f"{new_aux:06X}", f"{new_aux + len(aux):06X}"],
            "mode_before": "22 02 compressed/deduplicated",
            "mode_after": "00 02 raw 4bpp with explicit map",
            "cells": ASSET_CELLS,
            "unique_tiles_after_korean": len(unique),
            "raw_graphics_bytes": len(gfx),
            "extension_bytes_into_existing_ff": max(0, new_aux + len(aux) - AUX_END),
        },
        "targets": target_rows,
        "cleanup": cleanup_rows,
        "patched_states": patched_state_rows,
        "changed_rom_bytes_including_checksum": len(changed_offsets),
        "verification": {
            "quicksave_1_2_3_tilemaps_identical": True,
            "all_416_background_cells_identical_across_states": True,
            "all_12_labels_exact_focus_origin": len(target_rows) == 12,
            "all_labels_font_size_13": all(row["font_size"] == 13 for row in target_rows),
            "raw_tile_count_fits_9_bits": len(unique) < 0x200,
            "asset_extension_overwrites_only_ff": True,
            "changes_bounded_to_asset_and_checksum": not outside,
            "candidate_saveram_matches_main": sav_out.read_bytes() == args.base_sav.read_bytes(),
            "patched_states_round_trip_and_match_desired_pixels": True,
            "main_tip_not_modified": args.base_rom.read_bytes() == base_rom,
        },
        "previews": {
            "before": str(preview_dir / "before_main_tip_state1.png"),
            "after": str(preview_dir / "after_exact_static.png"),
        },
    }
    report_path = args.out_dir / "exact_nodedup_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"states verified : 3")
    print(f"labels exact    : {len(target_rows)}/12")
    print(f"unique tiles    : {len(unique)}")
    print(f"raw bytes       : {len(gfx)}")
    print(f"aux relocated   : {new_aux:06X}")
    print(f"ROM diff bytes  : {len(changed_offsets)}")
    print(f"checksum        : {checksum:04X}")
    print(f"candidate       : {rom_out}")
    print(f"states          : {state_out_dir}")
    print(f"report          : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
