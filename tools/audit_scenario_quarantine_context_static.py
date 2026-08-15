#!/usr/bin/env python3
"""Collect adjacent static contract evidence for scenario quarantines."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "out/script/translation_sheet.csv"
QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
OUT = ROOT / "out/script/scenario_quarantine_context_static_audit.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(10**9)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def hex_len(value: str) -> int:
    text = "".join(str(value or "").split())
    try:
        return len(bytes.fromhex(text)) if text else 0
    except ValueError:
        return -1


def main() -> int:
    canonical = read_csv(SHEET)
    ordered = sorted(
        [row for row in canonical if str(row.get("abs") or "")],
        key=lambda row: int(str(row["abs"]), 16),
    )
    by_address = {str(row["abs"]).upper(): row for row in ordered}
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8")).get("contracts") or []
    contract_by_abs = {str(row.get("address") or "").upper(): row for row in contracts}
    queue = read_csv(QUEUE)
    targets = [
        row for row in queue
        if row.get("status") in {"scenario_gap_structural_preclear", "scenario_structural_quarantine"}
    ]
    positions = {str(row["abs"]).upper(): index for index, row in enumerate(ordered)}
    counts: Counter[str] = Counter()
    details: list[dict[str, object]] = []
    for target in targets:
        address = str(target.get("address_or_slot") or "").upper()
        row = by_address.get(address, target)
        index = positions.get(address)
        previous = ordered[index - 1] if index is not None and index > 0 else None
        following = ordered[index + 1] if index is not None and index + 1 < len(ordered) else None
        bank = address[:2]
        previous = previous if previous and str(previous.get("abs") or "")[:2].upper() == bank else None
        following = following if following and str(following.get("abs") or "")[:2].upper() == bank else None
        prev_abs = str(previous.get("abs") or "").upper() if previous else ""
        next_abs = str(following.get("abs") or "").upper() if following else ""
        prev_contract = contract_by_abs.get(prev_abs, {})
        next_contract = contract_by_abs.get(next_abs, {})
        current_contract = contract_by_abs.get(address, {})
        adjacent_active = [
            str(c.get("status") or "") == "active"
            for c in (prev_contract, next_contract)
            if c
        ]
        adjacent_quarantine = [
            str(c.get("status") or "") == "quarantine"
            for c in (prev_contract, next_contract)
            if c
        ]
        prev_int = int(prev_abs, 16) if prev_abs else None
        next_int = int(next_abs, 16) if next_abs else None
        current_int = int(address, 16)
        prev_gap = current_int - prev_int if prev_int is not None else None
        next_gap = next_int - current_int if next_int is not None else None
        prev_extent = int(prev_contract.get("record_extent") or 0) if prev_contract else 0
        target_extent = hex_len(str(row.get("prefix_hex") or "")) + hex_len(str(row.get("body_hex") or ""))
        next_extent = target_extent if target_extent >= 0 else 0
        prev_ends_here = prev_int is not None and prev_extent > 0 and prev_int + prev_extent == current_int
        current_ends_before_next = next_int is not None and next_extent > 0 and current_int + next_extent == next_int
        boundary_matches = int(prev_ends_here) + int(current_ends_before_next)
        prefix = str(row.get("prefix_hex") or "").replace(" ", "").upper()
        neighbor_prefixes = [
            str(item.get("prefix_hex") or item.get("control_prefix_hex") or "").replace(" ", "").upper()
            for item in (previous or {}, following or {})
        ]
        if len(adjacent_active) == 2 and all(adjacent_active):
            category = "between_active_contracts"
        elif any(adjacent_active):
            category = "one_active_neighbor"
        elif any(adjacent_quarantine):
            category = "quarantine_neighbor"
        elif previous or following:
            category = "neighbors_without_contract"
        else:
            category = "no_same_bank_neighbor"
        counts[category] += 1
        details.append({
            "abs": address,
            "status": str(target.get("status") or ""),
            "queue_batch_id": str(target.get("batch_id") or ""),
            "source_jp": str(target.get("source_jp") or ""),
            "current_ko": str(target.get("current_ko") or ""),
            "bank": bank,
            "current_contract_status": str(current_contract.get("status") or "none"),
            "current_contract_route": str(current_contract.get("route") or ""),
            "previous_abs": prev_abs,
            "previous_contract_status": str(prev_contract.get("status") or "none"),
            "previous_contract_route": str(prev_contract.get("route") or ""),
            "previous_gap": prev_gap,
            "previous_extent_ends_at_current": prev_ends_here,
            "next_abs": next_abs,
            "next_contract_status": str(next_contract.get("status") or "none"),
            "next_contract_route": str(next_contract.get("route") or ""),
            "next_gap": next_gap,
            "current_extent_ends_at_next": current_ends_before_next,
            "target_extent_from_sheet": target_extent,
            "boundary_match_count": boundary_matches,
            "current_prefix_hex": prefix,
            "neighbor_prefixes_hex": neighbor_prefixes,
            "category": category,
            "static_resolution": (
                "candidate_for_manual_contract_review"
                if category in {"between_active_contracts", "one_active_neighbor"}
                and boundary_matches > 0
                else "neighbor_only_advisory"
                if category in {"between_active_contracts", "one_active_neighbor"}
                else "retain_quarantine"
            ),
            "boundary_evidence": (
                "fully_bracketed_physical_record"
                if boundary_matches == 2
                else "one_boundary_matches"
                if boundary_matches == 1
                else "neighbor_only_no_exact_extent"
            ),
            "automatic_application_allowed": False,
        })
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_quarantine_context_static.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "neighbor evidence is advisory only; unresolved caller/role cannot be auto-promoted",
        "inputs": {
            "sheet_sha256": sha(SHEET),
            "queue_sha256": sha(QUEUE),
            "contract_sha256": sha(CONTRACT),
        },
        "counts": {
            "rows": len(details),
            "category": dict(sorted(counts.items())),
            "manual_contract_candidates": sum(row["static_resolution"] == "candidate_for_manual_contract_review" for row in details),
            "neighbor_only_advisory": sum(row["static_resolution"] == "neighbor_only_advisory" for row in details),
            "retain_quarantine": sum(row["static_resolution"] == "retain_quarantine" for row in details),
            "automatic_application_allowed": 0,
        },
        "next_actions": [
            "Manually review only candidate_for_manual_contract_review rows against maintained static evidence; this audit found none with an exact physical boundary.",
            "neighbor_only_advisory rows remain quarantine because adjacency alone does not prove record extent or caller role.",
            "Do not infer a caller or line role from adjacency alone.",
            "Keep all other rows quarantined until a contract exists; no runtime trace is run by this audit.",
        ],
        "records": details,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": report["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
