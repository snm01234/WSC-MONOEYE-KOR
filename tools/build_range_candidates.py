#!/usr/bin/env python3
"""
Compose bisection ROMs: a working base plus the tip's bytes over chosen ranges.

READ-ONLY with respect to base and tip; candidates go to ``out/patch/ab/``.

Bisection state so far for the new-game event error (257 = ``0x0101``,
2049 = ``0x0801``):

* ``pre_ext3`` boots; the tip and every build since the ext3 session fails.
* ``e1_hook`` (hook + expansion payload, no script references) boots, so the hook
  and the ext3 dictionaries are not the fault.
* ``e2_dict5f`` boots; ``g2_61_62`` boots; ``g4_66_69`` boots; ``g3_63_65`` fails.
  → a record rewrite inside banks ``63``-``65`` breaks new game.
* Reverting every record flagged by ``looks_like_event_body`` does not help, so the
  offending record is indistinguishable from prose to that heuristic and has to be
  found by address.

Ranges are given as logical ``LO-HI`` (inclusive) and are copied whole from the
tip onto the base, so record boundaries do not matter as long as the range covers
them. Use ``--split N`` to cut each range into N equal candidates for a
binary-search round.

Example — isolate one bank per candidate::

    python tools/build_range_candidates.py --base out/patch/ab/e2_dict5f.wsc \\
        --range 630000-63FFFF --range 640000-64FFFF --range 650000-65FFFF
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

AB = ROOT / "out/patch/ab"
DEFAULT_BASE = AB / "e2_dict5f.wsc"
DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/range_candidates.json"


def parse_range(text: str) -> Tuple[int, int]:
    lo_s, _, hi_s = text.partition("-")
    if not hi_s:
        raise SystemExit(f"range must be LO-HI, got {text!r}")
    lo, hi = int(lo_s, 16), int(hi_s, 16)
    if hi < lo:
        raise SystemExit(f"range {text!r} is inverted")
    return lo, hi


def split_range(lo: int, hi: int, parts: int) -> List[Tuple[int, int]]:
    if parts <= 1:
        return [(lo, hi)]
    span = hi - lo + 1
    step = (span + parts - 1) // parts
    out: List[Tuple[int, int]] = []
    cur = lo
    while cur <= hi:
        end = min(cur + step - 1, hi)
        out.append((cur, end))
        cur = end + 1
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=DEFAULT_BASE, help="a ROM known to boot")
    ap.add_argument("--tip", type=Path, default=DEFAULT_TIP)
    ap.add_argument(
        "--range",
        action="append",
        required=True,
        metavar="LO-HI",
        help="logical range copied from the tip (hex, inclusive). Repeatable.",
    )
    ap.add_argument("--split", type=int, default=1, help="cut each range into N parts")
    ap.add_argument("--prefix", default="r", help="candidate filename prefix")
    ap.add_argument(
        "--cumulative",
        action="store_true",
        help="each candidate also includes all previous ranges",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    for p in (args.base, args.tip):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")
    base = bytes(load_rom(args.base))
    tip = bytes(load_rom(args.tip))
    if len(base) != len(tip):
        raise SystemExit("base and tip must be the same size")
    sb, stp = stock_base(base), stock_base(tip)

    ranges: List[Tuple[int, int]] = []
    for spec in args.range:
        ranges.extend(split_range(*parse_range(spec), args.split))

    AB.mkdir(parents=True, exist_ok=True)
    cands: List[dict] = []
    applied: List[Tuple[int, int]] = []
    for i, (lo, hi) in enumerate(ranges, start=1):
        rom = bytearray(base)
        applied = applied + [(lo, hi)] if args.cumulative else [(lo, hi)]
        for a_lo, a_hi in applied:
            rom[sb + a_lo : sb + a_hi + 1] = tip[stp + a_lo : stp + a_hi + 1]
        cs = update_ws_checksum(rom)
        name = f"{args.prefix}{i}_{lo:06X}_{hi:06X}"
        dest = AB / f"{name}.wsc"
        dest.write_bytes(rom)
        cands.append(
            {
                "name": name,
                "path": str(dest),
                "ranges": [f"{a:06X}-{b:06X}" for a, b in applied],
                "checksum": f"{cs:04X}",
            }
        )

    report = {
        "generated_by": "tools/build_range_candidates.py",
        "base": {
            "path": str(args.base),
            "checksum": f"{ws_header(base)['checksum']:04X}",
        },
        "tip": {"path": str(args.tip), "checksum": f"{ws_header(tip)['checksum']:04X}"},
        "cumulative": bool(args.cumulative),
        "candidates": cands,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"base {args.base.name} ({report['base']['checksum']})")
    for c in cands:
        print(f"  {c['name']:24s} checksum {c['checksum']}  {', '.join(c['ranges'])}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
