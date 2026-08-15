#!/usr/bin/env python3
r"""
Build minimal-mutation ROMs for the bank-72 menu atlas.

Two candidate kinds, matching the two open questions:

``--plate N``
    Zero one whole 640-byte plate. Answers "which plate feeds which on-screen
    button", replacing the old 2 KB slice bisection with a mutation whose extent
    is exactly one atlas entry.

``--tile N:T``
    Overwrite exactly one 32-byte 8x8 tile (tile ``T`` of plate ``N``) with a
    loud pattern. This is the gate the whole title-menu effort has been waiting
    on: docs/TITLE_MENU_FAILED_EXPERIMENT.md says no further patch may be built
    until "a minimal ROM where mutating one graphics candidate changes one
    specific on-screen tile" exists.

The base is the stock 8 MiB ROM, so a candidate differs from the capture baseline
only by the mutated bytes. The WonderSwan checksum is refreshed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import find_rom, load_rom, update_ws_checksum  # noqa: E402

ATLAS = ROOT / "out" / "title_menu_capture" / "bank72_atlas.json"
OUT_DIR = ROOT / "out" / "patch" / "menu_bisect"

TILE_BYTES = 32
#: Diagonal ramp: every nibble value 0-15 appears, so the tile cannot be mistaken
#: for stock content under any palette, and it is not flat (a flat fill can be
#: confused with a transparent/masked tile).
LOUD_TILE = bytes(
    ((y + x) % 16) << 4 | ((y + x + 8) % 16) for y in range(8) for x in range(4)
)


def load_atlas() -> dict:
    if not ATLAS.exists():
        raise SystemExit(
            f"missing {ATLAS}; run tools/analyze_bank72_menu_atlas.py first"
        )
    return json.loads(ATLAS.read_text(encoding="utf-8"))


def write_candidate(base: bytes, edits: list[tuple[int, bytes]], dest: Path) -> dict:
    rom = bytearray(base)
    changed = 0
    for off, payload in edits:
        before = bytes(rom[off : off + len(payload)])
        rom[off : off + len(payload)] = payload
        changed += sum(1 for a, b in zip(before, payload) if a != b)
    checksum = update_ws_checksum(rom)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(rom)
    return {
        "path": str(dest.relative_to(ROOT)),
        "bytes_changed": changed,
        "checksum": f"{checksum:04X}" if isinstance(checksum, int) else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=None, help="base ROM (default: stock)")
    ap.add_argument("--plate", type=int, action="append", default=[], help="zero plate N (repeatable)")
    ap.add_argument(
        "--tile",
        action="append",
        default=[],
        metavar="N:T",
        help="overwrite tile T of plate N with the loud pattern (repeatable)",
    )
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    if not args.plate and not args.tile:
        ap.error("give at least one --plate or --tile")

    atlas = load_atlas()
    plates = {p["index"]: p for p in atlas["plates"]}
    base = bytes(load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT)))

    made = []
    for n in args.plate:
        if n not in plates:
            raise SystemExit(f"no plate {n} in atlas")
        lo = plates[n]["abs_lo"]
        size = atlas["format"]["plate_stride"]
        tag = f"PLATE_{n:02d}"
        info = write_candidate(base, [(lo, bytes(size))], args.out_dir / f"{tag}.wsc")
        info.update(
            {
                "tag": tag,
                "kind": "plate_zero",
                "plate": n,
                "abs": f"{lo:06X}-{lo + size - 1:06X}",
            }
        )
        made.append(info)
        print(f"{tag}: zeroed {info['abs']} ({info['bytes_changed']} B differ) -> {info['path']}")

    for spec in args.tile:
        try:
            pn, tn = (int(x) for x in spec.split(":"))
        except ValueError:
            raise SystemExit(f"bad --tile spec {spec!r}; want N:T")
        if pn not in plates:
            raise SystemExit(f"no plate {pn} in atlas")
        if not 0 <= tn < plates[pn]["tiles"]:
            raise SystemExit(f"tile {tn} out of range for plate {pn}")
        lo = plates[pn]["abs_lo"] + tn * TILE_BYTES
        tag = f"TILE_{pn:02d}_{tn:02d}"
        info = write_candidate(base, [(lo, LOUD_TILE)], args.out_dir / f"{tag}.wsc")
        info.update(
            {
                "tag": tag,
                "kind": "tile_loud",
                "plate": pn,
                "tile": tn,
                "tile_xy": [tn % 10, tn // 10],
                "abs": f"{lo:06X}-{lo + TILE_BYTES - 1:06X}",
            }
        )
        made.append(info)
        print(
            f"{tag}: tile {tn} (col {tn % 10}, row {tn // 10}) of plate {pn} "
            f"at {info['abs']} ({info['bytes_changed']} B differ) -> {info['path']}"
        )

    manifest = args.out_dir / "menu_tile_candidates.json"
    manifest.write_text(
        json.dumps({"base": str(args.rom or find_rom(ROOT)), "candidates": made}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"\nmanifest -> {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
