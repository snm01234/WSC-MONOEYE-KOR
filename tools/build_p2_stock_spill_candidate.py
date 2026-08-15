#!/usr/bin/env python3
"""Build the cumulative P2 candidate using two union-true-free stock 5F slots.

The parent is ``p2_true_free_candidate.wsc`` (46 approved short records). This
stage appends two Korean phrases to the verified all-FF tail of bank 5F, changes
only the two selected stock dictionary pointers, and retargets the next highest-
impact reviewed 2-3 byte records size-preservingly. No full dictionary rebuild,
FF-page write, pair-steal, far-pointer relocation, or main-TIP overwrite occurs.
"""

from __future__ import annotations

import argparse
import hashlib
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
from build_p2_true_free_candidate import _target_row  # noqa: E402
from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
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
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    slice_bank,
    stock_base,
    update_ws_checksum,
)

EXPECTED_PARENT_SHA256 = "b6296bd2c001108a2b02ec7c38c2774d1acc4c38e1a680068b34bf8c8a90c569"
DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_true_free_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_true_free_candidate.sav"
DEFAULT_ANALYSIS_REPORT = ROOT / "out/patch/p2_stock_spill_capacity_report.json"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_stock_spill_candidate.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_stock_spill_candidate.sav"
DEFAULT_APPROVAL = ROOT / "out/patch/p2_stock_spill_approval.json"
DEFAULT_REPORT = ROOT / "out/patch/p2_stock_spill_report.json"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_stock_spill_gates"
DEFAULT_PRE_EXT3 = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/main_prefix_followup_evidence.json"
DEFAULT_GATE_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_UI_REPORT_DIR = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean"
DEFAULT_BASELINE_META = (
    ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"
)
SPILL_FLOOR = 0x99BA


class P2StockSpillError(RuntimeError):
    """Raised when the stock spill cannot satisfy the byte-preservation contract."""


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


def _stock_phrase_cursor(rom: bytes | bytearray) -> int:
    dictionary = Dictionary(rom)
    bank = bytes(slice_bank(rom, SEG_DICT))
    cursor = SPILL_FLOOR
    for pointer in dictionary.ptrs:
        if pointer < SPILL_FLOOR:
            continue
        if pointer >= BANK_SIZE:
            raise P2StockSpillError(f"stock pointer outside bank: {pointer:04X}")
        end = pointer
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        if end >= BANK_SIZE:
            raise P2StockSpillError(
                f"unterminated stock spill phrase at 5F:{pointer:04X}"
            )
        cursor = max(cursor, end + 1)
    return cursor


