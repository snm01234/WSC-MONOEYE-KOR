#!/usr/bin/env python3
r"""
Turn an ambiguous block -> ROM tile map into a resolved screen tilemap.

``find_screen_tile_in_rom.py --match overlay`` returns 2-4 candidates per block:
a glyph tile constrains only its non-zero pixels, so unrelated tiles with the same
ink structure also match. The ambiguity resolves on one observation -- the game
uploads a screen row's tiles as a contiguous run, so within a row the true offsets
satisfy ``offset = base + col*0x20``. Voting on ``hit - col*0x20`` per row picks the
base, and any block whose candidates include ``base + col*0x20`` is then settled.

Runs are also allowed to switch base mid-row: the atlas is deduplicated per row, so
a row can be served by more than one contiguous block. Each maximal run is reported
with its base so the switch points are visible instead of silently interpolated.

Output: JSON with, per screen row, the resolved offset per column plus the runs.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

TILE = 0x20


def resolve_row(cands: dict[int, list[int]], cols: int) -> tuple[dict[int, int], list[dict]]:
    """cands: col -> candidate offsets. Returns (col -> offset, runs)."""
    resolved: dict[int, int] = {}
    remaining = dict(cands)
    runs: list[dict] = []
    while remaining:
        votes: collections.Counter = collections.Counter()
        for c, offs in remaining.items():
            for o in offs:
                votes[o - c * TILE] += 1
        base, n = votes.most_common(1)[0]
        if n < 2:
            break
        taken = {}
        for c, offs in list(remaining.items()):
            want = base + c * TILE
            if want in offs:
                taken[c] = want
                del remaining[c]
        if not taken:
            break
        resolved.update(taken)
        ks = sorted(taken)
        runs.append(
            {
                "base": f"{base:06X}",
                "cols": [ks[0], ks[-1]],
                "n": len(ks),
                "contiguous": ks == list(range(ks[0], ks[-1] + 1)),
            }
        )
    return resolved, runs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map", type=Path, help="JSON from find_screen_tile_in_rom.py")
    ap.add_argument("--cols", type=int, default=28)
    ap.add_argument("--rows", type=int, default=18)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    data = json.loads(args.map.read_text(encoding="utf-8"))
    by_row: dict[int, dict[int, list[int]]] = collections.defaultdict(dict)
    for b in data["blocks"]:
        by_row[b["row"]][b["col"]] = [int(h, 16) for h in b["hits"]]

    out = {"source": str(args.map), "rows": {}}
    total = 0
    print("row  resolved/candidates   runs (base, cols)")
    for r in range(args.rows):
        cands = by_row.get(r, {})
        if not cands:
            continue
        resolved, runs = resolve_row(cands, args.cols)
        total += len(resolved)
        out["rows"][str(r)] = {
            "resolved": {str(c): f"{o:06X}" for c, o in sorted(resolved.items())},
            "runs": runs,
            "unresolved_cols": sorted(set(cands) - set(resolved)),
        }
        rs = "  ".join(f"{x['base']}[{x['cols'][0]}-{x['cols'][1]}]{'' if x['contiguous'] else '*'}" for x in runs)
        print(f"{r:3d}  {len(resolved):3d}/{len(cands):3d}          {rs}")
    print(f"\n{total} blocks resolved")

    dest = args.out or args.map.with_name(args.map.stem + "_resolved.json")
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"-> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
