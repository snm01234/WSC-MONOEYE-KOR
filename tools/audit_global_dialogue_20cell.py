#!/usr/bin/env python3
"""Current-state 20-cell audit for runtime dialogue.

Unlike the retired snapshot-bound guard, this audit does not read
``translation_sheet.csv`` or archived aux inventories.  It rebuilds the
runtime contract in memory from the exact target ROM and directly scans the
current bank-59 runtime-text region.

Scopes:
* bank 59: current ROM direct scan, Hangul-rendering records only;
* banks 60-63: target-bound scenario runtime contracts rebuilt in memory.

No historical generated report is an input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audit_bank59_event_width import scan_bank59_current, strip_pad  # noqa: E402
from dialogue_runtime_contracts import build_manifest  # noqa: E402
from monoeye_rom import Tbl, find_rom, load_rom  # noqa: E402

TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/global_dialogue_20cell_audit.json"
CELL_LIMIT = 20
SEMANTIC_GUARDS = {"627963": "그러니까……"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit_scenario(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for contract in manifest.get("contracts") or []:
        route = str(contract.get("route") or "")
        if route not in {"scenario_first", "scenario_continuation"}:
            continue
        address = str(contract.get("address") or "").upper()
        if not address or not (0x600000 <= int(address, 16) < 0x640000):
            continue
        text = strip_pad(str(contract.get("baseline_text") or ""))
        if not text:
            # Empty runtime-visible scenario rows are retained in the contract
            # for structural reasons but are not meaningful width targets.
            continue
        rows.append(
            {
                "address": address,
                "scope": "scenario_60_63_current_contract",
                "route": route,
                "status": str(contract.get("status") or ""),
                "text": text,
                "cells": len(text),
                "over_20": len(text) > CELL_LIMIT,
                "source_jp": str(contract.get("original_japanese") or ""),
            }
        )
    return rows, unreadable


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    target = bytes(load_rom(args.target))
    original = bytes(load_rom(find_rom(ROOT)))
    tbl = Tbl.load(TBL)

    # Rebuild target-bound contracts in memory.  Do not overwrite the canonical
    # manifest as a side effect of a width audit.
    manifest = build_manifest(original, target, target_path=args.target)
    bank59, bank59_unreadable, bank59_end = scan_bank59_current(target, tbl)
    scenario, scenario_unreadable = audit_scenario(manifest)
    rows = bank59 + scenario
    offenders = [row for row in rows if row["over_20"]]
    unreadable = bank59_unreadable + scenario_unreadable

    by_address = {row["address"]: row for row in rows}
    semantic_failures: list[dict[str, str]] = []
    for address, expected in SEMANTIC_GUARDS.items():
        actual = str((by_address.get(address) or {}).get("text") or "")
        if actual != expected:
            semantic_failures.append({"address": address, "expected": expected, "actual": actual})

    report = {
        "schema_version": 2,
        "generated_by": "tools/audit_global_dialogue_20cell.py",
        "status": "pass" if not offenders and not unreadable and not semantic_failures else "fail",
        "target": {"path": str(args.target), "size": len(target), "sha256": sha(target)},
        "policy": {
            "cell_limit": CELL_LIMIT,
            "scopes": [
                f"bank59 current direct scan 590000-{bank59_end:06X}",
                "banks60-63 scenario contracts rebuilt from exact target",
            ],
            "historical_generated_inputs": [],
            "translation_sheet_used": False,
            "canonical_manifest_written": False,
            "battle_quarantine_excluded": True,
        },
        "contract_counts": manifest.get("counts") or {},
        "counts": {
            "bank59_checked": len(bank59),
            "scenario_checked": len(scenario),
            "total_checked": len(rows),
            "over_20": len(offenders),
            "unreadable": len(unreadable),
            "semantic_guard_failures": len(semantic_failures),
        },
        "offenders": offenders,
        "unreadable": unreadable,
        "semantic_guard_failures": semantic_failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "target_sha256": report["target"]["sha256"],
        "counts": report["counts"],
        "offenders": [
            {"address": row["address"], "cells": row["cells"], "text": row["text"]}
            for row in offenders
        ],
        "semantic_guard_failures": semantic_failures,
        "report": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
