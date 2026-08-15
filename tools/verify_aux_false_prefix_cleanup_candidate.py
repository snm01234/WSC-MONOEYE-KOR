#!/usr/bin/env python3
"""Run blocking and parent-differential gates for the 5D/5E cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
PARENT = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "aux_false_prefix_cleanup_candidate.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/aux_false_prefix_cleanup_candidate.sav"
ANALYSIS = PATCH / "aux_false_prefix_cleanup_analysis.json"
BUILD_REPORT = PATCH / "aux_false_prefix_cleanup_build_report.json"
AUDIT_REPORT = PATCH / "aux_false_prefix_cleanup_audit.json"
PARENT_STRUCTURE = PATCH / "aux_false_prefix_cleanup_parent_structure.json"
CANDIDATE_STRUCTURE = PATCH / "aux_false_prefix_cleanup_structure.json"
PARENT_SEGPTR = PATCH / "aux_false_prefix_cleanup_parent_false_segptr.json"
CANDIDATE_SEGPTR = PATCH / "aux_false_prefix_cleanup_false_segptr.json"
PARENT_ND = PATCH / "aux_false_prefix_cleanup_parent_nondialogue.json"
CANDIDATE_ND = PATCH / "aux_false_prefix_cleanup_nondialogue.json"
PARENT_SMOKE = PATCH / "aux_false_prefix_cleanup_parent_smoke.json"
CANDIDATE_SMOKE = PATCH / "aux_false_prefix_cleanup_smoke.json"
REPORT = PATCH / "aux_false_prefix_cleanup_gate_summary.json"
EXPECTED_PARENT_SHA = "a47569820eed19ab0028b432dabf840bb35f9689cf403e63ed2af71f8431cf9a"
EXPECTED_CANDIDATE_SHA = "ec295935607b4843bc654c2709995262bade543d6c0be64556a45b6b240d4833"
EXPECTED_SAVE_SHA = "98acf8cfe76c128297acffcf3d8c6d2a4e9ffeaf7b5011236960647e3db09863"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def load_json(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise RuntimeError(f"JSON root is not an object: {rel(path)}")
    return document


def run(command: Sequence[str], accepted: set[int] = {0}) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return {
        "command": [str(value) for value in command],
        "returncode": result.returncode,
        "ok": result.returncode in accepted,
        "stdout_tail": result.stdout[-1000:],
        "stderr_tail": result.stderr[-1000:],
    }


def nd_counts(document: Mapping[str, Any]) -> dict[str, Any]:
    check = document["check_i_dict_expansion"]
    return {
        "failures": list(document.get("failures") or []),
        "records_with_tokens": check["records_with_tokens"],
        "records_compared": check["records_compared"],
        "approved_differences": check["approved_differences"],
        "dict_only_mismatches": check["dict_only_mismatches"],
        "rendered_mismatches": check["rendered_mismatches"],
    }


def main() -> int:
    required = [PARENT, CANDIDATE, PARENT_SAVE, CANDIDATE_SAVE, ANALYSIS, BUILD_REPORT]
    missing = [rel(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"missing files: {missing}")

    compile_failures: list[str] = []
    for path in (
        ROOT / "tools/analyze_aux_false_prefix_cleanup.py",
        ROOT / "tools/build_aux_false_prefix_cleanup_candidate.py",
        ROOT / "tools/audit_aux_false_prefix_cleanup_candidate.py",
        Path(__file__),
    ):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_failures.append(f"{rel(path)}: {exc}")

    commands: dict[str, dict[str, Any]] = {}
    commands["audit"] = run([sys.executable, "tools/audit_aux_false_prefix_cleanup_candidate.py"])
    for label, target, output in (
        ("parent_structure", PARENT, PARENT_STRUCTURE),
        ("candidate_structure", CANDIDATE, CANDIDATE_STRUCTURE),
    ):
        commands[label] = run(
            [
                sys.executable,
                "tools/scan_script_record_structure.py",
                "--target",
                str(target),
                "--lo",
                "0x5D0000",
                "--hi",
                "0x5EFFFF",
                "--out",
                str(output),
            ]
        )
    for label, target, output in (
        ("parent_segptr", PARENT, PARENT_SEGPTR),
        ("candidate_segptr", CANDIDATE, CANDIDATE_SEGPTR),
    ):
        commands[label] = run(
            [
                sys.executable,
                "tools/scan_false_segptr_writes.py",
                "--target",
                str(target),
                "--out",
                str(output),
            ]
        )
    for label, target, output in (
        ("parent_nondialogue", PARENT, PARENT_ND),
        ("candidate_nondialogue", CANDIDATE, CANDIDATE_ND),
    ):
        commands[label] = run(
            [
                sys.executable,
                "tools/verify_nondialogue_text.py",
                "--target",
                str(target),
                "--ui-report-dir",
                str(PATCH),
                "--out",
                str(output),
                "--quiet",
            ],
            accepted={0, 1},
        )
    for label, target, output in (
        ("parent_smoke", PARENT, PARENT_SMOKE),
        ("candidate_smoke", CANDIDATE, CANDIDATE_SMOKE),
    ):
        commands[label] = run(
            [
                sys.executable,
                "tools/verify_all_stages_smoke.py",
                "--rom",
                str(target),
                "--report",
                str(output),
            ],
            accepted={0, 1},
        )

    analysis = load_json(ANALYSIS)
    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT_REPORT)
    p_structure = load_json(PARENT_STRUCTURE)
    c_structure = load_json(CANDIDATE_STRUCTURE)
    p_segptr = load_json(PARENT_SEGPTR)
    c_segptr = load_json(CANDIDATE_SEGPTR)
    p_nd = load_json(PARENT_ND)
    c_nd = load_json(CANDIDATE_ND)
    p_smoke = load_json(PARENT_SMOKE)
    c_smoke = load_json(CANDIDATE_SMOKE)

    analysis_ok = (
        analysis.get("ok") is True
        and ((analysis.get("counts") or {}).get("targets")) == 308
        and ((analysis.get("counts") or {}).get("manual_control_exclusions")) == 41
    )
    build_ok = (
        build.get("ok") is True
        and build.get("status") == "candidate_static_verified"
        and ((build.get("candidate") or {}).get("sha256")) == EXPECTED_CANDIDATE_SHA
        and ((build.get("counts") or {}).get("targets")) == 308
        and ((build.get("verification") or {}).get("unaccounted_changed_bytes")) == 0
        and ((build.get("verification") or {}).get("japanese_residuals_in_targets")) == 0
    )
    audit_ok = (
        commands["audit"]["ok"]
        and audit.get("ok") is True
        and ((audit.get("counts") or {}).get("targets_exact")) == 308
        and ((audit.get("counts") or {}).get("target_japanese_residuals")) == 0
        and ((audit.get("counts") or {}).get("manual_controls_preserved")) == 41
        and ((audit.get("counts") or {}).get("already_fixed_preserved")) == 1
        and ((audit.get("counts") or {}).get("unexpected_changed_bytes")) == 0
    )
    structure_ok = (
        commands["parent_structure"]["ok"]
        and commands["candidate_structure"]["ok"]
        and p_structure.get("ok") is True
        and c_structure.get("ok") is True
        and p_structure.get("issues") == c_structure.get("issues") == 0
        and p_structure.get("records_walked") == c_structure.get("records_walked") == 24029
    )
    segptr_ok = (
        commands["parent_segptr"]["ok"]
        and commands["candidate_segptr"]["ok"]
        and p_segptr.get("sites_found") == c_segptr.get("sites_found") == 0
        and p_segptr.get("ok") is True
        and c_segptr.get("ok") is True
    )
    nd_structural_exact = all(
        p_nd[key] == c_nd[key] and c_nd[key].get("ok") is True
        for key in (
            "check_ii_marker_records",
            "check_iii_length_terminator",
            "check_iv_nested_dictionary_detachment",
        )
    )
    nondialogue_ok = (
        commands["parent_nondialogue"]["ok"]
        and commands["candidate_nondialogue"]["ok"]
        and nd_counts(p_nd) == nd_counts(c_nd)
        and nd_structural_exact
    )
    smoke_ok = (
        commands["parent_smoke"]["ok"]
        and commands["candidate_smoke"]["ok"]
        and p_smoke.get("unit_violation_sites") == c_smoke.get("unit_violation_sites")
        and p_smoke.get("jagd_ok") == c_smoke.get("jagd_ok") is True
        and p_smoke.get("opening_required_ok") == c_smoke.get("opening_required_ok") is True
        and p_smoke.get("hangul_ok") == c_smoke.get("hangul_ok") is True
        and len(p_smoke.get("unit_violation_sites") or [])
        == len(c_smoke.get("unit_violation_sites") or [])
        == 327
    )
    identity_ok = (
        sha256_file(PARENT) == EXPECTED_PARENT_SHA
        and sha256_file(CANDIDATE) == EXPECTED_CANDIDATE_SHA
        and sha256_file(PARENT_SAVE) == sha256_file(CANDIDATE_SAVE) == EXPECTED_SAVE_SHA
    )
    commands_ok = all(row["ok"] for row in commands.values())

    checks = {
        "python_compile": {"ok": not compile_failures, "failures": compile_failures},
        "reviewed_analysis": {"ok": analysis_ok, "report": identity(ANALYSIS)},
        "builder_static_proof": {"ok": build_ok, "report": identity(BUILD_REPORT)},
        "independent_target_audit": {"ok": audit_ok, "report": identity(AUDIT_REPORT)},
        "record_structure_5d_5e": {
            "ok": structure_ok,
            "records_walked": c_structure.get("records_walked"),
            "issues": c_structure.get("issues"),
        },
        "false_segmented_pointer": {"ok": segptr_ok, "sites": c_segptr.get("sites_found")},
        "nondialogue_parent_differential": {
            "ok": nondialogue_ok,
            "generic_parent_ok": p_nd.get("ok"),
            "generic_candidate_ok": c_nd.get("ok"),
            "parent_counts": nd_counts(p_nd),
            "candidate_counts": nd_counts(c_nd),
            "structural_checks_exact_and_ok": nd_structural_exact,
        },
        "legacy_smoke_parent_differential": {
            "ok": smoke_ok,
            "generic_parent_ok": p_smoke.get("overall_ok"),
            "generic_candidate_ok": c_smoke.get("overall_ok"),
            "unit_violation_count": len(c_smoke.get("unit_violation_sites") or []),
            "unit_findings_exact": p_smoke.get("unit_violation_sites")
            == c_smoke.get("unit_violation_sites"),
            "jagd_ok": c_smoke.get("jagd_ok"),
            "opening_required_ok": c_smoke.get("opening_required_ok"),
            "hangul_ok": c_smoke.get("hangul_ok"),
        },
        "rom_and_save_identity": {
            "ok": identity_ok,
            "parent": identity(PARENT),
            "candidate": identity(CANDIDATE),
            "parent_save": identity(PARENT_SAVE),
            "candidate_save": identity(CANDIDATE_SAVE),
        },
        "gate_commands": {"ok": commands_ok, "commands": commands},
    }
    accepted = all(row.get("ok") is True for row in checks.values())
    document = {
        "schema_version": 1,
        "generated_by": "tools/verify_aux_false_prefix_cleanup_candidate.py",
        "ok": accepted,
        "accepted_static": accepted,
        "parent": identity(PARENT),
        "candidate": identity(CANDIDATE),
        "checks": checks,
        "runtime_follow_up": {
            "status": "recommended",
            "blocking": False,
            "instruction": "During ordinary battle review, confirm repaired lines no longer begin with stray Japanese while portraits and timing remain normal.",
        },
    }
    REPORT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": accepted,
                "candidate_sha256": sha256_file(CANDIDATE),
                "checks": {name: row.get("ok") for name, row in checks.items()},
                "report": rel(REPORT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
