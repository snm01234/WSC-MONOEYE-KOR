#!/usr/bin/env python3
"""Read-only bounded batch analysis for safe duplicate-payload reclaim.

The parent candidate already contains the accepted P2 exact/true-free stages and
prior duplicate detachments.  This analyzer searches the complete current stock
dictionary for byte-identical payload groups and selects every independent group
that satisfies the conservative batch contract:

* non-FF, zstring-safe two-byte reclaim/keeper tokens;
* reclaim slot is not already owned by an accepted P2 allocation;
* reclaim slot is not a keeper used by an inherited detachment;
* at least one current external runtime consumer;
* zero current nested dictionary parents by default, or an explicitly bounded
  exact nested-parent set when ``--allow-nested-parents`` is enabled;
* every Original/current external occurrence is enumerated exactly;
* one reclaim per duplicate group, with no overlapping token writes.

It then allocates the reclaimed slots to the highest-impact remaining reviewed
short phrases.  No ROM or SaveRAM is written.
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

from analyze_p2_duplicate_detachment import (  # noqa: E402
    DuplicateDetachmentError,
    _baseline_rows,
    duplicate_groups,
    external_occurrence_map,
    nested_occurrence_map,
    raw_pair_non_runtime_hits,
)
from analyze_p2_short_records import (  # noqa: E402
    DEFAULT_BASE_MANIFEST,
    DEFAULT_EXT3_META,
    DEFAULT_EXT_META,
    DEFAULT_ORIGINAL_ROM,
    DEFAULT_SHEET,
    DEFAULT_TBL,
    DEFAULT_VALUES_DIR,
    analyze as analyze_short_records,
    build_parser as build_short_parser,
    build_true_free_plan,
    load_reviewed_values,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_reference_union import build_reference_union  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    BANK_SIZE,
    SEG_DICT,
    Tbl,
    dict_index_from_token,
    load_rom,
    token_from_dict_index,
)
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_duplicate_detach2_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_duplicate_detach2_candidate.sav"
DEFAULT_PARENT_DETACHMENT_APPROVAL = (
    ROOT / "out/patch/p2_duplicate_detach2_approval.json"
)
DEFAULT_OUT = ROOT / "out/patch/p2_duplicate_batch_capacity_report.json"


class DuplicateBatchError(RuntimeError):
    """Raised when the batch proof cannot be trusted."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size": len(payload),
        "sha256": _sha256(payload),
    }


def _short_analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_short_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.working_rom = args.parent_rom
    parsed.base_manifest = args.base_manifest
    parsed.values_dir = args.values_dir
    parsed.sheet = args.sheet
    parsed.tbl = args.tbl
    parsed.ext_meta = args.ext_meta
    parsed.ext3_meta = args.ext3_meta
    parsed.base_save = args.parent_save
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


def inherited_keeper_slots(document: Mapping[str, Any]) -> set[int]:
    """Every token used as an `after_hex` keeper anywhere in cumulative proof."""
    keepers: set[int] = set()
    for row in _walk_dicts(document):
        raw = row.get("after_hex")
        if not isinstance(raw, str) or len(raw) != 4:
            continue
        try:
            token = bytes.fromhex(raw)
            keepers.add(dict_index_from_token(token[0], token[1]))
        except (ValueError, IndexError):
            continue
    return keepers


def _nested_write_evidence(
    dictionary: Dictionary,
    occurrence: Mapping[str, Any],
    *,
    reclaim: int,
) -> dict[str, Any]:
    parent = int(str(occurrence["parent"]), 16)
    offset = int(occurrence["payload_offset"])
    parent_ptr = int(dictionary.ptrs[parent])
    parent_raw = bytes(dictionary.raw_entry(parent))
    token = bytes(token_from_dict_index(reclaim))
    if parent_raw.hex().upper() != str(occurrence["parent_payload_hex"]):
        raise DuplicateBatchError(f"nested parent payload drifted: {parent:04X}")
    if parent_raw[offset : offset + 2] != token:
        raise DuplicateBatchError(f"nested parent token drifted: {parent:04X}+{offset}")
    local_start = parent_ptr + offset
    local_end = local_start + 2
    aliases: list[dict[str, Any]] = []
    for index in range(dictionary.count):
        try:
            ptr = int(dictionary.ptrs[index])
            raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        if ptr <= local_start and local_end <= ptr + len(raw):
            inner = local_start - ptr
            if raw[inner : inner + 2] != token:
                raise DuplicateBatchError(
                    f"overlapping dictionary alias does not contain reclaim token: {index:04X}"
                )
            aliases.append(
                {
                    "index": f"{index:04X}",
                    "pointer": f"{ptr:04X}",
                    "payload_offset": inner,
                    "payload_hex": raw.hex().upper(),
                }
            )
    if not aliases:
        raise DuplicateBatchError(f"nested write has no owning entry: {parent:04X}")
    return {
        **dict(occurrence),
        "parent_ptr": f"{parent_ptr:04X}",
        "parent_logical": f"{SEG_DICT * BANK_SIZE + parent_ptr:06X}",
        "token_abs": f"{SEG_DICT * BANK_SIZE + local_start:06X}",
        "overlapping_entries": aliases,
    }


