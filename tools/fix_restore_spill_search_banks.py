#!/usr/bin/env python3
"""
Restore banks that exp_spill may still rewrite as far-pointer *sites*
but that also hold unit/event/scenario tables: 50–5B, 6A, 6B.

Copies those banks from clean 8 MiB ref onto tip. Leaves deny banks
(5C–5E/6C–6F), dialogue 60–69, and dict 5F alone.

Why: classifying individual off16+seg hits as \"legit\" (target is real
dialogue) still corrupts non-pointer fields that merely collide with a
dialogue offset — stage-2 allied MS can become Z Gundam etc.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum

RESTORE_BANKS = tuple(list(range(0x50, 0x5C)) + [0x6A, 0x6B])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument(
        "--ref-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc",
    )
    ap.add_argument(
        "--backup",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.pre_restore_spill_search_banks.wsc",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/restore_spill_search_banks_report.json",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tip = bytearray(load_rom(args.rom))
    ref = load_rom(args.ref_rom)
    changed: dict[str, int] = {}
    for seg in RESTORE_BANKS:
        ta = stock_base(tip) + (seg << 16)
        ra = stock_base(ref) + (seg << 16)
        n = 0
        for i in range(0x10000):
            if tip[ta + i] != ref[ra + i]:
                if not args.dry_run:
                    tip[ta + i] = ref[ra + i]
                n += 1
        changed[f"{seg:02X}"] = n

    report = {
        "banks": [f"{s:02X}" for s in RESTORE_BANKS],
        "bytes_restored": changed,
        "total": sum(changed.values()),
        "dry_run": bool(args.dry_run),
        "ref": str(args.ref_rom),
        "rom": str(args.rom),
    }
    print(f"total_bytes={report['total']} per={changed}")
    if args.dry_run:
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0

    shutil.copy2(args.rom, args.backup)
    report["backup"] = str(args.backup)
    report["checksum"] = f"{update_ws_checksum(tip):04X}"
    args.rom.write_bytes(tip)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"checksum={report['checksum']} bak={args.backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
