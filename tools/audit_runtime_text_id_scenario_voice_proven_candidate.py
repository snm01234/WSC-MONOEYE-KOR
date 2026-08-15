#!/usr/bin/env python3
"""Independent audit for the 514-record cumulative runtime-text candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import detect_ext3_alias_page_count  # noqa: E402
from monoeye_rom import update_ws_checksum  # noqa: E402
from structured_token_write_guard import PROTECTED_TABLES, validate_protected_table  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BASE = ROOT / "out/patch/runtime_text_id_scenario_all_candidate.wsc"
BASE_AUDIT = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_audit.json"
CANDIDATE = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/runtime_text_id_scenario_voice_proven_candidate.sav"
BUILD = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate_report.json"
FAMILY = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate_family_audit.json"
FALSE_SEGPTR = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate_false_segptr.json"
STRUCTURED = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate_structured_tables.json"
PARENT_STRUCTURE = ROOT / "out/patch/runtime_text_id_scenario_all_parent_structure.json"
CANDIDATE_STRUCTURE = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate_structure.json"
PARENT_MIXED = ROOT / "out/patch/runtime_text_id_scenario_all_parent_mixed.json"
CANDIDATE_MIXED = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate_mixed.json"
OUT = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate_audit.json"

EXPECTED_MAIN = "03a6f1c42e9fff43a143c5bc1dd45a0fa23abc7be02e61c207b9e877facfc0d8"
EXPECTED_BASE = "e40064e10ea792cf6eb587d05f4a18ac9784df062fc2cbe4d85e8a9b82a08a05"
EXPECTED_CANDIDATE = "5919fbf7bb25d692ca0593a63961fe34148a69ba9c0ef7b5a04b153b1c7414c4"
EXPECTED_CHECKSUM = "F162"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def checksum_state(data: bytes) -> tuple[str, bool]:
    scratch = bytearray(data)
    value = update_ws_checksum(scratch)
    return f"{value:04X}", bytes(scratch) == data


def main() -> int:
    main = MAIN.read_bytes()
    base = BASE.read_bytes()
    candidate = CANDIDATE.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(main) != ROM_SIZE or sha(main) != EXPECTED_MAIN:
        raise AuditError("main identity drifted")
    if len(base) != ROM_SIZE or sha(base) != EXPECTED_BASE:
        raise AuditError("base candidate identity drifted")
    if len(candidate) != ROM_SIZE or sha(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("cumulative candidate identity drifted")
    if len(live_save) != SAVE_SIZE or len(candidate_save) != SAVE_SIZE:
        raise AuditError("SaveRAM missing or wrong size")

    base_audit = load_json(BASE_AUDIT)
    build = load_json(BUILD)
    family = load_json(FAMILY)
    segptr = load_json(FALSE_SEGPTR)
    structured = load_json(STRUCTURED)
    parent_structure = load_json(PARENT_STRUCTURE)
    candidate_structure = load_json(CANDIDATE_STRUCTURE)
    parent_mixed = load_json(PARENT_MIXED)
    candidate_mixed = load_json(CANDIDATE_MIXED)

    build_counts = build.get("counts") or {}
    build_checks = build.get("checks") or {}
    family_counts = family.get("counts") or {}
    families = family_counts.get("by_family") or {}
    id_family = families.get("id_command_bundle") or {}
    scenario_family = families.get("prefixed_dialogue") or {}
    voice_family = families.get("voice_tagged_run") or {}
    checksum, checksum_exact = checksum_state(candidate)
    tables = [validate_protected_table(candidate, table) for table in PROTECTED_TABLES]

    parent_mixed_counts = parent_mixed.get("counts") or {}
    candidate_mixed_counts = candidate_mixed.get("counts") or {}
    invariant_mixed_keys = (
        "population_records", "aux_records", "name75_records",
        "preserved_prefixes_skipped", "broken_word_hits",
        "split_compound_hits", "particle_hits", "scan_errors",
    )
    mixed_invariants = all(
        parent_mixed_counts.get(key) == candidate_mixed_counts.get(key)
        for key in invariant_mixed_keys
    )
    hangul_delta = (
        int(candidate_mixed_counts.get("records_with_hangul") or 0)
        - int(parent_mixed_counts.get("records_with_hangul") or 0)
    )

    applied = list(build.get("applied") or [])
    expected_targets = {"5D1E3E", "5E6586", "5E65A7"}
    actual_targets = {str(row.get("target") or "") for row in applied}
    checks = {
        "base_independent_audit_ok": base_audit.get("ok") is True,
        "base_audit_candidate_bound": str(((base_audit.get("inputs") or {}).get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_BASE,
        "build_report_ok": build.get("ok") is True,
        "build_candidate_bound": str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CANDIDATE,
        "cumulative_population_exact": (
            int(build_counts.get("base_id_scenario_records", -1)) == 511
            and int(build_counts.get("duplicate_proven_voice_records", -1)) == 3
            and int(build_counts.get("total_cumulative_records", -1)) == 514
        ),
        "duplicate_proven_target_set_exact": actual_targets == expected_targets,
        "build_checks_all_true": bool(build_checks) and all(value is True for value in build_checks.values()),
        "family_audit_bound": str(((family.get("inputs") or {}).get("tip") or {}).get("sha256") or "").lower() == EXPECTED_CANDIDATE,
        "id_residual_zero": int(id_family.get("residuals", -1)) == 0,
        "scenario_residual_zero": int(scenario_family.get("residuals", -1)) == 0,
        "voice_diagnostic_fail_closed": (
            int(voice_family.get("residuals", -1)) == 0
            and int(family_counts.get("voice_boundary_unproven_quarantine", -1)) > 0
            and "voice_boundary_unproven_quarantine" in (voice_family.get("by_classification") or {})
        ),
        "false_segmented_pointer_zero": int(segptr.get("sites_found", -1)) == 0,
        "structured_report_clean": structured.get("ok") is True and int(structured.get("issue_count", -1)) == 0,
        "protected_tables_exact": all(row.get("ok") is True for row in tables),
        "script_structure_issue_set_unchanged": (
            parent_structure.get("issues") == candidate_structure.get("issues")
            and parent_structure.get("by_kind") == candidate_structure.get("by_kind")
            and parent_structure.get("first_issues") == candidate_structure.get("first_issues")
        ),
        "mixed_artifact_error_metrics_unchanged": mixed_invariants,
        "mixed_hangul_population_expected_minus_three": hangul_delta == -3,
        "checksum_exact": checksum_exact and checksum == EXPECTED_CHECKSUM,
        "five_alias_pages_still_active": detect_ext3_alias_page_count(candidate) == 5,
        "candidate_saveram_snapshot_present": len(candidate_save) == SAVE_SIZE,
        "main_tip_unchanged": MAIN.read_bytes() == main,
        "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save,
    }

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_runtime_text_id_scenario_voice_proven_candidate.py",
        "read_only": True,
        "ok": all(checks.values()),
        "inputs": {
            "main": identity(MAIN, main),
            "base_candidate": identity(BASE, base),
            "base_audit": identity(BASE_AUDIT),
            "candidate": identity(CANDIDATE, candidate),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "live_saveram": identity(LIVE_SAVE, live_save),
            "build_report": identity(BUILD),
            "family_audit": identity(FAMILY),
            "false_segptr": identity(FALSE_SEGPTR),
            "structured": identity(STRUCTURED),
            "candidate_structure": identity(CANDIDATE_STRUCTURE),
            "candidate_mixed": identity(CANDIDATE_MIXED),
        },
        "counts": {
            "cumulative_records": 514,
            "id_records": 379,
            "scenario_records": 132,
            "duplicate_proven_voice_records": 3,
            "id_residuals": id_family.get("residuals"),
            "scenario_residuals": scenario_family.get("residuals"),
            "voice_diagnostic_quarantine": family_counts.get("voice_boundary_unproven_quarantine"),
            "false_segmented_pointer_sites": segptr.get("sites_found"),
            "structured_issues": structured.get("issue_count"),
        },
        "checksum": checksum,
        "checks": checks,
        "mixed_metrics": {
            "parent": parent_mixed_counts,
            "candidate": candidate_mixed_counts,
            "records_with_hangul_delta": hangul_delta,
            "interpretation": "three mixed leading-glyph records became clean Korean",
        },
        "protected_tables": tables,
        "promotion": "blocked_pending_user_runtime_confirmation",
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
