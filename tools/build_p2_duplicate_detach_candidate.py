#!/usr/bin/env python3
"""Build one cumulative P2 duplicate-payload detachment candidate.

Parent: ``p2_stock_spill_candidate.wsc`` (58 approved short records).

The selected stock slot is reclaimed in two explicit stages:

1. every current runtime reference to the reclaim slot is retargeted to a
   byte-identical keeper token, and a detachment-only scratch state proves zero
   remaining old external/nested references while preserving former rendering;
2. only then is the reclaimed stock slot pointed at a new phrase in the verified
   all-FF bank-5F tail and assigned to the next reviewed short records.

The approval report binds the stock pointer and exact non-dialogue detachment
ranges to the final candidate SHA-256.  No full 5F rebuild, FF-page write,
far-pointer relocation, or main-TIP overwrite is performed.
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

from analyze_p2_duplicate_detachment import (  # noqa: E402
    DEFAULT_OUT as DEFAULT_ANALYSIS_REPORT,
    DEFAULT_PARENT_ROM,
    DEFAULT_PARENT_SAVE,
    analyze,
    build_parser as build_analysis_parser,
    external_occurrence_map,
    nested_occurrence_map,
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
    _covered,
    diff_runs,
    identity,
    sha256_bytes,
)
from build_p2_stock_spill_candidate import (  # noqa: E402
    SPILL_FLOOR,
    _stock_phrase_cursor,
)
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
from mixed_residual_reference_union import (  # noqa: E402
    _working_two_byte_external_refs,
)
from verify_stock_noninvasion import (  # noqa: E402
    load_approved_detachment,
    load_approved_stock_indices,
    verify_inherited_stock_approval,
)
from structured_token_write_guard import (  # noqa: E402
    StructuredTokenWriteError,
    guard_external_token_write,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    read_encoded_z_safe,
    slice_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

EXPECTED_PARENT_SHA256 = "c3664b043a2ea888845c2dffad5a6d3cc507d3e7ff46b9275f7e8cca268c8d83"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_duplicate_detach_candidate.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_duplicate_detach_candidate.sav"
DEFAULT_APPROVAL = ROOT / "out/patch/p2_duplicate_detach_approval.json"
DEFAULT_PARENT_STOCK_APPROVAL = ROOT / "out/patch/p2_stock_spill_approval.json"
DEFAULT_REPORT = ROOT / "out/patch/p2_duplicate_detach_report.json"
DEFAULT_GATE_PREFIX = "p2_duplicate_detach"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_duplicate_detach_gates"
DEFAULT_PRE_EXT3 = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/main_prefix_followup_evidence.json"
DEFAULT_GATE_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_UI_REPORT_DIR = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean"
DEFAULT_BASELINE_META = (
    ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"
)


class P2DuplicateDetachError(RuntimeError):
    """Raised when any detachment or preservation proof fails."""


def _analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_analysis_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.parent_rom = args.parent_rom
    parsed.parent_save = args.parent_save
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
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    _atomic_write(path, payload)


def _load_inherited_approvals(
    args: argparse.Namespace,
    *,
    parent_sha: str,
) -> dict[str, Any]:
    """Load and verify stock/detachment approvals inherited by this stage."""
    inherited_stock: set[int] = set()
    inherited_ranges: list[dict[str, Any]] = []
    inherited_writes: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}

    if args.parent_stock_approval is not None:
        stock_indices, stock_sha = load_approved_stock_indices(args.parent_stock_approval)
        if stock_sha is None:
            raise P2DuplicateDetachError("parent stock approval lacks a candidate SHA")
        stock_doc = json.loads(args.parent_stock_approval.read_text(encoding="utf-8"))
        approved_candidate = Path(str((stock_doc.get("candidate_rom") or {}).get("path") or ""))
        if not approved_candidate.is_file():
            raise P2DuplicateDetachError(
                f"parent stock approval candidate is missing: {approved_candidate}"
            )
        if sha256_bytes(approved_candidate.read_bytes()) != stock_sha:
            raise P2DuplicateDetachError("parent stock approval candidate hash drifted")
        stock_preservation = verify_inherited_stock_approval(
            stock_indices,
            baseline=approved_candidate,
            target=args.parent_rom,
        )
        if not stock_preservation.get("ok"):
            raise P2DuplicateDetachError(
                "stock approval is not preserved in the current parent: "
                + ", ".join(stock_preservation.get("failures") or [])
            )
        inherited_stock.update(stock_indices)
        evidence["stock_spill"] = {
            "report": identity(args.parent_stock_approval),
            "approved_candidate_sha256": stock_sha,
            "parent_preservation": stock_preservation,
        }

    if args.parent_detachment_approval is not None:
        det_indices, det_sha, det_ranges = load_approved_detachment(
            args.parent_detachment_approval
        )
        if det_sha != parent_sha:
            raise P2DuplicateDetachError(
                f"parent detachment approval is bound to {det_sha}, parent is {parent_sha}"
            )
        det_doc = json.loads(
            args.parent_detachment_approval.read_text(encoding="utf-8")
        )
        inherited_stock.update(det_indices)
        inherited_ranges.extend(
            {
                "logical_start": f"{lo:06X}",
                "logical_end_exclusive": f"{hi:06X}",
                "owner_id": owner,
            }
            for lo, hi, owner in det_ranges
        )
        inherited_writes.extend(
            dict(row)
            for row in ((det_doc.get("duplicate") or {}).get("detachment_writes") or [])
        )
        evidence["duplicate_detachment"] = {
            "report": identity(args.parent_detachment_approval),
            "candidate_sha256": det_sha,
            "approved_stock_slots": [f"{index:04X}" for index in sorted(det_indices)],
            "approved_detachment_ranges": inherited_ranges,
            "detachment_writes": inherited_writes,
        }

    return {
        "stock_indices": inherited_stock,
        "ranges": inherited_ranges,
        "writes": inherited_writes,
        "evidence": evidence,
    }


def _payload_at(rom: bytes | bytearray, logical: int) -> bytes:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical)
    if got is None:
        raise P2DuplicateDetachError(f"cannot read zstring at {logical:06X}")
    return bytes(got[0])


def _plan_document(
    analysis: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    parent_identity: Mapping[str, Any],
) -> dict[str, Any]:
    parent_rows = list(
        (analysis.get("current_p2_state") or {}).get("parent_exact_record_plan") or []
    )
    new_rows = list((analysis.get("allocation") or {}).get("record_plan") or [])
    targets = [
        _target_row(
            row,
            source_rows,
            strategy="parent_existing_exact_two_byte_token",
            index_key="existing_slot",
        )
        for row in parent_rows
    ]
    targets.extend(
        _target_row(
            row,
            source_rows,
            strategy="duplicate_payload_detach_reclaim",
            index_key="slot",
        )
        for row in new_rows
    )
    selected = analysis.get("selected") or {}
    return {
        "generated_by": "tools/build_p2_duplicate_detach_candidate.py",
        "inputs": {"working_rom": dict(parent_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
            "parent_exact": len(parent_rows),
            "duplicate_detach_reclaim": len(new_rows),
        },
        "targets": targets,
        "dictionary_changes": {
            "slots_written": 1,
            "slot_indices": [str(selected["reclaim_slot"])],
            "pointers_written": 1,
            "stock_5f_written": True,
            "full_rebuild": False,
            "ff_page_written": False,
            "detachment": {
                "reclaim_slot": selected["reclaim_slot"],
                "keeper_slot": selected["keeper_slot"],
                "former_external_refs": len(
                    selected.get("working_external_occurrences") or []
                ),
                "former_nested_refs": len(
                    selected.get("working_nested_occurrences") or []
                ),
            },
            "policy": "single_lowest_cost_duplicate_payload_detachment",
        },
        "guard_outcomes": {
            "detachment_then_current_only_free_write": {
                "ok": True,
                "reclaim_slot": selected["reclaim_slot"],
                "keeper_slot": selected["keeper_slot"],
            }
        },
        "ext3": {"used": False, "slots_written": 0},
    }


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent_rom.read_bytes()
    parent_sha = sha256_bytes(parent)
    expected_parent_sha = str(args.expected_parent_sha).lower()
    if parent_sha != expected_parent_sha:
        raise P2DuplicateDetachError(
            f"parent identity drifted: expected {expected_parent_sha}, got {parent_sha}"
        )
    if len(parent) != 16_777_216:
        raise P2DuplicateDetachError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise P2DuplicateDetachError("parent 32 KiB SaveRAM is missing")

    inherited = _load_inherited_approvals(args, parent_sha=parent_sha)

    if args.reuse_analysis_report:
        if not args.analysis_report.is_file():
            raise P2DuplicateDetachError("requested analysis reuse but report is missing")
        analysis = json.loads(args.analysis_report.read_text(encoding="utf-8"))
        if analysis.get("generated_by") != "tools/analyze_p2_duplicate_detachment.py":
            raise P2DuplicateDetachError("analysis reuse report has the wrong generator")
        bound_sha = ((analysis.get("inputs") or {}).get("parent_rom") or {}).get(
            "sha256"
        )
        if bound_sha != parent_sha:
            raise P2DuplicateDetachError(
                f"analysis report is bound to {bound_sha}, parent is {parent_sha}"
            )
    else:
        analysis = analyze(_analysis_args(args))
        _write_json(args.analysis_report, analysis)
    if (analysis.get("decision") or {}).get("candidate_generation_allowed") is not True:
        raise P2DuplicateDetachError("read-only analysis did not approve a candidate")

    selected = analysis.get("selected") or {}
    reclaim = int(str(selected["reclaim_slot"]), 16)
    keeper = int(str(selected["keeper_slot"]), 16)
    reclaim_token = bytes.fromhex(str(selected["token_before_hex"]))
    keeper_token = bytes.fromhex(str(selected["token_after_hex"]))
    if reclaim_token != bytes(token_from_dict_index(reclaim)):
        raise P2DuplicateDetachError("reclaim token encoding drifted")
    if keeper_token != bytes(token_from_dict_index(keeper)):
        raise P2DuplicateDetachError("keeper token encoding drifted")
    if reclaim >= 0xF00 or keeper >= 0xF00:
        raise P2DuplicateDetachError("duplicate detachment reached the FF page")

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    tbl = Tbl.load(args.tbl)
    dictionary_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    reclaim_payload_before = bytes(dictionary_parent.raw_entry(reclaim))
    keeper_payload = bytes(dictionary_parent.raw_entry(keeper))
    if reclaim_payload_before != keeper_payload:
        raise P2DuplicateDetachError("duplicate payloads are no longer byte-identical")
    if dictionary_parent.expand(reclaim_payload_before, tbl) != dictionary_parent.expand(
        keeper_payload, tbl
    ):
        raise P2DuplicateDetachError("duplicate payloads no longer render identically")

    working_occurrences = list(selected.get("working_external_occurrences") or [])
    working_nested = list(selected.get("working_nested_occurrences") or [])
    if not working_occurrences or working_nested:
        raise P2DuplicateDetachError(
            "candidate scope requires external consumers and zero current nested parents"
        )

    candidate = bytearray(parent)
    sb = stock_base(candidate)
    detachment_extents: list[tuple[int, int]] = []
    detachment_logical_ranges: list[dict[str, Any]] = []
    detachment_writes: list[dict[str, Any]] = []
    former_render_checks: list[dict[str, Any]] = []
    for row in working_occurrences:
        record_abs = int(str(row["record_abs"]), 16)
        token_abs = int(str(row["token_abs"]), 16)
        offset = int(row["payload_offset"])
        before_payload = _payload_at(parent, record_abs)
        if before_payload.hex().upper() != str(row["payload_hex"]):
            raise P2DuplicateDetachError(
                f"consumer payload drifted before detachment: {record_abs:06X}"
            )
        if before_payload[offset : offset + 2] != reclaim_token:
            raise P2DuplicateDetachError(
                f"consumer token drifted before detachment: {token_abs:06X}"
            )
        try:
            guard_external_token_write(
                parent,
                token_abs=token_abs,
                before=reclaim_token,
                after=keeper_token,
                region=str(row["region"]),
                kind=str(row["kind"]),
            )
        except StructuredTokenWriteError as exc:
            raise P2DuplicateDetachError(str(exc)) from exc
        file_start = sb + token_abs
        if bytes(candidate[file_start : file_start + 2]) != reclaim_token:
            raise P2DuplicateDetachError(
                f"ROM token bytes drifted before detachment: {token_abs:06X}"
            )
        candidate[file_start : file_start + 2] = keeper_token
        detachment_extents.append((file_start, file_start + 2))
        detachment_logical_ranges.append(
            {
                "logical_start": f"{token_abs:06X}",
                "logical_end_exclusive": f"{token_abs + 2:06X}",
                "owner_id": f"detach:{reclaim:04X}->{keeper:04X}",
            }
        )
        detachment_writes.append(
            {
                "record_abs": f"{record_abs:06X}",
                "token_abs": f"{token_abs:06X}",
                "region": row["region"],
                "kind": row["kind"],
                "before_hex": reclaim_token.hex().upper(),
                "after_hex": keeper_token.hex().upper(),
            }
        )

    detached = bytes(candidate)
    detached_external = external_occurrence_map(
        detached, ext3_aware=True, wanted={reclaim}
    ).get(reclaim, [])
    dictionary_detached = make_dictionary_ext3(detached, ext_meta, ext3_meta)
    detached_nested = nested_occurrence_map(
        dictionary_detached, wanted={reclaim}, ext3_aware=True
    ).get(reclaim, [])
    if detached_external or detached_nested:
        raise P2DuplicateDetachError(
            f"detachment stage still has old refs: external={detached_external}, "
            f"nested={detached_nested}"
        )

    for row in working_occurrences:
        record_abs = int(str(row["record_abs"]), 16)
        before_payload = bytes.fromhex(str(row["payload_hex"]))
        after_payload = _payload_at(detached, record_abs)
        before_render = dictionary_parent.expand(before_payload, tbl)
        after_render = dictionary_detached.expand(after_payload, tbl)
        if after_render != before_render:
            raise P2DuplicateDetachError(
                f"former consumer rendering changed at {record_abs:06X}"
            )
        former_render_checks.append(
            {
                "record_abs": f"{record_abs:06X}",
                "before_payload_hex": before_payload.hex().upper(),
                "after_payload_hex": after_payload.hex().upper(),
                "before_render": before_render,
                "after_render": after_render,
                "preserved": True,
            }
        )

    allocations = list((analysis.get("allocation") or {}).get("allocations") or [])
    new_rows = list((analysis.get("allocation") or {}).get("record_plan") or [])
    if len(allocations) != 1 or not new_rows:
        raise P2DuplicateDetachError("analysis allocation is not a single reclaimed slot")
    slot_payload = {
        reclaim: bytes.fromhex(str(allocations[0]["encoded_payload_hex"]))
    }

    dictionary_stock_before = Dictionary(detached)
    pointers_before = list(dictionary_stock_before.ptrs)
    payloads_before = [
        bytes(dictionary_stock_before.raw_entry(index))
        for index in range(dictionary_stock_before.count)
    ]
    bank_before = bytes(slice_bank(detached, SEG_DICT))
    phrase_start = _stock_phrase_cursor(detached)
    required = len(slot_payload[reclaim]) + 1
    if phrase_start + required > BANK_SIZE:
        raise P2DuplicateDetachError("stock spill capacity exhausted")
    if any(value != 0xFF for value in bank_before[phrase_start:]):
        raise P2DuplicateDetachError(
            f"bank-5F tail is not all FF from {phrase_start:04X}"
        )

    current_locs = _working_two_byte_external_refs(detached)
    if current_locs.get(reclaim):
        raise P2DuplicateDetachError("current-only guard map still sees reclaim refs")
    guard_hangul_slot_writes(
        detached,
        slot_payload,
        allow_aux_consumers=False,
        locs=current_locs,
    )
    pointers_after_write, phrase_end = write_dictionary_slots_spill(
        candidate,
        slot_payload,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=current_locs,
    )

    dictionary_stock_after = Dictionary(candidate)
    pointers_after = list(dictionary_stock_after.ptrs)
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != {reclaim}:
        raise P2DuplicateDetachError(
            f"stock pointer delta is not exactly {reclaim:04X}: "
            f"{sorted(changed_pointer_indices)}"
        )
    if pointers_after_write != pointers_after:
        raise P2DuplicateDetachError("writer pointer result differs from ROM table")
    if pointers_after[reclaim] != phrase_start:
        raise P2DuplicateDetachError("reclaimed pointer does not point to tail start")
    if bytes(dictionary_stock_after.raw_entry(reclaim)) != slot_payload[reclaim]:
        raise P2DuplicateDetachError("reclaimed payload verification failed")
    if phrase_end != phrase_start + required:
        raise P2DuplicateDetachError("stock spill end drifted")

    nonselected_payloads_preserved = True
    for index, before in enumerate(payloads_before):
        if index == reclaim:
            continue
        if pointers_after[index] != pointers_before[index]:
            raise P2DuplicateDetachError(f"nonselected pointer changed: {index:04X}")
        if bytes(dictionary_stock_after.raw_entry(index)) != before:
            nonselected_payloads_preserved = False
            raise P2DuplicateDetachError(f"nonselected payload changed: {index:04X}")

    bank_after_slot = bytes(slice_bank(candidate, SEG_DICT))
    pointer_local_extent = (
        DICT_PTR_START + reclaim * 2,
        DICT_PTR_START + reclaim * 2 + 2,
    )
    phrase_local_extent = (phrase_start, phrase_end)
    bad_bank_runs = [
        run
        for run in diff_runs(bank_before, bank_after_slot)
        if not _covered(run, [pointer_local_extent, phrase_local_extent])
    ]
    if bad_bank_runs:
        raise P2DuplicateDetachError(
            f"bank-5F diff outside reclaimed pointer/tail: {bad_bank_runs[:8]}"
        )

    record_file_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in new_rows:
        logical = int(str(row["abs"]), 16)
        rewritten = bytes.fromhex(str(row["rewrite_payload_hex"]))
        body_span = int(row["body_span"])
        prefix_len = len(rewritten) - body_span
        start = sb + logical
        end = start + len(rewritten)
        before = bytes(candidate[start:end])
        if before[:prefix_len] != rewritten[:prefix_len]:
            raise P2DuplicateDetachError(f"prefix drift before write: {row['record_id']}")
        terminator = int(str(row["approved_extent"]["terminator"]), 16)
        if candidate[sb + terminator] != 0:
            raise P2DuplicateDetachError(f"terminator drift: {row['record_id']}")
        candidate[start + prefix_len : end] = rewritten[prefix_len:]
        if candidate[sb + terminator] != 0:
            raise P2DuplicateDetachError(f"terminator overwritten: {row['record_id']}")
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
    pointer_file_extent = (
        bank_file_base + pointer_local_extent[0],
        bank_file_base + pointer_local_extent[1],
    )
    phrase_file_extent = (
        bank_file_base + phrase_local_extent[0],
        bank_file_base + phrase_local_extent[1],
    )
    approved_file_extents = (
        detachment_extents
        + [pointer_file_extent, phrase_file_extent]
        + record_file_extents
        + checksum_runs
    )
    all_runs = diff_runs(parent, candidate)
    unaccounted = [run for run in all_runs if not _covered(run, approved_file_extents)]
    if unaccounted:
        raise P2DuplicateDetachError(
            f"candidate has unapproved diff runs: {unaccounted[:8]}"
        )

    final = bytes(candidate)
    dictionary_final = make_dictionary_ext3(final, ext_meta, ext3_meta)
    parent_rows = list(
        (analysis.get("current_p2_state") or {}).get("parent_exact_record_plan") or []
    )
    decoded_parent = 0
    for row in parent_rows:
        logical = int(str(row["abs"]), 16)
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        actual = final[sb + logical : sb + logical + len(payload)]
        rendered = dictionary_final.expand(actual[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2DuplicateDetachError(f"parent target regressed: {row['record_id']}")
        decoded_parent += 1

    decoded_new = 0
    for row in new_rows:
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        rendered = dictionary_final.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2DuplicateDetachError(f"new target decode mismatch: {row['record_id']}")
        decoded_new += 1

    final_reclaim_refs = external_occurrence_map(
        final, ext3_aware=True, wanted={reclaim}
    ).get(reclaim, [])
    expected_new_records = {str(row["abs"]) for row in new_rows}
    actual_new_records = {str(row["record_abs"]) for row in final_reclaim_refs}
    if actual_new_records != expected_new_records:
        raise P2DuplicateDetachError(
            f"final reclaim consumers differ from new targets: "
            f"{actual_new_records ^ expected_new_records}"
        )
    final_nested = nested_occurrence_map(
        dictionary_final, wanted={reclaim}, ext3_aware=True
    ).get(reclaim, [])
    if final_nested:
        raise P2DuplicateDetachError(f"final reclaimed slot has nested parents: {final_nested}")

    for check in former_render_checks:
        record_abs = int(str(check["record_abs"]), 16)
        final_payload = _payload_at(final, record_abs)
        final_render = dictionary_final.expand(final_payload, tbl)
        if final_render != check["before_render"]:
            raise P2DuplicateDetachError(
                f"former consumer regressed after reclaim at {record_abs:06X}"
            )
        check["final_payload_hex"] = final_payload.hex().upper()
        check["final_render"] = final_render
        check["final_preserved"] = True

    false_hits = list(selected.get("raw_pair_non_runtime_hits") or [])
    for row in false_hits:
        token_abs = int(str(row["token_abs"]), 16)
        if final[sb + token_abs : sb + token_abs + 2] != reclaim_token:
            raise P2DuplicateDetachError(
                f"ext3-tail raw pair was unexpectedly changed at {token_abs:06X}"
            )

    inherited_stock_indices = set(inherited["stock_indices"])
    if reclaim in inherited_stock_indices:
        raise P2DuplicateDetachError(
            f"reclaim slot {reclaim:04X} is already owned by an inherited approval"
        )
    inherited_stock_rows: list[dict[str, Any]] = []
    inherited_stock_preserved = True
    parent_stock_dict = Dictionary(parent)
    final_stock_dict = Dictionary(final)
    for index in sorted(inherited_stock_indices):
        pointer_equal = parent_stock_dict.ptrs[index] == final_stock_dict.ptrs[index]
        payload_equal = bytes(parent_stock_dict.raw_entry(index)) == bytes(
            final_stock_dict.raw_entry(index)
        )
        inherited_stock_rows.append(
            {
                "index": f"{index:04X}",
                "pointer_preserved": pointer_equal,
                "payload_preserved": payload_equal,
            }
        )
        inherited_stock_preserved &= pointer_equal and payload_equal
    if not inherited_stock_preserved:
        raise P2DuplicateDetachError("an inherited stock slot changed in the child")

    inherited_range_rows = list(inherited["ranges"])
    inherited_ranges_preserved = True
    for row in inherited_range_rows:
        lo = int(str(row["logical_start"]), 16)
        hi = int(str(row["logical_end_exclusive"]), 16)
        if parent[sb + lo : sb + hi] != final[sb + lo : sb + hi]:
            inherited_ranges_preserved = False
            raise P2DuplicateDetachError(
                f"inherited detachment range changed: {lo:06X}-{hi:06X}"
            )

    cumulative_stock_indices = inherited_stock_indices | {reclaim}
    cumulative_detachment_ranges = inherited_range_rows + detachment_logical_ranges
    cumulative_detachment_ranges.sort(
        key=lambda row: int(str(row["logical_start"]), 16)
    )
    for left, right in zip(
        cumulative_detachment_ranges, cumulative_detachment_ranges[1:]
    ):
        if int(str(left["logical_end_exclusive"]), 16) > int(
            str(right["logical_start"]), 16
        ):
            raise P2DuplicateDetachError("cumulative detachment ranges overlap")
    cumulative_detachment_writes = list(inherited["writes"]) + detachment_writes

    candidate_payload = final
    _atomic_write(args.candidate_rom, candidate_payload)
    _atomic_copy(args.parent_save, args.candidate_save)
    candidate_identity = identity(args.candidate_rom, candidate_payload)
    save_identity = identity(args.candidate_save)
    parent_identity = identity(args.parent_rom, parent)

    approval = {
        "generated_by": "tools/build_p2_duplicate_detach_candidate.py",
        "mode": "pre_gate_detachment_approval",
        "ok": True,
        "parent_rom": parent_identity,
        "candidate_rom": candidate_identity,
        "approved_stock_slots": [
            f"{index:04X}" for index in sorted(cumulative_stock_indices)
        ],
        "approved_detachment_ranges": cumulative_detachment_ranges,
        "inherited_approvals": {
            "evidence": inherited["evidence"],
            "stock_preservation": inherited_stock_rows,
            "detachment_ranges_preserved": inherited_ranges_preserved,
        },
        "duplicate": {
            "reclaim_slot": f"{reclaim:04X}",
            "keeper_slot": f"{keeper:04X}",
            "payload_before_hex": reclaim_payload_before.hex().upper(),
            "keeper_payload_hex": keeper_payload.hex().upper(),
            "rendered_before": dictionary_parent.expand(reclaim_payload_before, tbl),
            "historical_union": selected["reference_union_audit"],
            "original_external_occurrences": selected[
                "original_external_occurrences"
            ],
            "working_external_occurrences": working_occurrences,
            "original_only_already_detached": selected[
                "original_only_already_detached"
            ],
            "working_nested_occurrences": working_nested,
            "raw_pair_non_runtime_hits": false_hits,
            "stage_detachment_writes": detachment_writes,
            "detachment_writes": cumulative_detachment_writes,
            "former_render_checks": former_render_checks,
            "final_new_consumers": final_reclaim_refs,
        },
        "pointer_changes": [
            {
                "index": f"{reclaim:04X}",
                "before": f"{pointers_before[reclaim]:04X}",
                "after": f"{pointers_after[reclaim]:04X}",
            }
        ],
        "tail": {
            "start": f"{phrase_start:04X}",
            "end_exclusive": f"{phrase_end:04X}",
            "free_before": BANK_SIZE - phrase_start,
            "used": phrase_end - phrase_start,
            "before_sha256": hashlib.sha256(bank_before[phrase_start:]).hexdigest(),
        },
        "proof": {
            "duplicate_payload_equal_before": reclaim_payload_before == keeper_payload,
            "historical_consumers_accounted": True,
            "all_current_external_refs_retargeted": len(detachment_writes)
            == len(working_occurrences),
            "all_current_nested_parents_retargeted": not working_nested,
            "detachment_stage_zero_old_refs": not detached_external
            and not detached_nested,
            "former_consumer_render_preserved": all(
                row.get("final_preserved") is True for row in former_render_checks
            ),
            "candidate_new_consumers_exact": actual_new_records
            == expected_new_records,
            "tail_was_all_ff": all(value == 0xFF for value in bank_before[phrase_start:]),
            "changed_pointer_indices_exact": changed_pointer_indices == {reclaim},
            "nonselected_pointers_preserved": all(
                pointers_after[index] == pointers_before[index]
                for index in range(len(pointers_before))
                if index != reclaim
            ),
            "nonselected_payloads_preserved": nonselected_payloads_preserved,
            "bank5f_diffs_within_approved_extents": not bad_bank_runs,
            "detachment_diffs_within_approved_extents": all(
                _covered(run, detachment_extents)
                for run in diff_runs(parent, detached)
            ),
            "inherited_stock_slots_preserved": inherited_stock_preserved,
            "inherited_detachment_ranges_preserved": inherited_ranges_preserved,
            "inherited_approval_candidate_matches_parent": all(
                entry.get("candidate_sha256", parent_sha) == parent_sha
                for entry in inherited["evidence"].values()
                if isinstance(entry, dict)
            ),
        },
    }
    if not all(approval["proof"].values()):
        raise P2DuplicateDetachError(f"approval proof failed: {approval['proof']}")
    _write_json(args.approval_report, approval)

    source_rows = _baseline_source_rows(args.base_manifest)
    plan = _plan_document(analysis, source_rows, parent_identity)
    validation = {
        "accepted": True,
        "unresolved_count": 0,
        "targets": len(plan["targets"]),
        "policy": "single_duplicate_payload_detachment_with_candidate_bound_proof",
    }
    approved_change_extents: list[dict[str, Any]] = []
    approved_change_extents.extend(
        {
            "kind": "duplicate_detachment_token",
            "owner_id": row["owner_id"],
            "start": row["logical_start"],
            "end_exclusive": row["logical_end_exclusive"],
        }
        for row in detachment_logical_ranges
    )
    approved_change_extents.append(
        {
            "kind": "dictionary_pointer",
            "owner_id": f"stock_slot:{reclaim:04X}",
            "file_start": f"{pointer_file_extent[0]:08X}",
            "file_end_exclusive": f"{pointer_file_extent[1]:08X}",
        }
    )
    approved_change_extents.append(
        {
            "kind": "dictionary_payload",
            "owner_id": f"reclaimed_slot:{reclaim:04X}",
            "file_start": f"{phrase_file_extent[0]:08X}",
            "file_end_exclusive": f"{phrase_file_extent[1]:08X}",
        }
    )
    approved_change_extents.extend(
        {
            "kind": "record_body",
            "owner_id": row["record_id"],
            "start": f"{int(row['abs'], 16) + len(bytes.fromhex(str(row['rewrite_payload_hex']))) - int(row['body_span']):06X}",
            "end_exclusive": f"{int(row['abs'], 16) + len(bytes.fromhex(str(row['rewrite_payload_hex']))):06X}",
        }
        for row in new_rows
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
        "targets_decoded": decoded_parent + decoded_new,
        "approved_change_extents": approved_change_extents,
        "checksum": f"{checksum:04X}",
    }
    apply_report = {
        "ok": True,
        "policy": "single_lowest_cost_duplicate_payload_detachment",
        "parent_targets_verified": decoded_parent,
        "records_applied": len(applied),
        "reclaim_slot": f"{reclaim:04X}",
        "keeper_slot": f"{keeper:04X}",
        "detachment_writes": detachment_writes,
        "former_consumers_preserved": former_render_checks,
        "dictionary_slots_written": 1,
        "dictionary_pointers_written": 1,
        "stock_5f_writes": 1,
        "full_dictionary_rebuild": False,
        "ff_page_writes": 0,
        "terminator_writes": 0,
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
        prefix=args.gate_prefix,
        baseline_meta=args.baseline_meta,
        approved_stock_report=None,
        approved_detachment_report=args.approval_report,
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
            "p2_phase": "P2-1_single_duplicate_payload_detachment",
            "parent_candidate": parent_identity,
            "analysis_report": identity(args.analysis_report),
            "approval_report": identity(args.approval_report),
            "candidate_save": save_identity,
            "published": False,
            "main_tip_modified": False,
            "detachment_contract": approval,
            "inherited_approvals": inherited["evidence"],
        }
    )
    _write_json(args.report, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    parser.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    parser.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    parser.add_argument(
        "--expected-parent-sha",
        default=EXPECTED_PARENT_SHA256,
        help="required SHA-256 identity for the parent candidate",
    )
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--analysis-sheet", type=Path, default=DEFAULT_ANALYSIS_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--analysis-report", type=Path, default=DEFAULT_ANALYSIS_REPORT)
    parser.add_argument(
        "--reuse-analysis-report",
        action="store_true",
        help="reuse the report only after validating its generator and parent SHA-256",
    )
    parser.add_argument("--candidate-rom", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--candidate-save", type=Path, default=DEFAULT_CANDIDATE_SAVE)
    parser.add_argument("--approval-report", type=Path, default=DEFAULT_APPROVAL)
    parser.add_argument(
        "--parent-stock-approval",
        type=Path,
        default=DEFAULT_PARENT_STOCK_APPROVAL,
    )
    parser.add_argument(
        "--parent-detachment-approval",
        type=Path,
        default=None,
        help="candidate-bound prior detachment approval to carry forward",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--gate-dir", type=Path, default=DEFAULT_GATE_DIR)
    parser.add_argument("--gate-prefix", default=DEFAULT_GATE_PREFIX)
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
        "new_duplicate_targets": report.get("apply_report", {}).get(
            "records_applied"
        ),
        "reclaim_slot": report.get("apply_report", {}).get("reclaim_slot"),
        "keeper_slot": report.get("apply_report", {}).get("keeper_slot"),
        "gates": {
            name: result.get("ok")
            for name, result in (report.get("gates") or {}).items()
        },
        "main_tip_modified": False,
        "report": str(args.report),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.get("accepted") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
