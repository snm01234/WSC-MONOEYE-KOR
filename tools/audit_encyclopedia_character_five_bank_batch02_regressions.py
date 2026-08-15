#!/usr/bin/env python3
"""Parent-delta regression summary for character five-bank batch02."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_regression_audit.json"

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_candidate.wsc"
STATIC = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_candidate_audit.json"
RESIDUAL_PARENT = ROOT / "out/patch/encyclopedia_character_current_residual_audit.json"
RESIDUAL_CANDIDATE = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_residual_audit.json"
STRUCTURE_PARENT = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_structure.json"
FALSE_PARENT = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_false_segptr.json"

EXPECTED_PARENT = "05a2124e5402c8e8f46b1ca7af3c131748a034939af7c6381cdbb4242d43f65e"
EXPECTED_CANDIDATE = "67f0e7401ec44e9c267ed0e86a010d078edea22afac2370b576fbf169fff26af"


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
    static = load(STATIC)
    rp = load(RESIDUAL_PARENT)
    rc = load(RESIDUAL_CANDIDATE)
    sp = load(STRUCTURE_PARENT)
    sc = load(STRUCTURE_CANDIDATE)
    fp = load(FALSE_PARENT)
    fc = load(FALSE_CANDIDATE)
    parent_counts = rp.get("counts") or {}
    candidate_counts = rc.get("counts") or {}
    checks = {
        "parent_identity": sha(PARENT) == EXPECTED_PARENT,
        "candidate_identity": sha(CANDIDATE) == EXPECTED_CANDIDATE,
        "independent_static_audit_ok": static.get("ok") is True,
        "residual_parent_603": int(parent_counts.get("actionable_records", -1)) == 603,
        "residual_candidate_539": int(candidate_counts.get("actionable_records", -1)) == 539,
        "residual_exactly_64_removed": (
            int(parent_counts.get("actionable_records", -1))
            - int(candidate_counts.get("actionable_records", -1))
            == 64
        ),
        "short_records_unchanged_7": (
            int(parent_counts.get("short_body_under_4", -1))
            == int(candidate_counts.get("short_body_under_4", -1))
            == 7
        ),
        "candidate_unreadable_zero": int(candidate_counts.get("unreadable_records", -1)) == 0,
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
        "generated_by": "tools/audit_encyclopedia_character_five_bank_batch02_regressions.py",
        "read_only": True,
        "ok": ok,
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(PARENT)},
        "candidate": {"path": str(CANDIDATE.relative_to(ROOT)), "sha256": sha(CANDIDATE)},
        "encyclopedia_residual": {
            "parent": parent_counts,
            "candidate": candidate_counts,
            "actionable_delta": int(candidate_counts.get("actionable_records", 0))
            - int(parent_counts.get("actionable_records", 0)),
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
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not ok:
        raise RegressionError("character batch02 regression audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
