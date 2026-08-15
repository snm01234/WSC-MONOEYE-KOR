#!/usr/bin/env python3
"""Build and statically gate the P2-1 exact-token-reuse candidate.

The only allowed record change is a size-preserving rewrite of one of the
read-only analysis report's approved 2-3 byte bodies to an already-existing
2-byte dictionary token.  Dictionary payloads, pointers and terminators are not
modified.  The main TIP is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_short_records import (  # noqa: E402
    DEFAULT_BASE_MANIFEST,
    DEFAULT_BASE_SAVE,
    DEFAULT_EXT_META,
    DEFAULT_ORIGINAL_ROM,
    DEFAULT_OUT as DEFAULT_ANALYSIS_REPORT,
    DEFAULT_SHEET,
    DEFAULT_TBL,
    DEFAULT_VALUES_DIR,
    DEFAULT_WORKING_ROM,
    analyze,
    build_parser as build_analysis_parser,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_gate_runner import (  # noqa: E402
    GateInputs,
    build_acceptance_report,
    run_static_gates,
)
from monoeye_rom import Tbl, stock_base, update_ws_checksum  # noqa: E402

EXPECTED_TIP_SHA256 = "ec37720a93cadd8cd91bb1ffcb490d4d89b05eb49363a38c05ed6be46d29a9cb"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_exact_reuse_candidate.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_exact_reuse_candidate.sav"
DEFAULT_REPORT = ROOT / "out/patch/p2_exact_reuse_report.json"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_exact_reuse_gates"
DEFAULT_PRE_EXT3 = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/main_prefix_followup_evidence.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_GATE_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_UI_REPORT_DIR = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean"
DEFAULT_BASELINE_META = (
    ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"
)


class P2CandidateError(RuntimeError):
    """Raised when candidate construction cannot remain fail-closed."""


def sha256_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | bytearray | None = None) -> dict[str, Any]:
    payload = bytes(data) if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve()),
        "present": True,
        "size": len(payload),
        "sha256": sha256_bytes(payload),
    }


def diff_runs(before: bytes | bytearray, after: bytes | bytearray) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise P2CandidateError("candidate size changed")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for offset, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = offset
        elif left == right and start is not None:
            runs.append((start, offset))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def _covered(run: tuple[int, int], extents: Iterable[tuple[int, int]]) -> bool:
    start, end = run
    return any(lo <= start and end <= hi for lo, hi in extents)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    if temporary.stat().st_size != len(payload):
        temporary.unlink(missing_ok=True)
        raise P2CandidateError(f"short write: {path}")
    os.replace(temporary, path)


def _atomic_copy(source: Path, destination: Path) -> None:
    payload = source.read_bytes()
    _atomic_write(destination, payload)
    if sha256_bytes(destination.read_bytes()) != sha256_bytes(payload):
        raise P2CandidateError(f"copy verification failed: {destination}")


def _analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_analysis_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.working_rom = args.baseline_rom
    parsed.base_manifest = args.base_manifest
    parsed.values_dir = args.values_dir
    parsed.sheet = args.analysis_sheet
    parsed.tbl = args.tbl
    parsed.ext_meta = args.ext_meta
    parsed.ext3_meta = args.ext3_meta
    parsed.base_save = args.base_save
    parsed.out = args.analysis_report
    parsed.stdout = True
    return parsed


def _baseline_source_rows(path: Path) -> dict[str, Mapping[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = (document.get("population") or {}).get("excluded") or []
    return {
        str(row.get("record_id") or ""): row
        for row in rows
        if isinstance(row, Mapping) and row.get("record_id")
    }


def _plan_document(
    analysis: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    baseline_identity: Mapping[str, Any],
) -> dict[str, Any]:
    records = (
        (analysis.get("strategy_results") or {})
        .get("existing_exact_two_byte_token", {})
        .get("record_plan", [])
    )
    targets: list[dict[str, Any]] = []
    for record in records:
        record_id = str(record["record_id"])
        source = source_rows.get(record_id)
        if source is None:
            raise P2CandidateError(f"missing baseline source row: {record_id}")
        payload_capacity = int((source.get("boundary") or {}).get("payload_capacity") or 0)
        prefix_hex = str(source.get("prefix_hex") or "")
        logical = int(str(record["abs"]), 16)
        targets.append(
            {
                "record_id": record_id,
                "region": record["region"],
                "bank": f"{logical >> 16:02X}",
                "abs": record["abs"],
                "payload_capacity": payload_capacity,
                "prefix_bytes": len(bytes.fromhex(prefix_hex)),
                "source_text": str(source.get("rendered_source_text") or source.get("source_text") or ""),
                "korean_text": record["target_ko"],
                "strategy": "existing_exact_two_byte_token",
                "dictionary_index": record["existing_slot"],
                "status": "resolved",
            }
        )
    return {
        "generated_by": "tools/build_p2_exact_reuse_candidate.py",
        "manifest_sha256": (analysis.get("inputs") or {}).get(
            "baseline_manifest_sha256"
        ),
        "inputs": {"working_rom": dict(baseline_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
        },
        "targets": targets,
        "dictionary_changes": {
            "slots_written": 0,
            "pointers_written": 0,
            "policy": "reuse_existing_payload_only",
        },
        "guard_outcomes": {
            "prefix_and_terminator_preserved": True,
            "fixed_roster_excluded": True,
            "pair_steal_used": False,
            "far_pointer_relocation_used": False,
        },
        "ext3": {"used": False, "slots_written": 0},
    }


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    baseline = args.baseline_rom.read_bytes()
    baseline_sha = sha256_bytes(baseline)
    if baseline_sha != EXPECTED_TIP_SHA256:
        raise P2CandidateError(
            f"main TIP identity drifted: expected {EXPECTED_TIP_SHA256}, got {baseline_sha}"
        )
    if len(baseline) != 16_777_216:
        raise P2CandidateError("main TIP is not 16 MiB")
    if not args.base_save.is_file() or args.base_save.stat().st_size != 32_768:
        raise P2CandidateError("approved 32 KiB base SaveRAM is missing")

    analysis = analyze(_analysis_args(args))
    exact = (analysis.get("strategy_results") or {}).get(
        "existing_exact_two_byte_token", {}
    )
    records = exact.get("record_plan") or []
    if exact.get("status") != "GO_read_only_plan" or not records:
        raise P2CandidateError("read-only P2 analysis did not approve exact reuse")

    args.analysis_report.parent.mkdir(parents=True, exist_ok=True)
    args.analysis_report.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    candidate = bytearray(baseline)
    sb = stock_base(candidate)
    target_file_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for record in records:
        logical = int(str(record["abs"]), 16)
        payload = bytes.fromhex(str(record["rewrite_payload_hex"]))
        start = sb + logical
        end = start + len(payload)
        terminator = int(str(record["approved_extent"]["terminator"]), 16)
        if candidate[sb + terminator] != 0:
            raise P2CandidateError(f"terminator drift before write: {record['record_id']}")
        before = bytes(candidate[start:end])
        candidate[start:end] = payload
        if candidate[sb + terminator] != 0:
            raise P2CandidateError(f"terminator overwritten: {record['record_id']}")
        target_file_extents.append((start, end))
        applied.append(
            {
                "record_id": record["record_id"],
                "abs": record["abs"],
                "before_hex": before.hex().upper(),
                "after_hex": payload.hex().upper(),
                "dictionary_index": record["existing_slot"],
                "target_ko": record["target_ko"],
            }
        )

    before_checksum = bytes(candidate)
    checksum = update_ws_checksum(candidate)
    checksum_runs = diff_runs(before_checksum, candidate)
    approved_file_extents = target_file_extents + checksum_runs
    all_runs = diff_runs(baseline, candidate)
    unaccounted = [run for run in all_runs if not _covered(run, approved_file_extents)]
    if unaccounted:
        raise P2CandidateError(f"candidate has unapproved diff runs: {unaccounted[:8]}")

    dictionary = make_dictionary_ext3(
        candidate, load_ext_meta(args.ext_meta), load_ext_meta(args.ext3_meta)
    )
    tbl = Tbl.load(args.tbl)
    decoded = 0
    for record in records:
        logical = int(str(record["abs"]), 16)
        payload = bytes.fromhex(str(record["rewrite_payload_hex"]))
        body_span = int(record["body_span"])
        prefix_length = len(payload) - body_span
        rendered = dictionary.expand(payload[prefix_length:], tbl).rstrip("\u3000 \t")
        expected = str(record["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2CandidateError(
                f"candidate decode mismatch {record['record_id']}: {rendered!r} != {expected!r}"
            )
        decoded += 1

    candidate_payload = bytes(candidate)
    _atomic_write(args.candidate_rom, candidate_payload)
    _atomic_copy(args.base_save, args.candidate_save)
    candidate_identity = identity(args.candidate_rom, candidate_payload)
    save_identity = identity(args.candidate_save)

    source_rows = _baseline_source_rows(args.base_manifest)
    baseline_identity = identity(args.baseline_rom, baseline)
    plan = _plan_document(analysis, source_rows, baseline_identity)
    validation = {
        "accepted": True,
        "unresolved_count": 0,
        "targets": len(records),
        "policy": "reviewed_values_bound_by_analysis_report",
    }
    approved_change_extents = [
        {
            "kind": "record_payload",
            "owner_id": record["record_id"],
            "start": record["abs"],
            "end_exclusive": f"{int(record['abs'], 16) + len(bytes.fromhex(record['rewrite_payload_hex'])):06X}",
        }
        for record in records
    ] + [
        {
            "kind": "checksum",
            "file_start": f"{start:08X}",
            "file_end_exclusive": f"{end:08X}",
        }
        for start, end in checksum_runs
    ]
    precommit = {
        "ok": True,
        "diff_bytes": sum(end - start for start, end in all_runs),
        "diff_runs": len(all_runs),
        "unaccounted_runs": [],
        "targets_decoded": decoded,
        "approved_change_extents": approved_change_extents,
        "checksum": f"{checksum:04X}",
    }
    apply_report = {
        "ok": True,
        "policy": "existing_exact_two_byte_token_only",
        "records_applied": len(applied),
        "dictionary_slots_written": 0,
        "pointer_writes": 0,
        "terminator_writes": 0,
        "applied": applied,
        "candidate_save": save_identity,
    }

    gate_inputs = GateInputs(
        original_rom=args.original_rom,
        pre_ext3_rom=args.pre_ext3_rom,
        baseline_rom=args.baseline_rom,
        candidate_rom=args.candidate_rom,
        blocks=args.blocks,
        prefix_evidence=args.prefix_evidence,
        tbl=args.tbl,
        ext_meta=args.ext_meta,
        ext3_meta=args.ext3_meta,
        sheet=args.gate_sheet,
        ui_report_dir=args.ui_report_dir,
        out_dir=args.gate_dir,
        prefix="p2_exact_reuse",
        baseline_meta=args.baseline_meta,
    )
    gates, runs = run_static_gates(
        gate_inputs,
        plan_document=plan,
        validation=validation,
        precommit=precommit,
    )
    report = build_acceptance_report(
        inputs=gate_inputs,
        plan_document=plan,
        validation=validation,
        precommit=precommit,
        gates=gates,
        runs=runs,
        apply_report=apply_report,
        candidate_identity=candidate_identity,
        emulator_evidence={
            "status": "skipped_per_user_scope",
            "blocking": False,
            "note": "runtime/emulator confirmation was explicitly excluded from this work scope",
        },
    )
    report.update(
        {
            "p2_phase": "P2-1_exact_existing_token_reuse",
            "analysis_report": identity(args.analysis_report),
            "candidate_save": save_identity,
            "published": False,
            "main_tip_modified": False,
        }
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    parser.add_argument("--baseline-rom", type=Path, default=DEFAULT_WORKING_ROM)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--analysis-sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--base-save", type=Path, default=DEFAULT_BASE_SAVE)
    parser.add_argument("--analysis-report", type=Path, default=DEFAULT_ANALYSIS_REPORT)
    parser.add_argument("--candidate-rom", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--candidate-save", type=Path, default=DEFAULT_CANDIDATE_SAVE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--gate-dir", type=Path, default=DEFAULT_GATE_DIR)
    parser.add_argument("--pre-ext3-rom", type=Path, default=DEFAULT_PRE_EXT3)
    parser.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    parser.add_argument("--prefix-evidence", type=Path, default=DEFAULT_PREFIX_EVIDENCE)
    parser.add_argument("--gate-sheet", type=Path, default=DEFAULT_GATE_SHEET)
    parser.add_argument("--ui-report-dir", type=Path, default=DEFAULT_UI_REPORT_DIR)
    parser.add_argument("--baseline-meta", type=Path, default=DEFAULT_BASELINE_META)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.candidate_rom.resolve() == args.baseline_rom.resolve():
        raise SystemExit("refusing to overwrite the main TIP")
    if args.candidate_rom.suffix.lower() != ".wsc":
        raise SystemExit("candidate ROM must use .wsc")
    if args.candidate_save.suffix.lower() != ".sav":
        raise SystemExit("candidate SaveRAM must use .sav")
    report = build_candidate(args)
    summary = {
        "status": report.get("status"),
        "accepted": report.get("accepted"),
        "candidate_rom": report.get("inputs", {}).get("candidate_rom"),
        "candidate_save": report.get("candidate_save"),
        "targets": len(report.get("targets") or []),
        "gates": {
            name: result.get("ok") for name, result in (report.get("gates") or {}).items()
        },
        "main_tip_modified": False,
        "report": str(args.report),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.get("accepted") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
