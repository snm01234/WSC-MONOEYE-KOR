#!/usr/bin/env python3
"""Audit historical P2 local-ext3 terminator moves for structural NUL loss.

The historical local-expansion gate allowed a record to consume one extra NUL
when the next manifest record start was unchanged.  Runtime evidence at 611DF0
shows that this is insufficient: the consumed NUL can itself be a separator that
makes the following 08/17/18 byte parse as event/dialogue control.

This read-only audit reports every approved +1 terminator move, verifies whether
the current TIP still has the terminator at the expanded location, counts the
original consecutive NUL separator bytes, and classifies the next record lead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, read_encoded_z_safe, stock_base

DEFAULT_APPROVAL = ROOT / "out/patch/p2_local_ext3_expansion_approval.json"
DEFAULT_ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/p2_local_terminator_move_audit.json"
CONTROL_LEADS = {0x08, 0x17, 0x18}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_moves(obj) -> dict[str, dict]:
    rows = []
    stack = [obj]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if {"record_id", "old_terminator", "new_terminator", "next_record_start"} <= set(value):
                rows.append(value)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return {str(r["record_id"]): r for r in rows}


def nul_run(data: bytes, start: int, end: int) -> int:
    n = 0
    for pos in range(start, end):
        if data[pos] != 0:
            break
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--approval", type=Path, default=DEFAULT_APPROVAL)
    ap.add_argument("--original", type=Path, default=DEFAULT_ORIGINAL)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    approval = json.loads(args.approval.read_text(encoding="utf-8"))
    moves = collect_moves(approval)
    original = bytes(load_rom(args.original))
    target = bytes(load_rom(args.target))
    sb = stock_base(target)

    rows = []
    for record_id, r in sorted(moves.items()):
        old_term = int(str(r["old_terminator"]), 16)
        new_term = int(str(r["new_terminator"]), 16)
        next_start = int(str(r["next_record_start"]), 16)
        if new_term != old_term + 1:
            continue
        got = read_encoded_z_safe(target, sb + int(str(r["abs"]), 16), max_len=256)
        current_term = None if got is None else int(got[1]) - sb
        original_nuls = nul_run(original, old_term, next_start)
        current_nuls_from_old = nul_run(target[sb:], old_term, next_start)
        lead = original[next_start]
        row = {
            "record_id": record_id,
            "abs": str(r["abs"]),
            "old_terminator": f"{old_term:06X}",
            "new_terminator": f"{new_term:06X}",
            "current_terminator": None if current_term is None else f"{current_term:06X}",
            "next_record_start": f"{next_start:06X}",
            "next_lead": f"{lead:02X}",
            "next_lead_control_class": lead in CONTROL_LEADS,
            "original_nul_run_before_next": original_nuls,
            "current_nul_run_from_original_term": current_nuls_from_old,
            "current_still_expanded": current_term == new_term,
            "separator_nul_lost": current_nuls_from_old < original_nuls,
            "historical_event_like_body_flag": (r.get("boundary_proof") or {}).get("event_like_body"),
        }
        row["runtime_risk"] = bool(
            row["current_still_expanded"]
            and row["separator_nul_lost"]
            and row["next_lead_control_class"]
        )
        rows.append(row)

    report = {
        "ok": not any(r["runtime_risk"] for r in rows),
        "generated_by": "tools/audit_p2_local_terminator_moves.py",
        "read_only": True,
        "inputs": {
            "approval": str(args.approval),
            "original": {"path": str(args.original), "sha256": sha(original)},
            "target": {"path": str(args.target), "sha256": sha(target)},
        },
        "counts": {
            "approved_plus1_moves": len(rows),
            "current_still_expanded": sum(r["current_still_expanded"] for r in rows),
            "separator_nul_lost": sum(r["separator_nul_lost"] for r in rows),
            "control_lead_after_gap": sum(r["next_lead_control_class"] for r in rows),
            "runtime_risk": sum(r["runtime_risk"] for r in rows),
            "next_leads": dict(Counter(r["next_lead"] for r in rows)),
        },
        "runtime_evidence": {
            "611DF0": "original probe works with terminator 611DF6 + separator 611DF7; all Korean candidates retaining terminator 611DF7 leak 18 as こ and end early",
        },
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
