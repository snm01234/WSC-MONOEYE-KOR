#!/usr/bin/env python3
"""Build a test ROM whose static intermission labels match the focus atlas wording.

The static background and the animated focus plates are separate bank-54 assets.
This builder keeps the four static group headings, but rebuilds the twelve leaf
labels from the stock overlay and renders the exact Korean wording approved by the
focus-atlas report.  The focus report's on-screen strip origin is the preferred
placement.  Because the static overlay deduplicates several tiles, every candidate
placement is checked as a global ROM-tile constraint; the nearest compatible origin
is selected if the exact focus origin would alter an unrelated screen position.
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

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_intermission_labels_ko import FONT, Rasteriser, draw_strip  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402
from resolve_tilemap import resolve_row  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
)

TILE_BYTES = 0x20
SCREEN_W = 224
SCREEN_H = 144
TILE_W = 8

TILEMAP_BASE = 0x3800

# The live WSRAM tilemap resolves nearly every screen tile through exact ROM-byte
# matches plus contiguous row runs.  These five cells are genuine deduplicated or
# isolated exceptions in the leaf-label bands; their live tile bytes identify the
# addresses below exactly.  In particular (11, 1) is 0x544840, not the old
# screenshot resolver's incorrect shared-tile guess 0x544A40.
STATE_MAP_OVERRIDES: dict[tuple[int, int], int] = {
    (11, 1): 0x544840,
    (12, 6): 0x544500,
    (14, 17): 0x547180,
    (19, 17): 0x5472A0,
    (20, 17): 0x547300,
    (21, 17): 0x5472A0,
}


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
    out = bytearray()
    for row in grid:
        for x in range(0, 8, 2):
            out.append(((row[x] & 0x0F) << 4) | (row[x + 1] & 0x0F))
    return bytes(out)


def load_screen_map(
    stock: bytes,
    base_rom: bytes,
    base: int,
    state_path: Path,
    zstd_path: Path,
) -> tuple[dict[tuple[int, int], int], dict]:
    """Recover the actual static-layer screen tilemap from serialized WSRAM.

    The earlier screenshot-only resolver treated transparent overlay pixels as
    unconstrained and consequently assigned a few shared blank/sliver tiles to the
    wrong screen cells.  The state contains both the 32x32 tilemap and the uploaded
    raw graphics, so each screen cell can instead be matched against stock and the
    current TIP byte-for-byte.  Row continuity resolves duplicates; unique hits and
    the small asserted exception table close the remaining label-band gaps.
    """
    zstd = Zstd(zstd_path)
    core, core_name = read_state_core(state_path, zstd)
    ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    current_body = base_rom[base : base + len(stock)]

    raw_to_addresses: dict[bytes, set[int]] = collections.defaultdict(set)
    for address in range(0x540000, 0x550000, TILE_BYTES):
        raw_to_addresses[bytes(stock[address : address + TILE_BYTES])].add(address)
        raw_to_addresses[
            bytes(current_body[address : address + TILE_BYTES])
        ].add(address)

    candidates_by_row: dict[int, dict[int, list[int]]] = collections.defaultdict(dict)
    live_by_pos: dict[tuple[int, int], bytes] = {}
    entry_by_pos: dict[tuple[int, int], int] = {}
    for row in range(SCREEN_H // TILE_W):
        for col in range(SCREEN_W // TILE_W):
            map_off = TILEMAP_BASE + (row * 32 + col) * 2
            entry = int.from_bytes(ram[map_off : map_off + 2], "little")
            tile = entry & 0x01FF
            gfx_base = 0x8000 if entry & 0x2000 else 0x4000
            gfx_off = gfx_base + tile * TILE_BYTES
            raw = bytes(ram[gfx_off : gfx_off + TILE_BYTES])
            pos = (col, row)
            live_by_pos[pos] = raw
            entry_by_pos[pos] = entry
            hits = sorted(raw_to_addresses.get(raw, ()))
            if hits:
                candidates_by_row[row][col] = hits

    mapping: dict[tuple[int, int], int] = {}
    row_reports = []
    for row in range(SCREEN_H // TILE_W):
        candidates = candidates_by_row.get(row, {})
        resolved, runs = resolve_row(candidates, SCREEN_W // TILE_W)
        for col, address in resolved.items():
            mapping[(col, row)] = address
        unique_added = []
        for col, hits in candidates.items():
            if (col, row) not in mapping and len(hits) == 1:
                mapping[(col, row)] = hits[0]
                unique_added.append(col)
        row_reports.append(
            {
                "row": row,
                "candidate_cells": len(candidates),
                "resolved_cells": sum(1 for col in range(28) if (col, row) in mapping),
                "runs": runs,
                "unique_cells_added": unique_added,
            }
        )

    for pos, address in STATE_MAP_OVERRIDES.items():
        live = live_by_pos[pos]
        if live not in (
            bytes(stock[address : address + TILE_BYTES]),
            bytes(current_body[address : address + TILE_BYTES]),
        ):
            raise RuntimeError(
                f"state-map override {pos} does not match live bytes at {address:06X}"
            )
        previous = mapping.get(pos)
        if previous is not None and previous != address:
            raise RuntimeError(
                f"state-map override {pos} conflicts: {previous:06X} != {address:06X}"
            )
        mapping[pos] = address

    return mapping, {
        "source_state": str(state_path),
        "core_member": core_name,
        "tilemap_wsram": f"{TILEMAP_BASE:04X}-{TILEMAP_BASE + 0x800 - 1:04X}",
        "resolved_screen_cells": len(mapping),
        "state_map_overrides": {
            f"{col},{row}": f"{address:06X}"
            for (col, row), address in sorted(
                STATE_MAP_OVERRIDES.items(), key=lambda item: (item[0][1], item[0][0])
            )
        },
        "rows": row_reports,
        "entries": {
            f"{col},{row}": f"{entry:04X}"
            for (col, row), entry in sorted(
                entry_by_pos.items(), key=lambda item: (item[0][1], item[0][0])
            )
            if (col, row) in mapping
        },
    }


def points_bbox(points: set[tuple[int, int]]) -> list[int]:
    return [
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points) + 1,
        max(y for _, y in points) + 1,
    ]


def candidate_origins(preferred: tuple[int, int], radius: int) -> list[tuple[int, int]]:
    px, py = preferred
    offsets = [
        (dx, dy)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
    ]
    offsets.sort(key=lambda p: (abs(p[0]) + abs(p[1]), max(abs(p[0]), abs(p[1])), abs(p[1]), abs(p[0]), p[1], p[0]))
    return [(px + dx, py + dy) for dx, dy in offsets]


def tile_positions_for_points(points: set[tuple[int, int]]) -> set[tuple[int, int]]:
    return {(x // 8, y // 8) for x, y in points}


def render_screen(
    rom: bytes,
    base: int,
    mapping: dict[tuple[int, int], int],
    path: Path,
    scale: int,
) -> None:
    image = Image.new("RGB", (SCREEN_W, SCREEN_H), (255, 0, 255))
    pixels = image.load()
    for (col, row), address in mapping.items():
        if not (0 <= col < SCREEN_W // 8 and 0 <= row < SCREEN_H // 8):
            continue
        tile = decode_tile(rom[base + address : base + address + TILE_BYTES])
        for y in range(8):
            for x in range(8):
                pixels[col * 8 + x, row * 8 + y] = GREYS_16[tile[y][x]]
    if scale > 1:
        image = image.resize((SCREEN_W * scale, SCREEN_H * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


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
        "--labels",
        type=Path,
        default=ROOT / "data/intermission_labels_ko.json",
    )
    ap.add_argument(
        "--source-state",
        type=Path,
        default=(
            ROOT
            / "BizHawk-2.11.1-win-x64/WonderSwan/State"
            / "monoeye ko expanded.Cygne/Mednafen.QuickSave1.State"
        ),
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--focus-report",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_static_focus_matched",
    )
    ap.add_argument("--search-radius", type=int, default=8)
    ap.add_argument("--max-ring-ink", type=int, default=64)
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args(argv)

    for path in (
        args.stock_rom,
        args.base_rom,
        args.base_sav,
        args.labels,
        args.source_state,
        args.zstd_dll,
        args.focus_report,
    ):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    stock = args.stock_rom.read_bytes()
    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    if len(stock) != 0x800000:
        raise SystemExit(f"stock ROM size is {len(stock):#x}, expected 0x800000")

    mapping, mapping_report = load_screen_map(
        stock, base_rom, base, args.source_state, args.zstd_dll
    )
    reverse: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for pos, address in mapping.items():
        reverse[address].append(pos)

    labels_data = json.loads(args.labels.read_text(encoding="utf-8"))
    entries_by_jp = {entry["jp"]: entry for entry in labels_data["labels"]}
    focus = json.loads(args.focus_report.read_text(encoding="utf-8"))
    targets = focus["targets"]
    if len(targets) != 12:
        raise RuntimeError(f"focus report has {len(targets)} targets, expected 12")

    ras = Rasteriser(FONT, 13)
    current_by_address = {
        address: bytes(base_rom[base + address : base + address + TILE_BYTES])
        for address in set(mapping.values())
    }
    stock_by_address = {
        address: bytes(stock[address : address + TILE_BYTES])
        for address in set(mapping.values())
    }

    global_constraints: dict[int, bytes] = {}
    global_owners: dict[int, str] = {}
    label_reports: list[dict] = []

    for target in targets:
        name = target["name"]
        jp = target["japanese"]
        text = target["korean"]
        entry = entries_by_jp.get(jp)
        if entry is None:
            raise RuntimeError(f"no static label entry for {jp}")
        width = int(target["strip_width"])
        preferred = (
            int(target["bounds_xyxy"][0])
            + int(target["composition"]["korean_origin_xy"][0]),
            int(target["bounds_xyxy"][1])
            + int(target["composition"]["korean_origin_xy"][1]),
        )
        strip = draw_strip(text, width, ras, 0x0F, 1, 1)
        local_points = {
            (x, y, value)
            for y, row in enumerate(strip)
            for x, value in enumerate(row)
            if value
        }
        local_xy = {(x, y) for x, y, _ in local_points}
        local_box = points_bbox(local_xy)

        original_positions = {
            (col, row)
            for col in range(int(entry["from"]), int(entry["to"]) + 1)
            for row in (int(entry["row"]), int(entry["row"]) + 1)
        }
        missing_original = sorted(pos for pos in original_positions if pos not in mapping)
        if missing_original:
            raise RuntimeError(f"{name}: unmapped original positions {missing_original}")

        ring_positions: set[tuple[int, int]] = set()
        for col in range(int(entry["from"]) - 1, int(entry["to"]) + 2):
            for row in range(int(entry["row"]) - 1, int(entry["row"]) + 3):
                pos = (col, row)
                if pos in original_positions or pos not in mapping:
                    continue
                raw = stock_by_address[mapping[pos]]
                tile = decode_tile(raw)
                ink = [value for line in tile for value in line if value]
                if ink and set(ink) <= {1, 0x0F} and len(ink) <= args.max_ring_ink:
                    ring_positions.add(pos)

        failures: collections.Counter[str] = collections.Counter()
        chosen = None
        for origin in candidate_origins(preferred, args.search_radius):
            absolute = {(origin[0] + x, origin[1] + y, value) for x, y, value in local_points}
            if any(not (0 <= x < SCREEN_W and 0 <= y < SCREEN_H) for x, y, _ in absolute):
                failures["outside_screen"] += 1
                continue
            draw_positions = tile_positions_for_points({(x, y) for x, y, _ in absolute})
            affected_positions = original_positions | ring_positions | draw_positions
            if any(pos not in mapping for pos in affected_positions):
                failures["unmapped_draw_tile"] += 1
                continue

            grids: dict[tuple[int, int], list[list[int]]] = {}
            for pos in affected_positions:
                address = mapping[pos]
                raw = stock_by_address[address] if pos in original_positions else current_by_address[address]
                grids[pos] = decode_tile(raw)

            # Restore the original strip from stock and remove its F/1 label ink.
            for pos in original_positions:
                tile = grids[pos]
                for y in range(8):
                    for x in range(8):
                        if tile[y][x] in (1, 0x0F):
                            tile[y][x] = 0

            # Keep the previous strict ring policy: only a pure, sparse label-ink
            # tile is cleared, so plate artwork using other palette indices survives.
            for pos in ring_positions:
                tile = grids[pos]
                for y in range(8):
                    for x in range(8):
                        if tile[y][x] in (1, 0x0F):
                            tile[y][x] = 0

            foreign = False
            for x, y, value in absolute:
                pos = (x // 8, y // 8)
                tile = grids[pos]
                tx, ty = x % 8, y % 8
                if tile[ty][tx] not in (0, 1, 0x0F):
                    foreign = True
                    break
                tile[ty][tx] = value
            if foreign:
                failures["draw_hits_artwork"] += 1
                continue

            per_address: dict[int, set[bytes]] = collections.defaultdict(set)
            positions_by_address: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
            for pos, grid in grids.items():
                address = mapping[pos]
                per_address[address].add(encode_tile(grid))
                positions_by_address[address].append(pos)
            if any(len(values) != 1 for values in per_address.values()):
                failures["shared_tile_internal_conflict"] += 1
                continue

            constraints = {address: next(iter(values)) for address, values in per_address.items()}
            outside_conflict = False
            for address, desired in constraints.items():
                if desired == current_by_address[address]:
                    continue
                outside = [pos for pos in reverse[address] if pos not in affected_positions]
                if outside:
                    outside_conflict = True
                    break
            if outside_conflict:
                failures["shared_tile_outside_target"] += 1
                continue

            global_conflict = False
            for address, desired in constraints.items():
                previous = global_constraints.get(address)
                if previous is not None and previous != desired:
                    global_conflict = True
                    break
            if global_conflict:
                failures["cross_label_tile_conflict"] += 1
                continue

            chosen = {
                "origin": origin,
                "affected_positions": affected_positions,
                "ring_positions": ring_positions,
                "draw_positions": draw_positions,
                "constraints": constraints,
                "positions_by_address": positions_by_address,
                "absolute_points": absolute,
            }
            break

        if chosen is None:
            raise RuntimeError(
                f"{name}: no compatible static placement within radius {args.search_radius}; "
                f"failures={dict(failures)}"
            )

        changed_tiles = []
        for address, desired in chosen["constraints"].items():
            previous = global_constraints.get(address)
            if previous is None:
                global_constraints[address] = desired
                global_owners[address] = name
            if desired != current_by_address[address]:
                changed_tiles.append(
                    {
                        "rom": f"{address:06X}",
                        "screen_positions": [list(pos) for pos in sorted(chosen["positions_by_address"][address], key=lambda p: (p[1], p[0]))],
                        "old_sha256": sha256_bytes(current_by_address[address]),
                        "new_sha256": sha256_bytes(desired),
                    }
                )

        chosen_origin = chosen["origin"]
        label_reports.append(
            {
                "name": name,
                "japanese": jp,
                "korean": text,
                "static_spec_korean": entry.get("ko"),
                "preferred_focus_strip_origin_xy": list(preferred),
                "selected_static_strip_origin_xy": list(chosen_origin),
                "origin_delta_xy": [chosen_origin[0] - preferred[0], chosen_origin[1] - preferred[1]],
                "exact_focus_origin": chosen_origin == preferred,
                "local_ink_bbox_xyxy": local_box,
                "absolute_ink_bbox_xyxy": [
                    chosen_origin[0] + local_box[0],
                    chosen_origin[1] + local_box[1],
                    chosen_origin[0] + local_box[2],
                    chosen_origin[1] + local_box[3],
                ],
                "original_static_tile_rect": {
                    "cols": [int(entry["from"]), int(entry["to"])],
                    "rows": [int(entry["row"]), int(entry["row"]) + 1],
                },
                "affected_screen_tiles": len(chosen["affected_positions"]),
                "ring_cleanup_tiles": [list(pos) for pos in sorted(chosen["ring_positions"], key=lambda p: (p[1], p[0]))],
                "changed_unique_rom_tiles": len(changed_tiles),
                "changed_tiles": changed_tiles,
                "search_failures_before_selection": dict(failures),
            }
        )
        print(
            f"{name:18s} preferred={preferred} selected={chosen_origin} "
            f"delta=({chosen_origin[0]-preferred[0]:+d},{chosen_origin[1]-preferred[1]:+d}) "
            f"changed={len(changed_tiles):2d}"
        )

    candidate = bytearray(base_rom)
    changed_addresses = []
    for address, desired in sorted(global_constraints.items()):
        if desired == current_by_address[address]:
            continue
        candidate[base + address : base + address + TILE_BYTES] = desired
        changed_addresses.append(address)
    checksum = update_ws_checksum(candidate)

    changed_bytes = [index for index, (old, new) in enumerate(zip(base_rom, candidate)) if old != new]
    allowed = set()
    for address in changed_addresses:
        allowed.update(range(base + address, base + address + TILE_BYTES))
    allowed.update((len(candidate) - 2, len(candidate) - 1))
    outside = [index for index in changed_bytes if index not in allowed]
    if outside:
        raise RuntimeError(f"candidate changed outside approved tiles at {outside[0]:07X}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_out = args.out_dir / "intermission_static_focus_matched_candidate.wsc"
    sav_out = ROOT / "sram/intermission_static_focus_matched_candidate.sav"
    rom_out.write_bytes(bytes(candidate))
    shutil.copy2(args.base_sav, sav_out)

    preview_dir = args.out_dir / "previews"
    render_screen(base_rom, base, mapping, preview_dir / "current_static_overlay.png", args.scale)
    render_screen(bytes(candidate), base, mapping, preview_dir / "focus_matched_static_overlay.png", args.scale)

    report = {
        "purpose": "static intermission leaf labels matched to the approved focus-atlas wording and placement constraints",
        "base_rom": str(args.base_rom),
        "base_rom_sha256": sha256_bytes(base_rom),
        "base_sav": str(args.base_sav),
        "base_sav_sha256": sha256_file(args.base_sav),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": sha256_file(rom_out),
        "candidate_sav": str(sav_out),
        "candidate_sav_sha256": sha256_file(sav_out),
        "candidate_sav_matches_current": sav_out.read_bytes() == args.base_sav.read_bytes(),
        "checksum": f"{checksum:04X}",
        "labels": len(label_reports),
        "exact_focus_origins": sum(1 for row in label_reports if row["exact_focus_origin"]),
        "unique_rom_tiles_changed": len(changed_addresses),
        "rom_changed_bytes_including_checksum": len(changed_bytes),
        "screen_map": mapping_report,
        "targets": label_reports,
        "previews": {
            "before": str(preview_dir / "current_static_overlay.png"),
            "after": str(preview_dir / "focus_matched_static_overlay.png"),
        },
    }
    report_path = args.out_dir / "static_focus_matched_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    allowlist_path = args.out_dir / "static_focus_matched_tiles.json"
    allowlist_path.write_text(
        json.dumps(
            {
                "_note": "Stock-relative 32-byte static intermission overlay tiles changed by build_intermission_static_focus_matched_candidate.py.",
                "tile_bytes": TILE_BYTES,
                "tiles": [f"{address:06X}" for address in changed_addresses],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"exact origins : {report['exact_focus_origins']}/{report['labels']}")
    print(f"ROM tiles     : {len(changed_addresses)}")
    print(f"ROM bytes     : {len(changed_bytes)}")
    print(f"checksum      : {checksum:04X}")
    print(f"candidate     : {rom_out}")
    print(f"SaveRAM       : {sav_out}")
    print(f"report        : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
