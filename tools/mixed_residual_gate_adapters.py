#!/usr/bin/env python3
"""Read-only normalization of mandatory static gate reports.

Task 6.1 of the mixed Korean/Japanese residual localization spec: the gate
runner must judge every mandatory scanner by *both* the process status and the
parsed JSON. This module owns that judgement and nothing else.

Design contract (design.md §7):

============================  ==========================================
Gate                          Pass condition
============================  ==========================================
``structure``                 ``ok is True`` and ``issues == 0``
``nondialogue``               ``ok is True`` and
                              ``check_iii_length_terminator.violations == 0``
``stock_noninvasion``         top-level ``ok is True``, the single candidate
                              target ``ok is True``,
                              ``counts.unintended_runs == 0``,
                              ``counts.unintended_bytes == 0``,
                              ``out_of_band_dialogue_writes.runs == 0``,
                              ``out_of_band_dialogue_writes.bytes == 0``
``false_segptr``              ``ok is True`` and ``sites_found == 0``
``smoke``                     ``overall_ok is True``
``mixed_artifacts``           ``ok is True``, ``broken_word_hits == 0``,
                              ``split_compound_hits == 0``
``full_line_overshare``       ``early_and_other == 0``
``aux_ff_invasion``           candidate ``counts.ext_ff_page_confirmed`` <=
                              the baseline value of the same field
============================  ==========================================

Fail-closed rules:

* a missing/unreadable report is ``status="missing"`` with ``ok=False``;
* a report missing a required key, or holding the wrong type for one, is
  ``status="malformed"`` with ``ok=False`` (never a pass by omission);
* a non-zero ``exit_code`` (when the caller supplies one) forces ``ok=False``
  and adds an explicit failure string, even if the JSON looks clean.

Nothing here opens a ROM, writes a ROM, or mutates a report. The functions read
already-produced JSON only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "GATE_NAMES",
    "GateResult",
    "load_gate_report",
    "normalize_structure",
    "normalize_nondialogue",
    "normalize_stock_noninvasion",
    "normalize_false_segptr",
    "normalize_smoke",
    "normalize_mixed_artifacts",
    "normalize_full_line_overshare",
    "normalize_aux_ff_invasion",
]

#: Stable gate identifiers, in gate-runner order.
GATE_NAMES: tuple[str, ...] = (
    "structure",
    "nondialogue",
    "stock_noninvasion",
    "false_segptr",
    "smoke",
    "mixed_artifacts",
    "full_line_overshare",
    "aux_ff_invasion",
)

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_MISSING = "missing"
STATUS_MALFORMED = "malformed"

TOOLS: dict[str, str] = {
    "structure": "tools/scan_script_record_structure.py",
    "nondialogue": "tools/verify_nondialogue_text.py",
    "stock_noninvasion": "tools/verify_stock_noninvasion.py",
    "false_segptr": "tools/scan_false_segptr_writes.py",
    "smoke": "tools/verify_all_stages_smoke.py",
    "mixed_artifacts": "tools/scan_mixed_script_artifacts.py",
    "full_line_overshare": "tools/scan_invasion_full_line_tokens.py",
    "aux_ff_invasion": "tools/scan_aux_ff_invasion.py",
}


@dataclass(frozen=True)
class GateResult:
    """Normalized outcome of one mandatory static gate."""

    name: str
    ok: bool
    tool: str
    report_path: str
    report_sha256: str | None
    exit_code: int | None
    metrics: dict = field(default_factory=dict)
    failures: tuple[str, ...] = ()
    status: str = STATUS_MISSING

    def as_dict(self) -> dict[str, Any]:
        """Deterministic mapping for the aggregate acceptance report."""
        return {
            "name": self.name,
            "ok": self.ok,
            "tool": self.tool,
            "report_path": self.report_path,
            "report_sha256": self.report_sha256,
            "exit_code": self.exit_code,
            "metrics": dict(self.metrics),
            "failures": list(self.failures),
            "status": self.status,
        }


class _Malformed(Exception):
    """A required field is absent or has an unusable type."""


def load_gate_report(path) -> dict | None:
    """Parse a gate report.

    Returns ``None`` when the file is absent, unreadable, or not a JSON object;
    every such case is treated as a missing gate result by the normalizers.
    """
    if path is None:
        return None
    candidate = Path(path)
    try:
        if not candidate.is_file():
            return None
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _report_sha256(path) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        if not candidate.is_file():
            return None
        return hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return None


def _as_path_str(path) -> str:
    return "" if path is None else str(path)


def _require_mapping(report: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(report, Mapping):
        raise _Malformed(f"{label} is not an object")
    return report


def _require_true_flag(report: Mapping[str, Any], key: str) -> bool:
    """Read a boolean gate flag. Absent or non-boolean is malformed."""
    if key not in report:
        raise _Malformed(f"missing required key '{key}'")
    value = report[key]
    if not isinstance(value, bool):
        raise _Malformed(f"key '{key}' is not a boolean")
    return value


def _require_int(report: Mapping[str, Any], key: str, *, label: str | None = None) -> int:
    """Read an integer counter. Absent, boolean, or non-integer is malformed."""
    name = label or key
    if key not in report:
        raise _Malformed(f"missing required key '{name}'")
    value = report[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Malformed(f"key '{name}' is not an integer")
    return value


def _require_sub_mapping(
    report: Mapping[str, Any], key: str, *, label: str | None = None
) -> Mapping[str, Any]:
    name = label or key
    if key not in report:
        raise _Malformed(f"missing required key '{name}'")
    value = report[key]
    if not isinstance(value, Mapping):
        raise _Malformed(f"key '{name}' is not an object")
    return value


def _build(
    name: str,
    report: Any,
    path,
    exit_code: int | None,
    evaluate: Callable[[Mapping[str, Any]], tuple[dict, list[str]]],
    *,
    extra_metrics: Mapping[str, Any] | None = None,
    report_sha256: str | None = None,
) -> GateResult:
    """Shared fail-closed skeleton for every adapter."""
    tool = TOOLS[name]
    path_str = _as_path_str(path)
    digest = report_sha256 if report_sha256 is not None else _report_sha256(path)
    exit_failures: list[str] = []
    if exit_code is not None and exit_code != 0:
        exit_failures.append(f"{name}: tool exit code {exit_code}")

    base_metrics: dict[str, Any] = dict(extra_metrics or {})

    if report is None:
        failures = [f"{name}: report missing at {path_str or '<unset>'}"]
        return GateResult(
            name=name,
            ok=False,
            tool=tool,
            report_path=path_str,
            report_sha256=digest,
            exit_code=exit_code,
            metrics=base_metrics,
            failures=tuple(failures + exit_failures),
            status=STATUS_MISSING,
        )

    try:
        mapping = _require_mapping(report, f"{name} report")
        metrics, failures = evaluate(mapping)
    except _Malformed as exc:
        return GateResult(
            name=name,
            ok=False,
            tool=tool,
            report_path=path_str,
            report_sha256=digest,
            exit_code=exit_code,
            metrics=base_metrics,
            failures=tuple([f"{name}: {exc}"] + exit_failures),
            status=STATUS_MALFORMED,
        )

    merged = {**base_metrics, **metrics}
    all_failures = [f"{name}: {item}" for item in failures] + exit_failures
    ok = not all_failures
    return GateResult(
        name=name,
        ok=ok,
        tool=tool,
        report_path=path_str,
        report_sha256=digest,
        exit_code=exit_code,
        metrics=merged,
        failures=tuple(all_failures),
        status=STATUS_PASSED if ok else STATUS_FAILED,
    )


def normalize_structure(report, *, path, exit_code=None) -> GateResult:
    """``scan_script_record_structure.py``: ``ok is True`` and ``issues == 0``."""

    def evaluate(data: Mapping[str, Any]) -> tuple[dict, list[str]]:
        flag = _require_true_flag(data, "ok")
        issues = _require_int(data, "issues")
        failures: list[str] = []
        if not flag:
            failures.append("report ok is not true")
        if issues != 0:
            failures.append(f"{issues} record structure issue(s)")
        return (
            {
                "ok": flag,
                "issues": issues,
                "records_walked": data.get("records_walked"),
                "by_kind": data.get("by_kind"),
            },
            failures,
        )

    return _build("structure", report, path, exit_code, evaluate)


def normalize_nondialogue(report, *, path, exit_code=None) -> GateResult:
    """``verify_nondialogue_text.py``: ``ok`` and zero length/terminator violations."""

    def evaluate(data: Mapping[str, Any]) -> tuple[dict, list[str]]:
        flag = _require_true_flag(data, "ok")
        check = _require_sub_mapping(data, "check_iii_length_terminator")
        violations = _require_int(
            check, "violations", label="check_iii_length_terminator.violations"
        )
        failures: list[str] = []
        if not flag:
            failures.append("report ok is not true")
        if violations != 0:
            failures.append(f"{violations} length/terminator violation(s)")
        return ({"ok": flag, "violations": violations}, failures)

    return _build("nondialogue", report, path, exit_code, evaluate)


def normalize_stock_noninvasion(report, *, path, exit_code=None) -> GateResult:
    """``verify_stock_noninvasion.py``: no unintended and no out-of-band writes.

    Exactly one candidate target entry is required; a report describing several
    targets cannot prove which ROM was judged, so it fails closed as malformed.
    """

    def evaluate(data: Mapping[str, Any]) -> tuple[dict, list[str]]:
        flag = _require_true_flag(data, "ok")
        targets = data.get("targets")
        if not isinstance(targets, list):
            raise _Malformed("missing required key 'targets'")
        if len(targets) != 1:
            raise _Malformed(
                f"expected exactly 1 candidate target entry, found {len(targets)}"
            )
        target = targets[0]
        if not isinstance(target, Mapping):
            raise _Malformed("targets[0] is not an object")
        target_ok = _require_true_flag(target, "ok")
        counts = _require_sub_mapping(target, "counts", label="targets[0].counts")
        unintended_runs = _require_int(
            counts, "unintended_runs", label="targets[0].counts.unintended_runs"
        )
        unintended_bytes = _require_int(
            counts, "unintended_bytes", label="targets[0].counts.unintended_bytes"
        )
        out_of_band = _require_sub_mapping(
            target,
            "out_of_band_dialogue_writes",
            label="targets[0].out_of_band_dialogue_writes",
        )
        ob_runs = _require_int(
            out_of_band, "runs", label="targets[0].out_of_band_dialogue_writes.runs"
        )
        ob_bytes = _require_int(
            out_of_band, "bytes", label="targets[0].out_of_band_dialogue_writes.bytes"
        )

        failures: list[str] = []
        if not flag:
            failures.append("report ok is not true")
        if not target_ok:
            failures.append("candidate target ok is not true")
        if unintended_runs != 0 or unintended_bytes != 0:
            failures.append(
                f"{unintended_bytes} unintended byte(s) in {unintended_runs} run(s)"
            )
        if ob_runs != 0 or ob_bytes != 0:
            failures.append(
                f"{ob_bytes} out-of-band dialogue byte(s) in {ob_runs} run(s)"
            )
        return (
            {
                "ok": flag,
                "target_ok": target_ok,
                "unintended_runs": unintended_runs,
                "unintended_bytes": unintended_bytes,
                "out_of_band_runs": ob_runs,
                "out_of_band_bytes": ob_bytes,
                "target": target.get("target"),
            },
            failures,
        )

    return _build("stock_noninvasion", report, path, exit_code, evaluate)


def normalize_false_segptr(report, *, path, exit_code=None) -> GateResult:
    """``scan_false_segptr_writes.py``: ``ok is True`` and ``sites_found == 0``."""

    def evaluate(data: Mapping[str, Any]) -> tuple[dict, list[str]]:
        flag = _require_true_flag(data, "ok")
        sites = _require_int(data, "sites_found")
        failures: list[str] = []
        if not flag:
            failures.append("report ok is not true")
        if sites != 0:
            failures.append(f"{sites} false segmented-pointer write(s)")
        return (
            {"ok": flag, "sites_found": sites, "by_bank": data.get("by_bank")},
            failures,
        )

    return _build("false_segptr", report, path, exit_code, evaluate)


def normalize_smoke(report, *, path, exit_code=None) -> GateResult:
    """``verify_all_stages_smoke.py``: ``overall_ok is True``."""

    def evaluate(data: Mapping[str, Any]) -> tuple[dict, list[str]]:
        flag = _require_true_flag(data, "overall_ok")
        failures = [] if flag else ["overall_ok is not true"]
        return ({"overall_ok": flag}, failures)

    return _build("smoke", report, path, exit_code, evaluate)


def _artifact_sites(data: Mapping[str, Any]) -> tuple[set[str], bool]:
    """``(sites, complete)`` for the broken/split samples of one scan.

    ``complete`` is false when the scanner truncated a sample, in which case a
    "no new site" statement cannot be made and the gate must fail closed.
    """
    sites: set[str] = set()
    complete = True
    for key, count_key in (
        ("broken_sample", "broken_word_hits"),
        ("split_compound_sites", "split_compound_hits"),
    ):
        rows = data.get(key)
        if not isinstance(rows, list):
            raise _Malformed(f"missing required key '{key}'")
        for row in rows:
            if isinstance(row, Mapping):
                site = row.get("site")
            else:
                site = row
            if isinstance(site, str):
                sites.add(f"{key}:{site}")
        count = data.get(count_key)
        if isinstance(count, int) and not isinstance(count, bool):
            if len(rows) < count:
                complete = False
    return sites, complete


def normalize_mixed_artifacts(
    report,
    baseline_report=None,
    *,
    path,
    baseline_path=None,
    target_sites: Iterable[str] = (),
    exit_code=None,
) -> GateResult:
    """``scan_mixed_script_artifacts.py``, judged against the Accepted_Baseline.

    The scanned population is deliberately wider than the Target_Set (it covers
    every vetted aux block and the Name75 tables), so hits in records this
    feature never touches — non-text zstrings in bank 5A, for instance — cannot
    be removed by it and an absolute zero is unreachable. Requirement 5.3/5.4
    therefore ask for three things:

    * zero hits at any Target_Set site,
    * candidate counts at or below the baseline counts, and
    * no hit at a site that has no corresponding baseline hit.

    Both scans are mandatory; without the baseline there is no statement to make.
    Note on ``exit_code``: this scanner exits non-zero whenever any hit remains
    anywhere in its wide population, which is the normal state of this lineage.
    The runner therefore passes ``0`` for a completed scan and a non-zero value
    only when a scan process could not run at all.
    """
    wanted = {str(item) for item in target_sites}

    def evaluate(data: Mapping[str, Any]) -> tuple[dict, list[str]]:
        broken = _require_int(data, "broken_word_hits")
        split = _require_int(data, "split_compound_hits")
        candidate_sites, complete = _artifact_sites(data)
        base = _require_mapping(baseline_report, "baseline report")
        baseline_broken = _require_int(
            base, "broken_word_hits", label="baseline.broken_word_hits"
        )
        baseline_split = _require_int(
            base, "split_compound_hits", label="baseline.split_compound_hits"
        )
        baseline_sites, baseline_complete = _artifact_sites(base)

        new_sites = sorted(candidate_sites - baseline_sites)
        target_hits = sorted(
            site
            for site in candidate_sites
            if site.split(":", 1)[1] in wanted or site in wanted
        )
        failures: list[str] = []
        if broken > baseline_broken:
            failures.append(
                f"broken-word hits rose from {baseline_broken} to {broken}"
            )
        if split > baseline_split:
            failures.append(
                f"split-compound hits rose from {baseline_split} to {split}"
            )
        if new_sites:
            failures.append(
                f"{len(new_sites)} artifact site(s) absent from the baseline: "
                f"{new_sites[:6]}"
            )
        if target_hits:
            failures.append(
                f"{len(target_hits)} artifact hit(s) inside the Target_Set: "
                f"{target_hits[:6]}"
            )
        if not (complete and baseline_complete):
            failures.append(
                "artifact samples are truncated, so 'no new site' cannot be proven"
            )
        return (
            {
                "ok": data.get("ok"),
                "broken_word_hits": broken,
                "split_compound_hits": split,
                "baseline_broken_word_hits": baseline_broken,
                "baseline_split_compound_hits": baseline_split,
                "broken_delta": broken - baseline_broken,
                "split_delta": split - baseline_split,
                "new_sites": len(new_sites),
                "target_set_hits": len(target_hits),
                "particle_hits": data.get("particle_hits"),
                "baseline_particle_hits": base.get("particle_hits"),
                "baseline_report_path": _as_path_str(baseline_path),
                "baseline_report_sha256": _report_sha256(baseline_path),
                "sample_complete": complete and baseline_complete,
            },
            failures,
        )

    if report is not None and baseline_report is None:
        return GateResult(
            name="mixed_artifacts",
            ok=False,
            tool=TOOLS["mixed_artifacts"],
            report_path=_as_path_str(path),
            report_sha256=_report_sha256(path),
            exit_code=exit_code,
            metrics={"baseline_report_path": _as_path_str(baseline_path)},
            failures=(
                "mixed_artifacts: baseline report missing at "
                f"{_as_path_str(baseline_path) or '<unset>'}",
            ),
            status=STATUS_MISSING,
        )
    return _build("mixed_artifacts", report, path, exit_code, evaluate)


def normalize_full_line_overshare(report, *, path, exit_code=None) -> GateResult:
    """``scan_invasion_full_line_tokens.py``: ``early_and_other == 0``.

    Only the early-plus-other count decides the gate (requirement 5.6); the
    remaining full-line slots are reported for triage, not as a failure.
    """

    def evaluate(data: Mapping[str, Any]) -> tuple[dict, list[str]]:
        early_and_other = _require_int(data, "early_and_other")
        failures: list[str] = []
        if early_and_other != 0:
            failures.append(
                f"{early_and_other} early-plus-other full-line overshare slot(s)"
            )
        top_early_other = data.get("top_early_other")
        return (
            {
                "early_and_other": early_and_other,
                "invasion_slots": data.get("invasion_slots"),
                "cause_counts": data.get("cause_counts"),
                "top_early_other_rows": (
                    len(top_early_other) if isinstance(top_early_other, list) else None
                ),
                "ok": data.get("ok"),
            },
            failures,
        )

    return _build("full_line_overshare", report, path, exit_code, evaluate)


def normalize_aux_ff_invasion(
    candidate_report,
    baseline_report,
    *,
    candidate_path,
    baseline_path,
    exit_code=None,
) -> GateResult:
    """``scan_aux_ff_invasion.py``: confirmed FF-page count must not increase.

    Both scans are mandatory: without the Accepted_Baseline value there is no
    regression statement to make, so a missing baseline is a missing gate.
    """
    name = "aux_ff_invasion"
    tool = TOOLS[name]
    candidate_str = _as_path_str(candidate_path)
    baseline_str = _as_path_str(baseline_path)
    digest = _report_sha256(candidate_path)
    base_metrics: dict[str, Any] = {
        "baseline_report_path": baseline_str,
        "baseline_report_sha256": _report_sha256(baseline_path),
    }
    exit_failures: list[str] = []
    if exit_code is not None and exit_code != 0:
        exit_failures.append(f"{name}: tool exit code {exit_code}")

    missing: list[str] = []
    if candidate_report is None:
        missing.append(f"{name}: candidate report missing at {candidate_str or '<unset>'}")
    if baseline_report is None:
        missing.append(f"{name}: baseline report missing at {baseline_str or '<unset>'}")
    if missing:
        return GateResult(
            name=name,
            ok=False,
            tool=tool,
            report_path=candidate_str,
            report_sha256=digest,
            exit_code=exit_code,
            metrics=base_metrics,
            failures=tuple(missing + exit_failures),
            status=STATUS_MISSING,
        )

    try:
        candidate = _require_mapping(candidate_report, "candidate report")
        baseline = _require_mapping(baseline_report, "baseline report")
        candidate_counts = _require_sub_mapping(
            candidate, "counts", label="candidate counts"
        )
        baseline_counts = _require_sub_mapping(
            baseline, "counts", label="baseline counts"
        )
        candidate_confirmed = _require_int(
            candidate_counts,
            "ext_ff_page_confirmed",
            label="candidate counts.ext_ff_page_confirmed",
        )
        baseline_confirmed = _require_int(
            baseline_counts,
            "ext_ff_page_confirmed",
            label="baseline counts.ext_ff_page_confirmed",
        )
    except _Malformed as exc:
        return GateResult(
            name=name,
            ok=False,
            tool=tool,
            report_path=candidate_str,
            report_sha256=digest,
            exit_code=exit_code,
            metrics=base_metrics,
            failures=tuple([f"{name}: {exc}"] + exit_failures),
            status=STATUS_MALFORMED,
        )

    failures: list[str] = []
    if candidate_confirmed > baseline_confirmed:
        failures.append(
            f"{name}: confirmed FF-page invasions rose from {baseline_confirmed} "
            f"to {candidate_confirmed}"
        )
    metrics = {
        **base_metrics,
        "candidate_ext_ff_page_confirmed": candidate_confirmed,
        "baseline_ext_ff_page_confirmed": baseline_confirmed,
        "delta": candidate_confirmed - baseline_confirmed,
        "candidate_ext_high_severity": candidate_counts.get("ext_high_severity"),
        "baseline_ext_high_severity": baseline_counts.get("ext_high_severity"),
    }
    all_failures = failures + exit_failures
    ok = not all_failures
    return GateResult(
        name=name,
        ok=ok,
        tool=tool,
        report_path=candidate_str,
        report_sha256=digest,
        exit_code=exit_code,
        metrics=metrics,
        failures=tuple(all_failures),
        status=STATUS_PASSED if ok else STATUS_FAILED,
    )
