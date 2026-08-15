#!/usr/bin/env python3
"""Run blocking and parent-differential gates for the 5E:BD90 cleanup.

Two legacy whole-ROM gates are already red on the accepted parent because they
compare the mature Korean TIP directly with the pristine Japanese ROM.  They
remain useful here as parent-differential gates: the candidate must preserve the
exact inherited findings while the dedicated audit proves that its only data
change is the requested one-record repair.
"""
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
CANDIDATE = PATCH / "battle_dialogue_prefix_cleanup_candidate.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/battle_dialogue_prefix_cleanup_candidate.sav"
BUILD_REPORT = PATCH / "battle_dialogue_prefix_cleanup_build_report.json"
AUDIT_REPORT = PATCH / "battle_dialogue_prefix_cleanup_audit.json"
REPORT = PATCH / "battle_dialogue_prefix_cleanup_gate_summary.json"

CAND_STRUCTURE = PATCH / "battle_dialogue_prefix_cleanup_structure.json"
PARENT_STRUCTURE = PATCH / "battle_dialogue_prefix_cleanup_parent_structure.json"
CAND_SEGPTR = PATCH / "battle_dialogue_prefix_cleanup_false_segptr.json"
PARENT_SEGPTR = PATCH / "battle_dialogue_prefix_cleanup_parent_false_segptr.json"
CAND_NONDIALOGUE = PATCH / "battle_dialogue_prefix_cleanup_nondialogue.json"
PARENT_NONDIALOGUE = PATCH / "battle_dialogue_prefix_cleanup_parent_nondialogue.json"
CAND_SMOKE = PATCH / "battle_dialogue_prefix_cleanup_smoke.json"
PARENT_SMOKE = PATCH / "battle_dialogue_prefix_cleanup_parent_smoke.json"


