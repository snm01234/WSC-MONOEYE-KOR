#!/usr/bin/env python3
"""
Restore the stage event/data banks 64–69 from the original ROM, plus the
coincidental 3-byte far-pointer writes that landed in dialogue banks 60–63.

Why
---
Two write paths put bytes into banks 64–69, which are **not** dialogue:

1. ``rebuild_script_banks.discover_pointer_hits`` recognised far pointers whose
   bank byte is the bare logical bank number (``0x60``–``0x69``). The game stores
   the bank *register* value instead (``(stock_base >> 16) + bank``, i.e. ``0xE4``
   for bank 64), so every one of those hits was a coincidence — three bytes of
   live event opcodes, dialogue text or tile data that happened to hold a valid
   record offset next to a ``0x6x`` byte. Confirmed on the tip: ``64:4458``
   overlapped the game's genuine pointer ``61 44 e4`` (= ``64:4461``) inside the
   **stage-3** event block and left bank byte ``0x06``, so the event interpreter
   jumps nowhere and the stage freezes.
2. An older ``apply_3byte_seq_ko``-era pass wrote ``marker + token + 0x01``
   padding over the fixed-stride tables in banks 66/67 (still carrying the retired
   marker ``E3DB``). Those tables are the per-stage (event-name, event-body)
   pointer pairs: ``66:13B2`` = ``ＳＴＧ１５Ｔオ－プニング``, ``66:30F9`` = 15N,
   ``66:495E`` = 16T, ``66:6876`` = 16N, ``66:8E44`` = 17T, ``66:A107`` = 17N,
   ``67:1A18`` = 19(後), ``67:89D6``/``67:9538`` = 20(前/後編), ``67:AEC0`` = 21T,
   ``67:C0A2`` = 21N. The "revert banks 64–69" step of the ext3 session only
   covered its own writes, so these survived into the tip.

Measured on the tip (checksum ``20BF``): banks 64/65/68/69 contain **only** the
coincidental 3-byte writes, and 66/67 contain those plus the ``E3DB`` invasion.
No Korean payload lives in 64–69, so a full byte-identical restore of those banks
loses no translation — the relocated Korean copies stay in the expansion banks,
they just become unreferenced (those lines render Japanese again).

Fail-closed
-----------
* refuses unless the target is a 16 MiB expanded ROM
* refuses if the original and the target disagree on ``stock_base`` geometry
* refuses to touch anything outside banks ``--lo-bank``..``--hi-bank`` and the
  3-byte sites reported by :mod:`scan_false_segptr_writes`
* refuses if a 60–63 site's surrounding bytes are not byte-identical to the
  original (that would mean the site sits inside a record this project rewrote,
  and reverting 3 bytes would corrupt the Korean instead of repairing data)
* ``--dry-run`` is the default; ``--commit`` backs the target up first

Report: ``out/patch/data_bank_invasion_repair.json``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    find_rom,
    is_expanded_rom,
    load_rom,
    stock_base,
    update_ws_checksum,
    ws_header,
)
from scan_false_segptr_writes import (  # noqa: E402
    classify,
    isolated_triples,
    is_ext3_token_prefix,
)

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_eventfix_work.wsc"
DEFAULT_OUT = ROOT / "out/patch/data_bank_invasion_repair.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

#: How far back to look for the containing record's start terminator.
RECORD_LOOKBACK = 256


def record_span(orig: bytes, at: int, lo: int, hi: int) -> tuple[int, int]:
    """[start, end) of the original zstring record containing ``at`` (no terminator).

    A fixed byte margin is not usable as a guard: the record *before* the site is
    often a legitimately ext3-rewritten one, so a +-8 window straddles it and the
    check refuses a repair it should allow. The record boundary is the right unit.
    """
    start = at
    limit = max(lo, at - RECORD_LOOKBACK)
    while start > limit and orig[start - 1] != 0x00:
        start -= 1
    end = at
    while end < hi and orig[end] != 0x00:
        end += 1
    return start, end


def revert_banks(
    orig: bytes, rom: bytearray, sb: int, lo_bank: int, hi_bank: int
) -> Dict[str, int]:
    """Copy banks [lo_bank, hi_bank] from the original. Returns bytes per bank."""
    per_bank: Dict[str, int] = {}
    for bank in range(lo_bank, hi_bank + 1):
        lo, hi = bank << 16, (bank << 16) + 0x10000
        n = sum(1 for a in range(lo, hi) if rom[sb + a] != orig[a])
        if n:
            rom[sb + lo : sb + hi] = orig[lo:hi]
            per_bank[f"{bank:02X}"] = n
    return per_bank


def revert_segptr_sites(
    orig: bytes, rom: bytearray, sb: int, lo_bank: int, hi_bank: int
) -> tuple[List[dict], List[dict]]:
    """Revert coincidental 3-byte pointer writes in [lo_bank, hi_bank]."""
    done: List[dict] = []
    refused: List[dict] = []
    ignored_ext3: List[dict] = []
    tgt = bytes(rom)
    for bank in range(lo_bank, hi_bank + 1):
        lo, hi = bank << 16, (bank << 16) + 0x10000
        for at in isolated_triples(orig, tgt, sb, lo, hi):
            target_triple = bytes(tgt[sb + at : sb + at + 3])
            if is_ext3_token_prefix(target_triple):
                ignored_ext3.append(
                    {
                        "site": f"{bank:02X}:{at & 0xFFFF:04X}",
                        "logical": f"{at:06X}",
                        "orig_hex": bytes(orig[at : at + 3]).hex(),
                        "target_hex": target_triple.hex(),
                        "reason": "target begins with ext3 magic E518",
                    }
                )
                continue
            info = classify(orig, tgt, sb, at)
            if info is None:
                continue
            row = {
                "site": f"{bank:02X}:{at & 0xFFFF:04X}",
                "logical": f"{at:06X}",
                "orig_hex": bytes(orig[at : at + 3]).hex(),
                "target_hex": bytes(tgt[sb + at : sb + at + 3]).hex(),
                **info,
            }
            r_lo, r_hi = record_span(orig, at, lo, hi)
            row["record"] = f"{r_lo:06X}-{r_hi:06X}"
            before_ok = orig[r_lo:at] == tgt[sb + r_lo : sb + at]
            after_ok = orig[at + 3 : r_hi] == tgt[sb + at + 3 : sb + r_hi]
            if not (before_ok and after_ok):
                row["refuse_reason"] = (
                    "the containing record diverges from the original outside the "
                    "3-byte site, so it was deliberately rewritten and reverting "
                    "these bytes would corrupt the Korean"
                )
                refused.append(row)
                continue
            rom[sb + at : sb + at + 3] = orig[at : at + 3]
            done.append(row)
    return done, refused, ignored_ext3


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=None, help="original ROM (auto-detected)")
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--lo-bank", type=lambda s: int(s, 0), default=0x64)
    ap.add_argument("--hi-bank", type=lambda s: int(s, 0), default=0x69)
    ap.add_argument(
        "--segptr-lo-bank",
        type=lambda s: int(s, 0),
        default=0x60,
        help="first bank scanned for coincidental 3-byte pointer writes",
    )
    ap.add_argument("--segptr-hi-bank", type=lambda s: int(s, 0), default=0x63)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default")
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a report over a .wsc")

    jp_path = args.jp or find_rom(ROOT)
    for p in (jp_path, args.target):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    orig = bytes(load_rom(jp_path))
    rom = bytearray(load_rom(args.target))
    if not is_expanded_rom(rom):
        raise SystemExit("refusing: target is not a 16 MiB expanded ROM")
    sb = stock_base(rom)
    if sb + len(orig) != len(rom):
        raise SystemExit(
            f"refusing: stock_base {sb:#x} + original {len(orig):#x} != target {len(rom):#x}"
        )

    checksum_before = f"{ws_header(load_rom(args.target))['checksum']:04X}"
    per_bank = revert_banks(orig, rom, sb, args.lo_bank, args.hi_bank)
    sites, refused, ignored_ext3 = revert_segptr_sites(
        orig, rom, sb, args.segptr_lo_bank, args.segptr_hi_bank
    )

    residual = {
        f"{bank:02X}": sum(
            1
            for a in range((bank << 16), (bank << 16) + 0x10000)
            if rom[sb + a] != orig[a]
        )
        for bank in range(args.lo_bank, args.hi_bank + 1)
    }
    residual = {k: v for k, v in residual.items() if v}

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
        "ok": not refused and not residual,
        "generated_by": "tools/repair_data_bank_invasion.py",
        "mode": "commit" if args.commit else "dry-run",
        "original": str(jp_path),
        "target": str(args.target),
        "data_banks_reverted": f"{args.lo_bank:02X}-{args.hi_bank:02X}",
        "data_bank_bytes_reverted": per_bank,
        "data_bank_bytes_total": sum(per_bank.values()),
        "data_bank_residual_diff": residual,
        "segptr_scan_banks": f"{args.segptr_lo_bank:02X}-{args.segptr_hi_bank:02X}",
        "segptr_sites_reverted": len(sites),
        "segptr_sites": sites,
        "segptr_sites_refused": refused,
        "segptr_sites_ignored_ext3": ignored_ext3,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "backup": str(backup) if backup else None,
        "revert": f"copy {backup} back" if backup else "no write performed",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"data banks {args.lo_bank:02X}-{args.hi_bank:02X}: "
          f"{report['data_bank_bytes_total']} B reverted {per_bank}")
    print(f"residual diff vs original: {residual or 'none'}")
    print(f"segptr sites in {args.segptr_lo_bank:02X}-{args.segptr_hi_bank:02X}: "
          f"{len(sites)} reverted, {len(refused)} refused, "
          f"{len(ignored_ext3)} intentional ext3 ignored")
    for row in sites:
        print(f"  {row['site']} {row['kind']} {row['target_hex']} -> {row['orig_hex']}")
    for row in refused:
        print(f"  REFUSED {row['site']}: {row['refuse_reason']}")
    if args.commit:
        print(f"backup   : {backup}")
        print(f"checksum : {checksum_before} → {checksum_after}")
    else:
        print("dry-run: nothing written. Add --commit to apply.")
    print(f"-> {args.out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
