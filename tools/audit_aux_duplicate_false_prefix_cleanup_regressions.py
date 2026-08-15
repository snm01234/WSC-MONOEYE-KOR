#!/usr/bin/env python3
"""Parent-delta regression summary for the three-record aux cleanup candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_regression_audit.json"
PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_candidate.wsc"
WORKLIST = ROOT / "out/patch/aux_duplicate_false_prefix_residual_worklist.json"
STATIC = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_candidate_audit.json"
STRUCTURE_PARENT = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_false_segptr_candidate.json"
EXPECTED_PARENT = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"
EXPECTED_CANDIDATE = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"


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
    sp = load(STRUCTURE_PARENT)
    sc = load(STRUCTURE_CANDIDATE)
    fp = load(FALSE_PARENT)
    fc = load(FALSE_CANDIDATE)
    target_checks = static.get("target_checks") or []
    checks = {
        "parent_identity": sha(PARENT) == EXPECTED_PARENT,
        "candidate_identity": sha(CANDIDATE) == EXPECTED_CANDIDATE,
        "duplicate_worklist_exactly_two": int((work.get("counts") or {}).get("targets", -1)) == 2,
        "independent_static_audit_ok": static.get("ok") is True,
        "all_three_targets_exact": len(target_checks) == 3 and all(row.get("ok") is True for row in target_checks),
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
        "generated_by": "tools/audit_aux_duplicate_false_prefix_cleanup_regressions.py",
        "read_only": True,
        "ok": ok,
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(PARENT)},
        "candidate": {"path": str(CANDIDATE.relative_to(ROOT)), "sha256": sha(CANDIDATE)},
        "targets": [
            {
                "abs": row.get("abs"),
                "after_text": row.get("after_text"),
                "duplicate_peers": [peer.get("abs") for peer in row.get("peers") or []],
                "ok": row.get("ok"),
            }
            for row in target_checks
        ],
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
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not ok:
        raise RegressionError("duplicate false-prefix regression audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
