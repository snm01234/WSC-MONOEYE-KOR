#!/usr/bin/env python3
"""Read-only capacity analysis for candidate-bound retired stock-slot reclaim.

A retired slot is not union-true-free: the Original ROM used it, but the current
candidate has no runtime external consumer and no nested dictionary parent.  This
analysis admits only the strongest subset:

* stock, non-FF and NUL-safe two-byte token;
* not owned by the cumulative parent approval;
* Original has external consumers but no nested parent;
* current candidate has neither external nor nested consumers;
* Original and current pointer/payload are byte-identical;
* the raw token byte pair does not occur anywhere in the current script/name75/
  aux scan scopes, even in a non-runtime context.

The selected slots are therefore unchanged dead dictionary entries whose entire
historical consumer set has already migrated away.  This tool writes JSON only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import (  # noqa: E402
    _baseline_rows,
    _reference_scopes,
    external_occurrence_map,
    nested_occurrence_map,
)
from analyze_p2_short_records import (  # noqa: E402
    DEFAULT_BASE_MANIFEST,
    DEFAULT_EXT3_META,
    DEFAULT_EXT_META,
    DEFAULT_ORIGINAL_ROM,
    DEFAULT_SHEET,
    DEFAULT_TBL,
    DEFAULT_VALUES_DIR,
    build_true_free_plan,
    load_reviewed_values,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_p2_stock_spill_candidate import _stock_phrase_cursor  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from mixed_residual_reference_union import is_ff_page_index  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    token_from_dict_index,
)
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_local_ext3_expansion_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_local_ext3_expansion_candidate.sav"
DEFAULT_PARENT_APPROVAL = ROOT / "out/patch/p2_local_ext3_expansion_approval.json"
DEFAULT_PARENT_REPORT = ROOT / "out/patch/p2_local_ext3_expansion_report.json"
DEFAULT_OUT = ROOT / "out/patch/p2_retired_slot_reclaim_capacity_report.json"


class RetiredSlotAnalysisError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size": len(payload),
        "sha256": _sha256(payload),
    }


def _raw_pair_hits(
    rom: bytes,
    indices: Sequence[int],
) -> dict[int, list[dict[str, Any]]]:
    """Find every raw selected token pair in the current approved scan scopes."""
    pair_to_index = {bytes(token_from_dict_index(index)): index for index in indices}
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            for offset in range(max(0, len(payload) - 1)):
                index = pair_to_index.get(payload[offset : offset + 2])
                if index is None:
                    continue
                out[index].append(
                    {
                        "record_abs": f"{logical:06X}",
                        "token_abs": f"{logical + offset:06X}",
                        "region": region,
                        "kind": kind,
                        "payload_offset": offset,
                        "payload_hex": payload.hex().upper(),
                    }
                )
    return dict(out)


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(args.original_rom))
    parent = bytes(load_rom(args.parent_rom))
    if len(parent) != 16_777_216:
        raise RetiredSlotAnalysisError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise RetiredSlotAnalysisError("same-stem 32 KiB SaveRAM is missing")

    protected_slots, approved_sha, inherited_ranges = load_approved_detachment(
        args.parent_approval
    )
    parent_sha = _sha256(parent)
    if approved_sha != parent_sha:
        raise RetiredSlotAnalysisError(
            f"parent approval is bound to {approved_sha}, parent is {parent_sha}"
        )

    parent_report = json.loads(args.parent_report.read_text(encoding="utf-8"))
    if parent_report.get("accepted") is not True:
        raise RetiredSlotAnalysisError("parent report is not accepted")
    resolved_targets = list(parent_report.get("targets") or [])
    resolved_ids = {str(row["record_id"]) for row in resolved_targets}

    remaining_rows = [
        row
        for row in _baseline_rows(args.base_manifest)
        if str(row["record_id"]) not in resolved_ids
    ]
    values, value_sources = load_reviewed_values(args.values_dir)
    missing_values = [
        str(row["record_id"])
        for row in remaining_rows
        if not values.get(str(row["record_id"]))
    ]
    if missing_values:
        raise RetiredSlotAnalysisError(
            f"remaining rows lack reviewed Korean: {missing_values[:8]}"
        )
    phrases = {
        values[str(row["record_id"])]
        for row in remaining_rows
    }

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    original_dictionary = Dictionary(original)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stock_count = int(original_dictionary.stock_count)
    wanted = {
        index
        for index in range(min(stock_count, 0xF00))
        if dict_token_safe_in_zstring(index) and not is_ff_page_index(index)
    }

    original_external = external_occurrence_map(
        original, ext3_aware=False, wanted=wanted
    )
    parent_external = external_occurrence_map(
        parent, ext3_aware=True, wanted=wanted
    )
    original_nested = nested_occurrence_map(
        original_dictionary, wanted=wanted, ext3_aware=False
    )
    parent_nested = nested_occurrence_map(
        parent_dictionary, wanted=wanted, ext3_aware=True
    )

    preliminary: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    for index in sorted(wanted):
        if index in protected_slots:
            rejection_counts["parent_approval_owned"] += 1
            continue
        if parent_external.get(index):
            rejection_counts["current_external_consumer"] += 1
            continue
        if parent_nested.get(index):
            rejection_counts["current_nested_parent"] += 1
            continue
        if original_nested.get(index):
            rejection_counts["original_nested_parent"] += 1
            continue
        historical = list(original_external.get(index) or [])
        if not historical:
            rejection_counts["not_historically_used"] += 1
            continue
        try:
            original_payload = bytes(original_dictionary.raw_entry(index))
            parent_payload = bytes(parent_dictionary.raw_entry(index))
        except Exception:
            rejection_counts["dictionary_read_failed"] += 1
            continue
        if not parent_payload:
            rejection_counts["empty_parent_payload"] += 1
            continue
        if original_dictionary.ptrs[index] != parent_dictionary.ptrs[index]:
            rejection_counts["pointer_changed_since_original"] += 1
            continue
        if original_payload != parent_payload:
            rejection_counts["payload_changed_since_original"] += 1
            continue
        preliminary.append(
            {
                "slot": index,
                "pointer": parent_dictionary.ptrs[index],
                "payload": parent_payload,
                "historical_external": historical,
            }
        )

    raw_hits = _raw_pair_hits(parent, [row["slot"] for row in preliminary])
    strong = [row for row in preliminary if not raw_hits.get(row["slot"])]
    strong.sort(
        key=lambda row: (
            len(row["historical_external"]),
            int(row["slot"]),
        )
    )

    required_slots = len(phrases)
    if len(strong) < required_slots:
        raise RetiredSlotAnalysisError(
            f"need {required_slots} strong retired slots, found {len(strong)}"
        )
    selected = strong[:required_slots]
    selected_indices = [int(row["slot"]) for row in selected]

    tbl = Tbl.load(args.tbl)
    allocations, record_plan = build_true_free_plan(
        remaining_rows,
        values,
        excluded_record_ids=set(),
        slots=selected_indices,
        tbl=tbl,
    )
    if len(allocations) != required_slots:
        raise RetiredSlotAnalysisError(
            f"allocation count {len(allocations)} != phrase count {required_slots}"
        )
    if len(record_plan) != len(remaining_rows):
        raise RetiredSlotAnalysisError(
            f"record plan {len(record_plan)} != remaining rows {len(remaining_rows)}"
        )
    if {str(row["record_id"]) for row in record_plan} != {
        str(row["record_id"]) for row in remaining_rows
    }:
        raise RetiredSlotAnalysisError("record plan does not exactly cover remaining IDs")

    phrase_start = _stock_phrase_cursor(parent)
    required_bytes = sum(
        len(bytes.fromhex(str(row["encoded_payload_hex"]))) + 1
        for row in allocations
    )
    if phrase_start + required_bytes > BANK_SIZE:
        raise RetiredSlotAnalysisError("bank-5F tail capacity is insufficient")

    selected_report: list[dict[str, Any]] = []
    selected_by_index = {int(row["slot"]): row for row in selected}
    for allocation in allocations:
        index = int(str(allocation["slot"]), 16)
        evidence = selected_by_index[index]
        selected_report.append(
            {
                "slot": f"{index:04X}",
                "old_pointer": f"{int(evidence['pointer']):04X}",
                "old_payload_hex": bytes(evidence["payload"]).hex().upper(),
                "historical_external_count": len(evidence["historical_external"]),
                "historical_external_occurrences": evidence["historical_external"],
                "current_external_count": 0,
                "original_nested_count": 0,
                "current_nested_count": 0,
                "current_raw_pair_hits": 0,
                "original_parent_pointer_equal": True,
                "original_parent_payload_equal": True,
                "new_phrase": str(allocation["target_ko"]),
                "new_record_count": int(allocation["record_count"]),
            }
        )

    body_counts = Counter(int(row["body_span"]) for row in record_plan)
    return {
        "generated_by": "tools/analyze_p2_retired_slot_reclaim.py",
        "read_only": True,
        "rom_written": False,
        "inputs": {
            "original_rom": _identity(args.original_rom, original),
            "parent_rom": _identity(args.parent_rom, parent),
            "parent_save": _identity(args.parent_save),
            "parent_approval": _identity(args.parent_approval),
            "parent_report": _identity(args.parent_report),
            "base_manifest": _identity(args.base_manifest),
            "tbl": _identity(args.tbl),
            "ext_meta": _identity(args.ext_meta),
            "ext3_meta": _identity(args.ext3_meta),
            "reviewed_value_sources": value_sources,
        },
        "current_state": {
            "resolved_records": len(resolved_targets),
            "remaining_records": len(remaining_rows),
            "remaining_unique_phrases": required_slots,
            "body_span_counts": {
                str(key): value for key, value in sorted(body_counts.items())
            },
            "corrected_previous_phrase_count": {
                "reported": int((parent_report.get("remaining") or {}).get("unique_phrases") or 0),
                "recomputed": required_slots,
                "reason": "stage-wise phrase subtraction ignored phrases shared by resolved and unresolved records",
            },
        },
        "retired_inventory": {
            "stock_non_ff_safe_slots_scanned": len(wanted),
            "preliminary_original_pointer_payload_equal": len(preliminary),
            "strong_raw_pair_clean_slots": len(strong),
            "selected_slots": len(selected_indices),
            "selected_historical_external_refs": sum(
                len(row["historical_external"]) for row in selected
            ),
            "rejection_counts": dict(sorted(rejection_counts.items())),
            "selection_policy": (
                "lowest historical external-ref count, then lowest slot index; "
                "Original/current pointer+payload identical, no Original nested, "
                "no current external/nested/raw-pair occurrence"
            ),
            "protected_parent_slots": [
                f"{index:04X}" for index in sorted(protected_slots)
            ],
            "inherited_detachment_ranges": len(inherited_ranges),
            "selected": selected_report,
        },
        "allocation": {
            "allocations": allocations,
            "record_plan": record_plan,
            "records": len(record_plan),
            "unique_phrases": len(allocations),
        },
        "storage": {
            "bank": "5F",
            "phrase_start": f"{phrase_start:04X}",
            "phrase_end_projected": f"{phrase_start + required_bytes:04X}",
            "required_bytes": required_bytes,
            "free_before": BANK_SIZE - phrase_start,
            "free_after_projected": BANK_SIZE - phrase_start - required_bytes,
            "full_dictionary_rebuild": False,
            "ff_page_written": False,
        },
        "decision": {
            "status": "GO_retired_stock_slot_reclaim_all_remaining",
            "candidate_generation_allowed": True,
            "new_records": len(record_plan),
            "new_phrases": len(allocations),
            "remaining_records_after": 0,
            "rom_write_performed": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    ap.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    ap.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    ap.add_argument("--parent-approval", type=Path, default=DEFAULT_PARENT_APPROVAL)
    ap.add_argument("--parent-report", type=Path, default=DEFAULT_PARENT_REPORT)
    ap.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    ap.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() in {".wsc", ".sav"}:
        raise SystemExit("refusing ROM/SaveRAM output")
    report = analyze(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "report": str(args.out),
                "status": report["decision"]["status"],
                "strong_slots": report["retired_inventory"]["strong_raw_pair_clean_slots"],
                "selected_slots": report["retired_inventory"]["selected_slots"],
                "new_records": report["decision"]["new_records"],
                "new_phrases": report["decision"]["new_phrases"],
                "remaining_records_after": 0,
                "rom_written": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
