#!/usr/bin/env python3
"""Prove static coverage of the current-TIP translation workstreams."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "out/script/translation_sheet.csv"
QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
OUT = ROOT / "out/script/translation_workstream_coverage_audit.json"
MAIN_SHA = "d2b7301b0f51071a566dd473be4a528d1d13a4305fc251de5543133ab5b0db20"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(10**9)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    canonical = read_csv(SHEET)
    queue = read_csv(QUEUE)
    queue_by_abs: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in queue:
        queue_by_abs[str(row.get("address_or_slot") or "").upper()].append(row)

    result_addresses: set[str] = set()
    result_files = []
    result_tip_mismatch = []
    missing_snapshots = []
    semantic_result_rows = 0
    for manifest_path in sorted(RESULT_DIR.glob("MR*_result_manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result_files.append(str(manifest_path.relative_to(ROOT)).replace("\\", "/"))
        if str(manifest.get("main_tip_sha256") or "").lower() != MAIN_SHA:
            result_tip_mismatch.append(str(manifest_path.relative_to(ROOT)).replace("\\", "/"))
        source = str(manifest.get("source_batch") or "")
        if source and not (ROOT / source).is_file():
            missing_snapshots.append(source)
        result_path = ROOT / str(manifest.get("result") or "")
        if result_path.is_file():
            for row in read_csv(result_path):
                if str(row.get("new_translation_source") or "") == "llm":
                    address = str(row.get("abs") or "").upper()
                    if address:
                        result_addresses.add(address)
                        semantic_result_rows += 1

    canonical_addresses = {str(row.get("abs") or "").upper() for row in canonical}
    queued_addresses = set(queue_by_abs)
    # Results are also represented in the static queue when they are staged or
    # quarantined.  Use the union for coverage, but keep result-only addresses
    # separate so the report distinguishes independent semantic evidence from
    # queue bookkeeping.
    covered = queued_addresses | result_addresses
    missing_canonical = sorted(canonical_addresses - covered)
    extra_queue = sorted(queued_addresses - canonical_addresses)
    queue_noncanonical = sorted(queued_addresses - canonical_addresses)
    unexpected_extra_queue = sorted(
        row.get("address_or_slot") or ""
        for row in queue
        if str(row.get("address_or_slot") or "").upper() not in canonical_addresses
        and str(row.get("workstream") or "") not in {
            "battle", "battle_or_uncovered_rebase", "battle_contract", "id_dialogue"
        }
    )

    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))
    active_contracts = [
        row for row in contracts.get("contracts") or []
        if str(row.get("status") or "") == "active"
    ]
    contract_missing: list[str] = []
    for contract in active_contracts:
        address = str(contract.get("address") or "").upper()
        route = str(contract.get("route") or "")
        if route.startswith("scenario"):
            present = address in result_addresses or address in queued_addresses
        else:
            present = address in queued_addresses
        if not present:
            contract_missing.append(address)

    duplicate_layers = {
        address: [
            {"workstream": row.get("workstream"), "batch_id": row.get("batch_id"), "status": row.get("status")}
            for row in rows
        ]
        for address, rows in queue_by_abs.items()
        if len(rows) > 1
    }
    unexpected_duplicate_layers = {
        address: layers
        for address, layers in duplicate_layers.items()
        if {
            str(item.get("workstream") or "") for item in layers
        } != {"battle", "battle_or_uncovered_rebase"}
        or not any(str(item.get("status") or "") in {
            "leading_fragment_quarantine", "battle_semantic_structural_hold",
            "battle_semantic_direct_fit_structural_hold", "battle_semantic_encoding_hold"
        } for item in layers)
        or not any(str(item.get("status") or "") == "stale_parent_tip_rebase" for item in layers)
    }
    invalid_batches = [
        {"address": row.get("address_or_slot"), "batch_id": row.get("batch_id"), "batch_order": row.get("batch_order")}
        for row in queue
        if not row.get("batch_id") or not row.get("batch_order") or int(row.get("batch_order") or 0) > 60
    ]
    invalid_context = [
        row.get("address_or_slot")
        for row in queue
        if (row.get("workstream") == "scenario" and row.get("context_required") != "yes")
        or (row.get("workstream") != "scenario" and row.get("context_required") != "no")
    ]

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_translation_workstream_coverage.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "inputs": {
            "main_tip_sha256": MAIN_SHA,
            "sheet_sha256": sha(SHEET),
            "contract_sha256": sha(CONTRACT),
        },
        "counts": {
            "canonical_rows": len(canonical),
            "canonical_addresses": len(canonical_addresses),
            "queue_rows": len(queue),
            "queued_addresses": len(queued_addresses),
            "semantic_result_rows": semantic_result_rows,
            "semantic_result_addresses": len(result_addresses),
            "covered_addresses": len(covered),
            "missing_canonical": len(missing_canonical),
            "extra_queue_addresses": len(extra_queue),
            "unexpected_extra_queue_addresses": len(unexpected_extra_queue),
            "active_contracts": len(active_contracts),
            "missing_active_contracts": len(contract_missing),
            "duplicate_layer_addresses": len(duplicate_layers),
            "invalid_batches": len(invalid_batches),
            "invalid_context": len(invalid_context),
            "result_manifests": len(result_files),
            "result_tip_mismatch": len(result_tip_mismatch),
            "missing_source_snapshots": len(missing_snapshots),
        },
        "checks": {
            "all_canonical_rows_covered": not missing_canonical,
            "all_active_contracts_covered": not contract_missing,
            "all_batch_rows_bounded": not invalid_batches,
            "context_policy_consistent": not invalid_context,
            "all_result_manifests_current_tip_bound": not result_tip_mismatch,
            "all_result_source_snapshots_present": not missing_snapshots,
            "noncanonical_queue_scope_allowed": not unexpected_extra_queue,
            "duplicate_layers_are_known_overlap": not unexpected_duplicate_layers,
            "duplicate_layer_policy": "allowed_only_for_overlapping_hold_layers; inspect duplicate_layer_addresses",
        },
        "missing_canonical": missing_canonical,
        "missing_active_contracts": contract_missing,
        "extra_queue_addresses": extra_queue,
        "queue_noncanonical_addresses": queue_noncanonical,
        "unexpected_extra_queue_addresses": unexpected_extra_queue,
        "duplicate_layer_addresses": duplicate_layers,
        "unexpected_duplicate_layer_addresses": unexpected_duplicate_layers,
        "invalid_batches": invalid_batches,
        "invalid_context": invalid_context,
        "result_tip_mismatch": result_tip_mismatch,
        "missing_source_snapshots": missing_snapshots,
        "result_manifests": result_files,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": report["counts"], "checks": report["checks"]}, ensure_ascii=False, indent=2))
    return 0 if all(
        report["checks"][key]
        for key in (
            "all_canonical_rows_covered",
            "all_active_contracts_covered",
            "all_batch_rows_bounded",
            "context_policy_consistent",
            "all_result_manifests_current_tip_bound",
            "all_result_source_snapshots_present",
            "noncanonical_queue_scope_allowed",
            "duplicate_layers_are_known_overlap",
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