def _option_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reclaim_slot": f"{int(row['reclaim_slot']):04X}",
        "keeper_slot": f"{int(row['keeper_slot']):04X}",
        "group_slots": [f"{int(slot):04X}" for slot in row["group_slots"]],
        "rendered": row["rendered"],
        "working_external": len(row["working_external"]),
        "working_nested": len(row["working_nested"]),
        "original_external": len(row["original_external"]),
        "original_nested": len(row["original_nested"]),
        "cost": int(row["cost"]),
        "eligible": bool(row["eligible"]),
        "refusal_reasons": list(row.get("refusal_reasons") or []),
    }


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(args.original_rom))
    parent = bytes(load_rom(args.parent_rom))
    if len(parent) != 16_777_216:
        raise DuplicateBatchError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise DuplicateBatchError("parent 32 KiB SaveRAM is missing")

    det_indices, det_sha, inherited_ranges = load_approved_detachment(
        args.parent_detachment_approval
    )
    parent_sha = _sha256(parent)
    if det_sha != parent_sha:
        raise DuplicateBatchError(
            f"parent detachment approval is bound to {det_sha}, parent is {parent_sha}"
        )
    inherited_document = json.loads(
        args.parent_detachment_approval.read_text(encoding="utf-8")
    )
    keeper_slots = inherited_keeper_slots(inherited_document)

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    stock_count = int(ext_meta["stock_count"])
    tbl = Tbl.load(args.tbl)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)

    short_report = analyze_short_records(_short_analysis_args(args))
    exact_strategy = (short_report.get("strategy_results") or {}).get(
        "existing_exact_two_byte_token"
    ) or {}
    parent_rows = list(exact_strategy.get("record_plan") or [])
    allocated_slots = {
        int(str(row["existing_slot"]), 16) for row in parent_rows
    }
    protected_reclaims = allocated_slots | set(det_indices) | keeper_slots

    groups, candidate_slots = duplicate_groups(
        parent_dictionary,
        stock_count=stock_count,
        protected_slots=protected_reclaims,
    )
    if not groups:
        raise DuplicateBatchError("no duplicate group remains after protection")

    original_external = external_occurrence_map(
        original, ext3_aware=False, wanted=candidate_slots
    )
    working_external = external_occurrence_map(
        parent, ext3_aware=True, wanted=candidate_slots
    )
    original_nested = nested_occurrence_map(
        original_dictionary, wanted=candidate_slots, ext3_aware=False
    )
    working_nested = nested_occurrence_map(
        parent_dictionary, wanted=candidate_slots, ext3_aware=True
    )
    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
        scan_nested=False,
    )

    all_options: list[dict[str, Any]] = []
    selected_internal: list[dict[str, Any]] = []
    for group in groups:
        slots = tuple(int(value) for value in group["slots"])
        raw = bytes(group["raw"])
        try:
            rendered = parent_dictionary.expand(raw, tbl)
        except Exception:
            rendered = ""
        group_options: list[dict[str, Any]] = []
        for reclaim in slots:
            keepers = [slot for slot in slots if slot != reclaim]
            keeper = sorted(
                keepers,
                key=lambda slot: (
                    -len(working_external.get(slot, [])),
                    -len(working_nested.get(slot, [])),
                    slot,
                ),
            )[0]
            current_refs = list(working_external.get(reclaim, []))
            current_parents = list(working_nested.get(reclaim, []))
            historical_refs = list(original_external.get(reclaim, []))
            historical_parents = list(original_nested.get(reclaim, []))
            refusal: list[str] = []
            if reclaim in protected_reclaims:
                refusal.append("protected_inherited_or_allocated_slot")
            if not current_refs:
                refusal.append("no_current_external_consumer")
            if current_parents and not args.allow_nested_parents:
                refusal.append("current_nested_parent_nonzero")
            if len(current_parents) > int(args.max_nested_per_reclaim):
                refusal.append("current_nested_parent_exceeds_bound")
            if bytes(parent_dictionary.raw_entry(reclaim)) != bytes(
                parent_dictionary.raw_entry(keeper)
            ):
                refusal.append("payload_not_identical")
            row = {
                "reclaim_slot": reclaim,
                "keeper_slot": keeper,
                "group_slots": slots,
                "raw": raw,
                "rendered": rendered,
                "working_external": current_refs,
                "working_nested": current_parents,
                "original_external": historical_refs,
                "original_nested": historical_parents,
                "cost": len(current_refs) + len(current_parents),
                "eligible": not refusal,
                "refusal_reasons": refusal,
            }
            group_options.append(row)
            all_options.append(row)
        eligible = [row for row in group_options if row["eligible"]]
        if eligible:
            eligible.sort(
                key=lambda row: (
                    int(row["cost"]),
                    len(row["working_external"]),
                    int(row["reclaim_slot"]),
                )
            )
            selected_internal.append(eligible[0])

    if not selected_internal:
        raise DuplicateBatchError("no zero-nested independent duplicate reclaim remains")
    selected_internal.sort(key=lambda row: (int(row["cost"]), int(row["reclaim_slot"])))

    selected_slots = {int(row["reclaim_slot"]) for row in selected_internal}
    keeper_set = {int(row["keeper_slot"]) for row in selected_internal}
    if selected_slots & keeper_set:
        raise DuplicateBatchError("batch reclaim/keeper sets overlap")

    seen_token_sites: set[int] = set()
    selected_rows: list[dict[str, Any]] = []
    for row in selected_internal:
        reclaim = int(row["reclaim_slot"])
        keeper = int(row["keeper_slot"])
        current_sites = {str(item["token_abs"]) for item in row["working_external"]}
        historical_only = [
            item
            for item in row["original_external"]
            if str(item["token_abs"]) not in current_sites
        ]
        for occurrence in row["working_external"]:
            token_abs = int(str(occurrence["token_abs"]), 16)
            if token_abs in seen_token_sites:
                raise DuplicateBatchError(
                    f"overlapping batch token write at {token_abs:06X}"
                )
            seen_token_sites.add(token_abs)
        audit = union.audit(reclaim)
        union_sites = {
            (f"{item.abs:06X}", str(item.region))
            for item in union.consumers_for(reclaim)
        }
        measured_sites = {
            (str(item["record_abs"]), str(item["region"]))
            for item in row["original_external"] + row["working_external"]
        }
        if union_sites != measured_sites:
            raise DuplicateBatchError(
                f"reference union mismatch for {reclaim:04X}: "
                f"{union_sites ^ measured_sites}"
            )
        selected_rows.append(
            {
                "reclaim_slot": f"{reclaim:04X}",
                "keeper_slot": f"{keeper:04X}",
                "group_slots": [f"{int(slot):04X}" for slot in row["group_slots"]],
                "payload_hex": bytes(row["raw"]).hex().upper(),
                "rendered": row["rendered"],
                "token_before_hex": bytes(token_from_dict_index(reclaim)).hex().upper(),
                "token_after_hex": bytes(token_from_dict_index(keeper)).hex().upper(),
                "cost": int(row["cost"]),
                "reference_union_audit": audit,
                "original_external_occurrences": row["original_external"],
                "working_external_occurrences": row["working_external"],
                "original_only_already_detached": historical_only,
                "original_nested_occurrences": row["original_nested"],
                "working_nested_occurrences": [
                    _nested_write_evidence(
                        parent_dictionary,
                        occurrence,
                        reclaim=reclaim,
                    )
                    for occurrence in row["working_nested"]
                ],
                "raw_pair_non_runtime_hits": raw_pair_non_runtime_hits(
                    parent,
                    index=reclaim,
                    runtime_occurrences=row["working_external"],
                ),
                "proof": {
                    "parent_payloads_byte_identical": True,
                    "parent_rendering_identical": True,
                    "reference_union_matches_exact_occurrences": True,
                    "working_external_occurrences_nonzero": True,
                    "working_nested_occurrences_within_bound": len(row["working_nested"])
                    <= int(args.max_nested_per_reclaim),
                    "historical_consumers_accounted": True,
                    "batch_token_write_unique": True,
                },
            }
        )

    values, _sources = load_reviewed_values(args.values_dir)
    baseline_rows = _baseline_rows(args.base_manifest)
    excluded_ids = {str(row["record_id"]) for row in parent_rows}
    allocations, record_plan = build_true_free_plan(
        baseline_rows,
        values,
        excluded_record_ids=excluded_ids,
        slots=tuple(int(row["reclaim_slot"], 16) for row in selected_rows),
        tbl=tbl,
    )
    if len(allocations) != len(selected_rows) or not record_plan:
        raise DuplicateBatchError(
            "not every safe reclaimed slot received an encodable remaining phrase"
        )

    current_remaining = (short_report.get("strategy_results") or {}).get(
        "remaining"
    ) or {}
    remaining_records = int(current_remaining.get("records") or 0) - len(record_plan)
    remaining_phrases = int(current_remaining.get("unique_phrases") or 0) - len(
        allocations
    )

    report = {
        "generated_by": "tools/analyze_p2_duplicate_batch.py",
        "analysis_mode": (
            "bounded_nested_parent_batch"
            if args.allow_nested_parents
            else "zero_nested_batch"
        ),
        "read_only": True,
        "rom_written": False,
        "inputs": {
            "original_rom": _identity(args.original_rom, original),
            "parent_rom": _identity(args.parent_rom, parent),
            "parent_save": _identity(args.parent_save),
            "parent_detachment_approval": _identity(
                args.parent_detachment_approval
            ),
            "base_manifest": _identity(args.base_manifest),
            "tbl": _identity(args.tbl),
            "ext_meta": _identity(args.ext_meta),
            "ext3_meta": _identity(args.ext3_meta),
        },
        "current_p2_state": {
            "resolved_records": int(exact_strategy.get("records") or 0),
            "resolved_phrases": int(exact_strategy.get("unique_phrases") or 0),
            "remaining_records": int(current_remaining.get("records") or 0),
            "remaining_phrases": int(current_remaining.get("unique_phrases") or 0),
            "parent_exact_record_plan": parent_rows,
        },
        "protection": {
            "allocated_or_approved_slots": [
                f"{slot:04X}" for slot in sorted(allocated_slots | set(det_indices))
            ],
            "inherited_keeper_slots": [
                f"{slot:04X}" for slot in sorted(keeper_slots)
            ],
            "inherited_detachment_ranges": [
                {
                    "logical_start": f"{lo:06X}",
                    "logical_end_exclusive": f"{hi:06X}",
                    "owner_id": owner,
                }
                for lo, hi, owner in inherited_ranges
            ],
        },
        "duplicate_inventory": {
            "groups": len(groups),
            "options": [
                _option_summary(row)
                for row in sorted(
                    all_options,
                    key=lambda item: (int(item["cost"]), int(item["reclaim_slot"])),
                )
            ],
            "safe_independent_groups": len(selected_rows),
        },
        "selected_groups": selected_rows,
        "batch_proof": {
            "one_reclaim_per_payload_group": True,
            "reclaim_keeper_sets_disjoint": True,
            "token_write_sites_nonoverlapping": True,
            "all_reclaims_nested_parents_within_bound": all(
                len(row.get("working_nested_occurrences") or [])
                <= int(args.max_nested_per_reclaim)
                for row in selected_rows
            ),
            "inherited_keeper_slots_protected": True,
            "inherited_owned_slots_protected": True,
        },
        "allocation": {
            "status": "GO_read_only_batch_plan",
            "allocations": allocations,
            "record_plan": record_plan,
            "slots": len(allocations),
            "records": len(record_plan),
            "unique_phrases": len(allocations),
        },
        "remaining": {
            "records": remaining_records,
            "unique_phrases": remaining_phrases,
            "nested_duplicate_groups": (
                "included_with_exact_dictionary_parent_retarget_contract"
                if args.allow_nested_parents
                else "deferred_requires_dictionary_parent_retarget_contract"
            ),
            "inherited_keeper_reclaim": "deferred_requires_superseding_detachment_chain_contract",
            "sole_far_pointer_relocation": "NO_GO_without_caller_XREF_and_runtime_read_evidence",
            "local_one_byte_expansion": "NO_GO_without_next_record_and_event_stream_contract",
        },
        "decision": {
            "status": (
                "partial_go_bounded_nested_duplicate_batch"
                if args.allow_nested_parents
                else "partial_go_zero_nested_duplicate_batch"
            ),
            "candidate_generation_allowed": True,
            "rom_write_performed": False,
            "safe_groups": len(selected_rows),
            "safe_slots": len(allocations),
            "new_records": len(record_plan),
            "next_safe_step": (
                "Build one cumulative batch candidate, verify all old references "
                "reach zero in a shared detachment-only state, bind cumulative "
                "stock/range ownership to the candidate SHA-256, then run the "
                "full static gate set once."
            ),
        },
    }
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
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--allow-nested-parents",
        action="store_true",
        help="allow exact size-preserving keeper retargets inside dictionary parent payloads",
    )
    parser.add_argument(
        "--max-nested-per-reclaim",
        type=int,
        default=0,
        help="maximum current nested parent occurrences allowed for one reclaim slot",
    )
    parser.add_argument("--stdout", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() in {".wsc", ".sav"}:
        raise SystemExit("refusing ROM/SaveRAM output: this tool is read-only")
    report = analyze(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.stdout:
        print(payload, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8", newline="\n")
        print(
            json.dumps(
                {
                    "report": str(args.out),
                    "status": report["decision"]["status"],
                    "safe_groups": report["decision"]["safe_groups"],
                    "safe_slots": report["decision"]["safe_slots"],
                    "new_records": report["decision"]["new_records"],
                    "remaining_records": report["remaining"]["records"],
                    "rom_written": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
