#!/usr/bin/env python3
"""Build the cumulative P2 exact-reuse + true-free two-byte candidate.

The parent is ``p2_exact_reuse_candidate.wsc``.  This stage writes only
Original+Working-union-proven true-free, non-FF extended slots in expansion bank
0x10, then retargets the approved 2-3 byte records size-preservingly.  Stock 5F,
FF-page slots, pair-steal and pointer relocation are out of scope.  The main TIP
is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_short_records import (  # noqa: E402
    DEFAULT_BASE_MANIFEST,
    DEFAULT_EXT3_META,
    DEFAULT_EXT_META,
    DEFAULT_ORIGINAL_ROM,
    DEFAULT_SHEET as DEFAULT_ANALYSIS_SHEET,
    DEFAULT_TBL,
    DEFAULT_VALUES_DIR,
    analyze,
    build_parser as build_analysis_parser,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_p2_exact_reuse_candidate import (  # noqa: E402
    _atomic_copy,
    _atomic_write,
    _baseline_source_rows,
    _covered,
    diff_runs,
    identity,
    sha256_bytes,
)
from mixed_residual_gate_runner import (  # noqa: E402
    GateInputs,
    build_acceptance_report,
    run_static_gates,
)
from mixed_residual_reference_union import (  # noqa: E402
    build_reference_union,
    guard_slot_writes,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from patch_exp_dictionary import write_exp_dictionary_slots  # noqa: E402

EXPECTED_PARENT_SHA256 = "ae31419d80c408d4b67c02d8d946f62581d5467de19433632f037850e5b91966"
DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_exact_reuse_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_exact_reuse_candidate.sav"
DEFAULT_ANALYSIS_REPORT = ROOT / "out/patch/p2_short_record_capacity_report.json"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_true_free_candidate.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_true_free_candidate.sav"
DEFAULT_REPORT = ROOT / "out/patch/p2_true_free_report.json"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_true_free_gates"
DEFAULT_PRE_EXT3 = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/main_prefix_followup_evidence.json"
DEFAULT_GATE_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_UI_REPORT_DIR = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean"
DEFAULT_BASELINE_META = (
    ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"
)


class P2TrueFreeError(RuntimeError):
    """Raised when the candidate cannot remain within the true-free contract."""


def _analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_analysis_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.working_rom = args.parent_rom
    parsed.base_manifest = args.base_manifest
    parsed.values_dir = args.values_dir
    parsed.sheet = args.analysis_sheet
    parsed.tbl = args.tbl
    parsed.ext_meta = args.ext_meta
    parsed.ext3_meta = args.ext3_meta
    parsed.base_save = args.parent_save
    parsed.out = args.analysis_report
    parsed.stdout = True
    return parsed


def _ext_phrase_cursor(rom: bytes | bytearray, meta: Mapping[str, Any]) -> int:
    ext_ptr_off = int(str(meta["ext_ptr_off"]), 16)
    slot_count = int(meta["slot_count"])
    bank = slice_expansion_bank(rom, int(str(meta["ext_seg"]), 16))
    cursor = ext_ptr_off + slot_count * 2
    for local in range(slot_count):
        pointer = bank[ext_ptr_off + local * 2] | (
            bank[ext_ptr_off + local * 2 + 1] << 8
        )
        if pointer < cursor or pointer >= BANK_SIZE:
            continue
        end = pointer
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        cursor = max(cursor, end + 1)
    return cursor


def _target_row(
    plan_row: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    *,
    strategy: str,
    index_key: str,
) -> dict[str, Any]:
    record_id = str(plan_row["record_id"])
    source = source_rows.get(record_id)
    if source is None:
        raise P2TrueFreeError(f"missing baseline source row: {record_id}")
    logical = int(str(plan_row["abs"]), 16)
    boundary = source.get("boundary") or {}
    prefix_hex = str(source.get("prefix_hex") or "")
    return {
        "record_id": record_id,
        "region": str(plan_row["region"]),
        "bank": f"{logical >> 16:02X}",
        "abs": f"{logical:06X}",
        "payload_capacity": int(boundary.get("payload_capacity") or 0),
        "prefix_bytes": len(bytes.fromhex(prefix_hex)),
        "source_text": str(
            source.get("rendered_source_text") or source.get("source_text") or ""
        ),
        "korean_text": str(plan_row["target_ko"]),
        "strategy": strategy,
        "dictionary_index": str(plan_row[index_key]),
        "status": "resolved",
    }


def _plan_document(
    analysis: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    parent_identity: Mapping[str, Any],
    guard: Mapping[str, Any],
) -> dict[str, Any]:
    strategies = analysis.get("strategy_results") or {}
    exact_rows = (strategies.get("existing_exact_two_byte_token") or {}).get(
        "record_plan", []
    )
    true_rows = (strategies.get("true_free_non_ff_ext_two_byte") or {}).get(
        "record_plan", []
    )
    targets = [
        _target_row(
            row,
            source_rows,
            strategy="existing_exact_two_byte_token",
            index_key="existing_slot",
        )
        for row in exact_rows
    ]
    targets.extend(
        _target_row(
            row,
            source_rows,
            strategy="true_free_non_ff_ext_two_byte",
            index_key="slot",
        )
        for row in true_rows
    )
    allocations = (strategies.get("true_free_non_ff_ext_two_byte") or {}).get(
        "allocations", []
    )
    return {
        "generated_by": "tools/build_p2_true_free_candidate.py",
        "manifest_sha256": (analysis.get("inputs") or {}).get(
            "baseline_manifest_sha256"
        ),
        "inputs": {"working_rom": dict(parent_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
            "exact_reuse": len(exact_rows),
            "true_free": len(true_rows),
        },
        "targets": targets,
        "dictionary_changes": {
            "slots_written": len(allocations),
            "slot_indices": [str(row["slot"]) for row in allocations],
            "pointers_written": len(allocations),
            "stock_5f_written": False,
            "ff_page_written": False,
            "policy": "union_true_free_non_ff_ext_only",
        },
        "guard_outcomes": {"true_free_slot_write": dict(guard)},
        "ext3": {"used": False, "slots_written": 0},
    }


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent_rom.read_bytes()
    parent_sha = sha256_bytes(parent)
    if parent_sha != EXPECTED_PARENT_SHA256:
        raise P2TrueFreeError(
            f"parent identity drifted: expected {EXPECTED_PARENT_SHA256}, got {parent_sha}"
        )
    if len(parent) != 16_777_216:
        raise P2TrueFreeError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise P2TrueFreeError("parent 32 KiB SaveRAM is missing")

    analysis = analyze(_analysis_args(args))
    strategies = analysis.get("strategy_results") or {}
    true_strategy = strategies.get("true_free_non_ff_ext_two_byte") or {}
    allocations = list(true_strategy.get("allocations") or [])
    true_rows = list(true_strategy.get("record_plan") or [])
    exact_rows = list(
        (strategies.get("existing_exact_two_byte_token") or {}).get(
            "record_plan", []
        )
    )
    if true_strategy.get("status") != "GO_read_only_plan" or not true_rows:
        raise P2TrueFreeError("read-only analysis did not approve true-free writes")

    args.analysis_report.parent.mkdir(parents=True, exist_ok=True)
    args.analysis_report.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    original = args.original_rom.read_bytes()
    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    slot_payload = {
        int(str(row["slot"]), 16): bytes.fromhex(str(row["encoded_payload_hex"]))
        for row in allocations
    }
    guard = guard_slot_writes(
        parent,
        slot_payload,
        union=union,
        require_free=True,
    )
    if not guard.ok:
        raise P2TrueFreeError(f"true-free guard refused write: {guard.outcome}")

    candidate = bytearray(parent)
    ext_ptr_off = int(str(ext_meta["ext_ptr_off"]), 16)
    stock_count = int(ext_meta["stock_count"])
    slot_count = int(ext_meta["slot_count"])
    ext_seg = int(str(ext_meta["ext_seg"]), 16)
    phrase_start = _ext_phrase_cursor(candidate, ext_meta)
    write_info = write_exp_dictionary_slots(
        candidate,
        slot_payload,
        ext_ptr_off=ext_ptr_off,
        stock_count=stock_count,
        slot_count=slot_count,
        allow_aux_consumers=False,
        locs=union.as_locs(),
    )
    phrase_end = int(write_info["phrase_end"])

    tbl = Tbl.load(args.tbl)
    dictionary_after_slots = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    for index, payload in slot_payload.items():
        if bytes(dictionary_after_slots.raw_entry(index)) != payload:
            raise P2TrueFreeError(f"slot payload verification failed: {index:04X}")

    sb = stock_base(candidate)
    record_file_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in true_rows:
        logical = int(str(row["abs"]), 16)
        rewritten = bytes.fromhex(str(row["rewrite_payload_hex"]))
        body_span = int(row["body_span"])
        prefix_len = len(rewritten) - body_span
        start = sb + logical
        end = start + len(rewritten)
        before = bytes(candidate[start:end])
        if before[:prefix_len] != rewritten[:prefix_len]:
            raise P2TrueFreeError(f"prefix drift before write: {row['record_id']}")
        terminator = int(str(row["approved_extent"]["terminator"]), 16)
        if candidate[sb + terminator] != 0:
            raise P2TrueFreeError(f"terminator drift before write: {row['record_id']}")
        candidate[start + prefix_len : end] = rewritten[prefix_len:]
        if candidate[sb + terminator] != 0:
            raise P2TrueFreeError(f"terminator overwritten: {row['record_id']}")
        record_file_extents.append((start + prefix_len, end))
        applied.append(
            {
                "record_id": row["record_id"],
                "abs": row["abs"],
                "before_hex": before.hex().upper(),
                "after_hex": bytes(candidate[start:end]).hex().upper(),
                "dictionary_index": row["slot"],
                "target_ko": row["target_ko"],
            }
        )

    before_checksum = bytes(candidate)
    checksum = update_ws_checksum(candidate)
    checksum_runs = diff_runs(before_checksum, candidate)

    expansion_base = ext_seg * BANK_SIZE
    pointer_file_extents = [
        (
            expansion_base + ext_ptr_off + (index - stock_count) * 2,
            expansion_base + ext_ptr_off + (index - stock_count) * 2 + 2,
        )
        for index in sorted(slot_payload)
    ]
    phrase_file_extents = [(expansion_base + phrase_start, expansion_base + phrase_end)]
    approved_file_extents = (
        pointer_file_extents
        + phrase_file_extents
        + record_file_extents
        + checksum_runs
    )
    all_runs = diff_runs(parent, candidate)
    unaccounted = [run for run in all_runs if not _covered(run, approved_file_extents)]
    if unaccounted:
        raise P2TrueFreeError(f"candidate has unapproved diff runs: {unaccounted[:8]}")

    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    decoded_true = 0
    for row in true_rows:
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        rendered = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2TrueFreeError(
                f"target decode mismatch {row['record_id']}: {rendered!r} != {expected!r}"
            )
        decoded_true += 1
    decoded_exact = 0
    for row in exact_rows:
        logical = int(str(row["abs"]), 16)
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        actual = bytes(candidate[sb + logical : sb + logical + len(payload)])
        rendered = dictionary.expand(actual[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2TrueFreeError(f"parent exact target regressed: {row['record_id']}")
        decoded_exact += 1

    candidate_payload = bytes(candidate)
    _atomic_write(args.candidate_rom, candidate_payload)
    _atomic_copy(args.parent_save, args.candidate_save)
    candidate_identity = identity(args.candidate_rom, candidate_payload)
    save_identity = identity(args.candidate_save)
    parent_identity = identity(args.parent_rom, parent)

    source_rows = _baseline_source_rows(args.base_manifest)
    plan = _plan_document(analysis, source_rows, parent_identity, guard.as_dict())
    validation = {
        "accepted": True,
        "unresolved_count": 0,
        "targets": len(plan["targets"]),
        "policy": "reviewed_values_bound_by_ext3_aware_union_analysis",
    }
    approved_change_extents = [
        {
            "kind": "dictionary_pointer",
            "owner_id": f"slot:{index:04X}",
            "file_start": f"{start:08X}",
            "file_end_exclusive": f"{end:08X}",
        }
        for index, (start, end) in zip(sorted(slot_payload), pointer_file_extents)
    ]
    approved_change_extents.extend(
        {
            "kind": "dictionary_payload",
            "owner_id": "expansion_bank10_true_free_phrases",
            "file_start": f"{start:08X}",
            "file_end_exclusive": f"{end:08X}",
        }
        for start, end in phrase_file_extents
    )
    approved_change_extents.extend(
        {
            "kind": "record_body",
            "owner_id": row["record_id"],
            "start": f"{int(row['abs'], 16) + len(bytes.fromhex(str(row['rewrite_payload_hex']))) - int(row['body_span']):06X}",
            "end_exclusive": f"{int(row['abs'], 16) + len(bytes.fromhex(str(row['rewrite_payload_hex']))):06X}",
        }
        for row in true_rows
    )
    approved_change_extents.extend(
        {
            "kind": "checksum",
            "file_start": f"{start:08X}",
            "file_end_exclusive": f"{end:08X}",
        }
        for start, end in checksum_runs
    )
    precommit = {
        "ok": True,
        "diff_bytes": sum(end - start for start, end in all_runs),
        "diff_runs": len(all_runs),
        "unaccounted_runs": [],
        "targets_decoded": decoded_exact + decoded_true,
        "approved_change_extents": approved_change_extents,
        "checksum": f"{checksum:04X}",
    }
    apply_report = {
        "ok": True,
        "policy": "union_true_free_non_ff_ext_two_byte_only",
        "parent_exact_targets_verified": decoded_exact,
        "records_applied": len(applied),
        "dictionary_slots_written": len(slot_payload),
        "dictionary_pointers_written": len(slot_payload),
        "stock_5f_writes": 0,
        "ff_page_writes": 0,
        "terminator_writes": 0,
        "guard": guard.as_dict(),
        "write_info": write_info,
        "applied": applied,
        "candidate_save": save_identity,
    }

    gate_inputs = GateInputs(
        original_rom=args.original_rom,
        pre_ext3_rom=args.pre_ext3_rom,
        baseline_rom=args.parent_rom,
        candidate_rom=args.candidate_rom,
        blocks=args.blocks,
        prefix_evidence=args.prefix_evidence,
        tbl=args.tbl,
        ext_meta=args.ext_meta,
        ext3_meta=args.ext3_meta,
        sheet=args.gate_sheet,
        ui_report_dir=args.ui_report_dir,
        out_dir=args.gate_dir,
        prefix="p2_true_free",
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
            "note": "runtime/emulator confirmation remains outside this work scope",
        },
    )
    report.update(
        {
            "p2_phase": "P2-1_true_free_non_ff_ext_two_byte",
            "parent_candidate": parent_identity,
            "analysis_report": identity(args.analysis_report),
            "candidate_save": save_identity,
            "published": False,
            "main_tip_modified": False,
            "reference_union_fix": {
                "working_two_byte_ext3_aware": True,
                "false_tail_example": "E5 18 FE FB is ext3 index 10EFB, not 2-byte index 0EFB",
            },
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
    parser.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    parser.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--analysis-sheet", type=Path, default=DEFAULT_ANALYSIS_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
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
    if args.candidate_rom.resolve() in {
        args.parent_rom.resolve(),
        (ROOT / "out/patch/monoeye_ko_expanded.wsc").resolve(),
    }:
        raise SystemExit("refusing to overwrite the parent or main TIP")
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
        "new_true_free_targets": report.get("apply_report", {}).get("records_applied"),
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
