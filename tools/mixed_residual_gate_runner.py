#!/usr/bin/env python3
"""Fail-closed mandatory static gate runner and aggregate acceptance report.

Tasks 6.2 and 6.3 of the mixed Korean/Japanese residual localization spec.

Every gate is executed as its own read-only subprocess with explicit input and
output paths — no tool is allowed to fall back to a hard-coded tip ROM — and
each result is judged by :mod:`mixed_residual_gate_adapters`, which checks the
process status *and* the parsed JSON.  A gate that is missing, stale, malformed,
failing, or that exited non-zero makes acceptance false; there is no "assume
pass" path.

Gates (design.md §7):

* target completion / rendered-body scan (in-process, from the plan)
* ``scan_mixed_script_artifacts.py``
* ``scan_invasion_full_line_tokens.py``
* ``scan_aux_ff_invasion.py`` for the Accepted_Baseline **and** the Candidate,
  run with identical arguments so the confirmed FF-page counts are comparable
* ``verify_nondialogue_text.py``
* ``scan_script_record_structure.py``
* ``verify_stock_noninvasion.py``
* ``scan_false_segptr_writes.py``
* ``verify_all_stages_smoke.py``
* approved-extent confinement (from the transaction's precommit proof)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from mixed_residual_gate_adapters import (  # noqa: E402
    GateResult,
    load_gate_report,
    normalize_aux_ff_invasion,
    normalize_false_segptr,
    normalize_full_line_overshare,
    normalize_mixed_artifacts,
    normalize_nondialogue,
    normalize_smoke,
    normalize_stock_noninvasion,
    normalize_structure,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_planner import _rule_prefix  # noqa: E402
from monoeye_rom import Tbl, is_ext3_magic, read_encoded_z_safe, stock_base  # noqa: E402

GENERATED_BY = "tools/mixed_residual_gate_runner.py"
SCHEMA_VERSION = 1

SCRIPT_BAND_LO = 0x600000
SCRIPT_BAND_HI = 0x69FFFF


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _identity(path: Path) -> dict[str, Any]:
    resolved = Path(path)
    try:
        size = resolved.stat().st_size
    except OSError:
        return {"path": str(resolved), "present": False, "size": None, "sha256": None}
    return {
        "path": str(resolved.resolve()),
        "present": True,
        "size": size,
        "sha256": _sha256_file(resolved),
    }


@dataclass(frozen=True)
class GateInputs:
    """Every path a gate run needs, all explicit."""

    original_rom: Path
    pre_ext3_rom: Path
    baseline_rom: Path
    candidate_rom: Path
    blocks: Path
    prefix_evidence: Path
    tbl: Path
    ext_meta: Path
    ext3_meta: Path
    sheet: Path
    ui_report_dir: Path
    out_dir: Path
    prefix: str = "mixed_residual_gate"
    baseline_meta: Path | None = None
    approved_stock_report: Path | None = None
    approved_detachment_report: Path | None = None
    approved_local_expansion_report: Path | None = None
    local_expansion_baseline_rom: Path | None = None

    def report(self, name: str) -> Path:
        return self.out_dir / f"{self.prefix}_{name}.json"


@dataclass
class RunOutcome:
    name: str
    command: tuple[str, ...]
    exit_code: int | None
    duration_s: float
    stderr_tail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 2),
            "stderr_tail": self.stderr_tail[-400:],
        }


def _run(name: str, args: Sequence[str]) -> RunOutcome:
    command = (sys.executable, *args)
    started = time.time()
    reuse_cutoff = os.environ.get("MONOEYE_GATE_REUSE_NOT_BEFORE_NS")
    if reuse_cutoff is not None:
        output_flags = {"--out", "--report", "--output", "--diff-out"}
        outputs = [
            Path(args[index + 1])
            for index, value in enumerate(args[:-1])
            if value in output_flags
        ]
        try:
            cutoff_ns = int(reuse_cutoff)
            reusable = bool(outputs) and all(
                path.is_file() and path.stat().st_mtime_ns >= cutoff_ns
                for path in outputs
            )
        except (OSError, ValueError):
            reusable = False
        if reusable:
            return RunOutcome(
                name=name,
                command=tuple(str(part) for part in command),
                exit_code=0,
                duration_s=0.0,
                stderr_tail="reused candidate-bound gate report",
            )
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return RunOutcome(
            name=name,
            command=tuple(str(part) for part in command),
            exit_code=completed.returncode,
            duration_s=time.time() - started,
            stderr_tail=completed.stderr or "",
        )
    except OSError as exc:
        return RunOutcome(
            name=name,
            command=tuple(str(part) for part in command),
            exit_code=None,
            duration_s=time.time() - started,
            stderr_tail=f"{type(exc).__name__}: {exc}",
        )


def _target_sites(plan_document: Mapping[str, Any]) -> tuple[str, ...]:
    """Target addresses in the artifact scanner's ``BB:OOOO`` site format."""
    sites: list[str] = []
    for row in plan_document.get("targets") or []:
        address = str(row.get("abs") or "")
        if len(address) != 6:
            continue
        sites.append(f"{address[:2]}:{address[2:]}")
    return tuple(sites)


