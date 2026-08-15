#!/usr/bin/env python3
"""Read-only P2-1 duplicate-payload detachment analysis.

This stage looks for a non-FF stock dictionary slot whose payload is byte-for-
byte identical to another live slot.  It may be reclaimed only when every
current external consumer can be retargeted size-preservingly to the keeper,
every historical Original consumer is accounted for, and no current nested
parent phrase still embeds the reclaim token.

The tool never writes a ROM or SaveRAM.  Its first implementation deliberately
selects only one lowest-cost stock group with zero current nested parents.  That
keeps the proof small enough to audit and avoids turning duplicate consolidation
into a broad shared-dictionary rewrite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_short_records import (  # noqa: E402
    DEFAULT_BASE_MANIFEST,
    DEFAULT_EXT3_META,
    DEFAULT_EXT_META,
    DEFAULT_ORIGINAL_ROM,
    DEFAULT_SHEET,
    DEFAULT_TBL,
    DEFAULT_VALUES_DIR,
    FIXED_ROSTER_HI,
    FIXED_ROSTER_LO,
    SHORT_REASON_BASE,
    analyze as analyze_short_records,
    build_parser as build_short_parser,
    build_true_free_plan,
    load_reviewed_values,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    _reference_scopes,
    _walk_zstring_range,
    build_reference_union,
    is_ff_page_index,
    iter_token_refs_with_offsets,
)
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    token_from_dict_index,
)

DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_stock_spill_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_stock_spill_candidate.sav"
DEFAULT_OUT = ROOT / "out/patch/p2_duplicate_detachment_capacity_report.json"


class DuplicateDetachmentError(RuntimeError):
    """Raised when duplicate-detachment evidence cannot be trusted."""


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


def external_occurrence_map(
    rom: bytes,
    *,
    ext3_aware: bool,
    wanted: set[int] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Exact external token positions using the selected runtime precedence."""
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            for index, length, offset in iter_token_refs_with_offsets(
                payload, ext3_aware=ext3_aware
            ):
                if length != 2 or (wanted is not None and index not in wanted):
                    continue
                out[index].append(
                    {
                        "record_abs": f"{logical:06X}",
                        "token_abs": f"{logical + offset:06X}",
                        "payload_offset": offset,
                        "region": region,
                        "kind": kind,
                        "payload_hex": payload.hex().upper(),
                        "token_hex": payload[offset : offset + 2].hex().upper(),
                    }
                )
    return dict(out)


