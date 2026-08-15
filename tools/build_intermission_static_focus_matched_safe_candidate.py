#!/usr/bin/env python3
"""Build a safe default-intermission background matching the focus atlas wording.

The animated focus plates and the static background are separate bank-54 assets.
Seven sparse static tiles are deduplicated across unrelated screen positions, so a
literal pixel-for-pixel copy of all focus labels is impossible without changing the
runtime tilemap.  This builder preserves every shared tile and applies the approved
focus wording with the smallest measured layout adjustments:

* exact focus placement for five labels;
* 1-5 px horizontal or 1-2 px vertical adjustments for six labels;
* ``임무/전황`` is split around its shared blank tile, retaining the focus baseline.

Every changed ROM tile must produce the same 8x8 result at all screen positions that
reference it.  The build aborts on a shared-tile conflict, artwork overwrite, change
outside the declared bank-54 tile allowlist, or SaveRAM mismatch.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_intermission_static_focus_matched_candidate as common  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_intermission_labels_ko import FONT, Rasteriser, draw_strip  # noqa: E402

TILE_BYTES = 0x20

# strip origin, font size.  The focus report remains the authority for wording and
# preferred origins; this table records only the measured static-layer exceptions.
LAYOUTS: dict[str, dict] = {
    "mission_status": {"mode": "split", "size": 13, "origin": [56, 9]},
    "scouting": {"mode": "strip", "size": 11, "origin": [142, 9]},
    "advance": {"mode": "strip", "size": 13, "origin": [176, 9]},
    "supply": {"mode": "strip", "size": 13, "origin": [104, 37]},
    "list": {"mode": "strip", "size": 13, "origin": [144, 37]},
    "assignment": {"mode": "strip", "size": 13, "origin": [185, 37]},
    "development_plan": {"mode": "strip", "size": 13, "origin": [59, 99]},
    "remodel": {"mode": "strip", "size": 13, "origin": [144, 98]},
    "disassemble": {"mode": "strip", "size": 13, "origin": [186, 98]},
    "save": {"mode": "strip", "size": 13, "origin": [88, 122]},
    "load": {"mode": "strip", "size": 13, "origin": [143, 123]},
    "library": {"mode": "strip", "size": 13, "origin": [185, 124]},
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def text_points(
    text: str,
    ras: Rasteriser,
    start_x: int,
    strip_y: int,
    fill: int = 0x0F,
    outline: int = 1,
    spacing: int = 1,
) -> set[tuple[int, int, int]]:
    """Render one uncentred text segment into absolute screen coordinates."""
    glyphs = [ras.bits(ch) for ch in text if ch != " "]
    if not glyphs:
        return set()
    widths = [len(bits[0]) for bits in glyphs]
    gh = len(glyphs[0])
    y0 = strip_y + (16 - gh) // 2 + 1
    ink: set[tuple[int, int]] = set()
    x = start_x
    for bits, width in zip(glyphs, widths):
        for y in range(gh):
            for xx in range(width):
                if bits[y][xx]:
                    ink.add((x + xx, y0 + y))
        x += width + spacing
    ring: set[tuple[int, int]] = set()
    for px, py in ink:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                point = (px + dx, py + dy)
                if point not in ink:
                    ring.add(point)
    points = {(x, y, outline) for x, y in ring}
    points.update((x, y, fill) for x, y in ink)
    return points


def render_target_points(target: dict, plan: dict) -> set[tuple[int, int, int]]:
    ras = Rasteriser(FONT, int(plan["size"]))
    origin = tuple(int(value) for value in plan["origin"])
    if plan["mode"] == "split":
        if target["name"] != "mission_status" or target["korean"] != "임무/전황":
            raise RuntimeError("split composition is only defined for 임무/전황")
        # Tile (11,1), x=88..95, is shared with an unrelated plate edge.  Keep it
        # byte-identical by placing the two readable segments on either side.
        left = text_points("임무", ras, 57, origin[1], spacing=0)
        right = text_points("/전황", ras, 97, origin[1])
        points = left | right
        if any(88 <= x <= 95 and 8 <= y <= 15 for x, y, _ in points):
            raise RuntimeError("mission split touched the protected shared tile")
        return points
    if plan["mode"] != "strip":
        raise RuntimeError(f"unknown layout mode: {plan['mode']}")
    strip = draw_strip(
        target["korean"],
        int(target["strip_width"]),
        ras,
        0x0F,
        1,
        1,
    )
    return {
        (origin[0] + x, origin[1] + y, value)
        for y, row in enumerate(strip)
        for x, value in enumerate(row)
        if value
    }


def bbox(points: set[tuple[int, int, int]]) -> list[int]:
    return [
        min(x for x, _, _ in points),
        min(y for _, y, _ in points),
        max(x for x, _, _ in points) + 1,
        max(y for _, y, _ in points) + 1,
    ]


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
        "--labels",
        type=Path,
        default=ROOT / "data/intermission_labels_ko.json",
    )
    ap.add_argument(
        "--focus-report",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_static_focus_matched_safe",
    )
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args(argv)

    for path in (
        args.stock_rom,
        args.base_rom,
        args.base_sav,
        args.source_state,
        args.zstd_dll,
        args.labels,
        args.focus_report,
    ):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    stock = args.stock_rom.read_bytes()
    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    mapping, mapping_report = common.load_screen_map(
        stock, base_rom, base, args.source_state, args.zstd_dll
    )
    reverse: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for pos, address in mapping.items():
        reverse[address].append(pos)

    current_tiles = {
        address: bytes(base_rom[base + address : base + address + TILE_BYTES])
        for address in set(mapping.values())
    }
    stock_tiles = {
        address: bytes(stock[address : address + TILE_BYTES])
        for address in set(mapping.values())
    }
    position_grids = {
        pos: common.decode_tile(current_tiles[address])
        for pos, address in mapping.items()
    }

    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    entries = {row["jp"]: row for row in labels["labels"]}
    focus = json.loads(args.focus_report.read_text(encoding="utf-8"))
    targets = focus["targets"]
    if {row["name"] for row in targets} != set(LAYOUTS):
        raise RuntimeError("layout table and focus report target sets differ")

    # Restore/clear every exclusive source tile in the old Japanese/static strip.
    # Shared tiles remain byte-identical; the selected layouts never draw into them.
    cleanup_report = []
    original_by_name: dict[str, set[tuple[int, int]]] = {}
    all_original_positions: set[tuple[int, int]] = set()
    for target in targets:
        entry = entries[target["japanese"]]
        original = {
            (col, row)
            for col in range(int(entry["from"]), int(entry["to"]) + 1)
            for row in (int(entry["row"]), int(entry["row"]) + 1)
        }
        original_by_name[target["name"]] = original
        all_original_positions |= original

    for target in targets:
        name = target["name"]
        original = original_by_name[name]
        clearable = []
        protected = []
        for pos in sorted(original, key=lambda p: (p[1], p[0])):
            if pos not in mapping:
                raise RuntimeError(f"{name}: original position is unmapped: {pos}")
            address = mapping[pos]
            exclusive = all(other in original for other in reverse[address])
            if not exclusive:
                protected.append(
                    {
                        "screen": list(pos),
                        "rom": f"{address:06X}",
                        "shared_with": [
                            list(other)
                            for other in sorted(reverse[address], key=lambda p: (p[1], p[0]))
                            if other != pos
                        ],
                    }
                )
                continue
            grid = common.decode_tile(stock_tiles[address])
            for y in range(8):
                for x in range(8):
                    if grid[y][x] in (1, 0x0F):
                        grid[y][x] = 0
            position_grids[pos] = grid
            clearable.append({"screen": list(pos), "rom": f"{address:06X}"})
        cleanup_report.append(
            {
                "name": name,
                "exclusive_tiles_cleared": clearable,
                "shared_tiles_preserved": protected,
            }
        )

    target_reports = []
    owners_by_pos: dict[tuple[int, int], set[str]] = collections.defaultdict(set)
    for target in targets:
        name = target["name"]
        plan = LAYOUTS[name]
        points = render_target_points(target, plan)
        if any(
            not (0 <= x < common.SCREEN_W and 0 <= y < common.SCREEN_H)
            for x, y, _ in points
        ):
            raise RuntimeError(f"{name}: rendered outside the 224x144 screen")
        for x, y, value in sorted(points, key=lambda p: (p[1], p[0], p[2])):
            pos = (x // 8, y // 8)
            if pos not in position_grids:
                raise RuntimeError(f"{name}: draw position is unmapped: {pos}")
            grid = position_grids[pos]
            tx, ty = x % 8, y % 8
            old = grid[ty][tx]
            if old not in (0, 1, 0x0F):
                raise RuntimeError(
                    f"{name}: draw would overwrite plate artwork at ({x},{y}) index={old:X}"
                )
            grid[ty][tx] = value
            owners_by_pos[pos].add(name)

        preferred = [
            int(target["bounds_xyxy"][0])
            + int(target["composition"]["korean_origin_xy"][0]),
            int(target["bounds_xyxy"][1])
            + int(target["composition"]["korean_origin_xy"][1]),
        ]
        selected = list(plan["origin"])
        target_reports.append(
            {
                "name": name,
                "japanese": target["japanese"],
                "korean": target["korean"],
                "static_spec_korean_before": entries[target["japanese"]].get("ko"),
                "mode": plan["mode"],
                "font_size": int(plan["size"]),
                "preferred_focus_strip_origin_xy": preferred,
                "selected_static_strip_origin_xy": selected,
                "origin_delta_xy": [selected[0] - preferred[0], selected[1] - preferred[1]],
                "exact_origin": selected == preferred,
                "absolute_ink_bbox_xyxy": bbox(points),
                "screen_tiles_touched": [
                    list(pos)
                    for pos in sorted(
                        {(x // 8, y // 8) for x, y, _ in points},
                        key=lambda p: (p[1], p[0]),
                    )
                ],
            }
        )

    # A ROM tile may appear at several screen positions.  All independently built
    # position grids must collapse to one byte-identical tile before any write.
    constraints: dict[int, bytes] = {}
    conflict_rows = []
    for address, positions in reverse.items():
        variants: dict[bytes, list[tuple[int, int]]] = collections.defaultdict(list)
        for pos in positions:
            variants[common.encode_tile(position_grids[pos])].append(pos)
        if len(variants) != 1:
            conflict_rows.append(
                {
                    "rom": f"{address:06X}",
                    "variants": [
                        {
                            "sha256": digest(raw),
                            "positions": [list(pos) for pos in group],
                            "owners": sorted(
                                set().union(*(owners_by_pos[pos] for pos in group))
                            ),
                        }
                        for raw, group in variants.items()
                    ],
                }
            )
            continue
        constraints[address] = next(iter(variants))
    if conflict_rows:
        raise RuntimeError(
            "shared static tile conflicts remain: "
            + ", ".join(row["rom"] for row in conflict_rows)
        )

    candidate = bytearray(base_rom)
    changed_tiles = []
    for address, desired in sorted(constraints.items()):
        current = current_tiles[address]
        if desired == current:
            continue
        candidate[base + address : base + address + TILE_BYTES] = desired
        changed_tiles.append(
            {
                "rom": f"{address:06X}",
                "screen_positions": [
                    list(pos)
                    for pos in sorted(reverse[address], key=lambda p: (p[1], p[0]))
                ],
                "owners": sorted(
                    set().union(*(owners_by_pos[pos] for pos in reverse[address]))
                ),
                "old_sha256": digest(current),
                "new_sha256": digest(desired),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    changed_offsets = [
        index
        for index, (old, new) in enumerate(zip(base_rom, candidate_bytes))
        if old != new
    ]
    allowed = set()
    for row in changed_tiles:
        address = int(row["rom"], 16)
        allowed.update(range(base + address, base + address + TILE_BYTES))
    allowed.update((len(candidate_bytes) - 2, len(candidate_bytes) - 1))
    outside = [index for index in changed_offsets if index not in allowed]
    if outside:
        raise RuntimeError(f"change outside allowlist at {outside[0]:07X}")

    # Focus sprite atlas and runtime hooks are separate accepted regions and must not
    # move while this static-background candidate is built.
    focus_unchanged = (
        base_rom[base + 0x542000 : base + 0x544400]
        == candidate_bytes[base + 0x542000 : base + 0x544400]
    )
    hook_unchanged = (
        base_rom[base + 0x7A0600 : base + 0x7A1000]
        == candidate_bytes[base + 0x7A0600 : base + 0x7A1000]
    )
    if not focus_unchanged or not hook_unchanged:
        raise RuntimeError("focus atlas or runtime hook changed")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_out = args.out_dir / "intermission_static_focus_matched_safe_candidate.wsc"
    sav_out = ROOT / "sram/intermission_static_focus_matched_safe_candidate.sav"
    rom_out.write_bytes(candidate_bytes)
    shutil.copy2(args.base_sav, sav_out)
    if sav_out.read_bytes() != args.base_sav.read_bytes():
        raise RuntimeError("candidate SaveRAM does not match the live main SaveRAM")

    preview_dir = args.out_dir / "previews"
    common.render_screen(
        base_rom,
        base,
        mapping,
        preview_dir / "before_static_overlay.png",
        args.scale,
    )
    common.render_screen(
        candidate_bytes,
        base,
        mapping,
        preview_dir / "after_static_overlay.png",
        args.scale,
    )

    tile_list = {
        "_note": "Stock-relative static intermission tiles changed by the safe focus-matched candidate.",
        "tile_bytes": TILE_BYTES,
        "tiles": [row["rom"] for row in changed_tiles],
    }
    tile_list_path = args.out_dir / "static_focus_matched_safe_tiles.json"
    tile_list_path.write_text(json.dumps(tile_list, indent=2) + "\n", encoding="utf-8")

    report = {
        "purpose": "default intermission background wording matched to the approved focus atlas with shared-tile-safe minimal layout adjustments",
        "base_rom": str(args.base_rom),
        "base_rom_sha256": digest(base_rom),
        "base_sav": str(args.base_sav),
        "base_sav_sha256": file_digest(args.base_sav),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": file_digest(rom_out),
        "candidate_sav": str(sav_out),
        "candidate_sav_sha256": file_digest(sav_out),
        "candidate_sav_matches_live": True,
        "checksum": f"{checksum:04X}",
        "screen_map": mapping_report,
        "targets": target_reports,
        "cleanup": cleanup_report,
        "changed_unique_rom_tiles": len(changed_tiles),
        "changed_rom_bytes_including_checksum": len(changed_offsets),
        "changed_tiles": changed_tiles,
        "shared_tile_conflicts": conflict_rows,
        "verification": {
            "all_12_focus_wordings_used": len(target_reports) == 12,
            "all_shared_tiles_byte_consistent": not conflict_rows,
            "focus_sprite_atlas_unchanged": focus_unchanged,
            "runtime_hook_region_unchanged": hook_unchanged,
            "changes_bounded_to_tile_allowlist_and_checksum": not outside,
            "candidate_saveram_matches_live": True,
            "main_tip_not_modified_by_builder": args.base_rom.read_bytes() == base_rom,
        },
        "previews": {
            "before": str(preview_dir / "before_static_overlay.png"),
            "after": str(preview_dir / "after_static_overlay.png"),
        },
        "tile_allowlist": str(tile_list_path),
    }
    report_path = args.out_dir / "static_focus_matched_safe_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"targets       : {len(target_reports)}")
    print(f"exact origins : {sum(row['exact_origin'] for row in target_reports)}/12")
    print(f"changed tiles : {len(changed_tiles)}")
    print(f"changed bytes : {len(changed_offsets)}")
    print(f"checksum      : {checksum:04X}")
    print(f"candidate     : {rom_out}")
    print(f"SaveRAM       : {sav_out}")
    print(f"report        : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
