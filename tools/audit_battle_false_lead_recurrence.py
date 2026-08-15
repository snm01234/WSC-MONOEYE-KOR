#!/usr/bin/env python3
"""Fail-closed audit for reintroduced visible Japanese battle-dialogue leads.

The 264 rows in ``battle_dialogue_false_lead_safe_targets.csv`` have independent
proof that their first original code unit is visible sentence text, not
speaker/portrait metadata. A later portrait repair must never prepend that unit
again. This audit is intentionally independent from the generic battle-prefix
parser so the same classification bug cannot hide itself from verification.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base

SAFE = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
DUPLICATE = ROOT / "out/script/battle_dialogue_duplicate_lead_stock_rehome_targets.csv"
PORTRAIT_REPORT = ROOT / "out/patch/dialogue_runtime_followup_portrait_report.json"
DEFAULT_TARGET = ROOT / "out/patch/dialogue_readability_candidate.wsc"
EXPECTED_SAFE = 264
EXPECTED_DUPLICATE = 70
# Runtime captures can overrule the historical one-byte prefix classifier.
# 5EB3AA proves 82 is visible Japanese text (displayed as 一).  The 2026-08-09
# "死죽！" capture likewise proves AD=死 is visible text in all three duplicate
# voices below.  None of these bytes may ever be restored as metadata prefixes.
RUNTIME_OVERRIDES = {
    "5D5982": bytes.fromhex("82"),
    "5D5B1F": bytes.fromhex("82"),
    "5EB3AA": bytes.fromhex("82"),
    "5EAB36": bytes.fromhex("AD"),
    "5EB6B2": bytes.fromhex("AD"),
    "5EC27C": bytes.fromhex("AD"),
}
ROM_SIZE = 16_777_216


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    rom = args.target.read_bytes()
    if len(rom) != ROM_SIZE:
        raise SystemExit(f"target size drifted: {len(rom)}")
    sb = stock_base(rom)

    with SAFE.open(encoding="utf-8-sig", newline="") as handle:
        safe = list(csv.DictReader(handle))
    if len(safe) != EXPECTED_SAFE:
        raise SystemExit(f"safe target population drifted: {len(safe)} != {EXPECTED_SAFE}")
    with DUPLICATE.open(encoding="utf-8-sig", newline="") as handle:
        duplicate = list(csv.DictReader(handle))
    if len(duplicate) != EXPECTED_DUPLICATE:
        raise SystemExit(
            f"duplicate-lead population drifted: {len(duplicate)} != {EXPECTED_DUPLICATE}"
        )

    # Historical portrait-report overlap is diagnostic only.  The recurrence
    # decision itself must depend solely on raw candidate bytes plus the 264
    # independently proven visible-text rows, so cleanup of old reports cannot
    # disable this gate.
    portrait_targets: set[str] = set()
    if PORTRAIT_REPORT.is_file():
        portrait = json.loads(PORTRAIT_REPORT.read_text(encoding="utf-8"))
        portrait_targets = {str(row["abs"]).upper() for row in portrait.get("targets") or []}
    safe_addresses = {str(row["abs"]).upper() for row in safe}

    rows: list[dict[str, Any]] = []
    for row in safe:
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        lead = bytes.fromhex(str(row["lead_hex"]))
        extent = len(bytes.fromhex(str(row["candidate_payload_hex"])))
        payload = rom[sb + logical:sb + logical + extent]
        reintroduced = payload.startswith(lead)
        rows.append({
            "family": "false_japanese_text_lead",
            "abs": address,
            "lead_hex": lead.hex().upper(),
            "lead_text": row.get("lead_text") or row.get("lead_text_removed") or "",
            "payload_hex": payload.hex().upper(),
            "reintroduced": reintroduced,
        })

    duplicate_rows: list[dict[str, Any]] = []
    for row in duplicate:
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        lead = bytes.fromhex(str(row["removed_lead_hex"]))
        extent = len(bytes.fromhex(str(row["before_hex"])))
        payload = rom[sb + logical:sb + logical + extent]
        duplicate_rows.append({
            "family": "duplicate_visible_first_word",
            "abs": address,
            "lead_hex": lead.hex().upper(),
            "lead_text": row.get("removed_lead_render") or "",
            "payload_hex": payload.hex().upper(),
            "reintroduced": payload.startswith(lead),
        })

    runtime_rows: list[dict[str, Any]] = []
    for address, lead in sorted(RUNTIME_OVERRIDES.items()):
        logical = int(address, 16)
        payload = rom[sb + logical:sb + logical + 16]
        runtime_rows.append({
            "family": "runtime_visible_override",
            "abs": address,
            "lead_hex": lead.hex().upper(),
            "lead_text": "runtime-proven visible Japanese lead",
            "payload_hex": payload.hex().upper(),
            "reintroduced": payload.startswith(lead),
        })

    all_rows = rows + duplicate_rows + runtime_rows
    bad = [row for row in all_rows if row["reintroduced"]]
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_false_lead_recurrence.py",
        "ok": not bad,
        "target": {
            "path": str(args.target),
            "size": len(rom),
            "sha256": sha(rom),
        },
        "counts": {
            "proven_visible_text_leads": len(rows),
            "duplicate_visible_leads": len(duplicate_rows),
            "runtime_visible_overrides": len(runtime_rows),
            "total_guarded_leads": len(all_rows),
            "reintroduced": len(bad),
            "clean": len(all_rows) - len(bad),
            "portrait_repair_targets": len(portrait_targets),
            "portrait_false_lead_overlap": len(portrait_targets & safe_addresses),
            "portrait_only_targets": len(portrait_targets - safe_addresses),
        },
        "failures": bad,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