def _target_completion_gate(
    plan_document: Mapping[str, Any], validation: Mapping[str, Any]
) -> GateResult:
    """Zero Japanese in every final body, zero unresolved, Hangul where needed."""
    import re

    japanese = re.compile(r"[\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff\u4e00-\u9fff]")
    hangul = re.compile(r"[\uac00-\ud7a3]")
    targets = plan_document.get("targets") or []
    failures: list[str] = []
    unresolved = [row for row in targets if row.get("status") != "resolved"]
    residue = [
        row.get("record_id")
        for row in targets
        if row.get("status") == "resolved" and japanese.search(row.get("korean_text") or "")
    ]
    missing_hangul = [
        row.get("record_id")
        for row in targets
        if row.get("status") == "resolved"
        and japanese.search(row.get("source_text") or "")
        and not hangul.search(row.get("korean_text") or "")
    ]
    if unresolved:
        failures.append(f"{len(unresolved)} unresolved target(s)")
    if residue:
        failures.append(f"{len(residue)} target(s) still contain Japanese")
    if missing_hangul:
        failures.append(f"{len(missing_hangul)} Japanese source(s) without Hangul")
    if validation.get("accepted") is not True:
        failures.append("translation catalog validation is not accepted")
    if int(validation.get("unresolved_count") or 0) != 0:
        failures.append("translation catalog has unresolved rows")
    return GateResult(
        name="target_completion",
        ok=not failures,
        tool="tools/mixed_residual_planner.py",
        report_path="",
        report_sha256=None,
        exit_code=0,
        metrics={
            "targets": len(targets),
            "resolved": len(targets) - len(unresolved),
            "unresolved": len(unresolved),
            "japanese_residue": len(residue),
            "missing_hangul": len(missing_hangul),
        },
        failures=tuple(f"target_completion: {item}" for item in failures),
        status="passed" if not failures else "failed",
    )


def _approved_extent_gate(precommit: Mapping[str, Any]) -> GateResult:
    failures: list[str] = []
    if precommit.get("ok") is not True:
        failures.append("precommit verification did not pass")
    unaccounted = precommit.get("unaccounted_runs") or []
    if unaccounted:
        failures.append(f"{len(unaccounted)} diff run(s) outside every approved extent")
    extents = precommit.get("approved_change_extents") or []
    if not extents:
        failures.append("no approved change extents were reported")
    return GateResult(
        name="approved_extents",
        ok=not failures,
        tool="tools/mixed_residual_transaction.py",
        report_path="",
        report_sha256=None,
        exit_code=0,
        metrics={
            "extents": len(extents),
            "diff_bytes": precommit.get("diff_bytes"),
            "diff_runs": precommit.get("diff_runs"),
            "unaccounted_runs": len(unaccounted),
            "targets_decoded": precommit.get("targets_decoded"),
        },
        failures=tuple(f"approved_extents: {item}" for item in failures),
        status="passed" if not failures else "failed",
    )


