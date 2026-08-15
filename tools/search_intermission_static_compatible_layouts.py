#!/usr/bin/env python3
"""Search safe static-label layouts near the approved focus-atlas positions.

The static intermission overlay deduplicates a handful of sparse edge tiles.  A
literal copy of the focus sprite pixels therefore asks one ROM tile to contain two
different images.  This diagnostic searches tiny origin/font-size adjustments while
keeping the approved Korean wording, and reports layouts that are compatible with
all screen positions sharing each ROM tile.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import build_intermission_static_focus_matched_candidate as build

ROOT = Path(__file__).resolve().parents[1]


def candidate_origins(preferred: tuple[int, int], radius: int):
    px, py = preferred
    offsets = [
        (dx, dy)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
    ]
    offsets.sort(
        key=lambda p: (
            abs(p[0]) + abs(p[1]),
            max(abs(p[0]), abs(p[1])),
            abs(p[1]),
            abs(p[0]),
            p[1],
            p[0],
        )
    )
    for dx, dy in offsets:
        yield px + dx, py + dy


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--radius", type=int, default=12)
    ap.add_argument("--sizes", default="13,12,11,10")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args(argv)
    sizes = [int(value) for value in args.sizes.split(",")]

    stock_path = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
    main_path = ROOT / "out/patch/monoeye_ko_expanded.wsc"
    stock = stock_path.read_bytes()
    main = main_path.read_bytes()
    base = build.stock_base(main)
    state = (
        ROOT
        / "BizHawk-2.11.1-win-x64/WonderSwan/State"
        / "monoeye ko expanded.Cygne/Mednafen.QuickSave1.State"
    )
    zstd = ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll"
    mapping, _ = build.load_screen_map(stock, main, base, state, zstd)
    reverse: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for pos, address in mapping.items():
        reverse[address].append(pos)

    stock_tiles = {
        address: stock[address : address + build.TILE_BYTES]
        for address in set(mapping.values())
    }
    current_tiles = {
        address: main[base + address : base + address + build.TILE_BYTES]
        for address in set(mapping.values())
    }
    labels = json.loads(
        (ROOT / "data/intermission_labels_ko.json").read_text(encoding="utf-8")
    )
    by_jp = {row["jp"]: row for row in labels["labels"]}
    focus = json.loads(
        (
            ROOT
            / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json"
        ).read_text(encoding="utf-8")
    )

    rasterisers = {size: build.Rasteriser(build.FONT, size) for size in sizes}
    report = {"radius": args.radius, "sizes": sizes, "targets": []}
    for target in focus["targets"]:
        entry = by_jp[target["japanese"]]
        preferred = (
            target["bounds_xyxy"][0]
            + target["composition"]["korean_origin_xy"][0],
            target["bounds_xyxy"][1]
            + target["composition"]["korean_origin_xy"][1],
        )
        original = {
            (col, row)
            for col in range(entry["from"], entry["to"] + 1)
            for row in (entry["row"], entry["row"] + 1)
        }
        options = []
        failures: collections.Counter[str] = collections.Counter()
        for size in sizes:
            strip = build.draw_strip(
                target["korean"],
                target["strip_width"],
                rasterisers[size],
                0x0F,
                1,
                1,
            )
            local_points = [
                (x, y, value)
                for y, row in enumerate(strip)
                for x, value in enumerate(row)
                if value
            ]
            for origin in candidate_origins(preferred, args.radius):
                absolute = [
                    (origin[0] + x, origin[1] + y, value)
                    for x, y, value in local_points
                ]
                if any(
                    not (0 <= x < build.SCREEN_W and 0 <= y < build.SCREEN_H)
                    for x, y, _ in absolute
                ):
                    failures["outside_screen"] += 1
                    continue
                draw_positions = {
                    (x // 8, y // 8) for x, y, _ in absolute
                }
                affected = original | draw_positions
                if any(pos not in mapping for pos in affected):
                    failures["unmapped"] += 1
                    continue

                grids = {}
                clearable_original = set()
                for pos in affected:
                    address = mapping[pos]
                    exclusive = all(other in original for other in reverse[address])
                    if pos in original and exclusive:
                        raw = stock_tiles[address]
                        clearable_original.add(pos)
                    else:
                        raw = current_tiles[address]
                    grids[pos] = build.decode_tile(raw)
                for pos in clearable_original:
                    for y in range(8):
                        for x in range(8):
                            if grids[pos][y][x] in (1, 0x0F):
                                grids[pos][y][x] = 0

                artwork = False
                for x, y, value in absolute:
                    pos = (x // 8, y // 8)
                    tx, ty = x % 8, y % 8
                    if grids[pos][ty][tx] not in (0, 1, 0x0F):
                        artwork = True
                        break
                    grids[pos][ty][tx] = value
                if artwork:
                    failures["artwork"] += 1
                    continue

                per_address: dict[int, set[bytes]] = collections.defaultdict(set)
                for pos, grid in grids.items():
                    per_address[mapping[pos]].add(build.encode_tile(grid))
                if any(len(values) != 1 for values in per_address.values()):
                    failures["internal_shared"] += 1
                    continue
                constraints = {
                    address: next(iter(values)) for address, values in per_address.items()
                }
                outside = False
                for address, desired in constraints.items():
                    if desired == current_tiles[address]:
                        continue
                    if any(pos not in affected for pos in reverse[address]):
                        outside = True
                        break
                if outside:
                    failures["outside_shared"] += 1
                    continue

                ink = [(x, y) for x, y, _ in absolute]
                bbox = [
                    min(x for x, _ in ink),
                    min(y for _, y in ink),
                    max(x for x, _ in ink) + 1,
                    max(y for _, y in ink) + 1,
                ]
                options.append(
                    {
                        "size": size,
                        "origin": list(origin),
                        "delta": [origin[0] - preferred[0], origin[1] - preferred[1]],
                        "bbox": bbox,
                        "changed_tiles": sum(
                            desired != current_tiles[address]
                            for address, desired in constraints.items()
                        ),
                    }
                )
                if len(options) >= args.limit:
                    break
            if len(options) >= args.limit:
                break

        row = {
            "name": target["name"],
            "korean": target["korean"],
            "preferred": list(preferred),
            "options": options,
            "failures": dict(failures),
        }
        report["targets"].append(row)
        best = options[0] if options else None
        print(
            f"{target['name']:18s} preferred={preferred} "
            f"best={best if best else 'NONE'}"
        )

    out = ROOT / "out/patch/intermission_static_focus_matched/layout_search.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
