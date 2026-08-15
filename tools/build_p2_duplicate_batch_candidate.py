#!/usr/bin/env python3
"""Build one cumulative zero-nested duplicate-payload batch candidate.

The batch is supplied by :mod:`analyze_p2_duplicate_batch`.  All selected
reclaim slots are detached in one shared scratch state, all old external/nested
references must become zero together, and only then are the slots repointed to
new Korean phrases in the verified all-FF bank-5F tail.

The final approval is cumulative: inherited stock ownership and detachment
ranges must remain byte-identical, while the new slots/ranges are added and the
whole contract is rebound to the child candidate SHA-256.  No full dictionary
rebuild, FF-page write, far-pointer relocation, or main-TIP overwrite occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_batch import (  # noqa: E402
    DEFAULT_OUT as DEFAULT_ANALYSIS_REPORT,
    DEFAULT_PARENT_DETACHMENT_APPROVAL,
    DEFAULT_PARENT_ROM,
    DEFAULT_PARENT_SAVE,
    analyze,
    build_parser as build_analysis_parser,
)
from analyze_p2_duplicate_detachment import (  # noqa: E402
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
from build_p2_duplicate_detach_candidate import (  # noqa: E402
    P2DuplicateDetachError,
    _payload_at,
    _write_json,
)
from build_p2_exact_reuse_candidate import (  # noqa: E402
    _atomic_copy,
    _atomic_write,
    _baseline_source_rows,
    _covered,
    diff_runs,
    identity,
    sha256_bytes,
)
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor  # noqa: E402
from build_p2_true_free_candidate import _target_row  # noqa: E402
from expand_dictionary import guard_hangul_slot_writes, write_dictionary_slots_spill  # noqa: E402
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
    token_from_dict_index,
    update_ws_checksum,
)
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402
from structured_token_write_guard import (  # noqa: E402
    StructuredTokenWriteError,
    guard_external_token_write,
)

EXPECTED_PARENT_SHA256 = "98e909d6eef48e0fa91d3c1bdb042c1820cf69d9d631e443d3311e143737ffcd"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_duplicate_batch_candidate.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_duplicate_batch_candidate.sav"
DEFAULT_APPROVAL = ROOT / "out/patch/p2_duplicate_batch_approval.json"
DEFAULT_REPORT = ROOT / "out/patch/p2_duplicate_batch_report.json"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_duplicate_batch_gates"
DEFAULT_PRE_EXT3 = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PREFIX_EVIDENCE = ROOT / "out/patch/main_prefix_followup_evidence.json"
DEFAULT_GATE_SHEET = ROOT / "out/script/translations_quality.json"
DEFAULT_UI_REPORT_DIR = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean"
DEFAULT_BASELINE_META = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"


class P2DuplicateBatchError(P2DuplicateDetachError):
    """Raised when any batch detachment or preservation proof fails."""


def _analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_analysis_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.parent_rom = args.parent_rom
    parsed.parent_save = args.parent_save
    parsed.parent_detachment_approval = args.parent_detachment_approval
    parsed.base_manifest = args.base_manifest
    parsed.values_dir = args.values_dir
    parsed.sheet = args.analysis_sheet
    parsed.tbl = args.tbl
    parsed.ext_meta = args.ext_meta
    parsed.ext3_meta = args.ext3_meta
    parsed.out = args.analysis_report
    parsed.allow_nested_parents = args.allow_nested_parents
    parsed.max_nested_per_reclaim = args.max_nested_per_reclaim
    parsed.stdout = True
    return parsed


def _walk_dicts(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _cumulative_detachment_writes(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for item in _walk_dicts(document):
        if not all(key in item for key in ("record_abs", "token_abs", "before_hex", "after_hex")):
            continue
        try:
            token_abs = int(str(item["token_abs"]), 16)
        except (TypeError, ValueError):
            continue
        row = {
            "record_abs": str(item["record_abs"]).upper(),
            "token_abs": f"{token_abs:06X}",
            "region": item.get("region", "aux"),
            "kind": item.get("kind", "zstring"),
            "before_hex": str(item["before_hex"]).upper(),
            "after_hex": str(item["after_hex"]).upper(),
        }
        previous = rows.get(token_abs)
        if previous is not None and previous != row:
            raise P2DuplicateBatchError(
                f"conflicting inherited detachment write at {token_abs:06X}"
            )
        rows[token_abs] = row
    return [rows[key] for key in sorted(rows)]


def _load_inherited(args: argparse.Namespace, *, parent: bytes, parent_sha: str) -> dict[str, Any]:
    indices, approved_sha, ranges = load_approved_detachment(
        args.parent_detachment_approval
    )
    if approved_sha != parent_sha:
        raise P2DuplicateBatchError(
            f"parent approval is bound to {approved_sha}, parent is {parent_sha}"
        )
    document = json.loads(
        args.parent_detachment_approval.read_text(encoding="utf-8")
    )
    return {
        "stock_indices": set(indices),
        "ranges": [
            {
                "logical_start": f"{lo:06X}",
                "logical_end_exclusive": f"{hi:06X}",
                "owner_id": owner,
            }
            for lo, hi, owner in ranges
        ],
        "writes": _cumulative_detachment_writes(document),
        "evidence": {
            "report": identity(args.parent_detachment_approval),
            "candidate_sha256": approved_sha,
            "approved_stock_slots": [f"{index:04X}" for index in sorted(indices)],
            "approved_detachment_ranges": [
                {
                    "logical_start": f"{lo:06X}",
                    "logical_end_exclusive": f"{hi:06X}",
                    "owner_id": owner,
                }
                for lo, hi, owner in ranges
            ],
        },
    }


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
            strategy="duplicate_payload_batch_reclaim",
            index_key="slot",
        )
        for row in new_rows
    )
    selected = list(analysis.get("selected_groups") or [])
    return {
        "generated_by": "tools/build_p2_duplicate_batch_candidate.py",
        "inputs": {"working_rom": dict(parent_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
            "parent_exact": len(parent_rows),
            "duplicate_batch_reclaim": len(new_rows),
            "batch_slots": len(selected),
        },
        "targets": targets,
        "dictionary_changes": {
            "slots_written": len(selected),
            "slot_indices": [str(row["reclaim_slot"]) for row in selected],
            "pointers_written": len(selected),
            "stock_5f_written": True,
            "full_rebuild": False,
            "ff_page_written": False,
            "detachment_groups": [
                {
                    "reclaim_slot": row["reclaim_slot"],
                    "keeper_slot": row["keeper_slot"],
                    "former_external_refs": len(
                        row.get("working_external_occurrences") or []
                    ),
                    "former_nested_refs": len(
                        row.get("working_nested_occurrences") or []
                    ),
                }
                for row in selected
            ],
            "policy": (
                "bounded_nested_duplicate_payload_batch"
                if analysis.get("analysis_mode") == "bounded_nested_parent_batch"
                else "bounded_zero_nested_duplicate_payload_batch"
            ),
        },
        "guard_outcomes": {
            "batch_detachment_then_current_only_free_write": {
                "ok": True,
                "reclaim_slots": [row["reclaim_slot"] for row in selected],
            }
        },
        "ext3": {"used": False, "slots_written": 0},
    }


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    parent = args.parent_rom.read_bytes()
    parent_sha = sha256_bytes(parent)
    if parent_sha != str(args.expected_parent_sha).lower():
        raise P2DuplicateBatchError(
            f"parent identity drifted: expected {args.expected_parent_sha}, got {parent_sha}"
        )
    if len(parent) != 16_777_216:
        raise P2DuplicateBatchError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise P2DuplicateBatchError("parent 32 KiB SaveRAM is missing")

    inherited = _load_inherited(args, parent=parent, parent_sha=parent_sha)
    if args.reuse_analysis_report:
        analysis = json.loads(args.analysis_report.read_text(encoding="utf-8"))
        if analysis.get("generated_by") != "tools/analyze_p2_duplicate_batch.py":
            raise P2DuplicateBatchError("analysis report has the wrong generator")
        bound_sha = ((analysis.get("inputs") or {}).get("parent_rom") or {}).get("sha256")
        if bound_sha != parent_sha:
            raise P2DuplicateBatchError(
                f"analysis report is bound to {bound_sha}, parent is {parent_sha}"
            )
    else:
        analysis = analyze(_analysis_args(args))
        _write_json(args.analysis_report, analysis)
    if (analysis.get("decision") or {}).get("candidate_generation_allowed") is not True:
        raise P2DuplicateBatchError("read-only batch analysis did not approve a candidate")

    selected = list(analysis.get("selected_groups") or [])
    if len(selected) < 2:
        raise P2DuplicateBatchError("batch candidate requires at least two safe groups")
    reclaims = {int(str(row["reclaim_slot"]), 16) for row in selected}
    keepers = {int(str(row["keeper_slot"]), 16) for row in selected}
    if len(reclaims) != len(selected) or reclaims & keepers:
        raise P2DuplicateBatchError("batch reclaim/keeper conflict")
    if reclaims & set(inherited["stock_indices"]):
        raise P2DuplicateBatchError("batch attempts to reclaim inherited owned slot")

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    tbl = Tbl.load(args.tbl)
    dictionary_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)

    candidate = bytearray(parent)
    sb = stock_base(candidate)
    detachment_extents: list[tuple[int, int]] = []
    stage_ranges: list[dict[str, Any]] = []
    stage_writes: list[dict[str, Any]] = []
    record_sites: dict[int, list[dict[str, Any]]] = {}
    selected_by_reclaim: dict[int, Mapping[str, Any]] = {}

    for group in selected:
        reclaim = int(str(group["reclaim_slot"]), 16)
        keeper = int(str(group["keeper_slot"]), 16)
        selected_by_reclaim[reclaim] = group
        reclaim_token = bytes.fromhex(str(group["token_before_hex"]))
        keeper_token = bytes.fromhex(str(group["token_after_hex"]))
        if reclaim_token != bytes(token_from_dict_index(reclaim)):
            raise P2DuplicateBatchError(f"reclaim token drifted: {reclaim:04X}")
        if keeper_token != bytes(token_from_dict_index(keeper)):
            raise P2DuplicateBatchError(f"keeper token drifted: {keeper:04X}")
        if reclaim >= 0xF00 or keeper >= 0xF00:
            raise P2DuplicateBatchError("batch reached FF-page token")
        reclaim_payload = bytes(dictionary_parent.raw_entry(reclaim))
        keeper_payload = bytes(dictionary_parent.raw_entry(keeper))
        if reclaim_payload != keeper_payload:
            raise P2DuplicateBatchError(f"payload equality drifted: {reclaim:04X}")
        if dictionary_parent.expand(reclaim_payload, tbl) != dictionary_parent.expand(
            keeper_payload, tbl
        ):
            raise P2DuplicateBatchError(f"render equality drifted: {reclaim:04X}")
        nested_occurrences = list(group.get("working_nested_occurrences") or [])
        if nested_occurrences and not args.allow_nested_parents:
            raise P2DuplicateBatchError(f"nested parent entered zero-nested batch: {reclaim:04X}")
        if len(nested_occurrences) > int(args.max_nested_per_reclaim):
            raise P2DuplicateBatchError(
                f"nested parent bound exceeded for {reclaim:04X}: {len(nested_occurrences)}"
            )
        occurrences = list(group.get("working_external_occurrences") or [])
        if not occurrences:
            raise P2DuplicateBatchError(f"no current consumer: {reclaim:04X}")
        for occurrence in occurrences:
            record_abs = int(str(occurrence["record_abs"]), 16)
            token_abs = int(str(occurrence["token_abs"]), 16)
            offset = int(occurrence["payload_offset"])
            before_payload = _payload_at(parent, record_abs)
            if before_payload.hex().upper() != str(occurrence["payload_hex"]):
                raise P2DuplicateBatchError(
                    f"consumer payload drifted at {record_abs:06X}"
                )
            if before_payload[offset : offset + 2] != reclaim_token:
                raise P2DuplicateBatchError(f"consumer token drifted at {token_abs:06X}")
            try:
                guard_external_token_write(
                    parent,
                    token_abs=token_abs,
                    before=reclaim_token,
                    after=keeper_token,
                    region=str(occurrence["region"]),
                    kind=str(occurrence["kind"]),
                )
            except StructuredTokenWriteError as exc:
                raise P2DuplicateBatchError(str(exc)) from exc
            file_start = sb + token_abs
            extent = (file_start, file_start + 2)
            if any(not (extent[1] <= lo or hi <= extent[0]) for lo, hi in detachment_extents):
                raise P2DuplicateBatchError(f"overlapping token write at {token_abs:06X}")
            if bytes(candidate[file_start : file_start + 2]) != reclaim_token:
                raise P2DuplicateBatchError(f"ROM token drifted at {token_abs:06X}")
            candidate[file_start : file_start + 2] = keeper_token
            detachment_extents.append(extent)
            range_row = {
                "logical_start": f"{token_abs:06X}",
                "logical_end_exclusive": f"{token_abs + 2:06X}",
                "owner_id": f"detach:{reclaim:04X}->{keeper:04X}",
            }
            write_row = {
                "record_abs": f"{record_abs:06X}",
                "token_abs": f"{token_abs:06X}",
                "region": occurrence["region"],
                "kind": occurrence["kind"],
                "before_hex": reclaim_token.hex().upper(),
                "after_hex": keeper_token.hex().upper(),
            }
            stage_ranges.append(range_row)
            stage_writes.append(write_row)
            record_sites.setdefault(record_abs, []).append(write_row)

        for occurrence in nested_occurrences:
            parent_index = int(str(occurrence["parent"]), 16)
            offset = int(occurrence["payload_offset"])
            parent_ptr = int(dictionary_parent.ptrs[parent_index])
            expected_ptr = int(str(occurrence["parent_ptr"]), 16)
            if parent_ptr != expected_ptr:
                raise P2DuplicateBatchError(
                    f"nested parent pointer drifted: {parent_index:04X}"
                )
            before_payload = bytes(dictionary_parent.raw_entry(parent_index))
            if before_payload.hex().upper() != str(occurrence["parent_payload_hex"]):
                raise P2DuplicateBatchError(
                    f"nested parent payload drifted: {parent_index:04X}"
                )
            if before_payload[offset : offset + 2] != reclaim_token:
                raise P2DuplicateBatchError(
                    f"nested parent token drifted: {parent_index:04X}+{offset}"
                )
            record_abs = SEG_DICT * BANK_SIZE + parent_ptr
            token_abs = record_abs + offset
            if f"{record_abs:06X}" != str(occurrence["parent_logical"]):
                raise P2DuplicateBatchError(
                    f"nested parent logical drifted: {parent_index:04X}"
                )
            if f"{token_abs:06X}" != str(occurrence["token_abs"]):
                raise P2DuplicateBatchError(
                    f"nested token logical drifted: {parent_index:04X}"
                )
            file_start = sb + token_abs
            extent = (file_start, file_start + 2)
            if any(not (extent[1] <= lo or hi <= extent[0]) for lo, hi in detachment_extents):
                raise P2DuplicateBatchError(f"overlapping nested write at {token_abs:06X}")
            if bytes(candidate[file_start : file_start + 2]) != reclaim_token:
                raise P2DuplicateBatchError(f"nested ROM token drifted at {token_abs:06X}")
            candidate[file_start : file_start + 2] = keeper_token
            detachment_extents.append(extent)
            range_row = {
                "logical_start": f"{token_abs:06X}",
                "logical_end_exclusive": f"{token_abs + 2:06X}",
                "owner_id": f"nested_detach:{reclaim:04X}->{keeper:04X}",
            }
            write_row = {
                "record_abs": f"{record_abs:06X}",
                "token_abs": f"{token_abs:06X}",
                "region": "dict5f",
                "kind": "nested_dictionary",
                "parent_index": f"{parent_index:04X}",
                "before_hex": reclaim_token.hex().upper(),
                "after_hex": keeper_token.hex().upper(),
                "overlapping_entries": list(occurrence.get("overlapping_entries") or []),
            }
            stage_ranges.append(range_row)
            stage_writes.append(write_row)
            record_sites.setdefault(record_abs, []).append(write_row)

    detached = bytes(candidate)
    detached_refs = external_occurrence_map(
        detached, ext3_aware=True, wanted=set(reclaims)
    )
    dictionary_detached = make_dictionary_ext3(detached, ext_meta, ext3_meta)
    detached_nested = nested_occurrence_map(
        dictionary_detached, wanted=set(reclaims), ext3_aware=True
    )
    for reclaim in sorted(reclaims):
        if detached_refs.get(reclaim) or detached_nested.get(reclaim):
            raise P2DuplicateBatchError(
                f"detachment-only state still references {reclaim:04X}"
            )

    nested_alias_checks: list[dict[str, Any]] = []
    seen_aliases: set[tuple[int, int]] = set()
    for group in selected:
        reclaim = int(str(group["reclaim_slot"]), 16)
        keeper = int(str(group["keeper_slot"]), 16)
        for occurrence in group.get("working_nested_occurrences") or []:
            token_abs = int(str(occurrence["token_abs"]), 16)
            for alias in occurrence.get("overlapping_entries") or []:
                index = int(str(alias["index"]), 16)
                key = (token_abs, index)
                if key in seen_aliases:
                    continue
                seen_aliases.add(key)
                before_raw = bytes.fromhex(str(alias["payload_hex"]))
                after_raw = bytes(dictionary_detached.raw_entry(index))
                before_render = dictionary_parent.expand(before_raw, tbl)
                after_render = dictionary_detached.expand(after_raw, tbl)
                if before_render != after_render:
                    raise P2DuplicateBatchError(
                        f"nested alias rendering changed: {index:04X} "
                        f"({reclaim:04X}->{keeper:04X})"
                    )
                nested_alias_checks.append(
                    {
                        "index": f"{index:04X}",
                        "token_abs": f"{token_abs:06X}",
                        "reclaim_slot": f"{reclaim:04X}",
                        "keeper_slot": f"{keeper:04X}",
                        "before_payload_hex": before_raw.hex().upper(),
                        "after_payload_hex": after_raw.hex().upper(),
                        "before_render": before_render,
                        "after_render": after_render,
                        "preserved": True,
                    }
                )

    affected_parent_indices = {
        int(str(row["index"]), 16) for row in nested_alias_checks
    }
    frontier = set(affected_parent_indices)
    while frontier:
        parent_map = nested_occurrence_map(
            dictionary_parent,
            wanted=frontier,
            ext3_aware=True,
        )
        discovered = {
            int(str(occurrence["parent"]), 16)
            for occurrences in parent_map.values()
            for occurrence in occurrences
        } - affected_parent_indices
        if not discovered:
            break
        affected_parent_indices.update(discovered)
        frontier = discovered

    nested_impact_records: list[dict[str, Any]] = []
    impact_external = external_occurrence_map(
        parent,
        ext3_aware=True,
        wanted=affected_parent_indices,
    )
    seen_impact_records: set[int] = set()
    for source_index, occurrences in sorted(impact_external.items()):
        for occurrence in occurrences:
            record_abs = int(str(occurrence["record_abs"]), 16)
            if record_abs in seen_impact_records:
                continue
            seen_impact_records.add(record_abs)
            before_payload = _payload_at(parent, record_abs)
            after_payload = _payload_at(detached, record_abs)
            before_render = dictionary_parent.expand(before_payload, tbl)
            after_render = dictionary_detached.expand(after_payload, tbl)
            if before_render != after_render:
                raise P2DuplicateBatchError(
                    f"nested indirect consumer changed at {record_abs:06X}"
                )
            nested_impact_records.append(
                {
                    "record_abs": f"{record_abs:06X}",
                    "source_index": f"{source_index:04X}",
                    "region": occurrence["region"],
                    "kind": occurrence["kind"],
                    "before_payload_hex": before_payload.hex().upper(),
                    "after_payload_hex": after_payload.hex().upper(),
                    "before_render": before_render,
                    "after_render": after_render,
                    "preserved": True,
                }
            )

    former_render_checks: list[dict[str, Any]] = []
    for record_abs in sorted(record_sites):
        before_payload = _payload_at(parent, record_abs)
        after_payload = _payload_at(detached, record_abs)
        before_render = dictionary_parent.expand(before_payload, tbl)
        after_render = dictionary_detached.expand(after_payload, tbl)
        if before_render != after_render:
            raise P2DuplicateBatchError(
                f"batch former consumer rendering changed at {record_abs:06X}"
            )
        former_render_checks.append(
            {
                "record_abs": f"{record_abs:06X}",
                "token_writes": record_sites[record_abs],
                "before_payload_hex": before_payload.hex().upper(),
                "after_payload_hex": after_payload.hex().upper(),
                "before_render": before_render,
                "after_render": after_render,
                "preserved": True,
            }
        )

    allocations = list((analysis.get("allocation") or {}).get("allocations") or [])
    new_rows = list((analysis.get("allocation") or {}).get("record_plan") or [])
    slot_payload = {
        int(str(row["slot"]), 16): bytes.fromhex(str(row["encoded_payload_hex"]))
        for row in allocations
    }
    if set(slot_payload) != reclaims:
        raise P2DuplicateBatchError("allocation slots do not equal reclaimed slots")

    dictionary_before = Dictionary(detached)
    pointers_before = list(dictionary_before.ptrs)
    payloads_before = [
        bytes(dictionary_before.raw_entry(index)) for index in range(dictionary_before.count)
    ]
    bank_before = bytes(slice_bank(detached, SEG_DICT))
    phrase_start = _stock_phrase_cursor(detached)
    required = sum(len(payload) + 1 for payload in slot_payload.values())
    if phrase_start + required > BANK_SIZE:
        raise P2DuplicateBatchError("stock spill capacity exhausted")
    if any(value != 0xFF for value in bank_before[phrase_start:]):
        raise P2DuplicateBatchError(f"bank-5F tail is not FF from {phrase_start:04X}")

    current_locs = _working_two_byte_external_refs(detached)
    for reclaim in reclaims:
        if current_locs.get(reclaim):
            raise P2DuplicateBatchError(f"guard still sees reclaim refs: {reclaim:04X}")
    guard_hangul_slot_writes(
        detached, slot_payload, allow_aux_consumers=False, locs=current_locs
    )
    pointers_after_write, phrase_end = write_dictionary_slots_spill(
        candidate,
        slot_payload,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=current_locs,
    )

    dictionary_after = Dictionary(candidate)
    pointers_after = list(dictionary_after.ptrs)
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != reclaims:
        raise P2DuplicateBatchError(
            f"pointer delta {sorted(changed_pointer_indices)} != {sorted(reclaims)}"
        )
    if pointers_after_write != pointers_after:
        raise P2DuplicateBatchError("writer pointer result differs from ROM")
    cursor = phrase_start
    for index, payload in sorted(slot_payload.items()):
        if pointers_after[index] != cursor:
            raise P2DuplicateBatchError(f"pointer placement drift: {index:04X}")
        if bytes(dictionary_after.raw_entry(index)) != payload:
            raise P2DuplicateBatchError(f"payload verification failed: {index:04X}")
        cursor += len(payload) + 1
    if cursor != phrase_end:
        raise P2DuplicateBatchError("spill end drifted")

    nonselected_payloads_preserved = True
    for index, before in enumerate(payloads_before):
        if index in reclaims:
            continue
        if pointers_after[index] != pointers_before[index]:
            raise P2DuplicateBatchError(f"nonselected pointer changed: {index:04X}")
        if bytes(dictionary_after.raw_entry(index)) != before:
            nonselected_payloads_preserved = False
            raise P2DuplicateBatchError(f"nonselected payload changed: {index:04X}")

    pointer_local_extents = [
        (DICT_PTR_START + index * 2, DICT_PTR_START + index * 2 + 2)
        for index in sorted(reclaims)
    ]
    phrase_local_extent = (phrase_start, phrase_end)
    bank_after = bytes(slice_bank(candidate, SEG_DICT))
    bad_bank_runs = [
        run
        for run in diff_runs(bank_before, bank_after)
        if not _covered(run, pointer_local_extents + [phrase_local_extent])
    ]
    if bad_bank_runs:
        raise P2DuplicateBatchError(f"bank-5F diff outside batch extents: {bad_bank_runs[:8]}")

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
            raise P2DuplicateBatchError(f"prefix drift: {row['record_id']}")
        terminator = int(str(row["approved_extent"]["terminator"]), 16)
        if candidate[sb + terminator] != 0:
            raise P2DuplicateBatchError(f"terminator drift: {row['record_id']}")
        candidate[start + prefix_len : end] = rewritten[prefix_len:]
        if candidate[sb + terminator] != 0:
            raise P2DuplicateBatchError(f"terminator overwritten: {row['record_id']}")
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
        (bank_file_base + lo, bank_file_base + hi) for lo, hi in pointer_local_extents
    ]
    phrase_file_extent = (
        bank_file_base + phrase_local_extent[0],
        bank_file_base + phrase_local_extent[1],
    )
    approved_file_extents = (
        detachment_extents
        + pointer_file_extents
        + [phrase_file_extent]
        + record_file_extents
        + checksum_runs
    )
    all_runs = diff_runs(parent, candidate)
    unaccounted = [run for run in all_runs if not _covered(run, approved_file_extents)]
    if unaccounted:
        raise P2DuplicateBatchError(f"unapproved candidate diff: {unaccounted[:8]}")

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
            raise P2DuplicateBatchError(f"parent target regressed: {row['record_id']}")
        decoded_parent += 1

    decoded_new = 0
    expected_by_slot: dict[int, set[str]] = {index: set() for index in reclaims}
    for row in new_rows:
        logical = int(str(row["abs"]), 16)
        payload = bytes.fromhex(str(row["rewrite_payload_hex"]))
        prefix_len = len(payload) - int(row["body_span"])
        actual = final[sb + logical : sb + logical + len(payload)]
        rendered = dictionary_final.expand(actual[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["target_ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise P2DuplicateBatchError(f"new target mismatch: {row['record_id']}")
        expected_by_slot[int(str(row["slot"]), 16)].add(str(row["abs"]))
        decoded_new += 1

    final_refs = external_occurrence_map(final, ext3_aware=True, wanted=set(reclaims))
    final_nested = nested_occurrence_map(
        dictionary_final, wanted=set(reclaims), ext3_aware=True
    )
    final_consumers: dict[str, list[dict[str, Any]]] = {}
    for reclaim in sorted(reclaims):
        actual_records = {
            str(row["record_abs"]) for row in final_refs.get(reclaim, [])
        }
        if actual_records != expected_by_slot[reclaim]:
            raise P2DuplicateBatchError(
                f"final consumer set drift for {reclaim:04X}: "
                f"{actual_records ^ expected_by_slot[reclaim]}"
            )
        if final_nested.get(reclaim):
            raise P2DuplicateBatchError(f"final nested parent: {reclaim:04X}")
        final_consumers[f"{reclaim:04X}"] = final_refs.get(reclaim, [])

    for check in former_render_checks:
        record_abs = int(str(check["record_abs"]), 16)
        final_payload = _payload_at(final, record_abs)
        final_render = dictionary_final.expand(final_payload, tbl)
        if final_render != check["before_render"]:
            raise P2DuplicateBatchError(
                f"former consumer regressed after reclaim: {record_abs:06X}"
            )
        check["final_payload_hex"] = final_payload.hex().upper()
        check["final_render"] = final_render
        check["final_preserved"] = True

    for impact in nested_impact_records:
        record_abs = int(str(impact["record_abs"]), 16)
        final_payload = _payload_at(final, record_abs)
        final_render = dictionary_final.expand(final_payload, tbl)
        if final_render != impact["before_render"]:
            raise P2DuplicateBatchError(
                f"nested indirect consumer regressed after reclaim: {record_abs:06X}"
            )
        impact["final_payload_hex"] = final_payload.hex().upper()
        impact["final_render"] = final_render
        impact["final_preserved"] = True

    for group in selected:
        reclaim = int(str(group["reclaim_slot"]), 16)
        reclaim_token = bytes.fromhex(str(group["token_before_hex"]))
        for row in group.get("raw_pair_non_runtime_hits") or []:
            token_abs = int(str(row["token_abs"]), 16)
            if final[sb + token_abs : sb + token_abs + 2] != reclaim_token:
                raise P2DuplicateBatchError(
                    f"non-runtime raw pair changed at {token_abs:06X}"
                )

    parent_stock = Dictionary(parent)
    final_stock = Dictionary(final)
    inherited_stock_rows: list[dict[str, Any]] = []
    inherited_stock_preserved = True
    for index in sorted(inherited["stock_indices"]):
        pointer_equal = parent_stock.ptrs[index] == final_stock.ptrs[index]
        payload_equal = bytes(parent_stock.raw_entry(index)) == bytes(
            final_stock.raw_entry(index)
        )
        inherited_stock_preserved &= pointer_equal and payload_equal
        inherited_stock_rows.append(
            {
                "index": f"{index:04X}",
                "pointer_preserved": pointer_equal,
                "payload_preserved": payload_equal,
            }
        )
    inherited_ranges_preserved = True
    for row in inherited["ranges"]:
        lo = int(str(row["logical_start"]), 16)
        hi = int(str(row["logical_end_exclusive"]), 16)
        if parent[sb + lo : sb + hi] != final[sb + lo : sb + hi]:
            inherited_ranges_preserved = False
            break
    if not inherited_stock_preserved or not inherited_ranges_preserved:
        raise P2DuplicateBatchError("inherited approval changed in batch child")

    candidate_payload = final
    _atomic_write(args.candidate_rom, candidate_payload)
    _atomic_copy(args.parent_save, args.candidate_save)
    candidate_identity = identity(args.candidate_rom, candidate_payload)
    save_identity = identity(args.candidate_save)
    parent_identity = identity(args.parent_rom, parent)

    cumulative_ranges = list(inherited["ranges"]) + stage_ranges
    cumulative_ranges.sort(key=lambda row: int(str(row["logical_start"]), 16))
    cumulative_writes = list(inherited["writes"]) + stage_writes
    by_site: dict[int, dict[str, Any]] = {}
    for row in cumulative_writes:
        token_abs = int(str(row["token_abs"]), 16)
        by_site[token_abs] = dict(row)
    cumulative_writes = [by_site[key] for key in sorted(by_site)]
    cumulative_stock = set(inherited["stock_indices"]) | reclaims

    proof = {
        "duplicate_payload_equal_before": True,
        "historical_consumers_accounted": True,
        "all_current_external_refs_retargeted": sum(
            1 for row in stage_writes if row.get("kind") != "nested_dictionary"
        )
        == sum(len(row.get("working_external_occurrences") or []) for row in selected),
        "all_current_nested_parents_retargeted": sum(
            1 for row in stage_writes if row.get("kind") == "nested_dictionary"
        )
        == sum(len(row.get("working_nested_occurrences") or []) for row in selected),
        "detachment_stage_zero_old_refs": all(
            not detached_refs.get(index) and not detached_nested.get(index)
            for index in reclaims
        ),
        "former_consumer_render_preserved": all(
            row.get("final_preserved") is True for row in former_render_checks
        ),
        "candidate_new_consumers_exact": all(
            {str(row["record_abs"]) for row in final_refs.get(index, [])}
            == expected_by_slot[index]
            for index in reclaims
        ),
        "tail_was_all_ff": all(value == 0xFF for value in bank_before[phrase_start:]),
        "changed_pointer_indices_exact": changed_pointer_indices == reclaims,
        "nonselected_pointers_preserved": all(
            pointers_after[index] == pointers_before[index]
            for index in range(len(pointers_before))
            if index not in reclaims
        ),
        "nonselected_payloads_preserved": nonselected_payloads_preserved,
        "bank5f_diffs_within_approved_extents": not bad_bank_runs,
        "detachment_diffs_within_approved_extents": all(
            _covered(run, detachment_extents) for run in diff_runs(parent, detached)
        ),
        "inherited_stock_slots_preserved": inherited_stock_preserved,
        "inherited_detachment_ranges_preserved": inherited_ranges_preserved,
        "inherited_approval_candidate_matches_parent": True,
        "batch_reclaim_keeper_sets_disjoint": not (reclaims & keepers),
        "batch_token_write_sites_nonoverlapping": len(detachment_extents)
        == len({lo for lo, _hi in detachment_extents}),
        "batch_nested_parent_sites_exact": sum(
            1 for row in stage_writes if row.get("kind") == "nested_dictionary"
        )
        == sum(len(row.get("working_nested_occurrences") or []) for row in selected),
        "batch_nested_parent_alias_render_preserved": all(
            row.get("preserved") is True for row in nested_alias_checks
        ),
        "batch_nested_indirect_consumers_preserved": all(
            row.get("final_preserved") is True for row in nested_impact_records
        ),
    }
    if not all(proof.values()):
        raise P2DuplicateBatchError(f"batch approval proof failed: {proof}")

    approval = {
        "generated_by": "tools/build_p2_duplicate_batch_candidate.py",
        "mode": "pre_gate_detachment_approval",
        "ok": True,
        "parent_rom": parent_identity,
        "candidate_rom": candidate_identity,
        "approved_stock_slots": [f"{index:04X}" for index in sorted(cumulative_stock)],
        "approved_detachment_ranges": cumulative_ranges,
        "duplicate": {
            "batch": True,
            "groups": selected,
            "detachment_writes": cumulative_writes,
            "stage_detachment_writes": stage_writes,
            "former_render_checks": former_render_checks,
            "nested_alias_checks": nested_alias_checks,
            "nested_impact_records": nested_impact_records,
            "final_new_consumers": final_consumers,
        },
        "pointer_changes": [
            {
                "index": f"{index:04X}",
                "before": f"{pointers_before[index]:04X}",
                "after": f"{pointers_after[index]:04X}",
            }
            for index in sorted(reclaims)
        ],
        "tail": {
            "start": f"{phrase_start:04X}",
            "end_exclusive": f"{phrase_end:04X}",
            "free_before": BANK_SIZE - phrase_start,
            "used": phrase_end - phrase_start,
            "before_sha256": hashlib.sha256(bank_before[phrase_start:]).hexdigest(),
        },
        "inherited": {
            "evidence": inherited["evidence"],
            "stock_preservation": inherited_stock_rows,
            "detachment_ranges_preserved": inherited_ranges_preserved,
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
        "policy": (
            "bounded_nested_duplicate_batch_with_cumulative_sha_bound_proof"
            if analysis.get("analysis_mode") == "bounded_nested_parent_batch"
            else "bounded_zero_nested_duplicate_batch_with_cumulative_sha_bound_proof"
        ),
    }
    approved_change_extents: list[dict[str, Any]] = []
    approved_change_extents.extend(
        {
            "kind": "duplicate_detachment_token",
            "owner_id": row["owner_id"],
            "start": row["logical_start"],
            "end_exclusive": row["logical_end_exclusive"],
        }
        for row in stage_ranges
    )
    approved_change_extents.extend(
        {
            "kind": "dictionary_pointer",
            "owner_id": f"stock_slot:{index:04X}",
            "file_start": f"{lo:08X}",
            "file_end_exclusive": f"{hi:08X}",
        }
        for index, (lo, hi) in zip(sorted(reclaims), pointer_file_extents)
    )
    approved_change_extents.append(
        {
            "kind": "dictionary_payload",
            "owner_id": "batch_reclaimed_slots",
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
        "policy": (
            "bounded_nested_duplicate_payload_batch"
            if analysis.get("analysis_mode") == "bounded_nested_parent_batch"
            else "bounded_zero_nested_duplicate_payload_batch"
        ),
        "parent_targets_verified": decoded_parent,
        "records_applied": len(applied),
        "batch_groups": len(selected),
        "reclaim_slots": [f"{index:04X}" for index in sorted(reclaims)],
        "keeper_slots": [f"{index:04X}" for index in sorted(keepers)],
        "detachment_writes": stage_writes,
        "former_consumers_preserved": former_render_checks,
        "dictionary_slots_written": len(reclaims),
        "dictionary_pointers_written": len(reclaims),
        "stock_5f_writes": len(reclaims),
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
        prefix="p2_duplicate_batch",
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
            "p2_phase": (
                "P2-1_bounded_nested_duplicate_batch"
                if analysis.get("analysis_mode") == "bounded_nested_parent_batch"
                else "P2-1_bounded_zero_nested_duplicate_batch"
            ),
            "parent_candidate": parent_identity,
            "analysis_report": identity(args.analysis_report),
            "approval_report": identity(args.approval_report),
            "candidate_save": save_identity,
            "remaining": analysis.get("remaining"),
            "published": False,
            "main_tip_modified": False,
            "detachment_contract": approval,
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
        "--parent-detachment-approval",
        type=Path,
        default=DEFAULT_PARENT_DETACHMENT_APPROVAL,
    )
    parser.add_argument("--expected-parent-sha", default=EXPECTED_PARENT_SHA256)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--analysis-sheet", type=Path, default=DEFAULT_ANALYSIS_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--analysis-report", type=Path, default=DEFAULT_ANALYSIS_REPORT)
    parser.add_argument("--reuse-analysis-report", action="store_true")
    parser.add_argument(
        "--allow-nested-parents",
        action="store_true",
        help="allow exact size-preserving keeper retargets inside dictionary payloads",
    )
    parser.add_argument(
        "--max-nested-per-reclaim",
        type=int,
        default=0,
        help="maximum nested parent occurrences permitted for each reclaim slot",
    )
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
    summary = {
        "status": report.get("status"),
        "accepted": report.get("accepted"),
        "candidate_rom": report.get("inputs", {}).get("candidate_rom"),
        "candidate_save": report.get("candidate_save"),
        "targets": len(report.get("targets") or []),
        "new_batch_targets": (report.get("apply_report") or {}).get("records_applied"),
        "batch_groups": (report.get("apply_report") or {}).get("batch_groups"),
        "reclaim_slots": (report.get("apply_report") or {}).get("reclaim_slots"),
        "gates": {
            name: result.get("ok") for name, result in (report.get("gates") or {}).items()
        },
        "remaining": report.get("remaining"),
        "main_tip_modified": False,
        "report": str(args.report),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report.get("accepted") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
