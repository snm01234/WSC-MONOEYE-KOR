#!/usr/bin/env python3
"""Read-only inventory for semantically identical stock dictionary entries.

Unlike byte-duplicate analysis, this stage groups non-FF stock entries by their
fully expanded code/text rendering.  A reclaim is considered only when its raw
payload differs from the keeper but every current external and bounded nested
consumer can be retargeted size-preservingly to a two-byte keeper token while
preserving the complete enclosing rendering.

No ROM or SaveRAM is written.
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

from analyze_p2_duplicate_batch import inherited_keeper_slots  # noqa: E402
from analyze_p2_duplicate_detachment import (  # noqa: E402
    _baseline_rows,
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
from mixed_residual_reference_union import build_reference_union, is_ff_page_index  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    token_from_dict_index,
)
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_nested_duplicate_batch_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_nested_duplicate_batch_candidate.sav"
DEFAULT_PARENT_APPROVAL = ROOT / "out/patch/p2_nested_duplicate_batch_approval.json"
DEFAULT_OUT = ROOT / "out/patch/p2_semantic_duplicate_batch_capacity_report.json"


class SemanticDuplicateError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": _sha256(payload)}


def _short_args(args: argparse.Namespace) -> argparse.Namespace:
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


def _nested_evidence(dictionary: Dictionary, occurrence: Mapping[str, Any], reclaim: int) -> dict[str, Any]:
    parent = int(str(occurrence["parent"]), 16)
    offset = int(occurrence["payload_offset"])
    ptr = int(dictionary.ptrs[parent])
    raw = bytes(dictionary.raw_entry(parent))
    token = bytes(token_from_dict_index(reclaim))
    if raw.hex().upper() != str(occurrence["parent_payload_hex"]):
        raise SemanticDuplicateError(f"nested parent payload drift: {parent:04X}")
    if raw[offset : offset + 2] != token:
        raise SemanticDuplicateError(f"nested token drift: {parent:04X}+{offset}")
    local = ptr + offset
    aliases: list[dict[str, Any]] = []
    for index in range(dictionary.count):
        try:
            other_ptr = int(dictionary.ptrs[index])
            other_raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        if other_ptr <= local and local + 2 <= other_ptr + len(other_raw):
            inner = local - other_ptr
            if other_raw[inner : inner + 2] != token:
                raise SemanticDuplicateError(f"alias token mismatch: {index:04X}")
            aliases.append(
                {
                    "index": f"{index:04X}",
                    "pointer": f"{other_ptr:04X}",
                    "payload_offset": inner,
                    "payload_hex": other_raw.hex().upper(),
                }
            )
    return {
        **dict(occurrence),
        "parent_ptr": f"{ptr:04X}",
        "parent_logical": f"{SEG_DICT * BANK_SIZE + ptr:06X}",
        "token_abs": f"{SEG_DICT * BANK_SIZE + local:06X}",
        "overlapping_entries": aliases,
    }


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(args.original_rom))
    parent = bytes(load_rom(args.parent_rom))
    if len(parent) != 16_777_216:
        raise SemanticDuplicateError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise SemanticDuplicateError("parent SaveRAM is missing or not 32 KiB")

    approved_indices, approved_sha, approved_ranges = load_approved_detachment(args.parent_approval)
    parent_sha = _sha256(parent)
    if approved_sha != parent_sha:
        raise SemanticDuplicateError(
            f"parent approval is bound to {approved_sha}, parent is {parent_sha}"
        )
    approval_doc = json.loads(args.parent_approval.read_text(encoding="utf-8"))
    inherited_keepers = inherited_keeper_slots(approval_doc)

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    stock_count = int(ext_meta["stock_count"])
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    tbl = Tbl.load(args.tbl)

    short_report = analyze_short_records(_short_args(args))
    exact = (short_report.get("strategy_results") or {}).get("existing_exact_two_byte_token") or {}
    parent_rows = list(exact.get("record_plan") or [])
    allocated = {int(str(row["existing_slot"]), 16) for row in parent_rows}
    protected = allocated | set(approved_indices) | inherited_keepers

    by_render: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index in range(min(stock_count, 0xF00)):
        if is_ff_page_index(index) or not dict_token_safe_in_zstring(index):
            continue
        try:
            raw = bytes(dictionary.raw_entry(index))
            rendered_codes = dictionary.expand(raw, as_codes=True)
            rendered_text = dictionary.expand(raw, tbl)
        except Exception:
            continue
        if not raw or not rendered_codes or "<BADDICT" in rendered_codes or "<TRUNC" in rendered_codes:
            continue
        by_render[rendered_codes].append(
            {
                "index": index,
                "raw": raw,
                "rendered_codes": rendered_codes,
                "rendered_text": rendered_text,
            }
        )

    groups = [
        rows
        for rows in by_render.values()
        if len(rows) >= 2 and len({bytes(row["raw"]) for row in rows}) >= 2
    ]
    candidate_slots = {int(row["index"]) for rows in groups for row in rows}
    original_external = external_occurrence_map(original, ext3_aware=False, wanted=candidate_slots)
    working_external = external_occurrence_map(parent, ext3_aware=True, wanted=candidate_slots)
    original_nested = nested_occurrence_map(original_dictionary, wanted=candidate_slots, ext3_aware=False)
    working_nested = nested_occurrence_map(dictionary, wanted=candidate_slots, ext3_aware=True)
    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
        scan_nested=False,
    )

    options: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    used_keepers: set[int] = set()
    for rows in groups:
        slots = sorted(int(row["index"]) for row in rows)
        raw_by_index = {int(row["index"]): bytes(row["raw"]) for row in rows}
        text = str(rows[0]["rendered_text"])
        codes = str(rows[0]["rendered_codes"])
        group_options: list[dict[str, Any]] = []
        for reclaim in slots:
            keeper_candidates = [slot for slot in slots if slot != reclaim]
            keeper = sorted(
                keeper_candidates,
                key=lambda slot: (
                    slot in protected,
                    len(working_external.get(slot, [])) + len(working_nested.get(slot, [])),
                    -slot,
                ),
                reverse=True,
            )[0]
            current_external = list(working_external.get(reclaim, []))
            current_nested = list(working_nested.get(reclaim, []))
            refusal: list[str] = []
            if reclaim in protected:
                refusal.append("protected_inherited_or_allocated_slot")
            if raw_by_index[reclaim] == raw_by_index[keeper]:
                refusal.append("raw_payload_already_identical")
            if not current_external and not current_nested:
                refusal.append("no_current_consumer")
            if len(current_nested) > int(args.max_nested_per_reclaim):
                refusal.append("nested_parent_exceeds_bound")
            if keeper in used_keepers:
                refusal.append("keeper_already_used_by_selected_group")
            row = {
                "reclaim_slot": reclaim,
                "keeper_slot": keeper,
                "group_slots": slots,
                "reclaim_raw": raw_by_index[reclaim],
                "keeper_raw": raw_by_index[keeper],
                "rendered_codes": codes,
                "rendered_text": text,
                "working_external": current_external,
                "working_nested": current_nested,
                "original_external": list(original_external.get(reclaim, [])),
                "original_nested": list(original_nested.get(reclaim, [])),
                "cost": len(current_external) + len(current_nested),
                "eligible": not refusal,
                "refusal_reasons": refusal,
            }
            group_options.append(row)
            options.append(row)
        eligible = [row for row in group_options if row["eligible"]]
        if not eligible:
            continue
        eligible.sort(key=lambda row: (int(row["cost"]), int(row["reclaim_slot"])))
        chosen = eligible[0]
        selected.append(chosen)
        used_keepers.add(int(chosen["keeper_slot"]))

    selected.sort(key=lambda row: (int(row["cost"]), int(row["reclaim_slot"])))
    reclaims = {int(row["reclaim_slot"]) for row in selected}
    keepers = {int(row["keeper_slot"]) for row in selected}
    if reclaims & keepers:
        raise SemanticDuplicateError("selected semantic reclaim/keeper sets overlap")

    token_sites: set[int] = set()
    selected_rows: list[dict[str, Any]] = []
    for row in selected:
        reclaim = int(row["reclaim_slot"])
        keeper = int(row["keeper_slot"])
        measured_sites = {
            (str(item["record_abs"]), str(item["region"]))
            for item in row["original_external"] + row["working_external"]
        }
        union_sites = {(f"{item.abs:06X}", str(item.region)) for item in union.consumers_for(reclaim)}
        if measured_sites != union_sites:
            raise SemanticDuplicateError(
                f"reference union mismatch for {reclaim:04X}: {measured_sites ^ union_sites}"
            )
        for occurrence in row["working_external"]:
            site = int(str(occurrence["token_abs"]), 16)
            if site in token_sites:
                raise SemanticDuplicateError(f"overlapping external token site: {site:06X}")
            token_sites.add(site)
        nested = [
            _nested_evidence(dictionary, occurrence, reclaim)
            for occurrence in row["working_nested"]
        ]
        for occurrence in nested:
            site = int(str(occurrence["token_abs"]), 16)
            if site in token_sites:
                raise SemanticDuplicateError(f"overlapping nested token site: {site:06X}")
            token_sites.add(site)
        current_sites = {str(item["token_abs"]) for item in row["working_external"]}
        selected_rows.append(
            {
                "reclaim_slot": f"{reclaim:04X}",
                "keeper_slot": f"{keeper:04X}",
                "group_slots": [f"{slot:04X}" for slot in row["group_slots"]],
                "reclaim_payload_hex": bytes(row["reclaim_raw"]).hex().upper(),
                "keeper_payload_hex": bytes(row["keeper_raw"]).hex().upper(),
                "rendered_codes": row["rendered_codes"],
                "rendered_text": row["rendered_text"],
                "token_before_hex": bytes(token_from_dict_index(reclaim)).hex().upper(),
                "token_after_hex": bytes(token_from_dict_index(keeper)).hex().upper(),
                "cost": int(row["cost"]),
                "reference_union_audit": union.audit(reclaim),
                "original_external_occurrences": row["original_external"],
                "working_external_occurrences": row["working_external"],
                "original_only_already_detached": [
                    item for item in row["original_external"]
                    if str(item["token_abs"]) not in current_sites
                ],
                "original_nested_occurrences": row["original_nested"],
                "working_nested_occurrences": nested,
                "raw_pair_non_runtime_hits": raw_pair_non_runtime_hits(
                    parent,
                    index=reclaim,
                    runtime_occurrences=row["working_external"],
                ),
                "proof": {
                    "raw_payloads_differ": bytes(row["reclaim_raw"]) != bytes(row["keeper_raw"]),
                    "expanded_code_render_identical": True,
                    "reference_union_matches_exact_occurrences": True,
                    "nested_within_bound": len(nested) <= int(args.max_nested_per_reclaim),
                    "token_sites_unique": True,
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
    if selected_rows and len(allocations) != len(selected_rows):
        raise SemanticDuplicateError("not every selected slot received an allocation")

    current_remaining = (short_report.get("strategy_results") or {}).get("remaining") or {}
    remaining_records = int(current_remaining.get("records") or 0) - len(record_plan)
    remaining_phrases = int(current_remaining.get("unique_phrases") or 0) - len(allocations)

    option_rows = [
        {
            "reclaim_slot": f"{int(row['reclaim_slot']):04X}",
            "keeper_slot": f"{int(row['keeper_slot']):04X}",
            "rendered_text": row["rendered_text"],
            "working_external": len(row["working_external"]),
            "working_nested": len(row["working_nested"]),
            "cost": int(row["cost"]),
            "eligible": bool(row["eligible"]),
            "refusal_reasons": list(row["refusal_reasons"]),
        }
        for row in sorted(options, key=lambda item: (int(item["cost"]), int(item["reclaim_slot"])))
    ]

    return {
        "generated_by": "tools/analyze_p2_semantic_duplicate_batch.py",
        "read_only": True,
        "rom_written": False,
        "inputs": {
            "original_rom": _identity(args.original_rom, original),
            "parent_rom": _identity(args.parent_rom, parent),
            "parent_save": _identity(args.parent_save),
            "parent_approval": _identity(args.parent_approval),
            "base_manifest": _identity(args.base_manifest),
            "tbl": _identity(args.tbl),
            "ext_meta": _identity(args.ext_meta),
            "ext3_meta": _identity(args.ext3_meta),
        },
        "current_p2_state": {
            "resolved_records": int(exact.get("records") or 0),
            "resolved_phrases": int(exact.get("unique_phrases") or 0),
            "remaining_records": int(current_remaining.get("records") or 0),
            "remaining_phrases": int(current_remaining.get("unique_phrases") or 0),
            "parent_exact_record_plan": parent_rows,
        },
        "protection": {
            "approved_or_allocated_slots": [f"{slot:04X}" for slot in sorted(allocated | set(approved_indices))],
            "inherited_keeper_slots": [f"{slot:04X}" for slot in sorted(inherited_keepers)],
            "approved_ranges": len(approved_ranges),
        },
        "semantic_inventory": {
            "render_groups_with_distinct_raw_payloads": len(groups),
            "options": option_rows,
            "selected_groups": len(selected_rows),
        },
        "selected_groups": selected_rows,
        "batch_proof": {
            "raw_payloads_distinct_but_render_equal": all(
                row["proof"]["raw_payloads_differ"] and row["proof"]["expanded_code_render_identical"]
                for row in selected_rows
            ),
            "reclaim_keeper_sets_disjoint": not (reclaims & keepers),
            "token_sites_nonoverlapping": True,
            "protected_slots_not_reclaimed": not (reclaims & protected),
            "nested_parent_bound": all(
                len(row.get("working_nested_occurrences") or []) <= int(args.max_nested_per_reclaim)
                for row in selected_rows
            ),
        },
        "allocation": {
            "status": "GO_read_only_semantic_batch_plan" if selected_rows else "NO_GO_no_safe_semantic_duplicate",
            "allocations": allocations,
            "record_plan": record_plan,
            "slots": len(allocations),
            "records": len(record_plan),
            "unique_phrases": len(allocations),
        },
        "remaining": {
            "records": remaining_records,
            "unique_phrases": remaining_phrases,
            "semantic_duplicate_groups_remaining": "none_selected" if not selected_rows else "selected_batch_requires_candidate_gates",
            "far_pointer_relocation": "NO_GO_without_caller_XREF_and_runtime_read_evidence",
            "local_one_byte_expansion": "NO_GO_without_next_record_and_event_stream_contract",
        },
        "decision": {
            "status": "partial_go_semantic_duplicate_batch" if selected_rows else "no_go_semantic_duplicate_batch",
            "candidate_generation_allowed": bool(selected_rows),
            "safe_groups": len(selected_rows),
            "safe_slots": len(allocations),
            "new_records": len(record_plan),
            "rom_write_performed": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    parser.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    parser.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    parser.add_argument("--parent-approval", type=Path, default=DEFAULT_PARENT_APPROVAL)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--max-nested-per-reclaim", type=int, default=1)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stdout", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() in {".wsc", ".sav"}:
        raise SystemExit("refusing ROM/SaveRAM output: read-only analyzer")
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
