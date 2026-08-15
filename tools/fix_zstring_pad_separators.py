#!/usr/bin/env python3
"""
Restore the NUL separator before control/speaker records.

Background:
  repair_zstring_nul_pad rewrote short dict tokens as
    [prefix][token][SPACE…][00][next]
  which removes the stock double-NUL gap before structural next records
  (control / speaker). Sequential 2-line readers then treat that control as
  "line 2", shifting windows and firing face commands (e.g. Sig mid-narration).

Policy:
  - next is dialogue → keep single NUL (so paired line-2 text still shows)
  - next is control/speaker/other → ensure [00][00] before next
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)

SPACE = 0x01


def is_dict_token_body(body: bytes) -> bool:
    """True for single-token or token+SPACE pad bodies."""
    if len(body) < 2:
        return False
    if not (0xF0 <= body[0] <= 0xFF):
        return False
    if len(body) == 2:
        return True
    return all(b == SPACE for b in body[2:])


def _seg_file_base(rom: bytes, seg: int) -> int:
    """File offset of logical script bank (honors 16MiB stock_base)."""
    return stock_base(rom) + (seg << 16)


def collect_starts(rom: bytes, seg_lo: int, seg_hi: int) -> List[int]:
    """Return file-absolute record starts in logical banks seg_lo..seg_hi."""
    starts: List[int] = []
    for seg in range(seg_lo, seg_hi + 1):
        base = _seg_file_base(rom, seg)
        end = base + 0x10000
        off = base
        while off < end:
            if rom[off] == 0:
                off += 1
                continue
            starts.append(off)
            got = read_encoded_z_safe(rom, off)
            if got is None:
                off += 1
            else:
                off = got[1] + 1
    return starts


def fix_at(rom: bytearray, abs_off: int, next_start: int) -> dict | None:
    got = read_encoded_z_safe(rom, abs_off)
    if got is None:
        return None
    payload, term = got
    prefix, body, kind = split_prefix_body(payload)
    if kind != "dialogue" or not is_dict_token_body(body):
        return None

    nxt = read_encoded_z_safe(rom, next_start)
    if nxt is None:
        return None
    _np, _nt = nxt
    _npref, _nbody, nkind = split_prefix_body(_np)
    if nkind == "dialogue":
        return None

    # Need at least two bytes before next: term + separator.
    if next_start < abs_off + len(prefix) + 2 + 2:
        return None

    # Current term should sit at next_start-1 (gap eaten) or already correct.
    if term == next_start - 2 and rom[next_start - 1] == 0:
        return None  # already has separator

    if term != next_start - 1:
        return None
    # [… SPACE][00][next] → [… ][00][00][next]
    sep = next_start - 2
    if rom[sep] not in (SPACE, 0):
        return None
    rom[sep] = 0
    rom[next_start - 1] = 0
    return {
        "abs": f"{abs_off:06X}",
        "next": f"{next_start:06X}",
        "next_kind": nkind,
        "next_lead": f"{rom[next_start]:02X}",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc"
    )
    ap.add_argument(
        "--out-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc"
    )
    ap.add_argument(
        "--banks",
        default="60-6B",
        help="Dialogue banks only (never 6C–6F unit/MS tables)",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/fix_zstring_pad_separators_report.json",
    )
    args = ap.parse_args()

    rom = bytearray(load_rom(args.rom))
    lo_s, hi_s = args.banks.split("-")
    lo, hi = int(lo_s, 16), int(hi_s, 16)
    starts = collect_starts(rom, lo, hi)
    fixed = []
    for i, abs_off in enumerate(starts):
        nxt = (
            starts[i + 1]
            if i + 1 < len(starts)
            else ((abs_off & ~0xFFFF) + 0x10000)
        )
        info = fix_at(rom, abs_off, nxt)
        if info:
            fixed.append(info)

    cs = update_ws_checksum(rom)
    args.out_rom.write_bytes(rom)
    sb = stock_base(rom)

    def _logical(file_abs: str) -> str:
        return f"{int(file_abs, 16) - sb:06X}"

    opening_fixed = [
        {**x, "abs_logical": _logical(x["abs"]), "next_logical": _logical(x["next"])}
        for x in fixed
        if 0x6040A0 <= (int(x["abs"], 16) - sb) <= 0x604500
    ]
    report = {
        "fixed": len(fixed),
        "checksum": f"{cs:04X}",
        "stock_base": f"{sb:06X}",
        "sample_6040A5": bytes(rom[sb + 0x6040A5 : sb + 0x6040B6]).hex(),
        "sample_604401": bytes(rom[sb + 0x604401 : sb + 0x604420]).hex(),
        "opening_fixed": opening_fixed,
        "samples": fixed[:40],
    }
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Fixed {len(fixed)} separators checksum={cs:04X}")
    print("6040A5", report["sample_6040A5"])
    print("604401", report["sample_604401"])


if __name__ == "__main__":
    main()
