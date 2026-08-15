#!/usr/bin/env python3
"""Parent-delta regression audit for the five-bank runtime probe candidate.

Several legacy gates intentionally fail on the current main TIP because their
original-ROM baselines include already promoted changes.  For this runtime-only
probe, the meaningful safety condition is that the complete historical issue
population remains identical to the parent, except for the four explicitly
redirected bank59 token bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/patch/ext3_five_bank_runtime_probe_regression_audit.json"

PARENT_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE_ROM = ROOT / "out/patch/ext3_five_bank_runtime_probe_candidate.wsc"
BUILD_REPORT = ROOT / "out/patch/ext3_five_bank_runtime_probe_report.json"
STATIC_AUDIT = ROOT / "out/patch/ext3_five_bank_runtime_probe_candidate_audit.json"
STRUCTURE_PARENT = ROOT / "out/patch/ext3_five_bank_runtime_probe_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/ext3_five_bank_runtime_probe_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/ext3_five_bank_runtime_probe_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/ext3_five_bank_runtime_probe_false_segptr_candidate.json"
NONDLG_PARENT = ROOT / "out/patch/ext3_five_bank_runtime_probe_nondialogue_parent.json"
NONDLG_CANDIDATE = ROOT / "out/patch/ext3_five_bank_runtime_probe_nondialogue_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/ext3_five_bank_runtime_probe_smoke_parent.json"
SMOKE_CANDIDATE = ROOT / "out/patch/ext3_five_bank_runtime_probe_smoke_candidate.json"

EXPECTED_PARENT_SHA256 = "0e060c6ab73d62acdf307afd9ddcc8cbf5853365b9f22196c52497937c23ea89"
EXPECTED_CANDIDATE_SHA256 = "05a2124e5402c8e8f46b1ca7af3c131748a034939af7c6381cdbb4242d43f65e"
EXPECTED_REDIRECT_SITES = {"59:001C", "59:0033", "59:0038", "59:004A"}


class AuditError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def main() -> int:
    build = load_object(BUILD_REPORT)
    static = load_object(STATIC_AUDIT)
    sp = load_object(STRUCTURE_PARENT)
    sc = load_object(STRUCTURE_CANDIDATE)
    fp = load_object(FALSE_PARENT)
    fc = load_object(FALSE_CANDIDATE)
    np = load_object(NONDLG_PARENT)
    nc = load_object(NONDLG_CANDIDATE)
    mp = load_object(SMOKE_PARENT)
    mc = load_object(SMOKE_CANDIDATE)

    parent_sites = {row["site"]: row for row in mp.get("unit_violation_sites") or []}
    candidate_sites = {row["site"]: row for row in mc.get("unit_violation_sites") or []}
    changed_common = {
        site
        for site in set(parent_sites) & set(candidate_sites)
        if parent_sites[site] != candidate_sites[site]
    }

    nondialogue_sections = (
        "check_i_dict_expansion",
        "check_ii_marker_records",
        "check_iii_length_terminator",
        "check_iv_nested_dictionary_detachment",
    )
    checks = {
        "parent_identity": sha256(PARENT_ROM) == EXPECTED_PARENT_SHA256,
        "candidate_identity": sha256(CANDIDATE_ROM) == EXPECTED_CANDIDATE_SHA256,
        "build_report_ok": build.get("ok") is True,
        "static_audit_ok": static.get("ok") is True,
        "structure_record_count_same": sp.get("records_walked") == sc.get("records_walked"),
        "structure_issue_count_same": sp.get("issues") == sc.get("issues"),
        "structure_issue_kinds_same": sp.get("by_kind") == sc.get("by_kind"),
        "structure_issue_rows_same": sp.get("first_issues") == sc.get("first_issues"),
        "false_segmented_pointer_parent_zero": fp.get("sites_found") == 0,
        "false_segmented_pointer_candidate_zero": fc.get("sites_found") == 0,
        "false_segmented_pointer_ext3_population_same": (
            fp.get("ext3_token_prefixes_ignored") == fc.get("ext3_token_prefixes_ignored")
        ),
        "nondialogue_failure_list_same": np.get("failures") == nc.get("failures"),
        "nondialogue_all_sections_same": all(np.get(name) == nc.get(name) for name in nondialogue_sections),
        "nondialogue_marker_misconsumption_zero": (
            (nc.get("check_ii_marker_records") or {}).get("misconsumed") == 0
        ),
        "nondialogue_structure_violations_zero": (
            (nc.get("check_iii_length_terminator") or {}).get("violations") == 0
        ),
        "nondialogue_nested_detachment_clean": (
            (nc.get("check_iv_nested_dictionary_detachment") or {}).get("ok") is True
        ),
        "smoke_jagd_same_and_ok": mp.get("jagd_ok") is True and mc.get("jagd_ok") is True,
        "smoke_hangul_same_and_ok": (
            mp.get("hangul_ok") is True
            and mc.get("hangul_ok") is True
            and mp.get("hangul_samples") == mc.get("hangul_samples")
        ),
        "smoke_violation_counts_same": mp.get("unit_run_counts") == mc.get("unit_run_counts"),
        "smoke_violation_site_set_same": set(parent_sites) == set(candidate_sites),
        "smoke_only_expected_rows_changed": changed_common == EXPECTED_REDIRECT_SITES,
        "smoke_candidate_diff_vs_tip_is_four_bank59_bytes": mc.get("unit_vs_tip_nonzero") == {"59": 4},
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ext3_five_bank_runtime_probe_regressions.py",
        "read_only": True,
        "ok": ok,
        "parent": {
            "path": str(PARENT_ROM.relative_to(ROOT)),
            "sha256": sha256(PARENT_ROM),
        },
        "candidate": {
            "path": str(CANDIDATE_ROM.relative_to(ROOT)),
            "sha256": sha256(CANDIDATE_ROM),
        },
        "structure": {
            "records_walked": sc.get("records_walked"),
            "historical_issues": sc.get("issues"),
            "by_kind": sc.get("by_kind"),
            "parent_delta": 0,
        },
        "false_segmented_pointer": {
            "parent_sites": fp.get("sites_found"),
            "candidate_sites": fc.get("sites_found"),
            "ignored_ext3_sites": fc.get("ext3_token_prefixes_ignored"),
        },
        "nondialogue": {
            "parent_ok": np.get("ok"),
            "candidate_ok": nc.get("ok"),
            "historical_failures": nc.get("failures"),
            "parent_delta": 0,
            "marker_misconsumed": (nc.get("check_ii_marker_records") or {}).get("misconsumed"),
            "structure_violations": (nc.get("check_iii_length_terminator") or {}).get("violations"),
        },
        "smoke": {
            "parent_overall_ok": mp.get("overall_ok"),
            "candidate_overall_ok": mc.get("overall_ok"),
            "historical_violation_runs": (mc.get("unit_run_counts") or {}).get("violation_runs"),
            "historical_violation_bytes": (mc.get("unit_run_counts") or {}).get("violation_bytes"),
            "added_or_removed_violation_sites": sorted(set(parent_sites) ^ set(candidate_sites)),
            "changed_common_sites": sorted(changed_common),
            "expected_redirect_sites": sorted(EXPECTED_REDIRECT_SITES),
            "candidate_unit_vs_tip": mc.get("unit_vs_tip_nonzero"),
            "jagd_ok": mc.get("jagd_ok"),
            "hangul_ok": mc.get("hangul_ok"),
        },
        "interpretation": (
            "Legacy original-ROM baseline failures are unchanged from the promoted parent. "
            "No new structure, pointer, non-dialogue, marker, or smoke violation population "
            "was introduced; the four changed smoke rows are the intended page1-4 token bytes."
        ),
        "checks": checks,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not ok:
        raise AuditError("five-bank runtime probe regression audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
