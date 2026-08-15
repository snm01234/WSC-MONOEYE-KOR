#!/usr/bin/env python3
"""
Audit stage-2 Jagd Doga guard byte at 6D937C and tip mtime.

Usage:
  python tools/audit_jagd_guard.py
  python tools/audit_jagd_guard.py --watch 5   # print on change every 5s
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base  # noqa: E402

GOOD = bytes.fromhex("3fa660")
LOGICAL = 0x6D937C
DEFAULT_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"


def sample(path: Path) -> dict:
    rom = load_rom(path)
    sb = stock_base(rom)
    val = bytes(rom[sb + LOGICAL : sb + LOGICAL + 3])
    st = path.stat()
    return {
        "path": str(path),
        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "size": st.st_size,
        "value": val.hex(),
        "status": "OK" if val == GOOD else "CORRUPT",
        "expected": GOOD.hex(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--watch", type=float, default=0, help="Seconds between polls")
    args = ap.parse_args()

    last = None
    while True:
        info = sample(args.rom)
        key = (info["mtime"], info["value"])
        if key != last:
            print(
                f"{info['mtime']}  {info['status']:7s}  6D937C={info['value']}  "
                f"(want {info['expected']})  size={info['size']}"
            )
            last = key
            if info["status"] != "OK" and args.watch <= 0:
                raise SystemExit(2)
        if args.watch <= 0:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
