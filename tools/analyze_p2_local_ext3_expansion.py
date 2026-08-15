#!/usr/bin/env python3
"""Read-only P2 plan for one-byte local body expansion into an existing NUL gap.

A proven short dialogue row with a three-byte body may use the already-installed
four-byte ``E5 18 xx yy`` ext3 token only when the Original-derived next-record
boundary leaves exactly one extra NUL byte after the current terminator.  The
old terminator becomes the fourth token byte; the pre-existing gap NUL becomes
the new terminator.  The following record start is never moved or overwritten.

This analyzer is deliberately strict and never writes a ROM or SaveRAM.
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

from analyze_p2_duplicate_detachment import _baseline_rows  # noqa: E402
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
    load_reviewed_values,
)
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    _reference_scopes,
    _walk_zstring_range,
    build_free_slot_inventory,
    build_reference_union,
)
from monoeye_rom import Tbl, load_rom, stock_base  # noqa: E402
from normalize_ko_text import try_encode_ko_text  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from patch_3byte_dict_token import (  # noqa: E402
    DEFAULT_NUM_BANKS,
    EXP3_SEG0,
    bank_local_for_index,
    token_from_ext3_index,
)
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_nested_duplicate_batch_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_nested_duplicate_batch_candidate.sav"
DEFAULT_PARENT_APPROVAL = ROOT / "out/patch/p2_nested_duplicate_batch_approval.json"
DEFAULT_OUT = ROOT / "out/patch/p2_local_ext3_expansion_capacity_report.json"


class LocalExpansionError(RuntimeError):
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


def _known_zstring_starts(rom: bytes) -> set[int]:
    starts: set[int] = set()
    for region, lo, hi, max_len in _reference_scopes():
        for logical, _payload, _kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            starts.add(logical)
    return starts


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(args.original_rom))
    parent = bytes(load_rom(args.parent_rom))
    if len(parent) != 16_777_216:
        raise LocalExpansionError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise LocalExpansionError("parent SaveRAM is missing or not 32 KiB")
    _indices, approval_sha, _ranges = load_approved_detachment(args.parent_approval)
    parent_sha = _sha256(parent)
    if approval_sha != parent_sha:
        raise LocalExpansionError(
            f"parent approval is bound to {approval_sha}, parent is {parent_sha}"
        )

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    num_banks = int(ext3_meta.get("num_banks") or DEFAULT_NUM_BANKS)
    tbl = Tbl.load(args.tbl)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    short_report = analyze_short_records(_short_args(args))
    exact = (short_report.get("strategy_results") or {}).get("existing_exact_two_byte_token") or {}
    parent_rows = list(exact.get("record_plan") or [])
    resolved_ids = {str(row["record_id"]) for row in parent_rows}

    values, _sources = load_reviewed_values(args.values_dir)
    baseline_rows = _baseline_rows(args.base_manifest)
    all_manifest = json.loads(args.base_manifest.read_text(encoding="utf-8"))
    known_manifest_starts = {
        int(row.get("logical_address") or -1)
        for bucket in ("included", "excluded")
        for row in ((all_manifest.get("population") or {}).get(bucket) or [])
        if isinstance(row, Mapping)
    }
    original_starts = _known_zstring_starts(original)
    parent_starts = _known_zstring_starts(parent)
    osb = stock_base(original)
    psb = stock_base(parent)

    refusal_counts: Counter[str] = Counter()
    eligible: list[dict[str, Any]] = []
    for row in baseline_rows:
        record_id = str(row.get("record_id") or "")
        if record_id in resolved_ids:
            continue
        logical = int(row.get("logical_address") or 0)
        boundary = row.get("boundary") or {}
        capacity = int(boundary.get("payload_capacity") or 0)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        body_span = capacity - len(prefix)
        terminator = int(boundary.get("terminator_offset") or -1)
        next_start_raw = boundary.get("next_record_start")
        reasons: list[str] = []
        if str(row.get("region") or "") != "script":
            reasons.append("not_script")
        if body_span != 3:
            reasons.append("body_not_three")
        if next_start_raw is None:
            reasons.append("next_record_unknown")
            next_start = -1
        else:
            next_start = int(next_start_raw)
            if next_start != terminator + 2:
                reasons.append("not_exactly_one_gap_byte")
        original_payload = original[osb + logical : osb + logical + capacity]
        parent_payload = parent[psb + logical : psb + logical + capacity]
        if len(original_payload) != capacity or len(parent_payload) != capacity:
            reasons.append("payload_out_of_range")
        elif original_payload[: len(prefix)] != prefix or parent_payload[: len(prefix)] != prefix:
            reasons.append("prefix_drift")
        original_body = original_payload[len(prefix) :]
        if body_span == 3 and looks_like_event_body(original_body):
            reasons.append("event_like_body")
        if terminator < 0 or terminator + 1 >= 0x800000:
            reasons.append("terminator_out_of_range")
        else:
            if original[osb + terminator] != 0 or parent[psb + terminator] != 0:
                reasons.append("current_terminator_not_nul")
            if original[osb + terminator + 1] != 0 or parent[psb + terminator + 1] != 0:
                reasons.append("gap_byte_not_stable_nul")
        if terminator in known_manifest_starts or terminator + 1 in known_manifest_starts:
            reasons.append("terminator_or_gap_is_manifest_record_start")
        if terminator in original_starts or terminator in parent_starts:
            reasons.append("old_terminator_is_known_zstring_start")
        target = values.get(record_id)
        if not target:
            reasons.append("missing_reviewed_target")
            encoded = None
        else:
            encoded = try_encode_ko_text(
                target,
                tbl,
                hangul_marker_code=marker_code(),
                hangul_marker_mode="run",
            )
            if encoded is None:
                reasons.append("target_encode_failed")
            elif b"\x00" in encoded:
                reasons.append("target_payload_contains_nul")
        if reasons:
            for reason in set(reasons):
                refusal_counts[reason] += 1
            continue
        eligible.append(
            {
                "record_id": record_id,
                "abs": f"{logical:06X}",
                "region": "script",
                "target_ko": target,
                "encoded_payload_hex": bytes(encoded).hex().upper(),
                "prefix_hex": prefix.hex().upper(),
                "old_payload_hex": parent_payload.hex().upper(),
                "old_body_hex": parent_payload[len(prefix) :].hex().upper(),
                "old_capacity": capacity,
                "old_terminator": f"{terminator:06X}",
                "gap_byte": f"{terminator + 1:06X}",
                "next_record_start": f"{next_start:06X}",
                "new_capacity": capacity + 1,
                "new_terminator": f"{terminator + 1:06X}",
                "boundary_proof": {
                    "original_terminator_nul": True,
                    "parent_terminator_nul": True,
                    "original_gap_nul": True,
                    "parent_gap_nul": True,
                    "next_record_start_unchanged": True,
                    "old_terminator_not_known_record_start": True,
                    "gap_not_manifest_record_start": True,
                    "event_like_body": False,
                },
            }
        )

    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    inventory = build_free_slot_inventory(
        parent,
        union=union,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )

    existing_payload: dict[bytes, int] = {}
    for index in range(0x1000, dictionary.count):
        try:
            raw = bytes(dictionary.raw_entry(index))
            token_from_ext3_index(index, num_banks=num_banks)
        except Exception:
            continue
        if raw:
            existing_payload.setdefault(raw, index)

    grouped: dict[bytes, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        grouped[bytes.fromhex(str(row["encoded_payload_hex"]))].append(row)
    phrases = sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), min(int(row["abs"], 16) for row in item[1])),
    )

    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in inventory.ext3_free:
        seg, _local = bank_local_for_index(index)
        free_by_bank[seg - EXP3_SEG0].append(index)
    for values_in_bank in free_by_bank.values():
        values_in_bank.sort()
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}

    assignments: dict[bytes, tuple[int, bool]] = {}
    new_slot_payload: dict[int, bytes] = {}
    allocation_failures: list[dict[str, Any]] = []
    for payload, rows in phrases:
        existing = existing_payload.get(payload)
        if existing is not None:
            assignments[payload] = (existing, False)
            continue
        need = len(payload) + 1
        chosen_bank = next(
            (
                bank
                for bank in sorted(room, key=lambda b: (-room[b], b))
                if room.get(bank, 0) >= need and free_by_bank.get(bank)
            ),
            None,
        )
        if chosen_bank is None:
            allocation_failures.append(
                {
                    "target_ko": rows[0]["target_ko"],
                    "records": len(rows),
                    "bytes_needed": need,
                }
            )
            continue
        index = free_by_bank[chosen_bank].pop(0)
        room[chosen_bank] -= need
        assignments[payload] = (index, True)
        new_slot_payload[index] = payload

    record_plan: list[dict[str, Any]] = []
    allocation_rows: list[dict[str, Any]] = []
    for payload, rows in phrases:
        assigned = assignments.get(payload)
        if assigned is None:
            continue
        index, write_required = assigned
        token = token_from_ext3_index(index, num_banks=num_banks)
        ids: list[str] = []
        for source in sorted(rows, key=lambda item: int(item["abs"], 16)):
            prefix = bytes.fromhex(str(source["prefix_hex"]))
            rewrite = prefix + token
            ids.append(str(source["record_id"]))
            record_plan.append(
                {
                    **source,
                    "ext3_index": f"{index:05X}",
                    "token_hex": token.hex().upper(),
                    "rewrite_payload_hex": rewrite.hex().upper(),
                    "write_required": write_required,
                    "approved_extent": {
                        "start": source["abs"],
                        "end_exclusive": f"{int(source['old_terminator'], 16) + 1:06X}",
                        "old_terminator": source["old_terminator"],
                        "new_terminator": source["new_terminator"],
                        "next_record_start": source["next_record_start"],
                    },
                }
            )
        allocation_rows.append(
            {
                "ext3_index": f"{index:05X}",
                "target_ko": rows[0]["target_ko"],
                "encoded_payload_hex": payload.hex().upper(),
                "write_required": write_required,
                "records": ids,
                "record_count": len(ids),
            }
        )

    current_remaining = (short_report.get("strategy_results") or {}).get("remaining") or {}
    planned_records = len(record_plan)
    planned_phrases = len(allocation_rows)
    remaining_records = int(current_remaining.get("records") or 0) - planned_records
    remaining_phrases = int(current_remaining.get("unique_phrases") or 0) - planned_phrases

    return {
        "generated_by": "tools/analyze_p2_local_ext3_expansion.py",
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
        "boundary_inventory": {
            "remaining_rows_scanned": len(baseline_rows) - len(resolved_ids),
            "eligible_exact_one_nul_gap_rows": len(eligible),
            "refusal_counts": dict(sorted(refusal_counts.items())),
            "contract": {
                "body_span": 3,
                "region": "script",
                "next_record_start_equals_old_terminator_plus_two": True,
                "old_terminator_and_gap_are_stable_nul_in_original_and_parent": True,
                "following_record_start_not_moved": True,
                "old_terminator_not_known_record_start": True,
                "event_like_body_rejected": True,
            },
        },
        "storage": {
            "ext3_free_slots": len(inventory.ext3_free),
            "ext3_room_before": {
                f"{EXP3_SEG0 + bank:02X}": value
                for bank, value in sorted(inventory.ext3_bank_room.items())
            },
            "ext3_room_after_plan": {
                f"{EXP3_SEG0 + bank:02X}": value for bank, value in sorted(room.items())
            },
            "new_slots": len(new_slot_payload),
            "reused_slots": sum(1 for _index, write in assignments.values() if not write),
            "new_slot_payloads": {
                f"{index:05X}": payload.hex().upper()
                for index, payload in sorted(new_slot_payload.items())
            },
            "allocation_failures": allocation_failures,
        },
        "allocation": {
            "status": "GO_read_only_local_ext3_plan" if record_plan and not allocation_failures else "NO_GO_incomplete_local_ext3_plan",
            "allocations": allocation_rows,
            "record_plan": record_plan,
            "records": planned_records,
            "unique_phrases": planned_phrases,
        },
        "remaining": {
            "records": remaining_records,
            "unique_phrases": remaining_phrases,
            "body2_records": 8,
            "gapless_body3_records": max(0, int(current_remaining.get("records") or 0) - planned_records - 8),
            "next_step": "gapless records require pointer relocation or a separately proven stream indirection contract",
        },
        "decision": {
            "status": "partial_go_local_ext3_one_nul_gap" if record_plan and not allocation_failures else "no_go_local_ext3_one_nul_gap",
            "candidate_generation_allowed": bool(record_plan) and not allocation_failures,
            "new_records": planned_records,
            "new_phrases": planned_phrases,
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
                    "new_records": report["decision"]["new_records"],
                    "new_phrases": report["decision"]["new_phrases"],
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
