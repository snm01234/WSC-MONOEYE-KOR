#!/usr/bin/env python3
"""Write the final static hold report for the current-TIP translation review.

This report is a planning/hand-off artifact only.  It does not translate,
encode, promote, or run BizHawk/runtime tracing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/script/translation_review_final_hold_report.json"


def load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def digest(rel: str) -> str:
    h = hashlib.sha256()
    with (ROOT / rel).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    audit = load("out/script/translation_workstreams_static_audit.json")
    matrix = load("out/script/promotion_readiness_matrix.json")
    coverage = load("out/script/translation_workstream_coverage_audit.json")
    scenario_storage = load("out/script/scenario_storage_static_audit.json")
    special_storage = load("out/script/special_route_storage_static_audit.json")
    quarantine = load("out/script/scenario_quarantine_context_static_audit.json")
    sheet_scope = load("out/script/translation_sheet_inventory_completeness.json")
    battle = load("out/script/battle_dialogue_llm_review/semantic_ready_static_audit.json")
    execution_plan = load("out/script/translation_review_execution_plan.json")
    execution_audit = load("out/script/translation_review_execution_plan_audit.json")
    test_candidate = load("out/patch/translation_review_overwrite_static_test_candidate.json")
    test_safety = load("out/patch/translation_review_overwrite_static_test_candidate_safety_gate.json")

    status_rows = {str(row["status"]): int(row["rows"]) for row in matrix.get("status_matrix", [])}
    report = {
        "schema_version": 1,
        "artifact": "translation-review-final-hold-report/v1",
        "purpose": "current-TIP translation review hand-off; no translation or promotion performed",
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "source_of_record": {
            "rom": "out/patch/monoeye_ko_expanded.wsc",
            "translation_sheet": "out/script/translation_sheet.csv",
            "saveram": "sram/monoeye_ko_expanded.sav",
            "contract_manifest": "out/script/dialogue_runtime_contracts.json",
            "sha256": {
                "rom": digest("out/patch/monoeye_ko_expanded.wsc"),
                "translation_sheet": digest("out/script/translation_sheet.csv"),
                "saveram": digest("sram/monoeye_ko_expanded.sav"),
                "contract_manifest": digest("out/script/dialogue_runtime_contracts.json"),
            },
        },
        "current_state": {
            "promotion_allowed": bool(matrix.get("promotion_allowed")),
            "promotion_allowed_rows": int((matrix.get("counts") or {}).get("promotion_allowed_rows") or 0),
            "queue_rows": int((matrix.get("counts") or {}).get("queue_rows") or 0),
            "canonical_rows": int((coverage.get("counts") or {}).get("canonical_rows") or 0),
            "active_contracts": int((coverage.get("counts") or {}).get("active_contracts") or 0),
            "sheet_like_files_classified": int((sheet_scope.get("counts") or {}).get("sheet_like_files") or 0),
            "sheet_like_files_unclassified": int((sheet_scope.get("counts") or {}).get("unclassified") or 0),
            "execution_batches": int((execution_plan.get("counts") or {}).get("execution_batches") or 0),
            "mandatory_llm_rereview_rows": int((execution_plan.get("decision_counts") or {}).get("llm_rereview_required") or 0),
            "test_rom_selected_rows": int((test_candidate.get("counts") or {}).get("selected_rows") or 0),
            "test_rom_changed_rows": int((test_candidate.get("counts") or {}).get("changed_rows") or 0),
            "test_rom_deferred_rows": int((test_candidate.get("retranslated_scope") or {}).get("total_deferred_rows") or 0),
        },
        "remaining_work": [
            {
                "id": "SCENARIO-SEMANTIC-REVIEW",
                "route": "scenario",
                "rows": int((audit.get("scenario") or {}).get("semantic_llm_rows") or 0),
                "action": "LLM 재검수/재번역 대상 확정 후 1·2행 bundle 단위로 배치",
                "blocked_by": "semantic result exists, but contract-bound storage/20-cell/terminator checks are still hold",
                "runtime_needed": False,
            },
            {
                "id": "SCENARIO-CONTRACT-GAPS",
                "route": "scenario",
                "rows": status_rows.get("scenario_gap_structural_preclear", 0) + status_rows.get("scenario_structural_quarantine", 0),
                "action": "원본 레코드 경계·호출 역할을 정적 자료로 확정하거나 quarantine 유지",
                "blocked_by": "844 rows lack a complete contract-bound semantic result; automatic application is zero",
                "detail": {
                    "neighbor_only_advisory": int(quarantine.get("counts", {}).get("neighbor_only_advisory") or 0),
                    "retain_quarantine": int(quarantine.get("counts", {}).get("retain_quarantine") or 0),
                },
                "runtime_needed": False,
            },
            {
                "id": "BATTLE-STORAGE",
                "route": "battle",
                "rows": status_rows.get("battle_contract_encoding_hold", 0) + status_rows.get("battle_semantic_encoding_hold", 0) + status_rows.get("battle_semantic_direct_fit_structural_hold", 0),
                "action": "direct payload capacity·native stock 재수납 후보를 정적 검토하고, 미증명 ext3/compact3는 금지",
                "blocked_by": "battle contract/storage route is not proven for the held rows",
                "detail": {
                    "semantic_quality_ok": int(battle.get("counts", {}).get("semantic_quality_ok") or 0),
                    "semantic_quality_quarantine": int((special_storage.get("counts") or {}).get("semantic_quality_quarantine") or 0),
                    "native_stock_candidates": int((special_storage.get("counts") or {}).get("native_stock_candidates") or 0),
                },
                "runtime_needed": False,
            },
            {
                "id": "ID-STORAGE-QUALITY",
                "route": "id_dialogue",
                "rows": status_rows.get("id_dialogue_encoding_hold", 0) + status_rows.get("id_contract_unreviewed_policy_required", 0),
                "action": "ID 본문 품질 재검수 후 입증된 native stock만 정적 후보로 유지",
                "blocked_by": "ID route forbids unproven ext3/dictionary storage; 4 rows remain semantic policy quarantine",
                "runtime_needed": False,
            },
            {
                "id": "REBASE-STALE-SNAPSHOTS",
                "route": "all",
                "rows": status_rows.get("stale_parent_tip_rebase", 0),
                "action": "최신 메인 TIP에서 원문 snapshot을 다시 만들고 동일한 LLM/계약 게이트를 재실행",
                "blocked_by": "current candidates derive from a stale parent TIP",
                "runtime_needed": False,
            },
            {
                "id": "BATTLE-STRUCTURE-QUARANTINE",
                "route": "battle",
                "rows": status_rows.get("leading_fragment_quarantine", 0) + status_rows.get("template_or_stub_quarantine", 0),
                "action": "화자 prefix/body 및 template 여부를 레코드별로 확정하고, 불명확하면 byte-exact quarantine 유지",
                "blocked_by": "speaker/body boundary or runtime visibility is unresolved",
                "runtime_needed": False,
            },
        ],
        "excluded_from_translation": {
            "fixed_data_rows": status_rows.get("fixed_data_structural_excluded_non_dialogue", 0),
            "fixed_data_production_targets": int((audit.get("fixed_data") or {}).get("inventory_production_targets") or 0),
            "reason": "64-6F current-TIP Original-boundary inventory proves these are non-dialogue/fixed data; do not translate them",
        },
        "observed_residuals": {
            "japanese_by_bank": (audit.get("canonical") or {}).get("japanese_residual_by_bank") or {},
            "control_or_nul_by_bank": (audit.get("canonical") or {}).get("control_or_nul_residual_by_bank") or {},
            "interpretation": "diagnostic only; fixed-data residuals remain out of translation scope until a contract proves otherwise",
        },
        "guardrails": {
            "authoritative_gate": "tools/audit_dialogue_runtime_safety_gate.py",
            "retired_heuristics": "tools/audit_dialogue_20cell_candidate.py and tools/audit_dialogue_runtime_evidence_matrix.py are fail-closed quarantine modules",
            "promotion_rule": "no row is promoted until semantic, structural, encoding, and current-TIP checks all pass",
            "no_global_wrap_or_renderer_hook": True,
        },
        "execution_plan": {
            "path": "out/script/translation_review_execution_plan.json",
            "batch_index": "out/script/translation_review_execution_batch_index.csv",
            "batch_directory": "out/script/translation_review_execution_batches",
            "checks": execution_plan.get("checks") or {},
            "audit_path": "out/script/translation_review_execution_plan_audit.json",
            "audit_checks": execution_audit.get("checks") or {},
        },
        "test_rom": {
            "rom": "out/patch/translation_review_overwrite_static_test_candidate.wsc",
            "report": "out/patch/translation_review_overwrite_static_test_candidate.json",
            "safety_gate": "out/patch/translation_review_overwrite_static_test_candidate_safety_gate.json",
            "candidate_contracts": "out/script/translation_review_overwrite_static_test_candidate_runtime_contracts.json",
            "promotion_allowed": bool(test_candidate.get("promotion_allowed")),
            "runtime_validation_performed": bool(test_candidate.get("runtime_validation_performed")),
            "counts": test_candidate.get("counts") or {},
            "retranslated_scope": test_candidate.get("retranslated_scope") or {},
            "safety_counts": test_safety.get("counts") or {},
        },
        "checks": {
            "main_tip_unchanged": bool((audit.get("gates") or {}).get("rom_unchanged")),
            "canonical_sheet_unchanged": bool((audit.get("gates") or {}).get("canonical_sheet_unchanged")),
            "saveram_unchanged": bool((audit.get("gates") or {}).get("saveram_unchanged")),
            "contract_unchanged": bool((audit.get("gates") or {}).get("contract_unchanged")),
            "coverage_complete": all(bool(v) for v in (coverage.get("checks") or {}).values() if isinstance(v, bool)),
            "sheet_scope_complete": all(bool(v) for v in (sheet_scope.get("checks") or {}).values()),
            "runtime_not_performed": audit.get("runtime_validation_performed") is False,
            "promotion_fail_closed": matrix.get("promotion_allowed") is False and int((matrix.get("counts") or {}).get("promotion_allowed_rows") or 0) == 0,
            "execution_plan_complete": all(bool(v) for v in (execution_plan.get("checks") or {}).values()),
            "execution_plan_audit_complete": all(bool(v) for v in (execution_audit.get("checks") or {}).values()),
            "test_rom_static_safety_ok": bool(test_safety.get("ok")) and int((test_safety.get("counts") or {}).get("hard_failures") or 0) == 0 and int((test_safety.get("counts") or {}).get("review_items") or 0) == 0,
            "test_rom_not_promoted": test_candidate.get("promotion_allowed") is False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = all(report["checks"].values())
    print(json.dumps({"ok": ok, "output": str(OUT.relative_to(ROOT)), "remaining_work_items": len(report["remaining_work"]), "promotion_allowed": report["current_state"]["promotion_allowed"]}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