def raw_pair_non_runtime_hits(
    rom: bytes,
    *,
    index: int,
    runtime_occurrences: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Raw byte-pair hits that the patched parser correctly does not treat as tokens."""
    token = bytes(token_from_dict_index(index))
    runtime_sites = {int(str(row["token_abs"]), 16) for row in runtime_occurrences}
    out: list[dict[str, Any]] = []
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            start = 0
            while True:
                offset = payload.find(token, start)
                if offset < 0:
                    break
                token_abs = logical + offset
                if token_abs not in runtime_sites:
                    out.append(
                        {
                            "record_abs": f"{logical:06X}",
                            "token_abs": f"{token_abs:06X}",
                            "payload_offset": offset,
                            "region": region,
                            "kind": kind,
                            "payload_hex": payload.hex().upper(),
                            "reason": "raw_pair_not_runtime_two_byte_token",
                        }
                    )
                start = offset + 1
    return out


def nested_occurrence_map(
    dictionary: Dictionary,
    *,
    wanted: set[int],
    ext3_aware: bool,
) -> dict[int, list[dict[str, Any]]]:
    """Targeted full-dictionary nested scan, including ext3 phrase banks."""
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in range(dictionary.count):
        try:
            raw = bytes(dictionary.raw_entry(parent))
        except Exception:
            continue
        if not raw:
            continue
        for child, length, offset in iter_token_refs_with_offsets(
            raw, ext3_aware=ext3_aware
        ):
            if length != 2 or child not in wanted or child == parent:
                continue
            out[child].append(
                {
                    "parent": f"{parent:04X}",
                    "payload_offset": offset,
                    "token_hex": raw[offset : offset + 2].hex().upper(),
                    "parent_payload_hex": raw.hex().upper(),
                }
            )
    return dict(out)


def duplicate_groups(
    dictionary: Dictionary,
    *,
    stock_count: int,
    protected_slots: set[int],
) -> tuple[list[dict[str, Any]], set[int]]:
    grouped: dict[bytes, list[int]] = defaultdict(list)
    for index in range(min(stock_count, 0xF00)):
        if is_ff_page_index(index) or not dict_token_safe_in_zstring(index):
            continue
        try:
            raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        if raw:
            grouped[raw].append(index)
    groups: list[dict[str, Any]] = []
    candidates: set[int] = set()
    for raw, slots in grouped.items():
        if len(slots) < 2:
            continue
        reclaimable = [slot for slot in slots if slot not in protected_slots]
        if not reclaimable:
            continue
        candidates.update(slots)
        groups.append(
            {
                "raw": raw,
                "slots": tuple(sorted(slots)),
                "reclaimable": tuple(sorted(reclaimable)),
            }
        )
    return groups, candidates


def _baseline_rows(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    excluded = (document.get("population") or {}).get("excluded") or []
    return [
        row
        for row in excluded
        if isinstance(row, dict)
        and str(row.get("reason") or "").startswith(SHORT_REASON_BASE + ":")
        and not (
            FIXED_ROSTER_LO
            <= int(row.get("logical_address") or 0)
            <= FIXED_ROSTER_HI
        )
    ]


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(args.original_rom))
    parent = bytes(load_rom(args.parent_rom))
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise DuplicateDetachmentError("parent 32 KiB SaveRAM is missing")

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
    protected_slots = {
        int(str(row["existing_slot"]), 16)
        for row in exact_strategy.get("record_plan") or []
    }

    groups, candidate_slots = duplicate_groups(
        parent_dictionary,
        stock_count=stock_count,
        protected_slots=protected_slots,
    )
    if not groups:
        raise DuplicateDetachmentError("no non-FF stock duplicate group remains")

    original_external = external_occurrence_map(
        original, ext3_aware=False, wanted=candidate_slots
    )
    working_external = external_occurrence_map(
        parent, ext3_aware=True, wanted=candidate_slots
    )
    original_nested = nested_occurrence_map(
        original_dictionary,
        wanted=candidate_slots,
        ext3_aware=False,
    )
    working_nested = nested_occurrence_map(
        parent_dictionary,
        wanted=candidate_slots,
        ext3_aware=True,
    )

    options: list[dict[str, Any]] = []
    for group in groups:
        raw = bytes(group["raw"])
        slots = tuple(int(value) for value in group["slots"])
        try:
            rendered = parent_dictionary.expand(raw, tbl)
        except Exception:
            rendered = ""
        for reclaim in group["reclaimable"]:
            reclaim = int(reclaim)
            keepers = [slot for slot in slots if slot != reclaim]
            keeper = sorted(
                keepers,
                key=lambda slot: (
                    -len(working_external.get(slot, [])),
                    -len(working_nested.get(slot, [])),
                    slot,
                ),
            )[0]
            current_refs = working_external.get(reclaim, [])
            current_parents = working_nested.get(reclaim, [])
            historical_refs = original_external.get(reclaim, [])
            historical_parents = original_nested.get(reclaim, [])
            current_sites = {str(row["token_abs"]) for row in current_refs}
            historical_only = [
                row
                for row in historical_refs
                if str(row["token_abs"]) not in current_sites
            ]
            options.append(
                {
                    "reclaim_slot": reclaim,
                    "keeper_slot": keeper,
                    "group_slots": slots,
                    "raw": raw,
                    "rendered": rendered,
                    "working_external": current_refs,
                    "working_nested": current_parents,
                    "original_external": historical_refs,
                    "original_nested": historical_parents,
                    "historical_only_external": historical_only,
                    "cost": len(current_refs) + len(current_parents),
                    "eligible": bool(current_refs)
                    and not current_parents
                    and bytes(parent_dictionary.raw_entry(reclaim))
                    == bytes(parent_dictionary.raw_entry(keeper)),
                }
            )

    eligible = [row for row in options if row["eligible"]]
    if not eligible:
        raise DuplicateDetachmentError(
            "no duplicate slot has external consumers and zero current nested parents"
        )
    eligible.sort(
        key=lambda row: (
            int(row["cost"]),
            len(row["working_nested"]),
            len(row["working_external"]),
            int(row["reclaim_slot"]),
        )
    )
    selected = eligible[0]
    reclaim = int(selected["reclaim_slot"])
    keeper = int(selected["keeper_slot"])

    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
        scan_nested=False,
    )
    union_audit = union.audit(reclaim)
    union_sites = {
        (str(row["abs"]), str(row["region"])) for row in union_audit["consumers"]
    }
    measured_sites = {
        (str(row["record_abs"]), str(row["region"]))
        for row in selected["original_external"] + selected["working_external"]
    }
    if union_sites != measured_sites:
        raise DuplicateDetachmentError(
            f"exact occurrence scan does not match reference union: {union_sites ^ measured_sites}"
        )

    values, _sources = load_reviewed_values(args.values_dir)
    rows = _baseline_rows(args.base_manifest)
    excluded_ids = {
        str(row["record_id"]) for row in exact_strategy.get("record_plan") or []
    }
    allocations, record_plan = build_true_free_plan(
        rows,
        values,
        excluded_record_ids=excluded_ids,
        slots=(reclaim,),
        tbl=tbl,
    )
    if len(allocations) != 1 or not record_plan:
        raise DuplicateDetachmentError("reclaimed slot has no encodable target phrase")

    current_remaining = (short_report.get("strategy_results") or {}).get(
        "remaining"
    ) or {}
    remaining_records = int(current_remaining.get("records") or 0) - len(record_plan)
    remaining_phrases = int(current_remaining.get("unique_phrases") or 0) - 1
    non_runtime_hits = raw_pair_non_runtime_hits(
        parent,
        index=reclaim,
        runtime_occurrences=selected["working_external"],
    )

    option_rows: list[dict[str, Any]] = []
    for row in sorted(options, key=lambda item: (item["cost"], item["reclaim_slot"])):
        option_rows.append(
            {
                "reclaim_slot": f"{int(row['reclaim_slot']):04X}",
                "keeper_slot": f"{int(row['keeper_slot']):04X}",
                "group_slots": [f"{int(slot):04X}" for slot in row["group_slots"]],
                "rendered": row["rendered"],
                "working_external": len(row["working_external"]),
                "working_nested": len(row["working_nested"]),
                "original_external": len(row["original_external"]),
                "original_nested": len(row["original_nested"]),
                "cost": row["cost"],
                "eligible": row["eligible"],
            }
        )

    report = {
        "generated_by": "tools/analyze_p2_duplicate_detachment.py",
        "read_only": True,
        "rom_written": False,
        "inputs": {
            "original_rom": _identity(args.original_rom, original),
            "parent_rom": _identity(args.parent_rom, parent),
            "parent_save": _identity(args.parent_save),
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
            "parent_exact_record_plan": list(
                exact_strategy.get("record_plan") or []
            ),
        },
        "duplicate_inventory": {
            "groups": len(groups),
            "options": option_rows,
        },
        "selected": {
            "reclaim_slot": f"{reclaim:04X}",
            "keeper_slot": f"{keeper:04X}",
            "group_slots": [
                f"{int(slot):04X}" for slot in selected["group_slots"]
            ],
            "payload_hex": bytes(selected["raw"]).hex().upper(),
            "rendered": selected["rendered"],
            "token_before_hex": bytes(token_from_dict_index(reclaim)).hex().upper(),
            "token_after_hex": bytes(token_from_dict_index(keeper)).hex().upper(),
            "reference_union_audit": union_audit,
            "original_external_occurrences": selected["original_external"],
            "working_external_occurrences": selected["working_external"],
            "original_only_already_detached": selected["historical_only_external"],
            "original_nested_occurrences": selected["original_nested"],
            "working_nested_occurrences": selected["working_nested"],
            "raw_pair_non_runtime_hits": non_runtime_hits,
            "proof": {
                "parent_payloads_byte_identical": True,
                "parent_rendering_identical": True,
                "reference_union_matches_exact_occurrences": True,
                "working_external_occurrences_nonzero": bool(
                    selected["working_external"]
                ),
                "working_nested_occurrences_zero": not selected["working_nested"],
                "historical_consumers_accounted": True,
                "ext3_tail_false_hits_excluded": True,
            },
        },
        "allocation": {
            "status": "GO_read_only_plan",
            "allocations": allocations,
            "record_plan": record_plan,
            "records": len(record_plan),
            "unique_phrases": 1,
        },
        "remaining": {
            "records": remaining_records,
            "unique_phrases": remaining_phrases,
            "pair_steal": "NO_GO_beyond_single_lowest_cost_duplicate_until_candidate_gates_pass",
            "sole_far_pointer_relocation": "NO_GO_without_caller_XREF_and_runtime_read_evidence",
            "local_one_byte_expansion": "NO_GO_without_next_record_and_event_stream_contract",
        },
        "decision": {
            "status": "partial_go_single_duplicate_detachment",
            "candidate_generation_allowed": True,
            "rom_write_performed": False,
            "next_safe_step": (
                "Build one cumulative duplicate-detachment candidate, bind the "
                "detachment and stock-pointer allowances to its SHA-256, and run "
                "the full static gate set."
            ),
        },
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    parser.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    parser.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
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
                    "reclaim_slot": report["selected"]["reclaim_slot"],
                    "keeper_slot": report["selected"]["keeper_slot"],
                    "new_records": report["allocation"]["records"],
                    "rom_written": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


__all__ = [
    "DuplicateDetachmentError",
    "analyze",
    "build_parser",
    "external_occurrence_map",
    "nested_occurrence_map",
    "raw_pair_non_runtime_hits",
]


if __name__ == "__main__":
    raise SystemExit(main())
