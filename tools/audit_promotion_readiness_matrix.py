#!/usr/bin/env python3
"""Produce a fail-closed promotion matrix for every current-TIP workstream."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SHEET = ROOT / "out/script/translation_sheet.csv"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
COVERAGE = ROOT / "out/script/translation_workstream_coverage_audit.json"
RUNTIME = ROOT / "out/script/dialogue_runtime_safety_gate.json"
QUARANTINE_CONTEXT = ROOT / "out/script/scenario_quarantine_context_static_audit.json"
MATRIX = ROOT / "out/script/promotion_readiness_matrix.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(10**9)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def main() -> int:
    queue = read_csv(QUEUE)
    status_counts = Counter(str(row.get("status") or "") for row in queue)
    policies = {
        "fixed_data_structural_excluded_non_dialogue": {
            "class": "excluded_non_dialogue",
            "promotion_allowed": False,
            "reason": "64-6F Original-boundary inventory proves zero production translation targets",
        },
        "scenario_llm_staged_structural_hold": {
            "class": "semantic_complete_structural_hold",
            "promotion_allowed": False,
            "reason": "semantic result exists but structural candidate has not been approved/applied",
        },
        "scenario_gap_structural_preclear": {
            "class": "contract_binding_hold",
            "promotion_allowed": False,
            "reason": "no complete contract-bound semantic result",
        },
        "scenario_structural_quarantine": {
            "class": "structural_quarantine",
            "promotion_allowed": False,
            "reason": "caller/boundary evidence is unresolved",
        },
        "battle_semantic_direct_fit_structural_hold": {
            "class": "direct_payload_structural_hold",
            "promotion_allowed": False,
            "reason": "direct payload fits but battle structural preclear is incomplete",
        },
        "battle_semantic_encoding_hold": {
            "class": "battle_storage_hold",
            "promotion_allowed": False,
            "reason": "semantic result exceeds direct body capacity",
        },
        "leading_fragment_quarantine": {
            "class": "battle_leading_fragment_quarantine",
            "promotion_allowed": False,
            "reason": "speaker prefix/body boundary is unresolved",
        },
        "template_or_stub_quarantine": {
            "class": "battle_template_quarantine",
            "promotion_allowed": False,
            "reason": "runtime visibility/template classification is unproven",
        },
        "stale_parent_tip_rebase": {
            "class": "stale_tip_rebase_hold",
            "promotion_allowed": False,
            "reason": "candidate derives from a non-current parent TIP",
        },
        "battle_contract_native_stock_reuse_ready": {
            "class": "native_stock_static_candidate",
            "promotion_allowed": False,
            "reason": "native stock token candidate still requires user approval",
        },
        "battle_contract_encoding_hold": {
            "class": "battle_contract_storage_hold",
            "promotion_allowed": False,
            "reason": "direct payload does not fit; unproven dictionary/storage route",
        },
        "id_dialogue_native_stock_reuse_ready": {
            "class": "native_stock_static_candidate",
            "promotion_allowed": False,
            "reason": "native stock token candidate still requires user approval",
        },
        "id_dialogue_encoding_hold": {
            "class": "id_storage_hold",
            "promotion_allowed": False,
            "reason": "ID route forbids unproven ext3/dictionary storage",
        },
        "id_contract_unreviewed_policy_required": {
            "class": "semantic_quality_quarantine",
            "promotion_allowed": False,
            "reason": "proposed text failed quality gate or lacks accepted semantic provenance",
        },
    }
    unknown = sorted(status for status in status_counts if status not in policies)
    matrix_rows = []
    for status, count in sorted(status_counts.items()):
        policy = policies.get(status, {
            "class": "unknown_status",
            "promotion_allowed": False,
            "reason": "unrecognized status; fail closed",
        })
        matrix_rows.append({
            "status": status,
            "rows": count,
            **policy,
        })

    coverage = load_json(COVERAGE)
    runtime = load_json(RUNTIME)
    quarantine_context = load_json(QUARANTINE_CONTEXT)
    candidates = {}
    for name in (
        "scenario_native_stock_static_candidate",
        "special_native_stock_static_candidate",
        "main_translation_static_candidate",
    ):
        path = ROOT / "out/patch" / f"{name}.json"
        report = load_json(path)
        gate_name = f"{name}_safety_gate.json"
        gate = load_json(ROOT / "out/patch" / gate_name)
        candidates[name] = {
            "report": str(path.relative_to(ROOT)).replace("\\", "/"),
            "counts": report.get("counts") or {},
            "promotion_allowed": bool(report.get("promotion_allowed")),
            "main_unchanged": report.get("main_unchanged"),
            "safety_gate": gate.get("counts") or {},
            "safety_ok": bool(gate.get("ok")),
        }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_promotion_readiness_matrix.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "remaining hold rows and user/runtime approval are present; matrix is fail-closed",
        "inputs": {
            "main_rom_sha256": sha(ROM),
            "translation_sheet_sha256": sha(SHEET),
            "saveram_sha256": sha(SAVE),
            "contract_sha256": sha(CONTRACT),
            "queue_sha256": sha(QUEUE),
        },
        "counts": {
            "queue_rows": len(queue),
            "status_kinds": len(status_counts),
            "unknown_status_kinds": len(unknown),
            "promotion_allowed_rows": sum(
                row["rows"] for row in matrix_rows if row["promotion_allowed"]
            ),
        },
        "checks": {
            "all_queue_statuses_have_policy": not unknown,
            "all_coverage_checks_pass": all(bool(value) for key, value in (coverage.get("checks") or {}).items() if key != "duplicate_layer_policy"),
            "main_runtime_gate_ok": bool(runtime.get("ok")),
            "main_runtime_hard_failures_zero": int((runtime.get("counts") or {}).get("hard_failures") or 0) == 0,
            "main_runtime_review_items_zero": int((runtime.get("counts") or {}).get("review_items") or 0) == 0,
            "all_candidates_not_promotable": all(not bool(item["promotion_allowed"]) for item in candidates.values()),
            "all_candidate_safety_gates_ok": all(bool(item["safety_ok"]) for item in candidates.values() if item["report"]),
            "scenario_context_audit_is_advisory_only": int((quarantine_context.get("counts") or {}).get("automatic_application_allowed") or 0) == 0,
        },
        "status_matrix": matrix_rows,
        "unknown_statuses": unknown,
        "coverage": coverage.get("checks") or {},
        "runtime_gate": runtime.get("counts") or {},
        "candidates": candidates,
        "scenario_quarantine_context": quarantine_context.get("counts") or {},
    }
    MATRIX.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(MATRIX.relative_to(ROOT)), "counts": report["counts"], "checks": report["checks"]}, ensure_ascii=False, indent=2))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
