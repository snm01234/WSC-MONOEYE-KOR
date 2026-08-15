#!/usr/bin/env python3
"""
Find fixed-stride table entries the extractor mistook for dialogue.

READ-ONLY unless ``--emit-candidate`` is given, and even then only a copy in
``out/patch/ab/`` is written — never the tip.

Banks 64-69 turned out to be data, not dialogue: their entries repeat one skeleton
whose decoded original text starts with the same two characters. Measured share of
the most common starting pair among changed records:

    bank 60  14% (``……``)      bank 64  38% (``をん``)
    bank 61  15% (``……``)      bank 65  23% (``をん``)
    bank 62  14% (``……``)      bank 66  37% (``をん``)
    bank 63  14% (``……``)      bank 67  28% (``をん``)

Ordinary prose tops out around 14% on ``……``; a single non-particle skeleton taking
a quarter of a bank means a table. Those banks are now excluded at the applier
level, but 36 records with the same ``をん`` skeleton also sit inside bank 62, which
*is* a dialogue bank — this tool locates leftovers like that so they can be judged
individually instead of by bank.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AB = ROOT / "out/patch/ab"
DEFAULT_OUT = ROOT / "out/patch/table_motif_records.json"

DEFAULT_MOTIF = "をん"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--lo", type=lambda s: int(s, 16), default=0x600000)
    ap.add_argument("--hi", type=lambda s: int(s, 16), default=0x69FFFF)
    ap.add_argument(
        "--motif",
        default=DEFAULT_MOTIF,
        help="starting characters that mark a table entry (default: をん)",
    )
    ap.add_argument("--tbl", type=Path, default=ROOT / "data/monoeye.tbl")
    ap.add_argument(
        "--emit-candidate",
        type=Path,
        default=None,
        help="write a copy of the target with the motif records reverted to --pre",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    for p in (args.jp, args.pre, args.target):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")
    if args.emit_candidate and args.emit_candidate.resolve() == args.target.resolve():
        raise SystemExit("--emit-candidate must not point at the target")

    jp = bytes(load_rom(args.jp))
    pre = bytes(load_rom(args.pre))
    rom = bytearray(load_rom(args.target))
    sj, sp, st = stock_base(jp), stock_base(pre), stock_base(rom)
    tbl = Tbl.load(args.tbl)
    d = Dictionary(jp)

    prefixes: Dict[int, Counter] = defaultdict(Counter)
    totals: Counter = Counter()
    motif_rows: List[dict] = []

    cursor = args.lo
    while cursor <= args.hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        n = (got[1] - sj) - cursor + 1
        start = cursor
        cursor = (got[1] - sj) + 1

        if bytes(rom[st + start : st + start + n]) == pre[sp + start : sp + start + n]:
            continue
        bank = start >> 16
        totals[bank] += 1
        body = split_prefix_body(jp[sj + start : sj + start + n - 1])[1]
        try:
            text = d.expand(body, tbl)
        except Exception:  # pragma: no cover - informational
            text = ""
        text = text or ""
        prefixes[bank][text[:2]] += 1
        if text.startswith(args.motif):
            motif_rows.append(
                {
                    "abs": f"{start:06X}",
                    "len": n,
                    "orig_text": text[:48],
                    "orig_hex": jp[sj + start : sj + start + n].hex()[:64],
                    "target_hex": bytes(rom[st + start : st + start + n]).hex()[:64],
                }
            )

    per_bank = {}
    for bank in sorted(totals):
        top = prefixes[bank].most_common(3)
        per_bank[f"{bank:02X}"] = {
            "changed_records": totals[bank],
            "top_prefixes": [{"prefix": p, "n": c} for p, c in top],
            "top_share": round(top[0][1] / totals[bank], 3) if top else 0.0,
            "motif_records": prefixes[bank].get(args.motif, 0),
        }

    candidate = None
    if args.emit_candidate:
        for row in motif_rows:
            start = int(row["abs"], 16)
            n = row["len"]
            rom[st + start : st + start + n] = pre[sp + start : sp + start + n]
        cs = update_ws_checksum(rom)
        args.emit_candidate.parent.mkdir(parents=True, exist_ok=True)
        args.emit_candidate.write_bytes(rom)
        candidate = {"path": str(args.emit_candidate), "checksum": f"{cs:04X}"}

    report = {
        "generated_by": "tools/scan_table_motif_records.py",
        "read_only": args.emit_candidate is None,
        "band": [f"{args.lo:06X}", f"{args.hi:06X}"],
        "motif": args.motif,
        "per_bank": per_bank,
        "motif_records": len(motif_rows),
        "candidate": candidate,
        "rows": motif_rows[:200],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"band {report['band'][0]}-{report['band'][1]}  motif {args.motif!r}")
    for bank, info in per_bank.items():
        print(
            f"  bank {bank}: {info['changed_records']:>5} changed | "
            f"top {info['top_prefixes'][0]['prefix']!r} "
            f"{info['top_share']:.0%} | motif {info['motif_records']}"
        )
    print(f"motif records total: {len(motif_rows)}")
    if candidate:
        print(f"candidate {candidate['path']} checksum {candidate['checksum']}")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