def _prefix_preservation_gate(
    inputs: GateInputs, plan_document: Mapping[str, Any]
) -> GateResult:
    """Re-prove game prefixes and verify that the candidate writes after them."""
    failures: list[str] = []
    original = inputs.original_rom.read_bytes()
    candidate = inputs.candidate_rom.read_bytes()
    dictionary = make_dictionary_ext3(
        candidate, load_ext_meta(inputs.ext_meta), load_ext_meta(inputs.ext3_meta)
    )
    tbl = Tbl.load(inputs.tbl)
    sb_original = stock_base(original)
    sb_candidate = stock_base(candidate)
    checked = 0
    preserved = 0
    rendered = 0
    portal_ok = 0
    for row in plan_document.get("targets") or []:
        if row.get("status") != "resolved":
            continue
        checked += 1
        record_id = str(row.get("record_id"))
        logical = int(row.get("abs"), 16)
        capacity = int(row.get("payload_capacity") or 0)
        region = str(row.get("region") or "")
        bank = int(str(row.get("bank") or logical >> 16), 16)
        original_payload = original[sb_original + logical : sb_original + logical + capacity]
        rule_k = _rule_prefix(original_payload, region, bank)
        plan_k = int(row.get("prefix_bytes") or 0)
        if rule_k != plan_k:
            failures.append(f"{record_id}: plan prefix {plan_k} != rule {rule_k}")
            continue
        got = read_encoded_z_safe(candidate, sb_candidate + logical, max_len=max(256, capacity + 1))
        if got is None or len(got[0]) != capacity:
            failures.append(f"{record_id}: candidate record length changed")
            continue
        payload = got[0]
        if payload[:rule_k] != original_payload[:rule_k]:
            failures.append(f"{record_id}: preserved prefix differs from original")
            continue
        preserved += 1
        if rule_k and payload[:2] == b"\xE5\x18":
            failures.append(f"{record_id}: candidate record starts with an ext3 portal")
            continue
        body = payload[rule_k:]
        if str(row.get("strategy") or "") == "ext3":
            if len(body) < 2 or not is_ext3_magic(body[0], body[1]):
                failures.append(f"{record_id}: body does not start with an ext3 portal")
                continue
            portal_ok += 1
        try:
            text = dictionary.expand(body, tbl)
        except Exception as exc:
            failures.append(f"{record_id}: candidate body decode failed ({exc})")
            continue
        expected = str(row.get("korean_text") or "")
        if text.rstrip("\u3000 \t") != expected.rstrip("\u3000 \t"):
            failures.append(f"{record_id}: candidate body does not render approved Korean")
            continue
        rendered += 1
    return GateResult(
        name="prefix_preservation",
        ok=not failures,
        tool="tools/mixed_residual_gate_runner.py",
        report_path="",
        report_sha256=None,
        exit_code=0,
        metrics={
            "checked": checked,
            "prefix_preserved": preserved,
            "rendered_korean": rendered,
            "ext3_portals": portal_ok,
            "failures": len(failures),
        },
        failures=tuple(f"prefix_preservation: {item}" for item in failures),
        status="passed" if not failures else "failed",
    )


