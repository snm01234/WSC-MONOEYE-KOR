#!/usr/bin/env python3
"""Build one ROM and twelve states with every leaf focus label localized cleanly."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import (  # noqa: E402
    Zstd,
    all_hits,
    read_state_core,
    sha256_file,
    write_state_with_core,
)
from build_intermission_supply_focus_ab import render_grid  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_intermission_labels_ko import FONT, Rasteriser, draw_strip  # noqa: E402
from render_bank_tiles import tiles_4bpp  # noqa: E402
from trace_intermission_all_focus_states import TARGETS  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    FOCUS_ATTR,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    parse_sprites,
)


TILE_BYTES = 0x20
FOCUS_ATLAS_LO = 0x542000
FOCUS_ATLAS_HI = 0x544400
FOCUS_NAMES = [
    "mission_status",
    "scouting",
    "advance",
    "supply",
    "list",
    "assignment",
    "development_plan",
    "remodel",
    "disassemble",
    "save",
    "load",
    "library",
]
JP_BY_NAME = {
    "mission_status": "任務／戦況",
    "scouting": "索敵",
    "advance": "進撃",
    "supply": "補給",
    "list": "一覧",
    "assignment": "配属",
    "development_plan": "開発プラン",
    "remodel": "改造",
    "disassemble": "分解",
    "save": "セーブ",
    "load": "ロード",
    "library": "図鑑",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode_tile(grid: list[list[int]]) -> bytes:
    if len(grid) != 8 or any(len(row) != 8 for row in grid):
        raise ValueError("tile grid must be 8x8")
    out = bytearray()
    for row in grid:
        for x in range(0, 8, 2):
            out.append(((row[x] & 0x0F) << 4) | (row[x + 1] & 0x0F))
    return bytes(out)


def decode_oriented(raw: bytes, flip_h: bool, flip_v: bool) -> list[list[int]]:
    tile = tiles_4bpp(raw)[0]
    return [
        [tile[7 - y if flip_v else y][7 - x if flip_h else x] for x in range(8)]
        for y in range(8)
    ]


def encode_unoriented(
    screen_tile: list[list[int]], flip_h: bool, flip_v: bool
) -> bytes:
    raw_grid = [[0] * 8 for _ in range(8)]
    for y in range(8):
        for x in range(8):
            sx = 7 - x if flip_h else x
            sy = 7 - y if flip_v else y
            raw_grid[sy][sx] = screen_tile[y][x]
    return encode_tile(raw_grid)


def cluster_grid(ram: bytes, sprites: list[dict]) -> tuple[list[list[int]], list[int]]:
    left = min(sprite["x"] for sprite in sprites)
    top = min(sprite["y"] for sprite in sprites)
    right = max(sprite["x"] + 8 for sprite in sprites)
    bottom = max(sprite["y"] + 8 for sprite in sprites)
    grid = [[0] * (right - left) for _ in range(bottom - top)]
    occupied = set()
    for sprite in reversed(sprites):
        origin = (sprite["x"] - left, sprite["y"] - top)
        if origin in occupied:
            raise RuntimeError(f"overlapping focus sprite origin {origin}")
        occupied.add(origin)
        raw = bytes(ram[sprite["wsram_offset"] : sprite["wsram_offset"] + TILE_BYTES])
        tile = decode_oriented(raw, sprite["flip_h"], sprite["flip_v"])
        ox, oy = origin
        for y in range(8):
            for x in range(8):
                if tile[y][x]:
                    grid[oy + y][ox + x] = tile[y][x]
    return grid, [left, top, right, bottom]


def glyph_component(grid: list[list[int]]) -> set[tuple[int, int]]:
    """F fill plus its one-pixel 1-valued outline.

    Flooding through all ``1`` pixels is too broad: on a few right-edge plates
    the glyph outline touches the plate border and would pull the whole frame
    into the cleanup box. The font contract is a one-pixel ring, so direct
    Chebyshev neighbours of F are the exact glyph outline we need.
    """
    height, width = len(grid), len(grid[0])
    fills = {
        (x, y)
        for y, row in enumerate(grid)
        for x, value in enumerate(row)
        if value == 0x0F
    }
    if not fills:
        raise RuntimeError("focus plate has no F/1 glyph component")
    glyph = set(fills)
    for x, y in fills:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if (
                    0 <= nx < width
                    and 0 <= ny < height
                    and grid[ny][nx] == 1
                ):
                    glyph.add((nx, ny))
    return glyph


def row_background(row: list[int]) -> int:
    candidates = [value for value in row if 2 <= value <= 0x0E]
    if not candidates:
        return 0
    return collections.Counter(candidates).most_common(1)[0][0]


def bbox(points: set[tuple[int, int]]) -> list[int]:
    return [
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points) + 1,
        max(y for _, y in points) + 1,
    ]


def localize_grid(
    original: list[list[int]],
    korean_strip: list[list[int]],
    origin_override: tuple[int | None, int | None] | None = None,
) -> tuple[list[list[int]], dict]:
    japanese = glyph_component(original)
    jp_box = bbox(japanese)
    backgrounds = [row_background(row) for row in original]
    for y in range(jp_box[1], jp_box[3]):
        if backgrounds[y] == 0:
            raise RuntimeError(f"glyph row {y} has no recoverable plate background")

    korean_points = {
        (x, y)
        for y, row in enumerate(korean_strip)
        for x, value in enumerate(row)
        if value
    }
    ko_box = bbox(korean_points)
    # Centre the Korean ink itself (not its padded strip) over the original
    # Japanese glyph. This also lets wide 세이브 use the plate's spare margins.
    dx = round(
        ((jp_box[0] + jp_box[2] - 1) - (ko_box[0] + ko_box[2] - 1)) / 2
    )
    dy = round(
        ((jp_box[1] + jp_box[3] - 1) - (ko_box[1] + ko_box[3] - 1)) / 2
    )
    # The 24 px plates share several third-row border tiles. Keep the 14 px
    # Korean glyph out of that shared row when the plate begins at y=1. Lower
    # development buttons begin at y=5; the 32 px development-plan plate begins
    # its text band at y=13. These origins preserve the common edge tiles.
    if len(original) == 24 and len(backgrounds) > 1 and backgrounds[1] == 6:
        dy = 2
    elif len(original) == 24 and len(backgrounds) > 5 and backgrounds[5] == 6:
        dy = 7
    elif len(original) == 32:
        # y=15 is the last scanline of a pair of duplicated bevel tiles in the
        # development-plan arrow. Start at 16 so both shared instances remain
        # byte-identical while the 14 px glyph still ends before the y=31 border.
        dy = 16
    if origin_override is not None:
        override_x, override_y = origin_override
        if override_x is not None:
            dx = override_x
        if override_y is not None:
            dy = override_y
    placed = {(x + dx, y + dy) for x, y in korean_points}
    width, height = len(original[0]), len(original)
    if any(not (0 <= x < width and 0 <= y < height) for x, y in placed):
        raise RuntimeError(f"centred Korean glyph falls outside {width}x{height}")
    if any(original[y][x] == 0 for x, y in placed):
        raise RuntimeError("centred Korean glyph falls on transparent plate pixels")

    out = [row[:] for row in original]
    for y in range(jp_box[1], jp_box[3]):
        for x in range(jp_box[0], jp_box[2]):
            out[y][x] = backgrounds[y]
    for x, y in korean_points:
        out[y + dy][x + dx] = korean_strip[y][x]

    # No Japanese fill survives because every F-bearing component was enclosed
    # by the rebuilt rectangle. Every F in the result must be Korean.
    result_fill = {(x, y) for y, row in enumerate(out) for x, value in enumerate(row) if value == 0x0F}
    korean_fill = {
        (x + dx, y + dy)
        for y, row in enumerate(korean_strip)
        for x, value in enumerate(row)
        if value == 0x0F
    }
    orphan_fill = result_fill - korean_fill
    if orphan_fill:
        raise RuntimeError(f"{len(orphan_fill)} Japanese F pixels survived cleanup")

    changed_outside_cleanup_or_korean = 0
    for y in range(height):
        for x in range(width):
            permitted = (
                jp_box[0] <= x < jp_box[2] and jp_box[1] <= y < jp_box[3]
            ) or (x, y) in placed
            if out[y][x] != original[y][x] and not permitted:
                changed_outside_cleanup_or_korean += 1
    if changed_outside_cleanup_or_korean:
        raise RuntimeError("pixels changed outside cleanup/Korean regions")

    return out, {
        "japanese_component_pixels": len(japanese),
        "japanese_bbox_xyxy": jp_box,
        "korean_origin_xy": [dx, dy],
        "korean_pixels": len(korean_points),
        "orphan_fill_pixels": len(orphan_fill),
        "changed_outside_cleanup_or_korean": changed_outside_cleanup_or_korean,
        "row_background_indices": backgrounds,
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
        "--labels",
        type=Path,
        default=ROOT / "data/intermission_labels_ko.json",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_clean",
    )
    ap.add_argument("--scale", type=int, default=5)
    args = ap.parse_args(argv)

    stock = args.stock_rom.read_bytes()
    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    spec = json.loads(args.labels.read_text(encoding="utf-8"))
    entries_by_jp = {
        entry["jp"]: entry for entry in spec["labels"] if entry.get("ko")
    }
    focus_entries = [entries_by_jp[JP_BY_NAME[name]] for name in FOCUS_NAMES]
    if len(focus_entries) != 12 or [name for name, _, _ in TARGETS] != FOCUS_NAMES:
        raise RuntimeError("focus target/spec ordering contract changed")
    rasters = {13: Rasteriser(FONT, 13)}
    zstd = Zstd(args.zstd_dll)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = args.out_dir / "previews"
    state_dir = args.out_dir / "states"
    preview_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    rom_patches: dict[int, tuple[bytes, str]] = {}
    # Include unchanged unique-source tiles in the contract. A later label must
    # not modify a tile that an earlier plate intentionally leaves untouched.
    rom_constraints: dict[int, tuple[bytes, str]] = {}
    target_reports = []
    state_outputs = []
    for (name, korean_name, state_path), entry in zip(TARGETS, focus_entries):
        core, core_name = read_state_core(state_path, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        sprites = [
            sprite
            for sprite in parse_sprites(
                core, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET
            )
            if sprite["attr"] == FOCUS_ATTR
        ]
        original, bounds = cluster_grid(ram, sprites)
        strip_width = (entry["to"] - entry["from"] + 1) * 8
        # TARGETS carries the approved on-screen wording.  In particular 一覧 is
        # intentionally "목록" here even though the older static-label draft
        # used "일람".
        font_size = 13
        korean_strip = draw_strip(
            korean_name, strip_width, rasters[font_size], 0x0F, 1, 1
        )
        origin_override = {
            "list": (1, 2),
            "load": (5, 2),
        }.get(name)
        localized, evidence = localize_grid(
            original, korean_strip, origin_override=origin_override
        )
        render_grid(original, preview_dir / f"{name}_before.png", args.scale)
        render_grid(localized, preview_dir / f"{name}_after.png", args.scale)

        left, top, _, _ = bounds
        desired_by_ram: dict[int, bytes] = {}
        old_by_ram: dict[int, bytes] = {}
        positions_by_ram: dict[int, list[list[int]]] = collections.defaultdict(list)
        for sprite in sprites:
            ox, oy = sprite["x"] - left, sprite["y"] - top
            crop = [row[ox : ox + 8] for row in localized[oy : oy + 8]]
            desired = encode_unoriented(crop, sprite["flip_h"], sprite["flip_v"])
            ram_off = sprite["wsram_offset"]
            old = bytes(ram[ram_off : ram_off + TILE_BYTES])
            if ram_off in desired_by_ram and desired_by_ram[ram_off] != desired:
                raise RuntimeError(
                    f"{name}: reused live tile {ram_off:04X} needs conflicting results"
                )
            desired_by_ram[ram_off] = desired
            old_by_ram[ram_off] = old
            positions_by_ram[ram_off].append([ox, oy])

        changed_tiles = []
        ambiguous_unchanged = 0
        for ram_off, desired in desired_by_ram.items():
            old = old_by_ram[ram_off]
            hits = [
                hit
                for hit in all_hits(stock, old)
                if 0x540000 <= hit < 0x550000
            ]
            atlas_hits = [
                hit
                for hit in hits
                if FOCUS_ATLAS_LO <= hit < FOCUS_ATLAS_HI and hit % TILE_BYTES == 6
            ]
            if len(atlas_hits) == 1:
                address = atlas_hits[0]
                previous_constraint = rom_constraints.get(address)
                if previous_constraint is not None and previous_constraint[0] != desired:
                    raise RuntimeError(
                        f"ROM tile {address:06X} constraint conflicts between "
                        f"{previous_constraint[1]} and {name}"
                    )
                rom_constraints[address] = (desired, name)
            if desired == old:
                if len(atlas_hits) != 1:
                    ambiguous_unchanged += 1
                continue
            if len(atlas_hits) != 1:
                raise RuntimeError(
                    f"{name}: changed tile {ram_off:04X} has {len(atlas_hits)} atlas hits "
                    f"({[f'{hit:06X}' for hit in hits]})"
                )
            address = atlas_hits[0]
            previous = rom_patches.get(address)
            if previous is not None and previous[0] != desired:
                raise RuntimeError(
                    f"ROM tile {address:06X} conflicts between {previous[1]} and {name}"
                )
            rom_patches[address] = (desired, name)
            changed_tiles.append(
                {
                    "wsram": f"{ram_off:04X}",
                    "rom": f"{address:06X}",
                    "positions": positions_by_ram[ram_off],
                    "old_sha256": sha256_bytes(old),
                    "new_sha256": sha256_bytes(desired),
                }
            )

        # Produce a load-immediate state for this one focus plate.
        core_out = bytearray(core)
        for ram_off, desired in desired_by_ram.items():
            start = WSRAM_CORE_OFFSET + ram_off
            core_out[start : start + TILE_BYTES] = desired
        state_out = state_dir / f"{name}_clean.State"
        write_state_with_core(state_path, state_out, core_name, bytes(core_out), zstd, 3)
        verify, verify_name = read_state_core(state_out, zstd)
        if verify_name != core_name or verify != bytes(core_out):
            raise RuntimeError(f"{name}: output State round trip failed")
        state_outputs.append(state_out)
        target_reports.append(
            {
                "name": name,
                "japanese": entry["jp"],
                "korean": korean_name,
                "source_state": str(state_path),
                "test_state": str(state_out),
                "test_state_sha256": sha256_file(state_out),
                "bounds_xyxy": bounds,
                "sprite_count": len(sprites),
                "strip_width": strip_width,
                "font_size": font_size,
                "composition": evidence,
                "changed_unique_rom_tiles": len(changed_tiles),
                "ambiguous_unchanged_tiles": ambiguous_unchanged,
                "changed_tiles": changed_tiles,
                "before_preview": str(preview_dir / f"{name}_before.png"),
                "after_preview": str(preview_dir / f"{name}_after.png"),
            }
        )
        print(
            f"{name:18s} sprites={len(sprites):2d} changed={len(changed_tiles):2d} "
            f"bbox={evidence['japanese_bbox_xyxy']} ko={evidence['korean_origin_xy']}"
        )

    candidate = bytearray(base_rom)
    for logical, (raw, _) in rom_patches.items():
        candidate[base + logical : base + logical + TILE_BYTES] = raw
    checksum = update_ws_checksum(candidate)
    rom_out = args.out_dir / "intermission_all_focus_clean.wsc"
    rom_out.write_bytes(candidate)
    reread = rom_out.read_bytes()
    if reread != bytes(candidate):
        raise RuntimeError("candidate ROM did not round trip")
    if (sum(reread[:-2]) & 0xFFFF) != int.from_bytes(reread[-2:], "little"):
        raise RuntimeError("candidate checksum failed")

    changed_bytes = [i for i, (a, b) in enumerate(zip(base_rom, reread)) if a != b]
    allowed = set()
    for logical in rom_patches:
        allowed.update(range(base + logical, base + logical + TILE_BYTES))
    allowed.update((len(reread) - 2, len(reread) - 1))
    outside = [offset for offset in changed_bytes if offset not in allowed]
    if outside:
        raise RuntimeError(f"candidate changed outside patched tiles at {outside[0]:07X}")

    report = {
        "purpose": "all twelve focusable intermission leaf labels, clean Korean plates",
        "base_rom": str(args.base_rom),
        "base_rom_sha256": sha256_bytes(base_rom),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": sha256_file(rom_out),
        "checksum": f"{checksum:04X}",
        "focus_labels": len(target_reports),
        "unique_rom_tiles_patched": len(rom_patches),
        "rom_changed_bytes_including_checksum": len(changed_bytes),
        "targets": target_reports,
    }
    report_path = args.out_dir / "all_focus_clean_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"ROM tiles : {len(rom_patches)}")
    print(f"ROM bytes : {len(changed_bytes)}")
    print(f"checksum  : {checksum:04X}")
    print(f"ROM       : {rom_out}")
    print(f"states    : {len(state_outputs)} in {state_dir}")
    print(f"report    : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
