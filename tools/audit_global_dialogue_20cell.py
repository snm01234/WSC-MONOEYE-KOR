#!/usr/bin/env python3
"""Hard 20-cell audit for runtime-facing bank59 + banks60-63 dialogue.

This is the project-wide guard for the user's 1-row/20-cell policy.  It uses the
current composite runtime's proven five-page E5 18 alias mapping, so it does not
mis-measure alias-backed phrases as ordinary ext3 entries.  Battle quarantine is
intentionally excluded because its record grammar is not fully proven.
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

from audit_bank59_event_width import (  # noqa: E402
    active_dictionary,
    payload_at,
    prefix_map,
    source_addresses,
    strip_pad,
)
from monoeye_rom import Tbl  # noqa: E402

TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
TRANSLATION_SHEET = ROOT / "out/script/translation_sheet.csv"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_MANIFEST = ROOT / "out/script/dialogue_runtime_contracts.json"
DEFAULT_OUT = ROOT / "out/patch/global_dialogue_20cell_audit.json"
CELL_LIMIT = 20
SEMANTIC_GUARDS = {"627963": "그러니까……"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_sheet() -> dict[str, dict[str, str]]:
    csv.field_size_limit(10_000_000)
    out: dict[str, dict[str, str]] = {}
    with TRANSLATION_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            address = str(row.get("abs") or "").upper()
            if address and address not in out:
                out[address] = row
    return out


def audit_bank59(target: bytes, tbl: Tbl) -> tuple[list[dict[str, Any]], list[str]]:
    dictionary = active_dictionary(target)
    prefixes = prefix_map()
    inventory = source_addresses()
    rows: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for logical, inv in sorted(inventory.items()):
        if not 0x590000 <= logical <= 0x59FFFF:
            continue
        payload = payload_at(target, logical)
        if payload is None:
            unreadable.append(f"{logical:06X}")
            continue
        prefix_len = int(inv.get("sheet_prefix_len", prefixes.get(logical, 0)))
        if logical not in prefixes and payload.startswith(bytes.fromhex("173418")):
            prefix_len = 3
        if prefix_len > len(payload):
            unreadable.append(f"{logical:06X}:prefix")
            continue
        text = strip_pad(dictionary.expand(payload[prefix_len:], tbl))
        snapshot_hex = str(inv.get("snapshot_current_payload_hex") or "")
        snapshot_body = str(inv.get("snapshot_current_body") or "")
        if not text and snapshot_body and snapshot_hex and payload.hex().upper() == snapshot_hex:
            text = strip_pad(snapshot_body)
        rows.append(
            {
                "address": f"{logical:06X}",
                "scope": "bank59_event_or_sprite",
                "route": "bank59",
                "text": text,
                "cells": len(text),
                "over_20": len(text) > CELL_LIMIT,
                "source_jp": str(inv.get("inventory_jp") or ""),
            }
        )
    return rows, unreadable


def audit_scenario(
    target: bytes,
    tbl: Tbl,
    manifest: dict[str, Any],
    sheet: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    dictionary = active_dictionary(target)
    rows: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for contract in manifest.get("contracts") or []:
        route = str(contract.get("route") or "")
        if route not in {"scenario_first", "scenario_continuation"}:
            continue
        address = str(contract.get("address") or "").upper()
        if not address or not (0x600000 <= int(address, 16) < 0x640000):
            continue
        source_row = sheet.get(address)
        if source_row is None:
            unreadable.append(f"{address}:missing_translation_sheet")
            continue
        payload = payload_at(target, int(address, 16))
        if payload is None:
            unreadable.append(address)
            continue
        prefix = bytes.fromhex(source_row.get("prefix_hex") or "")
        if prefix:
            if payload.startswith(prefix):
                body = payload[len(prefix) :]
            elif payload.hex().upper() == str(contract.get("baseline_body_hex") or "").upper():
                # Runtime-proven repairs may remove a source-era standalone 18
                # or return a continuation to native grammar.  The manifest is
                # target-bound, so an exact baseline-body match is authoritative
                # for the current record and is safer than reapplying stale
                # translation-sheet prefix metadata.
                body = payload
            else:
                unreadable.append(f"{address}:prefix_drift")
                continue
        else:
            body = payload
        text = strip_pad(dictionary.expand(body, tbl))
        rows.append(
            {
                "address": address,
                "scope": "scenario_60_63",
                "route": route,
                "status": str(contract.get("status") or ""),
                "text": text,
                "cells": len(text),
                "over_20": len(text) > CELL_LIMIT,
                "source_jp": str(source_row.get("jp") or ""),
            }
        )
    return rows, unreadable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    target = args.target.read_bytes()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL)
    sheet = load_sheet()
    bank59, bank59_unreadable = audit_bank59(target, tbl)
    scenario, scenario_unreadable = audit_scenario(target, tbl, manifest, sheet)
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
        "schema_version": 1,
        "generated_by": "tools/audit_global_dialogue_20cell.py",
        "status": "pass" if not offenders and not unreadable and not semantic_failures else "fail",
        "target": {"path": str(args.target), "size": len(target), "sha256": sha(target)},
        "manifest": str(args.manifest),
        "policy": {
            "cell_limit": CELL_LIMIT,
            "scopes": ["bank59 event/sprite", "banks60-63 scenario first/continuation"],
            "excluded": "battle quarantine and unrelated binary/data records",
            "runtime_alias_pages": 5,
            "runtime_alias_local_start": "0600",
        },
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
    print(
        json.dumps(
            {
                "status": report["status"],
                "target_sha256": report["target"]["sha256"],
                "counts": report["counts"],
                "offenders": [
                    {"address": row["address"], "cells": row["cells"], "text": row["text"]}
                    for row in offenders
                ],
                "semantic_guard_failures": semantic_failures,
                "report": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