class GateError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def load_json(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise GateError(f"JSON root is not an object: {rel(path)}")
    return document


def run(command: Sequence[str], *, accepted_returncodes: set[int] = {0}) -> dict[str, Any]:
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
    ok = result.returncode in accepted_returncodes
    return {
        "command": [str(value) for value in command],
        "returncode": result.returncode,
        "ok": ok,
        "stdout_tail": result.stdout[-1200:],
        "stderr_tail": result.stderr[-1200:],
    }


def report_identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def selected_nondialogue_counts(document: Mapping[str, Any]) -> dict[str, Any]:
    check_i = document["check_i_dict_expansion"]
    return {
        "failures": list(document.get("failures") or []),
        "records_with_tokens": check_i["records_with_tokens"],
        "records_compared": check_i["records_compared"],
        "approved_differences": check_i["approved_differences"],
        "dict_only_mismatches": check_i["dict_only_mismatches"],
        "rendered_mismatches": check_i["rendered_mismatches"],
    }


def selected_smoke(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "jagd_ok": document["jagd_ok"],
        "unit_banks_clean": document["unit_banks_clean"],
        "opening_required_ok": document["opening_required_ok"],
        "hangul_ok": document["hangul_ok"],
        "overall_ok": document["overall_ok"],
        "unit_violation_sites": document["unit_violation_sites"],
        "unit_diffs": document["unit_diffs"],
    }


def main() -> int:
    required = [
        PARENT,
        CANDIDATE,
        PARENT_SAVE,
        CANDIDATE_SAVE,
        BUILD_REPORT,
    ]
    missing = [rel(path) for path in required if not path.is_file()]
    if missing:
        raise GateError(f"missing required files: {missing}")

    compile_failures: list[str] = []
    for path in (
        ROOT / "tools/build_battle_dialogue_prefix_cleanup_candidate.py",
        ROOT / "tools/audit_battle_dialogue_prefix_cleanup.py",
        Path(__file__),
    ):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_failures.append(f"{rel(path)}: {exc}")

    commands: dict[str, dict[str, Any]] = {}
    commands["audit"] = run(
        [sys.executable, "tools/audit_battle_dialogue_prefix_cleanup.py"]
    )
    for label, target, output in (
        ("parent_structure", PARENT, PARENT_STRUCTURE),
        ("candidate_structure", CANDIDATE, CAND_STRUCTURE),
    ):
        commands[label] = run(
            [
                sys.executable,
                "tools/scan_script_record_structure.py",
                "--target",
                str(target),
                "--lo",
                "0x5E0000",
                "--hi",
                "0x5EFFFF",
                "--out",
                str(output),
            ]
        )
    for label, target, output in (
        ("parent_false_segptr", PARENT, PARENT_SEGPTR),
        ("candidate_false_segptr", CANDIDATE, CAND_SEGPTR),
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
        ("parent_nondialogue", PARENT, PARENT_NONDIALOGUE),
        ("candidate_nondialogue", CANDIDATE, CAND_NONDIALOGUE),
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
            accepted_returncodes={0, 1},
        )
    for label, target, output in (
        ("parent_smoke", PARENT, PARENT_SMOKE),
        ("candidate_smoke", CANDIDATE, CAND_SMOKE),
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
            accepted_returncodes={0, 1},
        )

    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT_REPORT)
    parent_structure = load_json(PARENT_STRUCTURE)
    candidate_structure = load_json(CAND_STRUCTURE)
    parent_segptr = load_json(PARENT_SEGPTR)
    candidate_segptr = load_json(CAND_SEGPTR)
    parent_nondialogue = load_json(PARENT_NONDIALOGUE)
    candidate_nondialogue = load_json(CAND_NONDIALOGUE)
    parent_smoke = load_json(PARENT_SMOKE)
    candidate_smoke = load_json(CAND_SMOKE)

    build_sha = ((build.get("candidate_rom") or {}).get("sha256"))
    candidate_sha = sha256_file(CANDIDATE)
    build_ok = (
        build.get("ok") is True
        and build.get("status") == "candidate_static_verified"
        and build_sha == candidate_sha
        and ((build.get("verification") or {}).get("unaccounted_changed_bytes")) == 0
        and ((build.get("verification") or {}).get("boundary_preserved")) is True
    )

    audit_ok = (
        commands["audit"]["ok"]
        and audit.get("ok") is True
        and ((audit.get("target") or {}).get("after_text")) == "우와아아아……！"
        and ((audit.get("target") or {}).get("japanese_residual_count")) == 0
        and ((audit.get("diff") or {}).get("unexpected_changed_bytes")) == 0
        and all(row.get("ok") is True for row in audit.get("surrounding_records") or [])
    )

    structure_ok = (
        commands["parent_structure"]["ok"]
        and commands["candidate_structure"]["ok"]
        and parent_structure.get("ok") is True
        and candidate_structure.get("ok") is True
        and parent_structure.get("issues") == 0
        and candidate_structure.get("issues") == 0
        and parent_structure.get("records_walked") == candidate_structure.get("records_walked")
    )
    segptr_ok = (
        commands["parent_false_segptr"]["ok"]
        and commands["candidate_false_segptr"]["ok"]
        and parent_segptr.get("ok") is True
        and candidate_segptr.get("ok") is True
        and parent_segptr.get("sites_found") == candidate_segptr.get("sites_found") == 0
    )

    parent_nd_counts = selected_nondialogue_counts(parent_nondialogue)
    candidate_nd_counts = selected_nondialogue_counts(candidate_nondialogue)
    nondialogue_structural_exact = all(
        parent_nondialogue[key] == candidate_nondialogue[key]
        and candidate_nondialogue[key].get("ok") is True
        for key in (
            "check_ii_marker_records",
            "check_iii_length_terminator",
            "check_iv_nested_dictionary_detachment",
        )
    )
    nondialogue_ok = (
        commands["parent_nondialogue"]["ok"]
        and commands["candidate_nondialogue"]["ok"]
        and parent_nd_counts == candidate_nd_counts
        and nondialogue_structural_exact
    )

    parent_smoke_selected = selected_smoke(parent_smoke)
    candidate_smoke_selected = selected_smoke(candidate_smoke)
    smoke_ok = (
        commands["parent_smoke"]["ok"]
        and commands["candidate_smoke"]["ok"]
        and parent_smoke_selected == candidate_smoke_selected
        and candidate_smoke.get("jagd_ok") is True
        and candidate_smoke.get("opening_required_ok") is True
        and candidate_smoke.get("hangul_ok") is True
    )

    save_ok = (
        PARENT_SAVE.stat().st_size == CANDIDATE_SAVE.stat().st_size == 32768
        and sha256_file(PARENT_SAVE) == sha256_file(CANDIDATE_SAVE)
    )
    commands_ok = all(row["ok"] for row in commands.values())

    checks = {
        "python_compile": {"ok": not compile_failures, "failures": compile_failures},
        "builder_static_proof": {"ok": build_ok, "report": report_identity(BUILD_REPORT)},
        "independent_target_audit": {"ok": audit_ok, "report": report_identity(AUDIT_REPORT)},
        "record_structure_5e": {
            "ok": structure_ok,
            "parent_issues": parent_structure.get("issues"),
            "candidate_issues": candidate_structure.get("issues"),
            "records_walked": candidate_structure.get("records_walked"),
        },
        "false_segmented_pointer": {
            "ok": segptr_ok,
            "parent_sites": parent_segptr.get("sites_found"),
            "candidate_sites": candidate_segptr.get("sites_found"),
        },
        "nondialogue_parent_differential": {
            "ok": nondialogue_ok,
            "blocking_mode": "candidate_specific",
            "generic_parent_ok": parent_nondialogue.get("ok"),
            "generic_candidate_ok": candidate_nondialogue.get("ok"),
            "parent_counts": parent_nd_counts,
            "candidate_counts": candidate_nd_counts,
            "structural_checks_exact_and_ok": nondialogue_structural_exact,
            "note": "The generic dictionary-identity finding is inherited byte-for-byte in count; the target audit and check (iii) prove this candidate adds no structural regression.",
        },
        "legacy_smoke_parent_differential": {
            "ok": smoke_ok,
            "blocking_mode": "candidate_specific",
            "generic_parent_ok": parent_smoke.get("overall_ok"),
            "generic_candidate_ok": candidate_smoke.get("overall_ok"),
            "unit_violation_count": len(candidate_smoke.get("unit_violation_sites") or []),
            "unit_findings_exact": parent_smoke.get("unit_violation_sites")
            == candidate_smoke.get("unit_violation_sites"),
            "jagd_ok": candidate_smoke.get("jagd_ok"),
            "opening_required_ok": candidate_smoke.get("opening_required_ok"),
            "hangul_ok": candidate_smoke.get("hangul_ok"),
            "note": "The mature parent already fails the pristine-ROM unit allowlist. Candidate and parent findings are exact; 5E:BD90 is outside that unit-bank scope.",
        },
        "save_pair": {
            "ok": save_ok,
            "parent": report_identity(PARENT_SAVE),
            "candidate": report_identity(CANDIDATE_SAVE),
        },
        "gate_commands": {"ok": commands_ok, "commands": commands},
    }
    accepted = all(row.get("ok") is True for row in checks.values())
    document = {
        "schema_version": 1,
        "generated_by": "tools/verify_battle_dialogue_prefix_cleanup_candidate.py",
        "accepted_static": accepted,
        "ok": accepted,
        "parent_rom": report_identity(PARENT),
        "candidate_rom": report_identity(CANDIDATE),
        "checks": checks,
        "runtime_follow_up": {
            "status": "pending",
            "blocking": False,
            "instruction": "Load the paired candidate SaveRAM and revisit the supplied battle scene; line 2 must begin with 우, not う.",
        },
        "evidence": [
            report_identity(path)
            for path in (
                BUILD_REPORT,
                AUDIT_REPORT,
                CAND_STRUCTURE,
                CAND_SEGPTR,
                CAND_NONDIALOGUE,
                CAND_SMOKE,
                PARENT_STRUCTURE,
                PARENT_SEGPTR,
                PARENT_NONDIALOGUE,
                PARENT_SMOKE,
            )
        ],
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
                "candidate_sha256": candidate_sha,
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
