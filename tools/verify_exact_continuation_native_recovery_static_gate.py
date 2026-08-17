#!/usr/bin/env python3
"""Aggregate the Phase A-D gates for exact-continuation native recovery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "exact_continuation_native_recovery_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/exact_continuation_native_recovery_candidate.sav"
ID_AUDIT = PATCH / "exact_continuation_native_recovery_id_audit.json"
BUILD = PATCH / "exact_continuation_native_recovery_candidate_report.json"
EXACT = PATCH / "exact_continuation_native_recovery_exact_audit.json"
RUNTIME = PATCH / "exact_continuation_native_recovery_runtime_safety.json"
BATTLE = PATCH / "exact_continuation_native_recovery_battle_audit.json"
TERM = PATCH / "exact_continuation_native_recovery_terminology_audit.json"
MATRIX = PATCH / "exact_continuation_native_recovery_test_matrix.json"
OUT = PATCH / "exact_continuation_native_recovery_static_gate.json"

MAIN_SHA = "fc7c3a426c866f8b60f5056571349c79d6ba11a2632beee4209dfebbf8a0c5e9"
CANDIDATE_SHA = "f68b3261beecc32047d17952e36bc2b891cd5d66410f9fc9293487571a0fc8e2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"invalid report object: {path}")
    return value


def main() -> int:
    reports = {name: load(path) for name, path in {
        "id_audit": ID_AUDIT,
        "build": BUILD,
        "exact": EXACT,
        "runtime": RUNTIME,
        "battle": BATTLE,
        "terminology": TERM,
        "matrix": MATRIX,
    }.items()}
    exact_counts = reports["exact"].get("counts") or {}
    runtime_counts = reports["runtime"].get("counts") or {}
    battle_counts = reports["battle"].get("counts") or {}
    gates: dict[str, bool] = {
        "main_sha_unchanged": sha(MAIN) == MAIN_SHA,
        "candidate_sha_bound": sha(CANDIDATE) == CANDIDATE_SHA,
        "phase_a_five_ids_reclaimable": reports["id_audit"].get("ok") is True and len(reports["id_audit"].get("reclaimable_ids") or []) == 5,
        "selected_exact_risk_9_to_0": int(exact_counts.get("parent_selected_control_following_exact_risks", -1)) == 9 and int(exact_counts.get("target_selected_control_following_exact_risks", -1)) == 0,
        "rendered_9_of_9": len(reports["exact"].get("targets") or []) == 9 and all(row.get("ok") is True for row in reports["exact"].get("targets") or []),
        "native_two_token_9_of_9": int(exact_counts.get("native_two_token_records", -1)) == 9,
        "direct_e518_zero": int(exact_counts.get("direct_e518_records", -1)) == 0,
        "compact3_zero": int(exact_counts.get("compact3_records", -1)) == 0,
        "helper_consumer_allowlist_exact": len(reports["exact"].get("helpers") or []) == 5 and all(row.get("ok") is True for row in reports["exact"].get("helpers") or []),
        "non_target_diff_zero": int(exact_counts.get("unexpected_diff_runs", -1)) == 0,
        "runtime_hard_zero_review_zero": reports["runtime"].get("ok") is True and int(runtime_counts.get("hard_failures", -1)) == 0 and int(runtime_counts.get("review_items", -1)) == 0,
        "battle_exact_clean": reports["battle"].get("ok") is True and int(battle_counts.get("failures", -1)) == 0,
        "terminology_clean": reports["terminology"].get("status") == "clean",
        "live_saveram_not_written_by_candidate_pipeline": CANDIDATE_SAVE.read_bytes() == LIVE_SAVE.read_bytes(),
        "matrix_has_nine_rows": len(reports["matrix"].get("rows") or []) == 9,
        "excluded_v3_hypotheses_preserved": len(reports["exact"].get("excluded_prior_v3_rows") or []) == 2 and all(row.get("byte_exact_to_main") is True for row in reports["exact"].get("excluded_prior_v3_rows") or []),
    }
    bound_reports = {
        "exact": ((reports["exact"].get("target") or {}).get("sha256")),
        "runtime": ((reports["runtime"].get("target") or {}).get("sha256")),
        "battle": ((reports["battle"].get("target") or {}).get("sha256")),
        "terminology": ((reports["terminology"].get("tip") or {}).get("sha256")),
        "build": ((reports["build"].get("candidate") or {}).get("sha256")),
        "matrix": reports["matrix"].get("candidate_sha256"),
    }
    gates["all_reports_bound_to_candidate_sha"] = all(str(value).lower() == CANDIDATE_SHA for value in bound_reports.values())
    ok = all(gates.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/verify_exact_continuation_native_recovery_static_gate.py",
        "ok": ok,
        "status": "static_pass_runtime_validation_pending" if ok else "static_gate_failed",
        "main": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(MAIN)},
        "candidate": {"path": str(CANDIDATE.relative_to(ROOT)), "sha256": sha(CANDIDATE)},
        "saveram": {
            "live_path": str(LIVE_SAVE.relative_to(ROOT)),
            "candidate_snapshot_path": str(CANDIDATE_SAVE.relative_to(ROOT)),
            "live_sha256": sha(LIVE_SAVE),
            "candidate_snapshot_sha256": sha(CANDIDATE_SAVE),
            "byte_exact_now": CANDIDATE_SAVE.read_bytes() == LIVE_SAVE.read_bytes(),
        },
        "candidate_report_bindings": bound_reports,
        "gates": gates,
        "counts": {
            "passed": sum(gates.values()),
            "total": len(gates),
            "failed": sum(not value for value in gates.values()),
        },
        "phase_e": {
            "test_matrix": str(MATRIX.relative_to(ROOT)),
            "rows": len(reports["matrix"].get("rows") or []),
            "runtime_validation_performed": False,
            "promotion_allowed": False,
            "note": "bundle/address/context mapping is present; stage labels are not guessed where no authoritative typed caller map exists",
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": ok, "status": report["status"], "counts": report["counts"], "out": str(OUT.relative_to(ROOT))}, ensure_ascii=True, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
