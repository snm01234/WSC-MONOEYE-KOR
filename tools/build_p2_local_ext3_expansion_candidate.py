#!/usr/bin/env python3
"""Build one cumulative P2 local one-NUL-gap ext3 candidate.

The parent already has the regular four-byte E5 18 ext3 runtime.  This stage
changes no runtime code.  For each read-only-approved three-byte body followed
by two NUL bytes, the first NUL becomes byte four of an ext3 token and the
second NUL remains the terminator.  The following record start and complete
following record stay byte-identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_local_ext3_expansion import (  # noqa: E402
    DEFAULT_OUT as DEFAULT_ANALYSIS_REPORT,
    DEFAULT_PARENT_APPROVAL,
    DEFAULT_PARENT_ROM,
    DEFAULT_PARENT_SAVE,
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
from build_p2_exact_reuse_candidate import (  # noqa: E402
    _atomic_copy,
    _atomic_write,
    _baseline_source_rows,
    diff_runs,
    identity,
    sha256_bytes,
)
from build_p2_true_free_candidate import _target_row  # noqa: E402
from mixed_residual_gate_runner import (  # noqa: E402
    GateInputs,
    build_acceptance_report,
    run_static_gates,
)
from mixed_residual_reference_union import (  # noqa: E402
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    le16,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from patch_3byte_dict_token import (  # noqa: E402
    DEFAULT_NUM_BANKS,
    EXP3_SEG0,
    bank_local_for_index,
    token_from_ext3_index,
)
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

EXPECTED_PARENT_SHA256 = "6b28ff72a70ce7bb9739f081f55cecfc9612ef5d207701e24093f947f7fed7d9"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_local_ext3_expansion_candidate.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_local_ext3_expansion_candidate.sav"
DEFAULT_APPROVAL = ROOT / "out/patch/p2_local_ext3_expansion_approval.json"
DEFAULT_REPORT = ROOT / "out/patch/p2_local_ext3_expansion_report.json"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_local_ext3_expansion_gates"
DEFAULT_PRE_EXT3 = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/main_prefix_followup_evidence.json"
DEFAULT_GATE_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_UI_REPORT_DIR = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean"
DEFAULT_BASELINE_META = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"


class LocalExpansionBuildError(RuntimeError):
    pass


def _analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_analysis_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.parent_rom = args.parent_rom
    parsed.parent_save = args.parent_save
    parsed.parent_approval = args.parent_approval
    parsed.base_manifest = args.base_manifest
    parsed.values_dir = args.values_dir
    parsed.sheet = args.analysis_sheet
    parsed.tbl = args.tbl
    parsed.ext_meta = args.ext_meta
    parsed.ext3_meta = args.ext3_meta
    parsed.out = args.analysis_report
    parsed.stdout = True
    return parsed


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write(path, payload)


def _covered(run: tuple[int, int], extents: Sequence[tuple[int, int]]) -> bool:
    """Return true when the complete run is covered by the union of extents."""
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


def _bank_cursor(rom: bytes | bytearray, seg: int) -> int:
    bank = slice_expansion_bank(rom, seg)
    empty_at = 0x1000 * 2
    cursor = empty_at + 1
    for local in range(0x1000):
        pointer = le16(bank, local * 2)
        if pointer < empty_at or pointer >= BANK_SIZE:
            continue
        end = pointer
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        cursor = max(cursor, end + 1)
    return cursor


def _plan_document(
    analysis: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    parent_identity: Mapping[str, Any],
) -> dict[str, Any]:
    parent_rows = list(
        (analysis.get("current_p2_state") or {}).get("parent_exact_record_plan") or []
    )
    local_rows = list((analysis.get("allocation") or {}).get("record_plan") or [])
    targets = [
        _target_row(
            row,
            source_rows,
            strategy="parent_existing_exact_two_byte_token",
            index_key="existing_slot",
        )
        for row in parent_rows
    ]
    for row in local_rows:
        source = source_rows.get(str(row["record_id"]))
        if source is None:
            raise LocalExpansionBuildError(f"missing source row: {row['record_id']}")
        logical = int(str(row["abs"]), 16)
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        targets.append(
            {
                "record_id": str(row["record_id"]),
                "region": str(row["region"]),
                "bank": f"{logical >> 16:02X}",
                "abs": f"{logical:06X}",
                "payload_capacity": int(row["new_capacity"]),
                "prefix_bytes": len(prefix),
                "source_text": str(
                    source.get("rendered_source_text") or source.get("source_text") or ""
                ),
                "korean_text": str(row["target_ko"]),
                "strategy": "ext3",
                "dictionary_index": str(row["ext3_index"]),
                "status": "resolved",
            }
        )
    return {
        "generated_by": "tools/build_p2_local_ext3_expansion_candidate.py",
        "inputs": {"working_rom": dict(parent_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
            "parent_exact": len(parent_rows),
            "local_ext3_expansion": len(local_rows),
        },
        "targets": targets,
        "dictionary_changes": {
            "new_ext3_slots": int((analysis.get("storage") or {}).get("new_slots") or 0),
            "reused_ext3_slots": int((analysis.get("storage") or {}).get("reused_slots") or 0),
            "stock_5f_written": False,
            "runtime_written": False,
            "local_terminator_moves": len(local_rows),
            "policy": "one_existing_nul_gap_to_regular_ext3_token",
        },
        "guard_outcomes": {
            "original_parent_boundary_rebind": {
                "ok": True,
                "rows": len(local_rows),
            }
        },
        "ext3": {"used": True, "slots_written": int((analysis.get("storage") or {}).get("new_slots") or 0)},
    }


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent_rom.read_bytes()
    parent_sha = sha256_bytes(parent)
    if parent_sha != str(args.expected_parent_sha).lower():
        raise LocalExpansionBuildError(
            f"parent identity drifted: expected {args.expected_parent_sha}, got {parent_sha}"
        )
    if len(parent) != 16_777_216:
        raise LocalExpansionBuildError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise LocalExpansionBuildError("parent SaveRAM missing or not 32 KiB")

    parent_indices, parent_approval_sha, parent_ranges = load_approved_detachment(
        args.parent_approval
    )
    if parent_approval_sha != parent_sha:
        raise LocalExpansionBuildError(
            f"parent approval is bound to {parent_approval_sha}, parent is {parent_sha}"
        )
    parent_approval = json.loads(args.parent_approval.read_text(encoding="utf-8"))

    if args.reuse_analysis_report:
        analysis = json.loads(args.analysis_report.read_text(encoding="utf-8"))
        if analysis.get("generated_by") != "tools/analyze_p2_local_ext3_expansion.py":
            raise LocalExpansionBuildError("analysis report has wrong generator")
        bound_sha = ((analysis.get("inputs") or {}).get("parent_rom") or {}).get("sha256")
        if bound_sha != parent_sha:
            raise LocalExpansionBuildError(
                f"analysis is bound to {bound_sha}, parent is {parent_sha}"
            )
    else:
        analysis = analyze(_analysis_args(args))
        _write_json(args.analysis_report, analysis)
    if (analysis.get("decision") or {}).get("candidate_generation_allowed") is not True:
        raise LocalExpansionBuildError("read-only analysis did not approve candidate generation")

    original = args.original_rom.read_bytes()
    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    num_banks = int(ext3_meta.get("num_banks") or DEFAULT_NUM_BANKS)
    tbl = Tbl.load(args.tbl)
    sb = stock_base(parent)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )

    rows = list((analysis.get("allocation") or {}).get("record_plan") or [])
    allocations = list((analysis.get("allocation") or {}).get("allocations") or [])
    if not rows or not allocations:
        raise LocalExpansionBuildError("analysis has no local expansion rows")
    new_slot_payload = {
        int(str(index), 16): bytes.fromhex(str(payload))
        for index, payload in ((analysis.get("storage") or {}).get("new_slot_payloads") or {}).items()
    }

    candidate = bytearray(parent)
    dictionary_extents: list[tuple[int, int]] = []
    dictionary_extent_rows: list[dict[str, Any]] = []
    expected_slot_pointer: dict[int, int] = {}
    by_seg: dict[int, list[tuple[int, bytes]]] = defaultdict(list)
    for index, payload in new_slot_payload.items():
        seg, local = bank_local_for_index(index)
        by_seg[seg].append((local, payload))
    for seg, entries in sorted(by_seg.items()):
        cursor = _bank_cursor(parent, seg)
        for local, payload in sorted(entries):
            index = 0x1000 + ((seg - EXP3_SEG0) << 12) + local
            pointer_extent = (seg * BANK_SIZE + local * 2, seg * BANK_SIZE + local * 2 + 2)
            payload_extent = (seg * BANK_SIZE + cursor, seg * BANK_SIZE + cursor + len(payload) + 1)
            expected_slot_pointer[index] = cursor
            dictionary_extents.extend([pointer_extent, payload_extent])
            dictionary_extent_rows.extend(
                [
                    {
                        "kind": "ext3_pointer",
                        "owner_id": f"ext3:{index:05X}",
                        "file_start": f"{pointer_extent[0]:08X}",
                        "file_end_exclusive": f"{pointer_extent[1]:08X}",
                    },
                    {
                        "kind": "ext3_payload",
                        "owner_id": f"ext3:{index:05X}",
                        "file_start": f"{payload_extent[0]:08X}",
                        "file_end_exclusive": f"{payload_extent[1]:08X}",
                    },
                ]
            )
            cursor += len(payload) + 1

    before_dictionary = bytes(candidate)
    write_info, slot_guard = write_ext3_slots_guarded(
        candidate,
        new_slot_payload,
        union=union,
        num_banks=num_banks,
    )
    dictionary_runs = diff_runs(before_dictionary, candidate)
    bad_dictionary_runs = [
        run for run in dictionary_runs if not _covered(run, dictionary_extents)
    ]
    if bad_dictionary_runs:
        raise LocalExpansionBuildError(
            f"ext3 write escaped selected pointer/payload extents: {bad_dictionary_runs[:8]}"
        )
    after_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    for index, payload in new_slot_payload.items():
        if after_dictionary.entry_offset(index) != expected_slot_pointer[index]:
            raise LocalExpansionBuildError(f"ext3 pointer mismatch: {index:05X}")
        if bytes(after_dictionary.raw_entry(index)) != payload:
            raise LocalExpansionBuildError(f"ext3 payload mismatch: {index:05X}")

    record_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    following_record_checks: list[dict[str, Any]] = []
    expected_new_by_index: dict[int, set[int]] = defaultdict(set)
    parent_consumer_by_index = {
        int(str(allocation["ext3_index"]), 16): {
            consumer.abs for consumer in union.consumers_for(int(str(allocation["ext3_index"]), 16))
        }
        for allocation in allocations
    }
    for row in sorted(rows, key=lambda item: int(str(item["abs"]), 16)):
        logical = int(str(row["abs"]), 16)
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        old_capacity = int(row["old_capacity"])
        old_term = int(str(row["old_terminator"]), 16)
        new_term = int(str(row["new_terminator"]), 16)
        next_start = int(str(row["next_record_start"]), 16)
        if new_term != old_term + 1 or next_start != new_term + 1:
            raise LocalExpansionBuildError(f"boundary drift in plan: {row['record_id']}")
        before_payload = parent[sb + logical : sb + logical + old_capacity]
        if before_payload.hex().upper() != str(row["old_payload_hex"]):
            raise LocalExpansionBuildError(f"parent payload drift: {row['record_id']}")
        if candidate[sb + old_term] != 0 or candidate[sb + new_term] != 0:
            raise LocalExpansionBuildError(f"two-NUL contract drift: {row['record_id']}")
        token = bytes.fromhex(str(row["token_hex"]))
        if len(token) != 4:
            raise LocalExpansionBuildError(f"token is not four bytes: {row['record_id']}")
        body_start = sb + logical + len(prefix)
        write_end = body_start + 4
        if write_end != sb + old_term + 1:
            raise LocalExpansionBuildError(f"token does not end after old terminator: {row['record_id']}")
        before_window = bytes(candidate[body_start:write_end])
        if before_window[:3] != bytes.fromhex(str(row["old_body_hex"])) or before_window[3] != 0:
            raise LocalExpansionBuildError(f"body/terminator drift: {row['record_id']}")
        next_before = read_encoded_z_safe(parent, sb + next_start, max_len=256)
        if next_before is None:
            raise LocalExpansionBuildError(f"following record unreadable: {row['record_id']}")
        candidate[body_start:write_end] = token
        if candidate[sb + new_term] != 0:
            raise LocalExpansionBuildError(f"new terminator overwritten: {row['record_id']}")
        record_extents.append((body_start, write_end))
        index = int(str(row["ext3_index"]), 16)
        expected_new_by_index[index].add(logical)
        applied.append(
            {
                "record_id": row["record_id"],
                "abs": row["abs"],
                "target_ko": row["target_ko"],
                "ext3_index": row["ext3_index"],
                "token_hex": row["token_hex"],
                "before_hex": before_window.hex().upper(),
                "after_hex": bytes(candidate[body_start:write_end]).hex().upper(),
                "old_terminator": row["old_terminator"],
                "new_terminator": row["new_terminator"],
                "next_record_start": row["next_record_start"],
            }
        )
        following_record_checks.append(
            {
                "owner_id": row["record_id"],
                "next_record_start": row["next_record_start"],
                "before_payload_hex": bytes(next_before[0]).hex().upper(),
                "before_terminator": f"{next_before[1] - sb:06X}",
            }
        )

    before_checksum = bytes(candidate)
    checksum = update_ws_checksum(candidate)
    checksum_runs = diff_runs(before_checksum, candidate)
    final = bytes(candidate)
    final_dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)

    parent_rows = list(
        (analysis.get("current_p2_state") or {}).get("parent_exact_record_plan") or []
    )
    decoded_parent = 0
    for row in parent_rows:
        logical = int(str(row["abs"]), 16)
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        actual = final[sb + logical : sb + logical + len(payload)]
        rendered = final_dictionary.expand(actual[prefix_len:], tbl).rstrip("\u3000 \t")
        if rendered != str(row["target_ko"]).rstrip("\u3000 \t"):
            raise LocalExpansionBuildError(f"parent target regressed: {row['record_id']}")
        decoded_parent += 1

    decoded_new = 0
    local_approval_rows: list[dict[str, Any]] = []
    for row in rows:
        logical = int(str(row["abs"]), 16)
        old_capacity = int(row["old_capacity"])
        new_capacity = int(row["new_capacity"])
        new_term = int(str(row["new_terminator"]), 16)
        got = read_encoded_z_safe(final, sb + logical, max_len=256)
        if got is None or len(got[0]) != new_capacity or got[1] - sb != new_term:
            raise LocalExpansionBuildError(f"expanded record boundary mismatch: {row['record_id']}")
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        body = bytes(got[0])[len(prefix):]
        if body != bytes.fromhex(str(row["token_hex"])):
            raise LocalExpansionBuildError(f"expanded body mismatch: {row['record_id']}")
        rendered = final_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        if rendered != str(row["target_ko"]).rstrip("\u3000 \t"):
            raise LocalExpansionBuildError(f"expanded render mismatch: {row['record_id']}")
        next_start = int(str(row["next_record_start"]), 16)
        next_after = read_encoded_z_safe(final, sb + next_start, max_len=256)
        check = next(
            item for item in following_record_checks if item["owner_id"] == row["record_id"]
        )
        if next_after is None:
            raise LocalExpansionBuildError(f"following record lost: {row['record_id']}")
        if (
            bytes(next_after[0]).hex().upper() != check["before_payload_hex"]
            or f"{next_after[1] - sb:06X}" != check["before_terminator"]
        ):
            raise LocalExpansionBuildError(f"following record changed: {row['record_id']}")
        check["after_payload_hex"] = bytes(next_after[0]).hex().upper()
        check["after_terminator"] = f"{next_after[1] - sb:06X}"
        check["preserved"] = True
        local_approval_rows.append(
            {
                "record_id": row["record_id"],
                "abs": row["abs"],
                "old_capacity": old_capacity,
                "new_capacity": new_capacity,
                "old_terminator": row["old_terminator"],
                "new_terminator": row["new_terminator"],
                "next_record_start": row["next_record_start"],
                "ext3_index": row["ext3_index"],
                "token_hex": row["token_hex"],
                "target_ko": row["target_ko"],
                "boundary_proof": row["boundary_proof"],
            }
        )
        decoded_new += 1

    candidate_union = build_reference_union(
        original,
        final,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    consumer_delta_rows: list[dict[str, Any]] = []
    for index, expected in sorted(expected_new_by_index.items()):
        actual = {consumer.abs for consumer in candidate_union.consumers_for(index)}
        before = parent_consumer_by_index[index]
        if actual - before != expected or before - actual:
            raise LocalExpansionBuildError(
                f"ext3 consumer delta mismatch {index:05X}: "
                f"added={sorted(actual-before)} removed={sorted(before-actual)} expected={sorted(expected)}"
            )
        consumer_delta_rows.append(
            {
                "ext3_index": f"{index:05X}",
                "before_consumers": [f"{value:06X}" for value in sorted(before)],
                "added_consumers": [f"{value:06X}" for value in sorted(expected)],
                "after_consumers": [f"{value:06X}" for value in sorted(actual)],
                "exact": True,
            }
        )

    inherited_ranges_preserved = all(
        parent[sb + lo : sb + hi] == final[sb + lo : sb + hi]
        for lo, hi, _owner in parent_ranges
    )
    if not inherited_ranges_preserved:
        raise LocalExpansionBuildError("inherited detachment range changed")

    all_extents = dictionary_extents + record_extents + checksum_runs
    all_runs = diff_runs(parent, final)
    unaccounted = [run for run in all_runs if not _covered(run, all_extents)]
    if unaccounted:
        raise LocalExpansionBuildError(f"unaccounted candidate diff: {unaccounted[:8]}")

    _atomic_write(args.candidate_rom, final)
    _atomic_copy(args.parent_save, args.candidate_save)
    candidate_identity = identity(args.candidate_rom, final)
    save_identity = identity(args.candidate_save)
    parent_identity = identity(args.parent_rom, parent)

    inherited_duplicate = dict(parent_approval.get("duplicate") or {})
    required_detachment_proof = {
        key: True
        for key in (
            "duplicate_payload_equal_before",
            "historical_consumers_accounted",
            "all_current_external_refs_retargeted",
            "all_current_nested_parents_retargeted",
            "detachment_stage_zero_old_refs",
            "former_consumer_render_preserved",
            "candidate_new_consumers_exact",
            "tail_was_all_ff",
            "changed_pointer_indices_exact",
            "nonselected_pointers_preserved",
            "nonselected_payloads_preserved",
            "bank5f_diffs_within_approved_extents",
            "detachment_diffs_within_approved_extents",
            "inherited_stock_slots_preserved",
            "inherited_detachment_ranges_preserved",
            "inherited_approval_candidate_matches_parent",
        )
    }
    proof = {
        **required_detachment_proof,
        "local_expansion_rows_exact": len(local_approval_rows) == len(rows),
        "old_and_gap_nuls_verified": all(
            all(
                row["boundary_proof"].get(name) is True
                for name in (
                    "original_terminator_nul",
                    "parent_terminator_nul",
                    "original_gap_nul",
                    "parent_gap_nul",
                    "next_record_start_unchanged",
                    "old_terminator_not_known_record_start",
                    "gap_not_manifest_record_start",
                )
            )
            and row["boundary_proof"].get("event_like_body") is False
            for row in local_approval_rows
        ),
        "next_record_boundaries_preserved": all(
            int(row["next_record_start"], 16) == int(row["new_terminator"], 16) + 1
            for row in local_approval_rows
        ),
        "new_terminators_exact": all(
            int(row["new_terminator"], 16) == int(row["old_terminator"], 16) + 1
            for row in local_approval_rows
        ),
        "following_records_byte_identical": all(
            row.get("preserved") is True for row in following_record_checks
        ),
        "ext3_slot_guard_passed": slot_guard.ok,
        "ext3_pointer_payload_diffs_exact": not bad_dictionary_runs,
        "ext3_consumer_deltas_exact": all(row["exact"] for row in consumer_delta_rows),
        "inherited_detachment_ranges_unchanged": inherited_ranges_preserved,
    }
    if not all(proof.values()):
        raise LocalExpansionBuildError(f"approval proof failed: {proof}")

    approval = {
        "generated_by": "tools/build_p2_local_ext3_expansion_candidate.py",
        "mode": "pre_gate_detachment_approval",
        "ok": True,
        "parent_rom": parent_identity,
        "candidate_rom": candidate_identity,
        "approved_stock_slots": [f"{index:04X}" for index in sorted(parent_indices)],
        "approved_detachment_ranges": [
            {
                "logical_start": f"{lo:06X}",
                "logical_end_exclusive": f"{hi:06X}",
                "owner_id": owner,
            }
            for lo, hi, owner in parent_ranges
        ],
        "duplicate": inherited_duplicate,
        "local_expansion": {
            "records": local_approval_rows,
            "following_record_checks": following_record_checks,
            "consumer_deltas": consumer_delta_rows,
            "dictionary_extents": dictionary_extent_rows,
            "dictionary_write": write_info,
            "slot_guard": slot_guard.as_dict(),
        },
        "proof": proof,
    }
    _write_json(args.approval_report, approval)

    source_rows = _baseline_source_rows(args.base_manifest)
    plan = _plan_document(analysis, source_rows, parent_identity)
    validation = {
        "accepted": True,
        "unresolved_count": 0,
        "targets": len(plan["targets"]),
        "policy": "candidate_bound_one_nul_gap_regular_ext3",
    }
    approved_change_extents = list(dictionary_extent_rows)
    approved_change_extents.extend(
        {
            "kind": "local_record_expansion",
            "owner_id": row["record_id"],
            "start": f"{int(row['abs'], 16) + len(bytes.fromhex(str(row['prefix_hex']))):06X}",
            "end_exclusive": f"{int(row['old_terminator'], 16) + 1:06X}",
        }
        for row in rows
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
        "targets_decoded": decoded_parent + decoded_new,
        "approved_change_extents": approved_change_extents,
        "checksum": f"{checksum:04X}",
    }
    apply_report = {
        "ok": True,
        "policy": "one_existing_nul_gap_to_regular_ext3_token",
        "parent_targets_verified": decoded_parent,
        "records_applied": len(applied),
        "unique_phrases": len(allocations),
        "new_ext3_slots": len(new_slot_payload),
        "reused_ext3_slots": len(allocations) - len(new_slot_payload),
        "runtime_writes": 0,
        "stock_5f_writes": 0,
        "terminator_moves": len(applied),
        "applied": applied,
        "following_records_preserved": following_record_checks,
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
        prefix="p2_local_ext3_expansion",
        baseline_meta=args.baseline_meta,
        approved_detachment_report=args.approval_report,
        approved_local_expansion_report=args.approval_report,
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
            "p2_phase": "P2-1_local_one_nul_gap_regular_ext3",
            "parent_candidate": parent_identity,
            "analysis_report": identity(args.analysis_report),
            "approval_report": identity(args.approval_report),
            "candidate_save": save_identity,
            "remaining": analysis.get("remaining"),
            "published": False,
            "main_tip_modified": False,
            "local_expansion_contract": approval,
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
    parser.add_argument("--expected-parent-sha", default=EXPECTED_PARENT_SHA256)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--analysis-sheet", type=Path, default=DEFAULT_ANALYSIS_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--analysis-report", type=Path, default=DEFAULT_ANALYSIS_REPORT)
    parser.add_argument("--reuse-analysis-report", action="store_true")
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
                "new_local_ext3_targets": (report.get("apply_report") or {}).get("records_applied"),
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
