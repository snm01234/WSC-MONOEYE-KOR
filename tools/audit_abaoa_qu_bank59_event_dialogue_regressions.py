#!/usr/bin/env python3
"""Parent-delta regression summary for the A Baoa Qu bank59 candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_regression_audit.json"
PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_candidate.wsc"
WORKLIST = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_worklist.json"
STATIC = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_candidate_audit.json"
RESIDUAL = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_residual_audit.json"
STRUCTURE_PARENT = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_false_segptr_candidate.json"
EXPECTED_PARENT = "186d6d04859146420f4ae826a38a0d35377633d54587046c30046125cbceb241"
EXPECTED_CANDIDATE = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"


class RegressionError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RegressionError(f"JSON root must be object: {path}")
    return value


def main() -> int:
    work = load(WORKLIST)
    static = load(STATIC)
    residual = load(RESIDUAL)
    sp = load(STRUCTURE_PARENT)
    sc = load(STRUCTURE_CANDIDATE)
    fp = load(FALSE_PARENT)
    fc = load(FALSE_CANDIDATE)
    wc = work.get("counts") or {}
    rc = residual.get("counts") or {}
    checks = {
        "parent_identity": sha(PARENT) == EXPECTED_PARENT,
        "candidate_identity": sha(CANDIDATE) == EXPECTED_CANDIDATE,
        "worklist_257_residuals": int(wc.get("targets", -1)) == 257,
        "independent_static_audit_ok": static.get("ok") is True,
        "candidate_all_257_exact": int(rc.get("exact_targets", -1)) == 257,
        "candidate_japanese_residual_zero": int(rc.get("japanese_residual_records", -1)) == 0,
        "script_structure_records_same": sp.get("records_walked") == sc.get("records_walked"),
        "script_structure_issue_count_same": sp.get("issues") == sc.get("issues"),
        "script_structure_kinds_same": sp.get("by_kind") == sc.get("by_kind"),
        "script_structure_rows_same": sp.get("first_issues") == sc.get("first_issues"),
        "false_segmented_pointer_parent_zero": fp.get("sites_found") == 0,
        "false_segmented_pointer_candidate_zero": fc.get("sites_found") == 0,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_abaoa_qu_bank59_event_dialogue_regressions.py",
        "read_only": True,
        "ok": ok,
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(PARENT)},
        "candidate": {"path": str(CANDIDATE.relative_to(ROOT)), "sha256": sha(CANDIDATE)},
        "dialogue_residual": {
            "parent_targets": int(wc.get("targets", 0)),
            "candidate_japanese_residuals": int(rc.get("japanese_residual_records", 0)),
            "exactly_removed": int(wc.get("targets", 0)) - int(rc.get("japanese_residual_records", 0)),
        },
        "script_structure": {
            "records_walked": sc.get("records_walked"),
            "historical_issues": sc.get("issues"),
            "parent_delta": 0,
        },
        "false_segmented_pointer": {
            "parent_sites": fp.get("sites_found"),
            "candidate_sites": fc.get("sites_found"),
        },
        "checks": checks,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not ok:
        raise RegressionError("A Baoa Qu regression audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
