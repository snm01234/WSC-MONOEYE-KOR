#!/usr/bin/env python3
"""Independent evidence audit for the cumulative ID/scenario candidate."""
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

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/runtime_text_id_scenario_all_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/runtime_text_id_scenario_all_candidate.sav"
BUILD = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_report.json"
FAMILY = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_family_audit.json"
FALSE_SEGPTR = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_false_segptr.json"
STRUCTURED = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_structured_tables.json"
PARENT_STRUCTURE = ROOT / "out/patch/runtime_text_id_scenario_all_parent_structure.json"
CANDIDATE_STRUCTURE = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_structure.json"
PARENT_MIXED = ROOT / "out/patch/runtime_text_id_scenario_all_parent_mixed.json"
CANDIDATE_MIXED = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_mixed.json"
OUT = ROOT / "out/patch/runtime_text_id_scenario_all_candidate_audit.json"

EXPECTED_PARENT = "03a6f1c42e9fff43a143c5bc1dd45a0fa23abc7be02e61c207b9e877facfc0d8"
EXPECTED_CANDIDATE = "e40064e10ea792cf6eb587d05f4a18ac9784df062fc2cbe4d85e8a9b82a08a05"
EXPECTED_RECORDS = 511
EXPECTED_ID = 379
EXPECTED_SCENARIO = 132
EXPECTED_CHECKSUM = "F28E"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root is not object: {path}")
    return value


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def checksum_state(data: bytes) -> tuple[str, bool]:
    scratch = bytearray(data)
    value = update_ws_checksum(scratch)
    return f"{value:04X}", bytes(scratch) == data


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if len(candidate) != ROM_SIZE or sha(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("candidate identity drifted")
    if len(live_save) != SAVE_SIZE or len(candidate_save) != SAVE_SIZE:
        raise AuditError("SaveRAM missing or wrong size")

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
    by_family = family_counts.get("by_family") or {}
    id_family = by_family.get("id_command_bundle") or {}
    scenario_family = by_family.get("prefixed_dialogue") or {}
    checksum, checksum_exact = checksum_state(candidate)
    tables = [validate_protected_table(candidate, table) for table in PROTECTED_TABLES]
    mixed_keys = (
        "population_records", "aux_records", "name75_records",
        "records_with_hangul", "preserved_prefixes_skipped",
        "broken_word_hits", "split_compound_hits", "particle_hits", "scan_errors",
    )
    parent_mixed_counts = parent_mixed.get("counts") or {}
    candidate_mixed_counts = candidate_mixed.get("counts") or {}
    mixed_equal = all(
        parent_mixed_counts.get(key) == candidate_mixed_counts.get(key)
        for key in mixed_keys
    )

    checks = {
        "build_report_ok": build.get("ok") is True,
        "build_candidate_bound": str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CANDIDATE,
        "build_population_exact": (
            int(build_counts.get("records", -1)) == EXPECTED_RECORDS
            and int(build_counts.get("id_records", -1)) == EXPECTED_ID
            and int(build_counts.get("scenario_records", -1)) == EXPECTED_SCENARIO
        ),
        "build_checks_all_true": bool(build_checks) and all(value is True for value in build_checks.values()),
        "family_audit_bound": str(((family.get("inputs") or {}).get("tip") or {}).get("sha256") or "").lower() == EXPECTED_CANDIDATE,
        "id_residual_zero": int(id_family.get("residuals", -1)) == 0,
        "scenario_residual_zero": int(scenario_family.get("residuals", -1)) == 0,
        "id_clean_population": int(id_family.get("records", -1)) == 689,
        "scenario_clean_population": int(scenario_family.get("records", -1)) == 8118,
        "false_segmented_pointer_zero": int(segptr.get("sites_found", -1)) == 0,
        "structured_report_clean": structured.get("ok") is True and int(structured.get("issue_count", -1)) == 0,
        "protected_tables_exact": all(row.get("ok") is True for row in tables),
        "script_structure_issue_set_unchanged": (
            parent_structure.get("issues") == candidate_structure.get("issues")
            and parent_structure.get("by_kind") == candidate_structure.get("by_kind")
            and parent_structure.get("first_issues") == candidate_structure.get("first_issues")
        ),
        "mixed_artifact_metrics_unchanged": mixed_equal,
        "checksum_exact": checksum_exact and checksum == EXPECTED_CHECKSUM,
        "five_alias_pages_still_active": detect_ext3_alias_page_count(candidate) == 5,
        "candidate_saveram_is_snapshot_only": len(candidate_save) == SAVE_SIZE,
        "main_tip_unchanged": PARENT.read_bytes() == parent,
        "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_runtime_text_id_scenario_all_candidate.py",
        "read_only": True,
        "ok": all(checks.values()),
        "inputs": {
            "parent": identity(PARENT, parent),
            "candidate": identity(CANDIDATE, candidate),
            "live_saveram": identity(LIVE_SAVE, live_save),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "build_report": identity(BUILD),
            "family_audit": identity(FAMILY),
            "false_segptr": identity(FALSE_SEGPTR),
            "structured": identity(STRUCTURED),
            "parent_structure": identity(PARENT_STRUCTURE),
            "candidate_structure": identity(CANDIDATE_STRUCTURE),
            "parent_mixed": identity(PARENT_MIXED),
            "candidate_mixed": identity(CANDIDATE_MIXED),
        },
        "counts": {
            "records": build_counts.get("records"),
            "id_records": build_counts.get("id_records"),
            "scenario_records": build_counts.get("scenario_records"),
            "id_residuals": id_family.get("residuals"),
            "scenario_residuals": scenario_family.get("residuals"),
            "false_segmented_pointer_sites": segptr.get("sites_found"),
            "structured_issues": structured.get("issue_count"),
        },
        "checksum": checksum,
        "checks": checks,
        "protected_tables": tables,
        "mixed_metrics_parent": {key: parent_mixed_counts.get(key) for key in mixed_keys},
        "mixed_metrics_candidate": {key: candidate_mixed_counts.get(key) for key in mixed_keys},
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
