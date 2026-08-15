#!/usr/bin/env python3
"""
Bisect the ext3 session by composing ROMs from ``pre_ext3`` + selected components.

READ-ONLY with respect to the tip; every candidate is written to ``out/patch/ab/``.

Manual testing established the boundary: ``monoeye_ko_expanded.pre_ext3.wsc``
reaches the opening narration, while every later build — including the tip as it
was before this session's repairs — fails new game with an event error
(257 = ``0x0101``, 2049 = ``0x0801``). So the fault was introduced by the ext3
session, not by the stock-invasion repair or the 5F pointer work.

The ext3 session changed five separable things. Each candidate starts from the
working ``pre_ext3`` image and adds them cumulatively, so the first candidate that
fails names the component responsible:

``e1_hook``      only the runtime hook and its payload: the ``7A`` hook sites, the
                 ``7F`` code cave and the whole expansion region (glyph pool,
                 bank10 extension, ext3 phrase banks). No script record references
                 an ext3 token yet, so this tests the hook's pass-through path —
                 the walkers still compare every text unit against the ext3 magic
                 and the font hook still runs on every string.
``e2_dict5f``    e1 plus bank ``5F``: the shared stock dictionary as the ext3
                 session left it.
``e3_script61``  e2 plus the dialogue banks ``61``-``69`` (ext3 tokens outside
                 bank 60).
``e4_script60``  e3 plus bank ``60`` — the bank the new-game/opening flow reads.
``e5_full``      e4 plus the remaining stock banks (``50``-``5D``, ``6A``-``6F``,
                 ``75``), i.e. everything except that the base is pre_ext3.

Components are copied by logical address, so the prepended 8 MiB expansion offset
is handled for both images.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

DEFAULT_BASE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AB = ROOT / "out/patch/ab"
DEFAULT_OUT = ROOT / "out/patch/ext3_bisect.json"

BANK = 0x10000

# Component -> list of (kind, start, end_exclusive).
#   "file"  = absolute file offset (expansion region of a 16 MiB image)
#   "stock" = logical address, resolved through stock_base() per ROM
COMPONENTS: Dict[str, List[Tuple[str, int, int]]] = {
    # Hook code + everything the hook reads out of the expansion region.
    "hook": [
        ("stock", 0x7A0000, 0x7B0000),
        ("stock", 0x7F0000, 0x7FFFF0),  # keep the header out of it
        ("file", 0x000000, 0x800000),
    ],
    "dict5f": [("stock", 0x5F0000, 0x600000)],
    "script61": [("stock", 0x610000, 0x6A0000)],
    "script60": [("stock", 0x600000, 0x610000)],
    "rest": [
        ("stock", 0x500000, 0x5F0000),
        ("stock", 0x6A0000, 0x700000),
        ("stock", 0x750000, 0x760000),
    ],
}

CANDIDATES: List[Tuple[str, List[str], str]] = [
    ("e1_hook", ["hook"], "hook sites 7A + cave 7F + whole expansion region"),
    ("e2_dict5f", ["hook", "dict5f"], "e1 + stock dictionary bank 5F"),
    ("e3_script61", ["hook", "dict5f", "script61"], "e2 + dialogue banks 61-69"),
    (
        "e4_script60",
        ["hook", "dict5f", "script61", "script60"],
        "e3 + bank 60 (the new-game / opening flow)",
    ),
    (
        "e5_full",
        ["hook", "dict5f", "script61", "script60", "rest"],
        "e4 + banks 50-5D, 6A-6F, 75",
    ),
]


def apply_component(
    dst: bytearray, src: bytes, name: str, *, sd: int, ss: int
) -> int:
    copied = 0
    for kind, lo, hi in COMPONENTS[name]:
        if kind == "file":
            dst[lo:hi] = src[lo:hi]
        else:
            dst[sd + lo : sd + hi] = src[ss + lo : ss + hi]
        copied += hi - lo
    return copied


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=DEFAULT_BASE)
    ap.add_argument("--tip", type=Path, default=DEFAULT_TIP)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    for p in (args.base, args.tip):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    base = bytes(load_rom(args.base))
    tip = bytes(load_rom(args.tip))
    if len(base) != len(tip):
        raise SystemExit(
            f"base and tip must be the same size, got {len(base):#x} / {len(tip):#x}"
        )
    sb, stp = stock_base(base), stock_base(tip)

    AB.mkdir(parents=True, exist_ok=True)
    out: List[dict] = []
    for name, comps, note in CANDIDATES:
        rom = bytearray(base)
        total = 0
        for c in comps:
            total += apply_component(rom, tip, c, sd=sb, ss=stp)
        cs = update_ws_checksum(rom)
        dest = AB / f"{name}.wsc"
        dest.write_bytes(rom)
        out.append(
            {
                "name": name,
                "path": str(dest),
                "components": comps,
                "note": note,
                "bytes_copied": total,
                "checksum": f"{cs:04X}",
            }
        )

    report = {
        "generated_by": "tools/build_ext3_bisect.py",
        "base": {
            "path": str(args.base),
            "checksum": f"{ws_header(base)['checksum']:04X}",
            "status": "reaches the opening narration (manually confirmed)",
        },
        "tip": {"path": str(args.tip), "checksum": f"{ws_header(tip)['checksum']:04X}"},
        "symptom": "new game: event error 257 (0x0101) / 2049 (0x0801)",
        "established": "pre_ext3 works; the tip and every intermediate build since "
        "the ext3 session fail, so the ext3 session introduced the fault",
        "test_order": [c["name"] for c in out],
        "candidates": out,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"base {args.base.name} checksum {report['base']['checksum']} (works)")
    for c in out:
        print(f"  {c['name']:14s} checksum {c['checksum']}  {c['note']}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