def _plan_document(
    analysis: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    parent_identity: Mapping[str, Any],
    guard: Mapping[str, Any],
) -> dict[str, Any]:
    strategies = analysis.get("strategy_results") or {}
    exact_rows = list(
        (strategies.get("existing_exact_two_byte_token") or {}).get(
            "record_plan", []
        )
    )
    stock_rows = list(
        (strategies.get("true_free_non_ff_stock_two_byte") or {}).get(
            "record_plan", []
        )
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
            strategy="true_free_non_ff_stock_two_byte",
            index_key="slot",
        )
        for row in stock_rows
    )
    allocations = list(
        (strategies.get("true_free_non_ff_stock_two_byte") or {}).get(
            "allocations", []
        )
    )
    return {
        "generated_by": "tools/build_p2_stock_spill_candidate.py",
        "manifest_sha256": (analysis.get("inputs") or {}).get(
            "baseline_manifest_sha256"
        ),
        "inputs": {"working_rom": dict(parent_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
            "parent_exact_reuse": len(exact_rows),
            "stock_true_free": len(stock_rows),
        },
        "targets": targets,
        "dictionary_changes": {
            "slots_written": len(allocations),
            "slot_indices": [str(row["slot"]) for row in allocations],
            "pointers_written": len(allocations),
            "stock_5f_written": True,
            "full_rebuild": False,
            "ff_page_written": False,
            "policy": "union_true_free_non_ff_stock_spill_only",
        },
        "guard_outcomes": {"stock_true_free_slot_write": dict(guard)},
        "ext3": {"used": False, "slots_written": 0},
    }


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    _atomic_write(path, payload)


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent_rom.read_bytes()
    parent_sha = sha256_bytes(parent)
    if parent_sha != EXPECTED_PARENT_SHA256:
        raise P2StockSpillError(
            f"parent identity drifted: expected {EXPECTED_PARENT_SHA256}, got {parent_sha}"
        )
    if len(parent) != 16_777_216:
        raise P2StockSpillError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise P2StockSpillError("parent 32 KiB SaveRAM is missing")

    analysis = analyze(_analysis_args(args))
    strategies = analysis.get("strategy_results") or {}
    ext_strategy = strategies.get("true_free_non_ff_ext_two_byte") or {}
    stock_strategy = strategies.get("true_free_non_ff_stock_two_byte") or {}
    exact_rows = list(
        (strategies.get("existing_exact_two_byte_token") or {}).get(
            "record_plan", []
        )
    )
    stock_rows = list(stock_strategy.get("record_plan") or [])
    allocations = list(stock_strategy.get("allocations") or [])
    if ext_strategy.get("record_plan"):
        raise P2StockSpillError(
            "parent still has an earlier-priority non-FF extended true-free plan"
        )
    if stock_strategy.get("status") != "GO_read_only_plan" or not stock_rows:
        raise P2StockSpillError("read-only analysis did not approve stock writes")

    _write_json(args.analysis_report, analysis)

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
    if not slot_payload or any(index >= 0xF00 for index in slot_payload):
        raise P2StockSpillError("stock plan contains no safe non-FF slot")
    guard = guard_slot_writes(
        parent,
        slot_payload,
        union=union,
        require_free=True,
    )
    if not guard.ok:
        raise P2StockSpillError(f"stock true-free guard refused write: {guard.outcome}")

    dictionary_before = Dictionary(parent)
    pointers_before = list(dictionary_before.ptrs)
    payloads_before = [bytes(dictionary_before.raw_entry(i)) for i in range(dictionary_before.count)]
    bank_before = bytes(slice_bank(parent, SEG_DICT))
    phrase_start = _stock_phrase_cursor(parent)
    required = sum(len(payload) + 1 for payload in slot_payload.values())
    if phrase_start + required > BANK_SIZE:
        raise P2StockSpillError(
            f"stock spill capacity exhausted: {phrase_start + required:04X} > FFFF"
        )
    if any(value != 0xFF for value in bank_before[phrase_start:]):
        raise P2StockSpillError(
            f"bank-5F tail is not all FF from {phrase_start:04X}"
        )

    candidate = bytearray(parent)
    pointers_after_write, phrase_end = write_dictionary_slots_spill(
        candidate,
        slot_payload,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=union.as_locs(),
    )
    dictionary_after = Dictionary(candidate)
    pointers_after = list(dictionary_after.ptrs)
    selected = set(slot_payload)
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != selected:
        raise P2StockSpillError(
            "stock pointer delta is not exactly the selected slots: "
            f"{sorted(changed_pointer_indices)} != {sorted(selected)}"
        )
    if pointers_after_write != pointers_after:
        raise P2StockSpillError("writer pointer result differs from ROM pointer table")

    cursor = phrase_start
    for index, payload in sorted(slot_payload.items()):
        if pointers_after[index] != cursor:
            raise P2StockSpillError(
                f"slot {index:04X} pointer {pointers_after[index]:04X} != {cursor:04X}"
            )
        if bytes(dictionary_after.raw_entry(index)) != payload:
            raise P2StockSpillError(f"slot payload verification failed: {index:04X}")
        cursor += len(payload) + 1
    if cursor != phrase_end:
        raise P2StockSpillError(f"phrase end drift: {cursor:04X} != {phrase_end:04X}")

    nonselected_payloads_preserved = True
    for index, before in enumerate(payloads_before):
        if index in selected:
            continue
        if pointers_after[index] != pointers_before[index]:
            raise P2StockSpillError(f"nonselected pointer changed: {index:04X}")
        if bytes(dictionary_after.raw_entry(index)) != before:
            nonselected_payloads_preserved = False
            raise P2StockSpillError(f"nonselected payload changed: {index:04X}")

    bank_after_slots = bytes(slice_bank(candidate, SEG_DICT))
    pointer_local_extents = [
        (DICT_PTR_START + index * 2, DICT_PTR_START + index * 2 + 2)
        for index in sorted(selected)
    ]
    phrase_local_extents = [(phrase_start, phrase_end)]
    bank_runs = diff_runs(bank_before, bank_after_slots)
    bad_bank_runs = [
        run
        for run in bank_runs
        if not _covered(run, pointer_local_extents + phrase_local_extents)
    ]
    if bad_bank_runs:
        raise P2StockSpillError(
            f"bank-5F diff outside selected pointers/tail: {bad_bank_runs[:8]}"
        )

    tbl = Tbl.load(args.tbl)
    sb = stock_base(candidate)
    record_file_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in stock_rows:
        logical = int(str(row["abs"]), 16)
        rewritten = bytes.fromhex(str(row["rewrite_payload_hex"]))
        body_span = int(row["body_span"])
        prefix_len = len(rewritten) - body_span
        start = sb + logical
        end = start + len(rewritten)
        before = bytes(candidate[start:end])
        if before[:prefix_len] != rewritten[:prefix_len]:
            raise P2StockSpillError(f"prefix drift before write: {row['record_id']}")
        terminator = int(str(row["approved_extent"]["terminator"]), 16)
        if candidate[sb + terminator] != 0:
            raise P2StockSpillError(f"terminator drift before write: {row['record_id']}")
        candidate[start + prefix_len : end] = rewritten[prefix_len:]
        if candidate[sb + terminator] != 0:
            raise P2StockSpillError(f"terminator overwritten: {row['record_id']}")
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

    bank_file_base = sb + SEG_DICT * BANK_SIZE
    pointer_file_extents = [
        (bank_file_base + start, bank_file_base + end)
        for start, end in pointer_local_extents
    ]
    phrase_file_extents = [
        (bank_file_base + phrase_start, bank_file_base + phrase_end)
    ]
    approved_file_extents = (
        pointer_file_extents
        + phrase_file_extents
        + record_file_extents
        + checksum_runs
    )
    all_runs = diff_runs(parent, candidate)
    unaccounted = [run for run in all_runs if not _covered(run, approved_file_extents)]
    if unaccounted:
        raise P2StockSpillError(f"candidate has unapproved diff runs: {unaccounted[:8]}")

    dictionary_final = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    decoded_stock = 0
    for row in stock_rows:
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        rendered = dictionary_final.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2StockSpillError(
                f"target decode mismatch {row['record_id']}: {rendered!r} != {expected!r}"
            )
        decoded_stock += 1

    decoded_exact = 0
    for row in exact_rows:
        logical = int(str(row["abs"]), 16)
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        actual = bytes(candidate[sb + logical : sb + logical + len(payload)])
        rendered = dictionary_final.expand(actual[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2StockSpillError(f"parent exact target regressed: {row['record_id']}")
        decoded_exact += 1

    candidate_payload = bytes(candidate)
    _atomic_write(args.candidate_rom, candidate_payload)
    _atomic_copy(args.parent_save, args.candidate_save)
    candidate_identity = identity(args.candidate_rom, candidate_payload)
    save_identity = identity(args.candidate_save)
    parent_identity = identity(args.parent_rom, parent)

    approval = {
        "generated_by": "tools/build_p2_stock_spill_candidate.py",
        "mode": "pre_gate_approval",
        "ok": True,
        "parent_rom": parent_identity,
        "candidate_rom": candidate_identity,
        "approved_stock_slots": [f"{index:04X}" for index in sorted(selected)],
        "slot_audits": {
            f"{index:04X}": union.audit(index) for index in sorted(selected)
        },
        "pointer_changes": [
            {
                "index": f"{index:04X}",
                "before": f"{pointers_before[index]:04X}",
                "after": f"{pointers_after[index]:04X}",
            }
            for index in sorted(selected)
        ],
        "tail": {
            "start": f"{phrase_start:04X}",
            "end_exclusive": f"{phrase_end:04X}",
            "free_before": BANK_SIZE - phrase_start,
            "used": phrase_end - phrase_start,
            "before_sha256": hashlib.sha256(bank_before[phrase_start:]).hexdigest(),
        },
        "proof": {
            "union_true_free": all(union.is_true_free(index) for index in selected),
            "tail_was_all_ff": all(value == 0xFF for value in bank_before[phrase_start:]),
            "changed_pointer_indices_exact": changed_pointer_indices == selected,
            "nonselected_pointers_preserved": all(
                pointers_after[index] == pointers_before[index]
                for index in range(len(pointers_before))
                if index not in selected
            ),
            "nonselected_payloads_preserved": nonselected_payloads_preserved,
            "bank5f_diffs_within_approved_extents": not bad_bank_runs,
        },
    }
    _write_json(args.approval_report, approval)

    source_rows = _baseline_source_rows(args.base_manifest)
    plan = _plan_document(analysis, source_rows, parent_identity, guard.as_dict())
    validation = {
        "accepted": True,
        "unresolved_count": 0,
        "targets": len(plan["targets"]),
        "policy": "reviewed_values_bound_by_ext3_aware_union_stock_spill_analysis",
    }
    approved_change_extents = [
        {
            "kind": "dictionary_pointer",
            "owner_id": f"stock_slot:{index:04X}",
            "file_start": f"{start:08X}",
            "file_end_exclusive": f"{end:08X}",
        }
        for index, (start, end) in zip(sorted(selected), pointer_file_extents)
    ]
    approved_change_extents.extend(
        {
            "kind": "dictionary_payload",
            "owner_id": "bank5f_true_free_stock_phrases",
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
        for row in stock_rows
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
        "targets_decoded": decoded_exact + decoded_stock,
        "approved_change_extents": approved_change_extents,
        "checksum": f"{checksum:04X}",
    }
    apply_report = {
        "ok": True,
        "policy": "union_true_free_non_ff_stock_spill_only",
        "parent_exact_targets_verified": decoded_exact,
        "records_applied": len(applied),
        "dictionary_slots_written": len(slot_payload),
        "dictionary_pointers_written": len(slot_payload),
        "stock_5f_writes": len(slot_payload),
        "full_dictionary_rebuild": False,
        "ff_page_writes": 0,
        "terminator_writes": 0,
        "guard": guard.as_dict(),
        "spill": {
            "floor": f"{SPILL_FLOOR:04X}",
            "phrase_start": f"{phrase_start:04X}",
            "phrase_end": f"{phrase_end:04X}",
            "bytes_used": phrase_end - phrase_start,
            "bytes_free": BANK_SIZE - phrase_end,
        },
        "applied": applied,
        "candidate_save": save_identity,
        "approval_report": identity(args.approval_report),
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
        prefix="p2_stock_spill",
        baseline_meta=args.baseline_meta,
        approved_stock_report=args.approval_report,
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
            "p2_phase": "P2-1_true_free_non_ff_stock_spill",
            "parent_candidate": parent_identity,
            "analysis_report": identity(args.analysis_report),
            "approval_report": identity(args.approval_report),
            "candidate_save": save_identity,
            "published": False,
            "main_tip_modified": False,
            "stock_5f_contract": approval,
        }
    )
    _write_json(args.report, report)
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
    parser.add_argument("--approval-report", type=Path, default=DEFAULT_APPROVAL)
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
    protected = {
        args.parent_rom.resolve(),
        (ROOT / "out/patch/monoeye_ko_expanded.wsc").resolve(),
    }
    if args.candidate_rom.resolve() in protected:
        raise SystemExit("refusing to overwrite the parent or main TIP")
    if args.candidate_rom.suffix.lower() != ".wsc":
        raise SystemExit("candidate ROM must use .wsc")
    if args.candidate_save.suffix.lower() != ".sav":
        raise SystemExit("candidate SaveRAM must use .sav")
    if args.candidate_rom.stem != args.candidate_save.stem:
        raise SystemExit("candidate ROM and SaveRAM must have the same stem")
    report = build_candidate(args)
    summary = {
        "status": report.get("status"),
        "accepted": report.get("accepted"),
        "candidate_rom": report.get("inputs", {}).get("candidate_rom"),
        "candidate_save": report.get("candidate_save"),
        "targets": len(report.get("targets") or []),
        "new_stock_targets": report.get("apply_report", {}).get("records_applied"),
        "stock_slots": report.get("stock_5f_contract", {}).get(
            "approved_stock_slots"
        ),
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
