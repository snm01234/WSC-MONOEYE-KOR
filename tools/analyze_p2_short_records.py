#!/usr/bin/env python3
"""Read-only P2-1 capacity analysis for 2-3 byte record bodies.

This tool never writes a ROM.  It rebinds the structurally proven
``excluded_shared_token_body_capacity`` population to the current main TIP,
reconciles the old broad ``too_short`` metric with the current guarded script
sheet, then evaluates the two lowest-risk P2 strategies: reuse of an existing
2-byte dictionary token whose expanded text exactly matches the reviewed Korean,
and allocation of a union-proven true-free non-FF extended 2-byte slot.

Pair-steal, far-pointer relocation and local record expansion are intentionally
reported as NO-GO here.  They require separate complete consumer/caller proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from mixed_residual_discovery import validate_manifest_digest  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
    is_ff_page_index,
    iter_token_refs,
)
from monoeye_rom import (  # noqa: E402
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
)
from normalize_ko_text import (  # noqa: E402
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)

DEFAULT_ORIGINAL_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_WORKING_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_BASE_MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"
DEFAULT_VALUES_DIR = ROOT / "data/mixed_residual_values"
DEFAULT_SHEET = ROOT / "out/script/translations_apply_all.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_BASE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
DEFAULT_OUT = ROOT / "out/patch/p2_short_record_capacity_report.json"

SHORT_REASON_BASE = "excluded_shared_token_body_capacity"
SAFE_SCRIPT_LO = 0x604571
SAFE_SCRIPT_HI = 0x63FFFF
FIXED_ROSTER_LO = 0x61E403
FIXED_ROSTER_HI = 0x61F480

_REASON_RE = re.compile(
    r"^excluded_shared_token_body_capacity:body=(?P<body>\d+),"
    r"slot=(?P<slot>[0-9A-Fa-f]{4}|none)"
    r"(?:,(?P<detail>.*))?$"
)


class P2AnalysisError(RuntimeError):
    """Raised when the read-only evidence cannot be trusted."""


@dataclass(frozen=True)
class ShortReason:
    body_span: int
    slot: int | None
    detail: str | None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size": len(payload),
        "sha256": _sha256(payload),
    }


def parse_short_reason(reason: str) -> ShortReason:
    match = _REASON_RE.fullmatch(str(reason or ""))
    if match is None:
        raise P2AnalysisError(f"malformed short-record reason: {reason!r}")
    slot_text = match.group("slot")
    return ShortReason(
        body_span=int(match.group("body")),
        slot=None if slot_text == "none" else int(slot_text, 16),
        detail=match.group("detail") or None,
    )


def load_reviewed_values(values_dir: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    values: dict[str, str] = {}
    sources: list[dict[str, Any]] = []
    paths = sorted(values_dir.glob("*.json"))
    if not paths:
        raise P2AnalysisError(f"no reviewed value files found in {values_dir}")
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        entries = document.get("entries") or {}
        if not isinstance(entries, Mapping):
            raise P2AnalysisError(f"values entries must be an object: {path}")
        for record_id, raw in entries.items():
            value = raw.get("ko") if isinstance(raw, Mapping) else raw
            ko = normalize_ko_text(str(value or ""))
            if not ko:
                continue
            previous = values.get(str(record_id))
            if previous is not None and previous != ko:
                raise P2AnalysisError(
                    f"conflicting reviewed values for {record_id}: {previous!r} != {ko!r}"
                )
            values[str(record_id)] = ko
        sources.append(_identity(path))
    return values, sources


def build_exact_phrase_index(dictionary: Any, tbl: Tbl) -> dict[str, tuple[int, ...]]:
    phrases: dict[str, list[int]] = defaultdict(list)
    for index in range(min(int(dictionary.count), 0x1000)):
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            raw = dictionary.raw_entry(index)
            rendered = dictionary.expand(raw, tbl).rstrip("\u3000")
        except Exception:
            continue
        if rendered:
            phrases[rendered].append(index)
    return {text: tuple(indices) for text, indices in phrases.items()}


def make_rewrite_payload(prefix: bytes, index: int, payload_capacity: int) -> bytes:
    token = bytes(token_from_dict_index(index))
    body_room = payload_capacity - len(prefix)
    if body_room not in (2, 3):
        raise P2AnalysisError(f"short-record body must be 2 or 3 bytes, got {body_room}")
    if len(token) != 2:
        raise P2AnalysisError(f"expected a 2-byte token for {index:04X}")
    return prefix + token + (b"\x01" * (body_room - len(token)))


def build_true_free_plan(
    rows: Sequence[Mapping[str, Any]],
    values: Mapping[str, str],
    *,
    excluded_record_ids: set[str],
    slots: Sequence[int],
    tbl: Tbl,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Allocate true-free non-FF 2-byte slots to the highest-impact phrases."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        record_id = str(row.get("record_id") or "")
        if record_id in excluded_record_ids:
            continue
        ko = values.get(record_id)
        if ko:
            grouped[ko].append(row)

    encodable: list[tuple[str, list[Mapping[str, Any]], bytes]] = []
    for ko, target_rows in grouped.items():
        encoded = try_encode_ko_text(
            ko,
            tbl,
            hangul_marker_code=marker_code(),
            hangul_marker_mode="run",
        )
        if encoded is None or b"\x00" in encoded:
            continue
        encodable.append((ko, target_rows, bytes(encoded)))
    encodable.sort(
        key=lambda item: (
            -len(item[1]),
            min(int(row.get("logical_address") or 0) for row in item[1]),
            item[0],
        )
    )

    allocations: list[dict[str, Any]] = []
    record_plan: list[dict[str, Any]] = []
    for slot, (ko, target_rows, encoded) in zip(slots, encodable):
        if is_ff_page_index(slot) or not dict_token_safe_in_zstring(slot):
            raise P2AnalysisError(f"unsafe true-free slot reached planner: {slot:04X}")
        allocation_records: list[str] = []
        for row in sorted(target_rows, key=lambda item: int(item["logical_address"])):
            record_id = str(row["record_id"])
            logical = int(row["logical_address"])
            boundary = row.get("boundary") or {}
            capacity = int(boundary.get("payload_capacity") or 0)
            terminator = int(boundary.get("terminator_offset") or -1)
            prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
            rewritten = make_rewrite_payload(prefix, slot, capacity)
            allocation_records.append(record_id)
            record_plan.append(
                {
                    "record_id": record_id,
                    "abs": f"{logical:06X}",
                    "region": str(row.get("region") or "?"),
                    "body_span": capacity - len(prefix),
                    "target_ko": ko,
                    "slot": f"{slot:04X}",
                    "token_hex": bytes(token_from_dict_index(slot)).hex().upper(),
                    "rewrite_payload_hex": rewritten.hex().upper(),
                    "approved_extent": {
                        "start": f"{logical:06X}",
                        "end_exclusive": f"{logical + capacity:06X}",
                        "terminator": f"{terminator:06X}",
                        "terminator_written": False,
                    },
                }
            )
        allocations.append(
            {
                "slot": f"{slot:04X}",
                "target_ko": ko,
                "encoded_payload_hex": encoded.hex().upper(),
                "records": allocation_records,
                "record_count": len(allocation_records),
            }
        )
    return allocations, record_plan


def _manifest_working_sha(manifest: Mapping[str, Any]) -> str | None:
    working = (manifest.get("inputs") or {}).get("working_rom") or {}
    sha = working.get("sha256")
    return str(sha) if sha else None


def _manifest_original_sha(manifest: Mapping[str, Any]) -> str | None:
    original = (manifest.get("inputs") or {}).get("original_rom") or {}
    sha = original.get("sha256")
    return str(sha) if sha else None


def _load_sheet_rows(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, list):
        return [row for row in document if isinstance(row, dict)]
    rows = document.get("lines") or []
    if not isinstance(rows, list):
        raise P2AnalysisError(f"sheet lines must be a list: {path}")
    return [row for row in rows if isinstance(row, dict)]


def summarize_guarded_sheet_short_records(
    original_rom: bytes, rows: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    sb = stock_base(original_rom)
    body_counts: Counter[int] = Counter()
    eligible = 0
    for row in rows:
        try:
            logical = int(str(row.get("abs") or ""), 16)
        except ValueError:
            continue
        if not (SAFE_SCRIPT_LO <= logical <= SAFE_SCRIPT_HI):
            continue
        if FIXED_ROSTER_LO <= logical <= FIXED_ROSTER_HI:
            continue
        ko = normalize_ko_text(str(row.get("ko") or ""))
        if not ko or is_low_quality_ko(ko):
            continue
        got = read_encoded_z_safe(original_rom, sb + logical)
        if got is None:
            continue
        prefix, _body, _kind = split_prefix_body(got[0])
        eligible += 1
        body_counts[len(got[0]) - len(prefix)] += 1
    short = {str(span): body_counts[span] for span in sorted(body_counts) if span < 4}
    return {
        "definition": {
            "sheet": "translations_apply_all",
            "logical_band": [f"{SAFE_SCRIPT_LO:06X}", f"{SAFE_SCRIPT_HI:06X}"],
            "fixed_roster_excluded": [f"{FIXED_ROSTER_LO:06X}", f"{FIXED_ROSTER_HI:06X}"],
            "requires_nonempty_non_low_quality_reviewed_ko": True,
            "record_boundary_source": "pristine_original_read_encoded_z_safe+split_prefix_body",
        },
        "eligible_records": eligible,
        "too_short_records": sum(body_counts[span] for span in body_counts if span < 4),
        "body_span_counts": short,
    }


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(args.original_rom))
    working = bytes(load_rom(args.working_rom))
    manifest = json.loads(args.base_manifest.read_text(encoding="utf-8"))
    if not validate_manifest_digest(manifest):
        raise P2AnalysisError("baseline manifest digest is stale or malformed")

    expected_original = _manifest_original_sha(manifest)
    if expected_original and expected_original.lower() != _sha256(original):
        raise P2AnalysisError("original ROM does not match the baseline manifest")

    values, value_sources = load_reviewed_values(args.values_dir)
    population = manifest.get("population") or {}
    excluded = population.get("excluded") or []
    baseline_rows = [
        row
        for row in excluded
        if isinstance(row, Mapping)
        and str(row.get("reason") or "").startswith(SHORT_REASON_BASE + ":")
    ]
    if not baseline_rows:
        raise P2AnalysisError("baseline manifest contains no proven short-record rows")

    # The mixed-residual manifest predates the fixed-roster hard exclusion that
    # now protects 61:E403-61:F480 as non-dialogue data.  Keep those historical
    # rows visible in the report, but never admit them to a P2 rewrite plan.
    current_policy_excluded = [
        row
        for row in baseline_rows
        if FIXED_ROSTER_LO <= int(row.get("logical_address") or 0) <= FIXED_ROSTER_HI
    ]
    rows = [row for row in baseline_rows if row not in current_policy_excluded]

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    dictionary = make_dictionary(working, ext_meta)
    tbl = Tbl.load(args.tbl)
    phrase_index = build_exact_phrase_index(dictionary, tbl)
    sb_original = stock_base(original)
    sb_working = stock_base(working)

    region_counts: Counter[str] = Counter()
    body_counts: Counter[int] = Counter()
    blocker_counts: Counter[str] = Counter()
    current_body_shape_counts: Counter[str] = Counter()
    exact_rows: list[dict[str, Any]] = []
    exact_phrases: dict[str, int] = {}
    rebound = 0

    for row in rows:
        record_id = str(row.get("record_id") or "")
        logical = int(row.get("logical_address"))
        boundary = row.get("boundary") or {}
        capacity = int(boundary.get("payload_capacity") or 0)
        terminator = int(boundary.get("terminator_offset") or -1)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        reason = parse_short_reason(str(row.get("reason") or ""))

        original_payload = original[sb_original + logical : sb_original + logical + capacity]
        current_payload = working[sb_working + logical : sb_working + logical + capacity]
        if _sha256(original_payload) != str(row.get("original_payload_sha256") or ""):
            raise P2AnalysisError(f"original payload digest mismatch: {record_id}")
        if terminator != logical + capacity:
            raise P2AnalysisError(f"boundary terminator mismatch: {record_id}")
        if original[sb_original + terminator] != 0 or working[sb_working + terminator] != 0:
            raise P2AnalysisError(f"terminator is not preserved in Original/TIP: {record_id}")
        if original_payload[: len(prefix)] != prefix or current_payload[: len(prefix)] != prefix:
            raise P2AnalysisError(f"prefix is not preserved in Original/TIP: {record_id}")
        body_span = capacity - len(prefix)
        if body_span != reason.body_span or body_span not in (2, 3):
            raise P2AnalysisError(f"short-record body mismatch: {record_id}")

        original_body = original_payload[len(prefix) :]
        original_tokens = [
            index for index, length in iter_token_refs(original_body) if length == 2
        ]
        if reason.slot is not None:
            if len(original_tokens) != 1 or original_tokens[0] != reason.slot:
                raise P2AnalysisError(f"original token does not match evidence: {record_id}")

        # The P0/P1 lineage may already have retargeted a short record away from
        # its Original slot while preserving its Japanese meaning.  Rebinding to
        # the current TIP therefore verifies the current record shape, not slot
        # identity with the historical manifest.
        current_body = current_payload[len(prefix) :]
        current_tokens = [
            index for index, length in iter_token_refs(current_body) if length == 2
        ]
        current_body_shape_counts[
            "single_two_byte_token" if len(current_tokens) == 1 else "direct_or_non_token_body"
        ] += 1

        ko = values.get(record_id)
        if not ko:
            raise P2AnalysisError(f"missing reviewed Korean value: {record_id}")

        region = str(row.get("region") or "?")
        region_counts[region] += 1
        body_counts[body_span] += 1
        if reason.detail:
            blocker_counts[reason.detail.split("=", 1)[0]] += 1
        else:
            blocker_counts["unspecified"] += 1
        rebound += 1

        candidates = phrase_index.get(ko, ())
        if not candidates:
            continue
        selected = candidates[0]
        rewritten = make_rewrite_payload(prefix, selected, capacity)
        exact_phrases.setdefault(ko, selected)
        exact_rows.append(
            {
                "record_id": record_id,
                "abs": f"{logical:06X}",
                "region": region,
                "body_span": body_span,
                "target_ko": ko,
                "existing_slot": f"{selected:04X}",
                "all_exact_slots": [f"{index:04X}" for index in candidates],
                "token_hex": bytes(token_from_dict_index(selected)).hex().upper(),
                "rewrite_payload_hex": rewritten.hex().upper(),
                "approved_extent": {
                    "start": f"{logical:06X}",
                    "end_exclusive": f"{logical + capacity:06X}",
                    "terminator": f"{terminator:06X}",
                    "terminator_written": False,
                },
            }
        )

    union = build_reference_union(
        original,
        working,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    inventory = build_free_slot_inventory(
        working,
        union=union,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    safe_ext_slots = tuple(
        index
        for index in inventory.ext_free
        if not is_ff_page_index(index) and dict_token_safe_in_zstring(index)
    )
    safe_stock_slots = tuple(
        index
        for index in inventory.stock_free
        if not is_ff_page_index(index) and dict_token_safe_in_zstring(index)
    )
    exact_record_ids = {str(row["record_id"]) for row in exact_rows}
    true_free_allocations, true_free_rows = build_true_free_plan(
        rows,
        values,
        excluded_record_ids=exact_record_ids,
        slots=safe_ext_slots,
        tbl=tbl,
    )
    ext_record_ids = {str(row["record_id"]) for row in true_free_rows}
    stock_allocations, stock_rows = build_true_free_plan(
        rows,
        values,
        excluded_record_ids=exact_record_ids | ext_record_ids,
        slots=safe_stock_slots,
        tbl=tbl,
    )

    unique_targets = {values[str(row.get("record_id"))] for row in rows}
    exact_target_phrases = set(exact_phrases)
    true_free_target_phrases = {
        str(allocation["target_ko"]) for allocation in true_free_allocations
    }
    stock_target_phrases = {
        str(allocation["target_ko"]) for allocation in stock_allocations
    }
    remaining_records = (
        len(rows) - len(exact_rows) - len(true_free_rows) - len(stock_rows)
    )
    remaining_phrases = len(
        unique_targets
        - exact_target_phrases
        - true_free_target_phrases
        - stock_target_phrases
    )
    sheet_summary = summarize_guarded_sheet_short_records(
        original, _load_sheet_rows(args.sheet)
    )

    current_sha = _sha256(working)
    old_manifest_tip = _manifest_working_sha(manifest)
    base_save_exists = args.base_save.exists()
    report: dict[str, Any] = {
        "generated_by": "tools/analyze_p2_short_records.py",
        "read_only": True,
        "rom_written": False,
        "inputs": {
            "original_rom": _identity(args.original_rom, original),
            "working_rom": _identity(args.working_rom, working),
            "baseline_manifest": _identity(args.base_manifest),
            "baseline_manifest_sha256": manifest.get("manifest_sha256"),
            "baseline_manifest_working_rom_sha256": old_manifest_tip,
            "reviewed_value_sources": value_sources,
            "sheet": _identity(args.sheet),
            "tbl": _identity(args.tbl),
            "ext_meta": _identity(args.ext_meta),
            "ext3_meta": _identity(args.ext3_meta),
        },
        "current_tip_rebind": {
            "tip_sha256": current_sha,
            "manifest_tip_changed": bool(old_manifest_tip and old_manifest_tip != current_sha),
            "historical_manifest_short_records": len(baseline_rows),
            "excluded_by_current_fixed_roster_policy": len(current_policy_excluded),
            "fixed_roster_record_ids": [
                str(row.get("record_id") or "") for row in current_policy_excluded
            ],
            "current_policy_proven_records": len(rows),
            "rebound_and_verified": rebound,
            "all_boundaries_prefixes_terminators_verified": rebound == len(rows),
        },
        "metric_reconciliation": {
            "historical_documented_too_short_approx": 1861,
            "historical_metric_reproduced": False,
            "current_guarded_script_sheet": sheet_summary,
            "proven_mixed_residual_overlap": {
                "historical_manifest_records": len(baseline_rows),
                "current_policy_records": len(rows),
                "fixed_roster_rows_removed": len(current_policy_excluded),
                "regions": dict(sorted(region_counts.items())),
                "body_span_counts": {
                    str(span): body_counts[span] for span in sorted(body_counts)
                },
                "unique_reviewed_phrases": len(unique_targets),
            },
            "interpretation": (
                "The old approximate 1,861 figure used a broader/stale population. "
                "The current guarded script-sheet definition yields 250 records. "
                "The older manifest proves 222 short records, but 17 are now excluded "
                "as fixed-roster data, leaving 205 current-policy records for P2-1."
            ),
        },
        "blockers_in_proven_population": dict(sorted(blocker_counts.items())),
        "current_body_shapes": dict(sorted(current_body_shape_counts.items())),
        "reference_union": {
            "summary": union.summary(),
            "free_slots": inventory.as_dict(),
            "safe_non_ff_ext_two_byte_slots": [
                f"{index:04X}" for index in safe_ext_slots
            ],
            "safe_non_ff_stock_two_byte_slots": [
                f"{index:04X}" for index in safe_stock_slots
            ],
            "scanner_fix": (
                "Working-ROM two-byte references are parsed with ext3 runtime "
                "precedence; E5 18 xx yy tails are not counted as 2-byte tokens."
            ),
        },
        "strategy_results": {
            "existing_exact_two_byte_token": {
                "status": "GO_read_only_plan",
                "records": len(exact_rows),
                "unique_phrases": len(exact_target_phrases),
                "existing_slots": sorted({row["existing_slot"] for row in exact_rows}),
                "record_plan": exact_rows,
                "safety_contract": [
                    "existing dictionary payload is not modified",
                    "only the proven record payload is retargeted",
                    "2-byte body uses token only; 3-byte body uses token plus one 0x01 pad",
                    "record prefix and terminator location are preserved",
                ],
            },
            "true_free_non_ff_ext_two_byte": {
                "status": (
                    "GO_read_only_plan" if true_free_rows else "NO_GO_no_safe_slot"
                ),
                "records": len(true_free_rows),
                "unique_phrases": len(true_free_allocations),
                "slots": [allocation["slot"] for allocation in true_free_allocations],
                "allocations": true_free_allocations,
                "record_plan": true_free_rows,
                "safety_contract": [
                    "Original+Working full reference union proves the slot true-free",
                    "Working-ROM token scan is ext3-aware",
                    "FF-page indices are excluded",
                    "guard_hangul_slot_writes uses the installed marker code",
                    "only an extended bank10 slot is written; stock 5F is unchanged",
                    "record prefix and terminator location are preserved",
                ],
            },
            "true_free_non_ff_stock_two_byte": {
                "status": (
                    "GO_read_only_plan" if stock_rows else "NO_GO_no_safe_slot"
                ),
                "records": len(stock_rows),
                "unique_phrases": len(stock_allocations),
                "slots": [allocation["slot"] for allocation in stock_allocations],
                "allocations": stock_allocations,
                "record_plan": stock_rows,
                "safety_contract": [
                    "Original+Working full reference union proves the stock slot true-free",
                    "Working-ROM token scan is ext3-aware",
                    "FF-page indices are excluded",
                    "guard_hangul_slot_writes uses the installed marker code",
                    "stock payloads must append only to a verified all-FF bank-5F tail",
                    "only selected stock pointers may change; every other pointer and payload must remain byte-identical",
                    "record prefix and terminator location are preserved",
                ],
            },
            "remaining": {
                "records": remaining_records,
                "unique_phrases": remaining_phrases,
                "pair_steal": "NO_GO_without_full_current_union_and_preserve_retarget_proof",
                "sole_far_pointer_relocation": "NO_GO_without_caller_XREF_and_runtime_read_evidence",
                "local_one_byte_expansion": "NO_GO_without_next_record_and_event_stream_contract",
            },
        },
        "save_ram": {
            "required_for_any_test_rom": True,
            "path": str(args.base_save.resolve()),
            "exists": base_save_exists,
        },
        "decision": {
            "status": (
                "partial_go_ext_true_free_available"
                if true_free_rows
                else (
                    "partial_go_stock_true_free_available"
                    if stock_rows
                    else "partial_go_exact_only"
                )
            ),
            "exact_reuse_candidate_generation_allowed": base_save_exists,
            "true_free_candidate_generation_allowed": bool(
                base_save_exists and true_free_rows
            ),
            "stock_spill_candidate_generation_allowed": bool(
                base_save_exists and stock_rows
            ),
            "candidate_generation_blockers": (
                []
                if base_save_exists
                else ["approved base SaveRAM is missing at sram/monoeye_ko_expanded.sav"]
            ),
            "rom_write_performed": False,
            "next_safe_step": (
                "Build a cumulative exact-reuse + non-FF ext true-free candidate with a same-stem .sav and run the full current TIP gate set."
                if base_save_exists and true_free_rows
                else (
                    "Build a cumulative stock true-free spill candidate with a same-stem .sav and run the full current TIP gate set."
                    if base_save_exists and stock_rows
                    else "Restore the approved base SaveRAM before building any P2 candidate."
                )
            ),
        },
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    parser.add_argument("--working-rom", type=Path, default=DEFAULT_WORKING_ROM)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    parser.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    parser.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    parser.add_argument("--base-save", type=Path, default=DEFAULT_BASE_SAVE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the report without writing a file",
    )
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
                    "proven_records": report["current_tip_rebind"][
                        "current_policy_proven_records"
                    ],
                    "exact_reuse_records": report["strategy_results"][
                        "existing_exact_two_byte_token"
                    ]["records"],
                    "true_free_records": report["strategy_results"][
                        "true_free_non_ff_ext_two_byte"
                    ]["records"],
                    "stock_true_free_records": report["strategy_results"][
                        "true_free_non_ff_stock_two_byte"
                    ]["records"],
                    "rom_written": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


__all__ = [
    "P2AnalysisError",
    "ShortReason",
    "build_exact_phrase_index",
    "build_true_free_plan",
    "load_reviewed_values",
    "make_rewrite_payload",
    "parse_short_reason",
    "summarize_guarded_sheet_short_records",
]


if __name__ == "__main__":
    raise SystemExit(main())
