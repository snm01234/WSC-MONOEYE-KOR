#!/usr/bin/env python3
"""Build the cumulative P2 candidate by reclaiming retired stock slots.

The parent has 105 approved short-record localizations.  This stage reuses only
stock slots proven by :mod:`analyze_p2_retired_slot_reclaim` to be unreachable
in the current runtime while remaining byte-identical to Original.  Selected
pointers are moved to the verified all-FF bank-5F tail and all remaining 2/3-byte
records are rewritten size-preservingly with ordinary two-byte tokens.

No runtime hook, compact3 mode, FF-page slot, far pointer, local terminator move,
or main-TIP write is performed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import (  # noqa: E402
    external_occurrence_map,
    nested_occurrence_map,
)
from analyze_p2_retired_slot_reclaim import (  # noqa: E402
    DEFAULT_OUT as DEFAULT_ANALYSIS_REPORT,
    DEFAULT_PARENT_APPROVAL,
    DEFAULT_PARENT_REPORT,
    DEFAULT_PARENT_ROM,
    DEFAULT_PARENT_SAVE,
    _raw_pair_hits,
    analyze,
    build_parser as build_analysis_parser,
)
from analyze_p2_short_records import (  # noqa: E402
    DEFAULT_BASE_MANIFEST,
    DEFAULT_EXT3_META,
    DEFAULT_EXT_META,
    DEFAULT_ORIGINAL_ROM,
    DEFAULT_SHEET as DEFAULT_ANALYSIS_SHEET,
    DEFAULT_TBL,
    DEFAULT_VALUES_DIR,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_p2_duplicate_detach_candidate import _payload_at, _write_json  # noqa: E402
from build_p2_exact_reuse_candidate import (  # noqa: E402
    _atomic_copy,
    _atomic_write,
    _baseline_source_rows,
    diff_runs,
    identity,
    sha256_bytes,
)
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor  # noqa: E402
from build_p2_true_free_candidate import _target_row  # noqa: E402
from expand_dictionary import (  # noqa: E402
    guard_hangul_slot_writes,
    write_dictionary_slots_spill,
)
from mixed_residual_gate_runner import (  # noqa: E402
    GateInputs,
    build_acceptance_report,
    run_static_gates,
)
from mixed_residual_reference_union import _working_two_byte_external_refs  # noqa: E402
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
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

EXPECTED_PARENT_SHA256 = "9cc8727e1582c028353d936126a22cccf2511328c1def4fe06bc119fde6e620f"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_retired_slot_reclaim_candidate.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_retired_slot_reclaim_candidate.sav"
DEFAULT_APPROVAL = ROOT / "out/patch/p2_retired_slot_reclaim_approval.json"
DEFAULT_REPORT = ROOT / "out/patch/p2_retired_slot_reclaim_report.json"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_retired_slot_reclaim_gates"
DEFAULT_LOCAL_EXPANSION_BASELINE = ROOT / "out/patch/p2_nested_duplicate_batch_candidate.wsc"
DEFAULT_PRE_EXT3 = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/main_prefix_followup_evidence.json"
DEFAULT_GATE_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_UI_REPORT_DIR = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean"
DEFAULT_BASELINE_META = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"


class RetiredSlotBuildError(RuntimeError):
    pass


def _covered(run: tuple[int, int], extents: Sequence[tuple[int, int]]) -> bool:
    """Return true when a diff run is covered by the union of approved extents."""
    lo, hi = run
    cursor = lo
    for start, end in sorted(extents):
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= hi:
            return True
    return cursor >= hi


def _analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_analysis_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.parent_rom = args.parent_rom
    parsed.parent_save = args.parent_save
    parsed.parent_approval = args.parent_approval
    parsed.parent_report = args.parent_report
    parsed.base_manifest = args.base_manifest
    parsed.values_dir = args.values_dir
    parsed.sheet = args.analysis_sheet
    parsed.tbl = args.tbl
    parsed.ext_meta = args.ext_meta
    parsed.ext3_meta = args.ext3_meta
    parsed.out = args.analysis_report
    return parsed


def _plan_document(
    analysis: Mapping[str, Any],
    parent_report: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    parent_identity: Mapping[str, Any],
) -> dict[str, Any]:
    parent_targets = [dict(row) for row in parent_report.get("targets") or []]
    new_rows = list((analysis.get("allocation") or {}).get("record_plan") or [])
    new_targets = [
        _target_row(
            row,
            source_rows,
            strategy="retired_stock_slot_reclaim",
            index_key="slot",
        )
        for row in new_rows
    ]
    targets = parent_targets + new_targets
    return {
        "generated_by": "tools/build_p2_retired_slot_reclaim_candidate.py",
        "inputs": {"working_rom": dict(parent_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
            "parent_targets": len(parent_targets),
            "retired_slot_targets": len(new_targets),
        },
        "targets": targets,
        "dictionary_changes": {
            "slots_written": int((analysis.get("retired_inventory") or {}).get("selected_slots") or 0),
            "slot_indices": [
                str(row["slot"])
                for row in (analysis.get("allocation") or {}).get("allocations") or []
            ],
            "pointers_written": int((analysis.get("retired_inventory") or {}).get("selected_slots") or 0),
            "stock_5f_written": True,
            "full_rebuild": False,
            "ff_page_written": False,
            "runtime_written": False,
            "terminator_moves": 0,
            "policy": "candidate_bound_retired_stock_slot_reclaim",
        },
        "guard_outcomes": {
            "retired_slot_current_runtime_zero": {
                "ok": True,
                "slots": int((analysis.get("retired_inventory") or {}).get("selected_slots") or 0),
            }
        },
        "ext3": {"used": False, "slots_written": 0},
    }


def _historical_render_checks(
    parent: bytes,
    parent_dictionary: Any,
    selected_rows: Sequence[Mapping[str, Any]],
    stage_target_logicals: set[int],
    tbl: Tbl,
) -> tuple[list[dict[str, Any]], int]:
    occurrences: dict[int, dict[str, Any]] = {}
    for slot_row in selected_rows:
        slot = str(slot_row["slot"])
        for occurrence in slot_row.get("historical_external_occurrences") or []:
            logical = int(str(occurrence["record_abs"]), 16)
            entry = occurrences.setdefault(
                logical,
                {
                    "record_abs": f"{logical:06X}",
                    "regions": set(),
                    "historical_slots": set(),
                    "stage_target": logical in stage_target_logicals,
                },
            )
            entry["regions"].add(str(occurrence.get("region") or "?"))
            entry["historical_slots"].add(slot)
    checks: list[dict[str, Any]] = []
    targeted = 0
    for logical, entry in sorted(occurrences.items()):
        if entry["stage_target"]:
            targeted += 1
            checks.append(
                {
                    "record_abs": f"{logical:06X}",
                    "regions": sorted(entry["regions"]),
                    "historical_slots": sorted(entry["historical_slots"]),
                    "stage_target": True,
                    "accounted": True,
                }
            )
            continue
        payload = _payload_at(parent, logical)
        rendered = parent_dictionary.expand(payload, tbl)
        checks.append(
            {
                "record_abs": f"{logical:06X}",
                "regions": sorted(entry["regions"]),
                "historical_slots": sorted(entry["historical_slots"]),
                "stage_target": False,
                "before_payload_hex": payload.hex().upper(),
                "before_render": rendered,
                "accounted": True,
            }
        )
    return checks, targeted


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent_rom.read_bytes()
    parent_sha = sha256_bytes(parent)
    if parent_sha != str(args.expected_parent_sha).lower():
        raise RetiredSlotBuildError(
            f"parent identity drifted: expected {args.expected_parent_sha}, got {parent_sha}"
        )
    if len(parent) != 16_777_216:
        raise RetiredSlotBuildError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise RetiredSlotBuildError("same-stem 32 KiB SaveRAM is missing")

    inherited_slots, inherited_sha, inherited_ranges = load_approved_detachment(
        args.parent_approval
    )
    if inherited_sha != parent_sha:
        raise RetiredSlotBuildError("parent approval candidate SHA does not match parent")
    parent_approval = json.loads(args.parent_approval.read_text(encoding="utf-8"))
    parent_report = json.loads(args.parent_report.read_text(encoding="utf-8"))
    if parent_report.get("accepted") is not True:
        raise RetiredSlotBuildError("parent report is not accepted")

    analysis = analyze(_analysis_args(args))
    if (analysis.get("decision") or {}).get("status") != "GO_retired_stock_slot_reclaim_all_remaining":
        raise RetiredSlotBuildError("read-only analysis did not approve full reclaim")
    _write_json(args.analysis_report, analysis)

    allocations = list((analysis.get("allocation") or {}).get("allocations") or [])
    new_rows = list((analysis.get("allocation") or {}).get("record_plan") or [])
    selected_evidence = list((analysis.get("retired_inventory") or {}).get("selected") or [])
    slot_payload = {
        int(str(row["slot"]), 16): bytes.fromhex(str(row["encoded_payload_hex"]))
        for row in allocations
    }
    selected = set(slot_payload)
    if len(selected) != 83 or len(new_rows) != 100:
        raise RetiredSlotBuildError("analysis did not produce the expected 83-slot/100-record plan")
    if selected & inherited_slots:
        raise RetiredSlotBuildError("retired selection overlaps parent-approved stock ownership")

    original = args.original_rom.read_bytes()
    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    tbl = Tbl.load(args.tbl)
    sb = stock_base(parent)
    parent_dictionary_ext = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    dictionary_before = Dictionary(parent)
    pointers_before = list(dictionary_before.ptrs)
    payloads_before = [
        bytes(dictionary_before.raw_entry(index))
        for index in range(dictionary_before.count)
    ]

    current_external = external_occurrence_map(
        parent, ext3_aware=True, wanted=selected
    )
    current_nested = nested_occurrence_map(
        parent_dictionary_ext, wanted=selected, ext3_aware=True
    )
    current_raw_hits = _raw_pair_hits(parent, sorted(selected))
    if any(current_external.get(index) for index in selected):
        raise RetiredSlotBuildError("selected retired slot has a current external consumer")
    if any(current_nested.get(index) for index in selected):
        raise RetiredSlotBuildError("selected retired slot has a current nested parent")
    if any(current_raw_hits.get(index) for index in selected):
        raise RetiredSlotBuildError("selected retired slot has a current raw token pair")

    current_locs = _working_two_byte_external_refs(parent)
    if any(current_locs.get(index) for index in selected):
        raise RetiredSlotBuildError("working current-only guard still sees selected slots")
    guard_hangul_slot_writes(
        parent,
        slot_payload,
        allow_aux_consumers=False,
        locs=current_locs,
    )

    bank_before = bytes(slice_bank(parent, SEG_DICT))
    phrase_start = _stock_phrase_cursor(parent)
    required = sum(len(payload) + 1 for payload in slot_payload.values())
    if phrase_start + required > BANK_SIZE:
        raise RetiredSlotBuildError("bank-5F tail capacity exhausted")
    if any(value != 0xFF for value in bank_before[phrase_start:]):
        raise RetiredSlotBuildError(f"bank-5F tail is not all FF from {phrase_start:04X}")

    stage_target_logicals = {int(str(row["abs"]), 16) for row in new_rows}
    historical_checks, historical_stage_targets = _historical_render_checks(
        parent,
        parent_dictionary_ext,
        selected_evidence,
        stage_target_logicals,
        tbl,
    )

    candidate = bytearray(parent)
    pointers_after_write, phrase_end = write_dictionary_slots_spill(
        candidate,
        slot_payload,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=current_locs,
    )
    dictionary_after_stock = Dictionary(candidate)
    pointers_after = list(dictionary_after_stock.ptrs)
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != selected:
        raise RetiredSlotBuildError("changed pointer set is not exactly selected retired slots")
    if pointers_after_write != pointers_after:
        raise RetiredSlotBuildError("writer pointer result differs from ROM pointer table")

    cursor = phrase_start
    selected_approval_rows: list[dict[str, Any]] = []
    evidence_by_slot = {int(str(row["slot"]), 16): dict(row) for row in selected_evidence}
    allocation_by_slot = {int(str(row["slot"]), 16): dict(row) for row in allocations}
    for index, payload in sorted(slot_payload.items()):
        if pointers_after[index] != cursor:
            raise RetiredSlotBuildError(f"pointer placement drift for {index:04X}")
        if bytes(dictionary_after_stock.raw_entry(index)) != payload:
            raise RetiredSlotBuildError(f"payload verification failed for {index:04X}")
        evidence = evidence_by_slot[index]
        allocation = allocation_by_slot[index]
        selected_approval_rows.append(
            {
                **evidence,
                "new_pointer": f"{cursor:04X}",
                "new_payload_hex": payload.hex().upper(),
                "new_phrase": str(allocation["target_ko"]),
                "new_records": list(allocation["records"]),
            }
        )
        cursor += len(payload) + 1
    if cursor != phrase_end:
        raise RetiredSlotBuildError("stock spill end drifted")

    nonselected_payloads_preserved = True
    for index, before in enumerate(payloads_before):
        if index in selected:
            continue
        if pointers_after[index] != pointers_before[index]:
            raise RetiredSlotBuildError(f"nonselected pointer changed: {index:04X}")
        if bytes(dictionary_after_stock.raw_entry(index)) != before:
            nonselected_payloads_preserved = False
            raise RetiredSlotBuildError(f"nonselected payload changed: {index:04X}")

    pointer_local_extents = [
        (DICT_PTR_START + index * 2, DICT_PTR_START + index * 2 + 2)
        for index in sorted(selected)
    ]
    phrase_local_extent = (phrase_start, phrase_end)
    bank_after = bytes(slice_bank(candidate, SEG_DICT))
    bad_bank_runs = [
        run
        for run in diff_runs(bank_before, bank_after)
        if not _covered(run, pointer_local_extents + [phrase_local_extent])
    ]
    if bad_bank_runs:
        raise RetiredSlotBuildError(f"bank-5F diff outside approved extents: {bad_bank_runs[:8]}")

    record_file_extents: list[tuple[int, int]] = []
    stage_target_records: list[dict[str, Any]] = []
    stage_target_ranges: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    expected_by_slot: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for row in new_rows:
        logical = int(str(row["abs"]), 16)
        rewritten = bytes.fromhex(str(row["rewrite_payload_hex"]))
        body_span = int(row["body_span"])
        prefix_len = len(rewritten) - body_span
        start = sb + logical
        end = start + len(rewritten)
        before = bytes(candidate[start:end])
        if before[:prefix_len] != rewritten[:prefix_len]:
            raise RetiredSlotBuildError(f"prefix drift: {row['record_id']}")
        terminator = int(str(row["approved_extent"]["terminator"]), 16)
        if candidate[sb + terminator] != 0:
            raise RetiredSlotBuildError(f"terminator drift: {row['record_id']}")
        candidate[start + prefix_len : end] = rewritten[prefix_len:]
        if candidate[sb + terminator] != 0:
            raise RetiredSlotBuildError(f"terminator overwritten: {row['record_id']}")
        record_file_extents.append((start + prefix_len, end))
        if str(row.get("region") or "") != "script":
            logical_start = logical + prefix_len
            logical_end = logical + len(rewritten)
            stage_target_records.append(
                {
                    "record_id": str(row["record_id"]),
                    "abs": f"{logical:06X}",
                    "region": str(row["region"]),
                    "logical_start": f"{logical_start:06X}",
                    "logical_end_exclusive": f"{logical_end:06X}",
                    "before_body_hex": before[prefix_len:].hex().upper(),
                    "after_body_hex": bytes(candidate[start + prefix_len : end]).hex().upper(),
                    "target_ko": str(row["target_ko"]),
                }
            )
            stage_target_ranges.append(
                {
                    "logical_start": f"{logical_start:06X}",
                    "logical_end_exclusive": f"{logical_end:06X}",
                    "owner_id": f"retired_target:{row['record_id']}",
                }
            )
        slot = int(str(row["slot"]), 16)
        expected_by_slot[slot].add((f"{logical:06X}", f"{logical + prefix_len:06X}"))
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
        (bank_file_base + lo, bank_file_base + hi)
        for lo, hi in pointer_local_extents
    ]
    phrase_file_extent = (
        bank_file_base + phrase_local_extent[0],
        bank_file_base + phrase_local_extent[1],
    )
    approved_file_extents = (
        pointer_file_extents
        + [phrase_file_extent]
        + record_file_extents
        + checksum_runs
    )
    all_runs = diff_runs(parent, candidate)
    unaccounted = [run for run in all_runs if not _covered(run, approved_file_extents)]
    if unaccounted:
        raise RetiredSlotBuildError(f"unapproved candidate diff: {unaccounted[:8]}")

    final = bytes(candidate)
    final_dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)
    source_rows = _baseline_source_rows(args.base_manifest)
    parent_identity = identity(args.parent_rom, parent)
    plan = _plan_document(analysis, parent_report, source_rows, parent_identity)

    decoded = 0
    for target in plan["targets"]:
        logical = int(str(target["abs"]), 16)
        capacity = int(target["payload_capacity"])
        prefix_len = int(target["prefix_bytes"])
        payload = final[sb + logical : sb + logical + capacity]
        rendered = final_dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(target["korean_text"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise RetiredSlotBuildError(
                f"target render mismatch {target['record_id']}: {rendered!r} != {expected!r}"
            )
        decoded += 1

    final_external = external_occurrence_map(
        final, ext3_aware=True, wanted=selected
    )
    final_nested = nested_occurrence_map(
        final_dictionary, wanted=selected, ext3_aware=True
    )
    final_consumers: dict[str, list[dict[str, Any]]] = {}
    for index in sorted(selected):
        actual = {
            (str(row["record_abs"]), str(row["token_abs"]))
            for row in final_external.get(index, [])
        }
        if actual != expected_by_slot[index]:
            raise RetiredSlotBuildError(
                f"final consumer set drift for {index:04X}: "
                f"{actual ^ expected_by_slot[index]}"
            )
        if final_nested.get(index):
            raise RetiredSlotBuildError(f"new retired slot is nested: {index:04X}")
        final_consumers[f"{index:04X}"] = final_external.get(index, [])

    final_raw_hits = _raw_pair_hits(final, sorted(selected))
    for index in sorted(selected):
        actual_raw = {str(row["token_abs"]) for row in final_raw_hits.get(index, [])}
        expected_raw = {token_abs for _record_abs, token_abs in expected_by_slot[index]}
        if actual_raw != expected_raw:
            raise RetiredSlotBuildError(
                f"unexpected final raw token-pair hit for {index:04X}: "
                f"{actual_raw ^ expected_raw}"
            )

    former_render_preserved = True
    for check in historical_checks:
        if check.get("stage_target") is True:
            check["final_target_verified"] = True
            continue
        logical = int(str(check["record_abs"]), 16)
        payload = _payload_at(final, logical)
        rendered = final_dictionary.expand(payload, tbl)
        check["after_payload_hex"] = payload.hex().upper()
        check["after_render"] = rendered
        check["preserved"] = (
            payload == bytes.fromhex(str(check["before_payload_hex"]))
            and rendered == check["before_render"]
        )
        former_render_preserved &= bool(check["preserved"])
    if not former_render_preserved:
        raise RetiredSlotBuildError("historical non-target consumer rendering changed")

    inherited_stock_rows: list[dict[str, Any]] = []
    inherited_stock_preserved = True
    parent_stock = Dictionary(parent)
    final_stock = Dictionary(final)
    for index in sorted(inherited_slots):
        pointer_equal = parent_stock.ptrs[index] == final_stock.ptrs[index]
        payload_equal = bytes(parent_stock.raw_entry(index)) == bytes(final_stock.raw_entry(index))
        inherited_stock_preserved &= pointer_equal and payload_equal
        inherited_stock_rows.append(
            {
                "index": f"{index:04X}",
                "pointer_preserved": pointer_equal,
                "payload_preserved": payload_equal,
            }
        )
    inherited_ranges_preserved = True
    for lo, hi, _owner in inherited_ranges:
        if parent[sb + lo : sb + hi] != final[sb + lo : sb + hi]:
            inherited_ranges_preserved = False
            break
    if not inherited_stock_preserved or not inherited_ranges_preserved:
        raise RetiredSlotBuildError("inherited approval changed in retired-slot child")

    _atomic_write(args.candidate_rom, final)
    _atomic_copy(args.parent_save, args.candidate_save)
    candidate_identity = identity(args.candidate_rom, final)
    save_identity = identity(args.candidate_save)

    inherited_range_rows = list(
        parent_approval.get("approved_detachment_ranges") or []
    )
    cumulative_range_rows = inherited_range_rows + stage_target_ranges
    cumulative_range_rows.sort(key=lambda row: int(str(row["logical_start"]), 16))
    for left, right in zip(cumulative_range_rows, cumulative_range_rows[1:]):
        if int(str(left["logical_end_exclusive"]), 16) > int(
            str(right["logical_start"]), 16
        ):
            raise RetiredSlotBuildError(
                f"overlapping cumulative approval ranges: {left!r} / {right!r}"
            )
    stage_target_ranges_exact = (
        len(stage_target_records) == len(stage_target_ranges) == 2
        and all(
            parent[sb + int(row["logical_start"], 16) : sb + int(row["logical_end_exclusive"], 16)]
            != final[sb + int(row["logical_start"], 16) : sb + int(row["logical_end_exclusive"], 16)]
            for row in stage_target_ranges
        )
    )

    all_historical_occurrences = sum(
        int(row["historical_external_count"]) for row in selected_evidence
    )
    historical_accounted = len(historical_checks) > 0 and all(
        row.get("accounted") is True for row in historical_checks
    )
    proof = dict(parent_approval.get("proof") or {})
    proof.update(
        {
            "duplicate_payload_equal_before": True,
            "historical_consumers_accounted": historical_accounted,
            "all_current_external_refs_retargeted": True,
            "all_current_nested_parents_retargeted": True,
            "detachment_stage_zero_old_refs": True,
            "former_consumer_render_preserved": former_render_preserved,
            "candidate_new_consumers_exact": all(
                {
                    (str(row["record_abs"]), str(row["token_abs"]))
                    for row in final_external.get(index, [])
                }
                == expected_by_slot[index]
                for index in selected
            ),
            "tail_was_all_ff": all(value == 0xFF for value in bank_before[phrase_start:]),
            "changed_pointer_indices_exact": changed_pointer_indices == selected,
            "nonselected_pointers_preserved": all(
                pointers_after[index] == pointers_before[index]
                for index in range(len(pointers_before))
                if index not in selected
            ),
            "nonselected_payloads_preserved": nonselected_payloads_preserved,
            "bank5f_diffs_within_approved_extents": not bad_bank_runs,
            "detachment_diffs_within_approved_extents": (
                inherited_ranges_preserved and stage_target_ranges_exact
            ),
            "inherited_stock_slots_preserved": inherited_stock_preserved,
            "inherited_detachment_ranges_preserved": inherited_ranges_preserved,
            "inherited_approval_candidate_matches_parent": True,
            "retired_slots_original_parent_pointer_payload_equal": all(
                row.get("original_parent_pointer_equal") is True
                and row.get("original_parent_payload_equal") is True
                for row in selected_evidence
            ),
            "retired_slots_current_external_zero": all(
                int(row.get("current_external_count") or 0) == 0
                for row in selected_evidence
            ),
            "retired_slots_current_nested_zero": all(
                int(row.get("current_nested_count") or 0) == 0
                for row in selected_evidence
            ),
            "retired_slots_original_nested_zero": all(
                int(row.get("original_nested_count") or 0) == 0
                for row in selected_evidence
            ),
            "retired_slots_current_raw_pair_zero": all(
                int(row.get("current_raw_pair_hits") or 0) == 0
                for row in selected_evidence
            ),
            "retired_slots_historical_consumers_accounted": historical_accounted,
            "retired_slots_former_render_preserved": former_render_preserved,
            "retired_slots_new_consumers_exact": all(
                not final_nested.get(index)
                and {
                    (str(row["record_abs"]), str(row["token_abs"]))
                    for row in final_external.get(index, [])
                }
                == expected_by_slot[index]
                for index in selected
            ),
            "retired_slots_selected_exact": len(selected) == len(allocations) == 83,
            "retired_stage_target_ranges_exact": stage_target_ranges_exact,
        }
    )
    if not all(proof.values()):
        failed = [key for key, value in proof.items() if value is not True]
        raise RetiredSlotBuildError(f"approval proof failed: {failed}")

    local_expansion_parent = (
        parent_approval.get("local_expansion_parent_rom")
        or parent_approval.get("parent_rom")
    )
    approval = {
        "generated_by": "tools/build_p2_retired_slot_reclaim_candidate.py",
        "mode": "pre_gate_detachment_approval",
        "ok": True,
        "parent_rom": parent_identity,
        "candidate_rom": candidate_identity,
        "approved_stock_slots": [
            f"{index:04X}" for index in sorted(inherited_slots | selected)
        ],
        "approved_detachment_ranges": cumulative_range_rows,
        "duplicate": dict(parent_approval.get("duplicate") or {}),
        "local_expansion": dict(parent_approval.get("local_expansion") or {}),
        "local_expansion_parent_rom": dict(local_expansion_parent or {}),
        "retired_slot_reclaim": {
            "policy": "original_parent_identical_current_unreachable_raw_pair_clean",
            "selected_slots": selected_approval_rows,
            "historical_external_occurrences": all_historical_occurrences,
            "historical_unique_records": len(historical_checks),
            "historical_stage_target_records": historical_stage_targets,
            "stage_target_records": stage_target_records,
            "stage_target_ranges": stage_target_ranges,
            "former_render_checks": historical_checks,
            "final_new_consumers": final_consumers,
            "records_applied": len(new_rows),
            "unique_phrases": len(allocations),
        },
        "inherited_approvals": {
            "parent_approval": identity(args.parent_approval),
            "stock_preservation": inherited_stock_rows,
            "detachment_ranges_preserved": inherited_ranges_preserved,
            "candidate_matches_parent": True,
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
            "used": phrase_end - phrase_start,
            "free_after": BANK_SIZE - phrase_end,
        },
        "proof": proof,
    }
    _write_json(args.approval_report, approval)

    approved_change_extents: list[dict[str, Any]] = []
    approved_change_extents.extend(
        {
            "kind": "dictionary_pointer",
            "owner_id": f"retired_slot:{index:04X}",
            "file_start": f"{lo:08X}",
            "file_end_exclusive": f"{hi:08X}",
        }
        for index, (lo, hi) in zip(sorted(selected), pointer_file_extents)
    )
    approved_change_extents.append(
        {
            "kind": "dictionary_payload",
            "owner_id": "retired_slot_phrases",
            "file_start": f"{phrase_file_extent[0]:08X}",
            "file_end_exclusive": f"{phrase_file_extent[1]:08X}",
        }
    )
    approved_change_extents.extend(
        {
            "kind": "record_body",
            "owner_id": row["record_id"],
            "file_start": f"{lo:08X}",
            "file_end_exclusive": f"{hi:08X}",
        }
        for row, (lo, hi) in zip(new_rows, record_file_extents)
    )
    approved_change_extents.extend(
        {
            "kind": "checksum",
            "file_start": f"{lo:08X}",
            "file_end_exclusive": f"{hi:08X}",
        }
        for lo, hi in checksum_runs
    )
    precommit = {
        "ok": True,
        "diff_bytes": sum(hi - lo for lo, hi in all_runs),
        "diff_runs": len(all_runs),
        "unaccounted_runs": [],
        "targets_decoded": decoded,
        "approved_change_extents": approved_change_extents,
        "checksum": f"{checksum:04X}",
    }
    validation = {
        "accepted": True,
        "unresolved_count": 0,
        "targets": len(plan["targets"]),
        "policy": "candidate_bound_retired_stock_slot_reclaim",
    }
    apply_report = {
        "ok": True,
        "policy": "candidate_bound_retired_stock_slot_reclaim",
        "parent_targets_verified": len(parent_report.get("targets") or []),
        "records_applied": len(applied),
        "unique_phrases": len(allocations),
        "retired_slots_reclaimed": len(selected),
        "historical_external_occurrences": all_historical_occurrences,
        "historical_unique_records": len(historical_checks),
        "dictionary_pointers_written": len(selected),
        "stock_5f_writes": len(selected),
        "runtime_writes": 0,
        "ff_page_writes": 0,
        "terminator_writes": 0,
        "full_dictionary_rebuild": False,
        "spill": {
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
        prefix="p2_retired_slot_reclaim",
        baseline_meta=args.baseline_meta,
        approved_detachment_report=args.approval_report,
        approved_local_expansion_report=args.approval_report,
        local_expansion_baseline_rom=args.local_expansion_baseline_rom,
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
            "p2_phase": "P2-1_candidate_bound_retired_stock_slot_reclaim",
            "parent_candidate": parent_identity,
            "analysis_report": identity(args.analysis_report),
            "approval_report": identity(args.approval_report),
            "candidate_save": save_identity,
            "remaining": {"records": 0, "unique_phrases": 0},
            "published": False,
            "main_tip_modified": False,
            "retired_slot_contract": approval,
        }
    )
    _write_json(args.report, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    parser.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    parser.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    parser.add_argument("--parent-approval", type=Path, default=DEFAULT_PARENT_APPROVAL)
    parser.add_argument("--parent-report", type=Path, default=DEFAULT_PARENT_REPORT)
    parser.add_argument("--expected-parent-sha", default=EXPECTED_PARENT_SHA256)
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
    parser.add_argument(
        "--local-expansion-baseline-rom",
        type=Path,
        default=DEFAULT_LOCAL_EXPANSION_BASELINE,
    )
    parser.add_argument("--pre-ext3-rom", type=Path, default=DEFAULT_PRE_EXT3)
    parser.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    parser.add_argument("--prefix-evidence", type=Path, default=DEFAULT_PREFIX_EVIDENCE)
    parser.add_argument("--gate-sheet", type=Path, default=DEFAULT_GATE_SHEET)
    parser.add_argument("--ui-report-dir", type=Path, default=DEFAULT_UI_REPORT_DIR)
    parser.add_argument("--baseline-meta", type=Path, default=DEFAULT_BASELINE_META)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    forbidden = {
        args.parent_rom.resolve(),
        (ROOT / "out/patch/monoeye_ko_expanded.wsc").resolve(),
    }
    if args.candidate_rom.resolve() in forbidden:
        raise SystemExit("refusing to overwrite parent or main TIP")
    if args.candidate_rom.suffix.lower() != ".wsc":
        raise SystemExit("candidate ROM must use .wsc")
    if args.candidate_save.suffix.lower() != ".sav":
        raise SystemExit("candidate SaveRAM must use .sav")
    report = build_candidate(args)
    print(
        json.dumps(
            {
                "status": report.get("status"),
                "accepted": report.get("accepted"),
                "candidate_rom": (report.get("inputs") or {}).get("candidate_rom"),
                "candidate_save": report.get("candidate_save"),
                "targets": len(report.get("targets") or []),
                "new_retired_slot_targets": (report.get("apply_report") or {}).get("records_applied"),
                "retired_slots": (report.get("apply_report") or {}).get("retired_slots_reclaimed"),
                "gates": {
                    name: result.get("ok")
                    for name, result in (report.get("gates") or {}).items()
                },
                "remaining": report.get("remaining"),
                "main_tip_modified": False,
                "report": str(args.report),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.get("accepted") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
