#!/usr/bin/env python3
"""
Revert whole script records in a logical band from a reference ROM.

Why this exists: the opening narration window (``0x6040A5``–``0x604570``) is not
rendered through the ext3 hook. Measured on this lineage, that window carried 54
stock-5F tokens, 2 bank10 ext-dict tokens and exactly **1** ext3 token before the
re-homing pass, while every window after it already carried hundreds of working
ext3 tokens (early_tut 436, bank60 1616, bank61 2872, bank62 2576). Writing
4-byte ``E5 18 xx yy`` tokens into that window leaves them undecoded and the
following ``0x01`` padding is walked as event opcodes, which the game reports as
an event error (``0x0101`` = 257, ``0x0801`` = 2049) on new game.

Record boundaries are walked on the ORIGINAL ROM, so a record is always reverted
whole — never half a token. ``--dry-run`` is the default; ``--commit`` backs the
target up to ``out/patch/backup/<timestamp>/`` first and updates the checksum.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
    ws_header,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/revert_band_records_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

# The opening narration window: the ext3 hook does not cover its renderer.
OPENING_LO = 0x6040A5
OPENING_HI = 0x604570


def walk_records(jp: bytes, lo: int, hi: int) -> List[tuple[int, int]]:
    """(logical_start, byte_len_including_NUL) for records starting in [lo, hi]."""
    sj = stock_base(jp)
    out: List[tuple[int, int]] = []
    cursor = lo
    while cursor <= hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        term = got[1] - sj
        out.append((cursor, term - cursor + 1))
        cursor = term + 1
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP, help="record-boundary reference")
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument(
        "--from-rom",
        type=Path,
        required=True,
        help="ROM whose record bytes are copied into the target",
    )
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=OPENING_LO)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=OPENING_HI)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default")
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a report over a .wsc")
    for p in (args.jp, args.target, args.from_rom):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    jp = bytes(load_rom(args.jp))
    src = bytes(load_rom(args.from_rom))
    rom = bytearray(load_rom(args.target))
    ss, sd = stock_base(src), stock_base(rom)

    records = walk_records(jp, args.lo, args.hi)
    changes: List[dict] = []
    for start, n in records:
        before = bytes(rom[sd + start : sd + start + n])
        after = src[ss + start : ss + start + n]
        if before == after:
            continue
        rom[sd + start : sd + start + n] = after
        changes.append(
            {
                "abs": f"{start:06X}",
                "len": n,
                "before": before.hex(),
                "after": after.hex(),
            }
        )

    checksum_before = f"{ws_header(load_rom(args.target))['checksum']:04X}"
    backup = None
    checksum_after = None
    if args.commit:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        (BACKUP_ROOT / stamp).mkdir(parents=True, exist_ok=True)
        backup = BACKUP_ROOT / stamp / args.target.name
        shutil.copy2(args.target, backup)
        checksum_after = f"{update_ws_checksum(rom):04X}"
        args.target.write_bytes(rom)

    report = {
        "ok": True,
        "generated_by": "tools/revert_band_records.py",
        "mode": "commit" if args.commit else "dry-run",
        "target": str(args.target),
        "from_rom": str(args.from_rom),
        "boundary_reference": str(args.jp),
        "band": [f"{args.lo:06X}", f"{args.hi:06X}"],
        "records_in_band": len(records),
        "records_reverted": len(changes),
        "bytes_reverted": sum(c["len"] for c in changes),
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "backup": str(backup) if backup else None,
        "revert": f"copy {backup} back" if backup else "no write performed",
        "changes": changes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"band            : {report['band'][0]}-{report['band'][1]}")
    print(f"records in band : {len(records)}")
    print(f"records reverted: {len(changes)} ({report['bytes_reverted']} B)")
    if args.commit:
        print(f"backup          : {backup}")
        print(f"checksum        : {checksum_before} → {checksum_after}")
    else:
        print("dry-run: nothing written. Add --commit to apply.")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