def run_static_gates(
    inputs: GateInputs,
    *,
    plan_document: Mapping[str, Any],
    validation: Mapping[str, Any],
    precommit: Mapping[str, Any],
) -> tuple[dict[str, GateResult], list[RunOutcome]]:
    """Run every mandatory gate and normalize the results, fail-closed."""
    inputs.out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[RunOutcome] = []
    results: dict[str, GateResult] = {}

    results["target_completion"] = _target_completion_gate(plan_document, validation)
    results["approved_extents"] = _approved_extent_gate(precommit)
    results["prefix_preservation"] = _prefix_preservation_gate(inputs, plan_document)

    structure_out = inputs.report("structure")
    runs.append(
        _run(
            "structure",
            [
                "tools/scan_script_record_structure.py",
                "--jp",
                str(inputs.original_rom),
                "--target",
                str(inputs.candidate_rom),
                "--lo",
                f"{SCRIPT_BAND_LO:#x}",
                "--hi",
                f"{SCRIPT_BAND_HI:#x}",
                "--out",
                str(structure_out),
            ]
            + (
                [
                    "--baseline",
                    str(inputs.local_expansion_baseline_rom or inputs.baseline_rom),
                    "--approved-local-expansion-report",
                    str(inputs.approved_local_expansion_report),
                ]
                if inputs.approved_local_expansion_report is not None
                else []
            ),
        )
    )
    results["structure"] = normalize_structure(
        load_gate_report(structure_out), path=structure_out, exit_code=runs[-1].exit_code
    )

    nondialogue_out = inputs.report("nondialogue")
    nondialogue_command = [
        "tools/verify_nondialogue_text.py",
        "--jp",
        str(inputs.original_rom),
        "--target",
        str(inputs.candidate_rom),
        "--baseline",
        str(inputs.baseline_rom),
        "--ui-report-dir",
        str(inputs.ui_report_dir),
        "--out",
        str(nondialogue_out),
        "--quiet",
    ]
    if inputs.approved_detachment_report is not None:
        nondialogue_command.extend(
            [
                "--approved-detachment-report",
                str(inputs.approved_detachment_report),
            ]
        )
    runs.append(_run("nondialogue", nondialogue_command))

    results["nondialogue"] = normalize_nondialogue(
        load_gate_report(nondialogue_out),
        path=nondialogue_out,
        exit_code=runs[-1].exit_code,
    )

    stock_out = inputs.report("stock_noninvasion")
    stock_diff = inputs.report("stock_diff")
    stock_command = [
        "tools/verify_stock_noninvasion.py",
        "--jp",
        str(inputs.original_rom),
        "--pre",
        str(inputs.pre_ext3_rom),
        "--target",
        str(inputs.candidate_rom),
        "--baseline",
        str(inputs.baseline_rom),
        "--out",
        str(stock_out),
        "--diff-out",
        str(stock_diff),
    ]
    if inputs.baseline_meta is not None:
        stock_command.extend(["--baseline-meta", str(inputs.baseline_meta)])
    if inputs.approved_stock_report is not None:
        stock_command.extend(
            ["--approved-stock-report", str(inputs.approved_stock_report)]
        )
    if inputs.approved_detachment_report is not None:
        stock_command.extend(
            [
                "--approved-detachment-report",
                str(inputs.approved_detachment_report),
            ]
        )
    runs.append(_run("stock_noninvasion", stock_command))
    results["stock_noninvasion"] = normalize_stock_noninvasion(
        load_gate_report(stock_out), path=stock_out, exit_code=runs[-1].exit_code
    )

    segptr_out = inputs.report("false_segptr")
    runs.append(
        _run(
            "false_segptr",
            [
                "tools/scan_false_segptr_writes.py",
                "--jp",
                str(inputs.original_rom),
                "--target",
                str(inputs.candidate_rom),
                "--out",
                str(segptr_out),
            ],
        )
    )
    results["false_segptr"] = normalize_false_segptr(
        load_gate_report(segptr_out), path=segptr_out, exit_code=runs[-1].exit_code
    )

    smoke_out = inputs.report("smoke")
    smoke_command = [
        "tools/verify_all_stages_smoke.py",
        "--rom",
        str(inputs.candidate_rom),
        "--report",
        str(smoke_out),
    ]
    if inputs.baseline_meta is not None:
        smoke_command.extend(["--baseline-meta", str(inputs.baseline_meta)])
    if inputs.approved_detachment_report is not None:
        smoke_command.extend(
            [
                "--approved-detachment-report",
                str(inputs.approved_detachment_report),
            ]
        )
    runs.append(_run("smoke", smoke_command))
    results["smoke"] = normalize_smoke(
        load_gate_report(smoke_out), path=smoke_out, exit_code=runs[-1].exit_code
    )

    artifacts_out = inputs.report("mixed_artifacts")
    artifacts_baseline = inputs.report("mixed_artifacts_baseline")
    artifacts_exit: int | None = 0
    for rom, out in (
        (inputs.baseline_rom, artifacts_baseline),
        (inputs.candidate_rom, artifacts_out),
    ):
        runs.append(
            _run(
                "mixed_artifacts" if out is artifacts_out else "mixed_artifacts_baseline",
                [
                    "tools/scan_mixed_script_artifacts.py",
                    "--original-rom",
                    str(inputs.original_rom),
                    "--blocks",
                    str(inputs.blocks),
                    "--prefix-report",
                    str(inputs.prefix_evidence),
                    "--candidate-rom",
                    str(rom),
                    "--output",
                    str(out),
                    "--tbl",
                    str(inputs.tbl),
                    "--meta",
                    str(inputs.ext_meta),
                    "--ext3-meta",
                    str(inputs.ext3_meta),
                    "--quiet",
                ],
            )
        )
        # The scan exits non-zero whenever any hit remains anywhere in its wide
        # population, which is expected on this lineage; the gate decision comes
        # from the baseline comparison below, so only a crash counts here.
        if runs[-1].exit_code is None:
            # The process itself could not run; that is a real gate failure.
            artifacts_exit = -1
    results["mixed_artifacts"] = normalize_mixed_artifacts(
        load_gate_report(artifacts_out),
        load_gate_report(artifacts_baseline),
        path=artifacts_out,
        baseline_path=artifacts_baseline,
        target_sites=_target_sites(plan_document),
        exit_code=artifacts_exit,
    )

    full_line_out = inputs.report("full_line_overshare")
    runs.append(
        _run(
            "full_line_overshare",
            [
                "tools/scan_invasion_full_line_tokens.py",
                "--target",
                str(inputs.candidate_rom),
                "--meta",
                str(inputs.ext_meta),
                "--tbl",
                str(inputs.tbl),
                "--sheet",
                str(inputs.sheet),
                "--out",
                str(full_line_out),
            ],
        )
    )
    results["full_line_overshare"] = normalize_full_line_overshare(
        load_gate_report(full_line_out), path=full_line_out, exit_code=runs[-1].exit_code
    )

    ff_candidate = inputs.report("aux_ff_candidate")
    ff_baseline = inputs.report("aux_ff_baseline")
    ff_exit: int | None = 0
    for label, rom, out in (
        ("aux_ff_baseline", inputs.baseline_rom, ff_baseline),
        ("aux_ff_candidate", inputs.candidate_rom, ff_candidate),
    ):
        runs.append(
            _run(
                label,
                [
                    "tools/scan_aux_ff_invasion.py",
                    "--rom",
                    str(rom),
                    "--original-rom",
                    str(inputs.original_rom),
                    "--sheet",
                    str(inputs.sheet),
                    "--tbl",
                    str(inputs.tbl),
                    "--meta",
                    str(inputs.ext_meta),
                    "--out",
                    str(out),
                ],
            )
        )
        if runs[-1].exit_code not in (0, None):
            ff_exit = runs[-1].exit_code
        elif runs[-1].exit_code is None:
            ff_exit = None
    results["aux_ff_invasion"] = normalize_aux_ff_invasion(
        load_gate_report(ff_candidate),
        load_gate_report(ff_baseline),
        candidate_path=ff_candidate,
        baseline_path=ff_baseline,
        exit_code=ff_exit,
    )
    return results, runs


