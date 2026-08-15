#!/usr/bin/env python3
"""Compare project-wide static gates for MS encyclopedia batch 02."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/patch/encyclopedia_ms_batch02_common_gate_delta.json"
PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/encyclopedia_ms_batch02_candidate.wsc"
FILES = {
    "independent": ROOT / "out/patch/encyclopedia_ms_batch02_candidate_audit.json",
    "structure_parent": ROOT / "out/patch/encyclopedia_ms_batch02_structure_parent.json",
    "structure_candidate": ROOT / "out/patch/encyclopedia_ms_batch02_structure_candidate.json",
    "false_parent": ROOT / "out/patch/encyclopedia_ms_batch02_false_segptr_parent.json",
    "false_candidate": ROOT / "out/patch/encyclopedia_ms_batch02_false_segptr_candidate.json",
    "nondialogue_parent": ROOT / "out/patch/encyclopedia_ms_batch02_nondialogue_parent.json",
    "nondialogue_candidate": ROOT / "out/patch/encyclopedia_ms_batch02_nondialogue_candidate.json",
    "mixed_parent": ROOT / "out/patch/encyclopedia_ms_batch02_mixed_parent.json",
    "mixed_candidate": ROOT / "out/patch/encyclopedia_ms_batch02_mixed_candidate.json",
    "smoke_parent": ROOT / "out/patch/encyclopedia_ms_batch02_smoke_parent.json",
    "smoke_candidate": ROOT / "out/patch/encyclopedia_ms_batch02_smoke_candidate.json",
    "stock_parent": ROOT / "out/patch/encyclopedia_ms_batch02_stock_parent_gate.json",
    "stock_candidate": ROOT / "out/patch/encyclopedia_ms_batch02_stock_gate.json",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"invalid JSON root: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": sha256(path)}


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def issue_signature(rows: Iterable[Any], fields: tuple[str, ...]) -> set[tuple[Any, ...]]:
    out: set[tuple[Any, ...]] = set()
    for row in rows or []:
        if isinstance(row, dict):
            out.add(tuple(row.get(field) for field in fields))
        else:
            out.add((str(row),))
    return out


def nondialogue_summary(report: dict[str, Any]) -> dict[str, Any]:
    check = report.get("check_i_dict_expansion") or {}
    return {key: check.get(key) for key in (
        "records_compared",
        "records_with_tokens",
        "records_skipped_unresolvable_in_original",
        "dict_only_mismatches",
        "rendered_mismatches",
        "approved_differences",
        "ui_explained_dict_only",
        "ui_explained_rendered",
        "detachment_explained_dict_only",
        "detachment_explained_rendered",
        "name75_rewrite_ranges",
    )}


def main() -> int:
    docs = {name: load(path) for name, path in FILES.items()}
    structure_parent = docs["structure_parent"]
    structure_candidate = docs["structure_candidate"]
    structure_parent_sig = issue_signature(structure_parent.get("first_issues") or [], ("abs", "kind", "orig_terminator", "target_terminator", "delta"))
    structure_candidate_sig = issue_signature(structure_candidate.get("first_issues") or [], ("abs", "kind", "orig_terminator", "target_terminator", "delta"))

    false_parent_rows = docs["false_parent"].get("writes") or docs["false_parent"].get("sites") or []
    false_candidate_rows = docs["false_candidate"].get("writes") or docs["false_candidate"].get("sites") or []
    nond_parent_summary = nondialogue_summary(docs["nondialogue_parent"])
    nond_candidate_summary = nondialogue_summary(docs["nondialogue_candidate"])
    mixed_parent = docs["mixed_parent"].get("counts") or {}
    mixed_candidate = docs["mixed_candidate"].get("counts") or {}

    smoke_parent = docs["smoke_parent"]
    smoke_candidate = docs["smoke_candidate"]
    smoke_parent_sig = issue_signature(smoke_parent.get("unit_violation_sites") or [], ("bank", "logical", "site", "reason", "len"))
    smoke_candidate_sig = issue_signature(smoke_candidate.get("unit_violation_sites") or [], ("bank", "logical", "site", "reason", "len"))
    smoke_critical = ("jagd_ok", "opening_required_ok", "hangul_ok")

    stock_parent = (docs["stock_parent"].get("targets") or [{}])[0]
    stock_candidate = (docs["stock_candidate"].get("targets") or [{}])[0]
    stock_parent_sig = issue_signature(stock_parent.get("unintended") or [], ("site", "len", "orig_hex", "target_hex", "classification"))
    stock_candidate_sig = issue_signature(stock_candidate.get("unintended") or [], ("site", "len", "orig_hex", "target_hex", "classification"))

    independent = docs["independent"]
    checks = {
        "independent_candidate_audit_ok": independent.get("ok") is True,
        "independent_target_population_565": int((independent.get("counts") or {}).get("targets") or 0) == 565,
        "structure_issue_count_unchanged": structure_parent.get("issues") == structure_candidate.get("issues"),
        "structure_issue_signatures_unchanged": structure_parent_sig == structure_candidate_sig,
        "false_segmented_pointer_writes_zero": not false_parent_rows and not false_candidate_rows,
        "nondialogue_check_i_summary_unchanged": nond_parent_summary == nond_candidate_summary,
        "nondialogue_marker_gate_ok": (docs["nondialogue_candidate"].get("check_ii_marker_records") or {}).get("ok") is True,
        "nondialogue_length_terminator_gate_ok": (docs["nondialogue_candidate"].get("check_iii_length_terminator") or {}).get("ok") is True,
        "nondialogue_nested_detachment_gate_ok": (docs["nondialogue_candidate"].get("check_iv_nested_dictionary_detachment") or {}).get("ok") is True,
        "mixed_artifact_counts_unchanged": mixed_parent == mixed_candidate,
        "mixed_scan_errors_zero": int(mixed_candidate.get("scan_errors", -1)) == 0,
        "smoke_critical_checks_unchanged_and_true": all(smoke_parent.get(key) is True and smoke_candidate.get(key) is True for key in smoke_critical),
        "smoke_violation_signatures_unchanged": smoke_parent_sig == smoke_candidate_sig,
        "smoke_only_5c_diff_from_parent": set((smoke_candidate.get("unit_vs_tip_nonzero") or {}).keys()) <= {"5C"},
        "stock_inherited_failures_unchanged": stock_parent.get("failures") == stock_candidate.get("failures"),
        "stock_unintended_signatures_unchanged": stock_parent_sig == stock_candidate_sig,
        "stock_5f_pointer_gate_unchanged": stock_parent.get("dict_5f_pointer_gate") == stock_candidate.get("dict_5f_pointer_gate"),
        "stock_new_changes_all_intended": (
            int((stock_candidate.get("counts") or {}).get("unintended_runs", -1)) == int((stock_parent.get("counts") or {}).get("unintended_runs", -2))
            and int((stock_candidate.get("counts") or {}).get("unintended_bytes", -1)) == int((stock_parent.get("counts") or {}).get("unintended_bytes", -2))
            and int((stock_candidate.get("counts") or {}).get("intended_runs", 0)) >= int((stock_parent.get("counts") or {}).get("intended_runs", 0))
        ),
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_ms_batch02_common_gates.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "no_common_gate_regression" if ok else "failed",
        "inputs": {
            "parent": identity(PARENT),
            "candidate": identity(CANDIDATE),
            "reports": {name: identity(path) for name, path in FILES.items()},
        },
        "checks": checks,
        "delta": {
            "structure_issues": {"parent": structure_parent.get("issues"), "candidate": structure_candidate.get("issues")},
            "nondialogue_check_i": {"parent": nond_parent_summary, "candidate": nond_candidate_summary},
            "mixed_counts": {"parent": mixed_parent, "candidate": mixed_candidate},
            "smoke": {
                "parent_overall_ok": smoke_parent.get("overall_ok"),
                "candidate_overall_ok": smoke_candidate.get("overall_ok"),
                "inherited_violation_count": len(smoke_parent_sig),
                "candidate_only_violation_signatures": len(smoke_candidate_sig - smoke_parent_sig),
                "unit_vs_tip_nonzero": smoke_candidate.get("unit_vs_tip_nonzero"),
            },
            "stock": {
                "parent_counts": stock_parent.get("counts"),
                "candidate_counts": stock_candidate.get("counts"),
                "inherited_unintended_count": len(stock_parent_sig),
                "candidate_only_unintended_signatures": len(stock_candidate_sig - stock_parent_sig),
                "failures": stock_candidate.get("failures"),
            },
        },
        "promotion": "authorized_by_user_visual_confirmation",
    }
    write_json(OUT, report)
    print(json.dumps({"ok": ok, "checks": checks, "out": str(OUT.resolve())}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
