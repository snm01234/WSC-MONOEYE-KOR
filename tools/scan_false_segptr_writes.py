#!/usr/bin/env python3
"""
Find 3-byte far-pointer rewrites in the dialogue/data banks that the game cannot
read back.

Background
----------
``rebuild_script_banks.discover_pointer_hits`` recognises three "segmented" far
pointer shapes whose bank byte is the **bare** logical bank number:

    off16_seg8     oo oo ss
    off16_00_seg8  oo oo 00 ss
    seg8_off16     ss oo oo          with ss in 0x60..0x69

Measured on the original ROM, the game's own far pointers do **not** look like
that: they are ``off16`` followed by ``0x80 | bank`` (and often a trailing
``0x00``) — e.g. the stage event tables in bank 66 hold ``b2 13 e6 00`` for
``66:13B2`` and the bank-65 event name/body table holds ``bc 60 e5 00`` for
``65:60BC``. A bare ``0x6x`` bank byte cannot address stock ROM on this cart
(8 MiB = 128 banks numbered 0x80..0xFF), so every hit of those three shapes is a
coincidence: three bytes of live text, tile data or event opcodes that merely
happen to contain a valid record offset next to a 0x6x byte.

Confirmed examples (original ROM):
  * ``61:84E3`` ``18 f2 60 07 35 e0`` — middle of a dialogue payload
  * ``68:2747`` ``c0 66 66 64 60 66 66 66`` — 4bpp tile data
  * ``64:4458`` ``15 19 61 44 e4 00``  — event stream; the tool's framing
    overlaps the game's genuine pointer ``61 44 e4`` (= ``64:4461``) and
    rewriting it leaves bank byte ``0x06``

This scanner reports each site where the target ROM replaced such a byte triple
with an expansion-bank pointer, so the damage can be reverted from the original.

Read-only. Report: ``out/patch/false_segptr_writes.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import find_rom, load_rom, stock_base  # noqa: E402

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/false_segptr_writes.json"

#: Expansion banks the free-space writer relocates records into.
EXP_LO, EXP_HI = 0x30, 0x4F
#: Bare bank bytes the bogus shapes accept.
BARE_LO, BARE_HI = 0x60, 0x69
#: A target triple beginning with E5 18 is an ext3 token, not a pointer write.
EXT3_MAGIC = bytes.fromhex("E518")


def is_ext3_token_prefix(target_triple: bytes) -> bool:
    return len(target_triple) >= 2 and target_triple[:2] == EXT3_MAGIC


def file_identity(path: Path, data: bytes | bytearray) -> Dict[str, Any]:
    """path/size/sha256 identity of an input the scan actually read."""
    payload = bytes(data)
    return {
        "path": str(Path(path).resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def isolated_triples(orig: bytes, tgt: bytes, sb: int, lo: int, hi: int) -> List[int]:
    """Logical starts of runs of exactly 3 differing bytes, isolated by equality."""
    out: List[int] = []
    a = lo
    while a < hi:
        if orig[a] == tgt[sb + a]:
            a += 1
            continue
        run = a
        while a < hi and orig[a] != tgt[sb + a]:
            a += 1
        if a - run == 3:
            out.append(run)
    return out


def classify(orig: bytes, tgt: bytes, sb: int, at: int) -> dict | None:
    o = bytes(orig[at : at + 3])
    t = bytes(tgt[sb + at : sb + at + 3])
    # seg8_off16: segment byte first.
    if BARE_LO <= o[0] <= BARE_HI and EXP_LO <= t[0] <= EXP_HI:
        return {
            "kind": "seg8_off16",
            "old_target": f"{o[0]:02X}:{o[2] << 8 | o[1]:04X}",
            "new_target": f"{t[0]:02X}:{t[2] << 8 | t[1]:04X}",
        }
    # off16_seg8: segment byte last.
    if BARE_LO <= o[2] <= BARE_HI and EXP_LO <= t[2] <= EXP_HI:
        return {
            "kind": "off16_seg8",
            "old_target": f"{o[2]:02X}:{o[1] << 8 | o[0]:04X}",
            "new_target": f"{t[2]:02X}:{t[1] << 8 | t[0]:04X}",
        }
    return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=None, help="original ROM (auto-detected)")
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--lo-bank", type=lambda s: int(s, 0), default=0x60)
    ap.add_argument("--hi-bank", type=lambda s: int(s, 0), default=0x69)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")
    original_path = Path(args.jp or find_rom(ROOT))
    orig = bytes(load_rom(original_path))
    tgt = bytes(load_rom(args.target))
    sb = stock_base(tgt)

    sites: List[dict] = []
    ignored_ext3: List[dict] = []
    per_bank: dict = {}
    for bank in range(args.lo_bank, args.hi_bank + 1):
        lo, hi = bank << 16, (bank << 16) + 0x10000
        for at in isolated_triples(orig, tgt, sb, lo, hi):
            target_triple = bytes(tgt[sb + at : sb + at + 3])
            if is_ext3_token_prefix(target_triple):
                ignored_ext3.append(
                    {
                        "logical": f"{at:06X}",
                        "site": f"{bank:02X}:{at & 0xFFFF:04X}",
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
                "logical": f"{at:06X}",
                "site": f"{bank:02X}:{at & 0xFFFF:04X}",
                "orig_hex": bytes(orig[at : at + 3]).hex(),
                "target_hex": bytes(tgt[sb + at : sb + at + 3]).hex(),
                **info,
            }
            sites.append(row)
            per_bank[f"{bank:02X}"] = per_bank.get(f"{bank:02X}", 0) + 1

    report = {
        "ok": not sites,
        "generated_by": "tools/scan_false_segptr_writes.py",
        "read_only": True,
        "original": str(original_path),
        "target": str(args.target),
        "inputs": {
            "original": file_identity(original_path, orig),
            "target": file_identity(args.target, tgt),
        },
        "banks": f"{args.lo_bank:02X}-{args.hi_bank:02X}",
        "sites_found": len(sites),
        "by_bank": per_bank,
        "sites": sites,
        "ext3_token_prefixes_ignored": len(ignored_ext3),
        "ext3_token_prefix_sites": ignored_ext3,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"false segmented-pointer writes: {len(sites)}  {per_bank}")
    for row in sites[:30]:
        print(
            f"  {row['site']} {row['kind']:<12} {row['orig_hex']} -> {row['target_hex']}"
            f"   {row['old_target']} -> {row['new_target']}"
        )
    if len(sites) > 30:
        print(f"  ... +{len(sites) - 30} more")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