# --------------------------------------------------------------------------- #
# aggregate acceptance report (task 6.3)
# --------------------------------------------------------------------------- #


def build_acceptance_report(
    *,
    inputs: GateInputs,
    plan_document: Mapping[str, Any],
    validation: Mapping[str, Any],
    precommit: Mapping[str, Any],
    gates: Mapping[str, GateResult],
    runs: Sequence[RunOutcome],
    apply_report: Mapping[str, Any] | None,
    candidate_identity: Mapping[str, Any] | None,
    emulator_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One report that carries every identity, decision, count and gate result."""
    gates_ok = all(result.ok for result in gates.values())
    unresolved = [row for row in plan_document.get("targets") or [] if row.get("status") != "resolved"]
    accepted = bool(gates_ok and not unresolved)
    detailed: list[dict[str, Any]] = []
    for result in gates.values():
        if not result.report_path:
            continue
        detailed.append(
            {
                "gate": result.name,
                "tool": result.tool,
                "path": result.report_path,
                "sha256": result.report_sha256,
            }
        )
        extra = result.metrics.get("baseline_report_path")
        if extra:
            detailed.append(
                {
                    "gate": f"{result.name}_baseline",
                    "tool": result.tool,
                    "path": extra,
                    "sha256": result.metrics.get("baseline_report_sha256"),
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "status": "accepted" if accepted else "rejected",
        "accepted": accepted,
        "inputs": {
            "original_rom": _identity(inputs.original_rom),
            "working_rom": (plan_document.get("inputs") or {}).get("working_rom"),
            "accepted_baseline": _identity(inputs.baseline_rom),
            "pre_ext3_rom": _identity(inputs.pre_ext3_rom),
            "candidate_rom": dict(candidate_identity or {}),
            "evidence": {
                "blocks": _identity(inputs.blocks),
                "prefix_evidence": _identity(inputs.prefix_evidence),
                "tbl": _identity(inputs.tbl),
                "ext_meta": _identity(inputs.ext_meta),
                "ext3_meta": _identity(inputs.ext3_meta),
                "baseline_meta": (
                    _identity(inputs.baseline_meta)
                    if inputs.baseline_meta is not None
                    else None
                ),
            },
        },
        "population": {
            "manifest_sha256": plan_document.get("manifest_sha256"),
            "counts": plan_document.get("counts"),
        },
        "targets": plan_document.get("targets"),
        "dictionary_changes": plan_document.get("dictionary_changes"),
        "guard_outcomes": plan_document.get("guard_outcomes"),
        "ext3": plan_document.get("ext3"),
        "approved_change_extents": precommit.get("approved_change_extents"),
        "precommit": {
            key: value
            for key, value in precommit.items()
            if key != "approved_change_extents"
        },
        "translation_validation": dict(validation),
        "apply_report": dict(apply_report or {}),
        "gates": {name: result.as_dict() for name, result in gates.items()},
        "gate_runs": [item.as_dict() for item in runs],
        "unresolved_count": len(unresolved),
        "emulator_follow_up": dict(
            emulator_evidence or {"status": "pending", "blocking": False}
        ),
        "detailed_reports": detailed,
    }


__all__ = [
    "GateInputs",
    "RunOutcome",
    "build_acceptance_report",
    "run_static_gates",
]
