#!/usr/bin/env python3
"""
Enforcing gate around the three-way stock diff (requirements 2.8/2.9/2.10, 3.10).

READ-ONLY. This tool never opens a .wsc for writing.

Wraps ``tools/diff_stock_3way.py`` — the classifier, the three-way attribution
and the signature-based tool guess all come from there, nothing is duplicated —
and turns its result into a pass/fail gate:

* any UNINTENDED run in the stock address space (logical banks 00–7F) → exit 1
* 5F dictionary pointers matching the original below the 3,802 / 3,831 floor
  (requirement 3.10) → exit 1

Reports to ``out/patch/stock_noninvasion_gate.json``. The wrapped diff's own
report path (``out/patch/stock_noninvasion_report.json``) is left alone so a
standalone ``diff_stock_3way.py`` run and this gate never clobber each other;
use ``--diff-out`` if you also want the full run list dumped.

Out-of-band dialogue-bank writes (bank 60–69 below 0x6040A5) are surfaced in
their own report section. They are NOT on any restore list — that decision is
still pending — but the gate does count them as UNINTENDED, which is the point.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from baseline_metadata import load_stock_approved_ranges  # noqa: E402

from diff_stock_3way import (  # noqa: E402
    DIALOGUE_HI,
    DIALOGUE_LO,
    UNINTENDED,
    print_summary,
    run_diff,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/stock_noninvasion_gate.json"
DIFF_TOOL_OWN_REPORT = ROOT / "out/patch/stock_noninvasion_report.json"

# Retired: the count-based 5F pointer floor.
#
# 3,802 / 3,831 was measured before the ext3 promotion, on a lineage where almost
# no shared dictionary slot had been localized. The number cannot survive
# non-dialogue localization: write_dictionary_slots_spill retargets exactly one
# pointer per localized slot, so "how many pointers still match the original"
# falls by one for every UI term Koreanized. It was already violated by the
# untouched tip (3,778) before any of this work, which is the tell that it was
# measuring effort rather than safety.
#
# It is replaced by the property it was a proxy for: no shared slot may be
# retargeted outside the curated set. See ptr_semantic_gate.
LEGACY_PTR_GATE_MIN = 3802
DIALOGUE_PTR_BASELINE = ROOT / "data/dict5f_dialogue_pointer_moves.json"

OUT_OF_BAND_NOTE = (
    "dialogue-bank writes outside the allowed band 0x6040A5-0x63FFFF: below the "
    "band (bugfix.md §Fix 4.1) and above it in banks 64-69, which are fixed-stride "
    "data tables (per-stage event name/body pointer pairs), not dialogue. Both are "
    "surfaced and counted as UNINTENDED."
)


def load_relocation_allowlist(path: Path | None) -> set[str]:
    if path is None:
        return set()
    if not path.exists():
        raise SystemExit(f"missing relocation report: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid relocation report: {path}: {exc}")
    values = payload.get("pointer_allowlist")
    if not isinstance(values, list):
        raise SystemExit(
            f"relocation report lacks pointer_allowlist: {path}"
        )
    return {str(value).upper() for value in values}


def load_opening_safe_indices(path: Path | None) -> set[int]:
    """Load slot provenance from the dedicated opening 2-byte-token pass."""
    if path is None:
        return set()
    if not path.exists():
        raise SystemExit(f"missing opening-safe report: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid opening-safe report: {path}: {exc}")
    if payload.get("generated_by") != "tools/apply_opening_safe_slots.py":
        raise SystemExit(f"not an opening-safe report: {path}")
    if payload.get("mode") != "commit" or not payload.get("ok"):
        raise SystemExit(f"opening-safe report is not a successful commit: {path}")
    if payload.get("band") != ["6040A5", "604570"]:
        raise SystemExit(f"opening-safe report has an unexpected band: {path}")
    rows = payload.get("sites")
    if not isinstance(rows, list):
        raise SystemExit(f"opening-safe report lacks sites: {path}")
    out: set[int] = set()
    for row in rows:
        raw = row.get("slot") if isinstance(row, dict) else None
        try:
            index = int(raw, 16) if isinstance(raw, str) else int(raw)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"invalid opening-safe slot in {path}: {raw!r}") from exc
        if not 0 <= index <= 0xEFF:
            raise SystemExit(f"opening-safe slot is not stock 5F: {index:#x}")
        out.add(index)
    return out


def load_approved_stock_indices(path: Path | None) -> tuple[set[int], str | None]:
    """Load a candidate-bound P2 stock-spill approval report.

    The report is produced before the stock non-invasion gate runs. It must
    prove that the selected slots were union-true-free, that only those pointer
    entries moved, and that every other stock pointer and payload stayed
    byte-identical. The embedded candidate SHA binds the allowance to one ROM.
    """
    if path is None:
        return set(), None
    if not path.exists():
        raise SystemExit(f"missing approved stock report: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid approved stock report: {path}: {exc}")
    if payload.get("generated_by") != "tools/build_p2_stock_spill_candidate.py":
        raise SystemExit(f"not a P2 stock-spill approval report: {path}")
    if payload.get("mode") != "pre_gate_approval" or payload.get("ok") is not True:
        raise SystemExit(f"stock-spill approval is not accepted: {path}")
    proof = payload.get("proof") or {}
    required = (
        "union_true_free",
        "tail_was_all_ff",
        "changed_pointer_indices_exact",
        "nonselected_pointers_preserved",
        "nonselected_payloads_preserved",
        "bank5f_diffs_within_approved_extents",
    )
    missing = [name for name in required if proof.get(name) is not True]
    if missing:
        raise SystemExit(
            f"stock-spill approval lacks required proof {missing}: {path}"
        )
    rows = payload.get("approved_stock_slots")
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"stock-spill approval lacks slots: {path}")
    indices: set[int] = set()
    for raw in rows:
        try:
            index = int(raw, 16) if isinstance(raw, str) else int(raw)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"invalid stock slot in {path}: {raw!r}") from exc
        if not 0 <= index <= 0xEFF:
            raise SystemExit(f"approved stock slot is outside non-FF 5F range: {index:#x}")
        indices.add(index)
    candidate = payload.get("candidate_rom") or {}
    sha = candidate.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        raise SystemExit(f"stock-spill approval lacks candidate SHA-256: {path}")
    return indices, sha.lower()


def load_approved_detachment(
    path: Path | None,
) -> tuple[set[int], str | None, tuple[tuple[int, int, str], ...]]:
    """Load candidate-bound duplicate-detachment proof and byte allowances."""
    if path is None:
        return set(), None, ()
    if not path.exists():
        raise SystemExit(f"missing approved detachment report: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid approved detachment report: {path}: {exc}")
    if payload.get("generated_by") not in {
        "tools/build_p2_duplicate_detach_candidate.py",
        "tools/build_p2_duplicate_batch_candidate.py",
        "tools/build_p2_local_ext3_expansion_candidate.py",
        "tools/build_p2_retired_slot_reclaim_candidate.py",
        "tools/build_p2_slot0208_stage_name_repair_candidate.py",
    }:
        raise SystemExit(f"not a P2 duplicate-detachment approval report: {path}")
    if (
        payload.get("mode") != "pre_gate_detachment_approval"
        or payload.get("ok") is not True
    ):
        raise SystemExit(f"duplicate-detachment approval is not accepted: {path}")
    proof = payload.get("proof") or {}
    required = (
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
    )
    inherited = payload.get("inherited_approvals") or {}
    retired = payload.get("retired_slot_reclaim") or {}
    repair = payload.get("slot0208_stage_name_repair") or {}
    if repair:
        required = required + (
            "slot0208_restored_to_shared_payload",
            "replacement_slot_strong_retired",
            "replacement_slot_points_to_existing_oo_payload",
            "oo_targets_migrated_exact",
            "hidden_stage_name_consumers_restored",
            "repair_pointer_changes_exact",
            "repair_record_changes_exact",
        )
    if retired:
        required = required + (
            "retired_slots_original_parent_pointer_payload_equal",
            "retired_slots_current_external_zero",
            "retired_slots_current_nested_zero",
            "retired_slots_original_nested_zero",
            "retired_slots_current_raw_pair_zero",
            "retired_slots_historical_consumers_accounted",
            "retired_slots_former_render_preserved",
            "retired_slots_new_consumers_exact",
            "retired_slots_selected_exact",
            "retired_stage_target_ranges_exact",
        )
    if inherited:
        required = required + (
            "inherited_stock_slots_preserved",
            "inherited_detachment_ranges_preserved",
            "inherited_approval_candidate_matches_parent",
        )
    missing = [name for name in required if proof.get(name) is not True]
    if missing:
        raise SystemExit(
            f"duplicate-detachment approval lacks required proof {missing}: {path}"
        )
    if retired:
        selected_rows = retired.get("selected_slots") or []
        if not selected_rows:
            raise SystemExit(f"retired-slot approval has no selected slots: {path}")
        if any(
            row.get("original_parent_pointer_equal") is not True
            or row.get("original_parent_payload_equal") is not True
            or int(row.get("current_external_count") or 0) != 0
            or int(row.get("current_nested_count") or 0) != 0
            or int(row.get("original_nested_count") or 0) != 0
            or int(row.get("current_raw_pair_hits") or 0) != 0
            for row in selected_rows
            if isinstance(row, dict)
        ):
            raise SystemExit(f"retired-slot selection proof failed: {path}")
    if inherited:
        stock_rows = inherited.get("stock_preservation") or []
        if any(
            row.get("pointer_preserved") is not True
            or row.get("payload_preserved") is not True
            for row in stock_rows
            if isinstance(row, dict)
        ):
            raise SystemExit(
                f"duplicate-detachment inherited stock preservation failed: {path}"
            )
        if inherited.get("detachment_ranges_preserved") is not True:
            raise SystemExit(
                f"duplicate-detachment inherited range preservation failed: {path}"
            )
    rows = payload.get("approved_stock_slots")
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"duplicate-detachment approval lacks stock slots: {path}")
    indices: set[int] = set()
    for raw in rows:
        try:
            index = int(raw, 16) if isinstance(raw, str) else int(raw)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"invalid detached stock slot in {path}: {raw!r}") from exc
        if not 0 <= index <= 0xEFF:
            raise SystemExit(
                f"detached stock slot is outside non-FF 5F range: {index:#x}"
            )
        indices.add(index)
    raw_ranges = payload.get("approved_detachment_ranges")
    if not isinstance(raw_ranges, list) or not raw_ranges:
        raise SystemExit(f"duplicate-detachment approval lacks byte ranges: {path}")
    ranges: list[tuple[int, int, str]] = []
    for row in raw_ranges:
        if not isinstance(row, dict):
            raise SystemExit(f"invalid detachment range in {path}: {row!r}")
        try:
            lo = int(str(row["logical_start"]), 16)
            hi = int(str(row["logical_end_exclusive"]), 16)
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"invalid detachment range in {path}: {row!r}") from exc
        if not (0 <= lo < hi <= 0x800000):
            raise SystemExit(f"detachment range outside stock logical ROM: {row!r}")
        ranges.append((lo, hi, str(row.get("owner_id") or "duplicate_detachment")))
    ranges.sort()
    if retired:
        range_pairs = {(lo, hi) for lo, hi, _owner in ranges}
        stage_rows = retired.get("stage_target_records") or []
        if not stage_rows:
            raise SystemExit(f"retired-slot approval has no stage target records: {path}")
        for row in stage_rows:
            try:
                lo = int(str(row["logical_start"]), 16)
                hi = int(str(row["logical_end_exclusive"]), 16)
            except (KeyError, TypeError, ValueError) as exc:
                raise SystemExit(
                    f"invalid retired-slot target range in {path}: {row!r}"
                ) from exc
            if (lo, hi) not in range_pairs:
                raise SystemExit(
                    f"retired-slot target range lacks approved extent in {path}: "
                    f"{lo:06X}-{hi:06X}"
                )
    for left, right in zip(ranges, ranges[1:]):
        if left[1] > right[0]:
            raise SystemExit(f"overlapping detachment ranges in {path}")
    candidate = payload.get("candidate_rom") or {}
    sha = candidate.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        raise SystemExit(f"duplicate-detachment approval lacks candidate SHA-256: {path}")
    return indices, sha.lower(), tuple(ranges)


def verify_inherited_stock_approval(
    indices: set[int],
    *,
    baseline: Path,
    target: Path,
) -> dict:
    """Prove parent-approved stock slots are unchanged in a child candidate."""
    from monoeye_rom import Dictionary, load_rom

    baseline_dict = Dictionary(load_rom(baseline))
    target_dict = Dictionary(load_rom(target))
    failures: list[str] = []
    rows: list[dict] = []
    for index in sorted(indices):
        baseline_pointer = baseline_dict.ptrs[index]
        target_pointer = target_dict.ptrs[index]
        baseline_payload = bytes(baseline_dict.raw_entry(index))
        target_payload = bytes(target_dict.raw_entry(index))
        ok = baseline_pointer == target_pointer and baseline_payload == target_payload
        if not ok:
            failures.append(f"{index:04X}")
        rows.append(
            {
                "index": f"{index:04X}",
                "baseline_pointer": f"{baseline_pointer:04X}",
                "target_pointer": f"{target_pointer:04X}",
                "pointer_preserved": baseline_pointer == target_pointer,
                "payload_preserved": baseline_payload == target_payload,
            }
        )
    return {"ok": not failures, "failures": failures, "slots": rows}


def ptr_semantic_gate(
    jp: Path,
    target: Path,
    opening_safe_indices: set[int] | None = None,
    approved_stock_indices: set[int] | None = None,
) -> dict:
    """
    Every moved 5F pointer must be accounted for, by index.

    Accounted = the curated UI localization set (read from the apply reports, so
    it is exactly what the writers wrote) plus the dialogue pipeline's
    pre-existing moves (measured once and stored in
    ``data/dict5f_dialogue_pointer_moves.json``, so the accounted set cannot grow
    silently after each promotion the way a "compare against the current tip"
    rule would).

    Anything else is an unaccounted shared-slot retarget and fails closed. This
    is the property the retired count floor was standing in for, and unlike a
    count it does not degrade as more UI terms are legitimately localized.
    """
    from monoeye_rom import Dictionary, load_rom  # local: small import surface

    from verify_nondialogue_text import load_localized_indices

    orig_ptrs = Dictionary(load_rom(jp)).ptrs
    tgt_ptrs = Dictionary(load_rom(target)).ptrs
    total = min(len(orig_ptrs), len(tgt_ptrs))
    moved = {i for i in range(total) if tgt_ptrs[i] != orig_ptrs[i]}

    curated = set(load_localized_indices(ROOT / "out/patch"))

    baseline: set[int] = set()
    baseline_note = "missing"
    if DIALOGUE_PTR_BASELINE.exists():
        try:
            blob = json.loads(DIALOGUE_PTR_BASELINE.read_text(encoding="utf-8"))
            baseline = {int(x, 16) for x in blob.get("indices", [])}
            baseline_note = f"{len(baseline)} stored dialogue moves"
        except (OSError, ValueError):
            baseline_note = "unreadable"

    opening_safe_indices = opening_safe_indices or set()
    approved_stock_indices = approved_stock_indices or set()
    unaccounted = sorted(
        moved - curated - baseline - opening_safe_indices - approved_stock_indices
    )
    return {
        "ok": not unaccounted,
        "pointer_count": total,
        "pointers_moved": len(moved),
        "pointers_match_original": total - len(moved),
        "curated_ui_indices": len(curated),
        "dialogue_baseline": baseline_note,
        "opening_safe_indices": len(opening_safe_indices),
        "approved_stock_indices": len(approved_stock_indices),
        "accounted": len(moved) - len(unaccounted),
        "unaccounted_count": len(unaccounted),
        "unaccounted": [f"{i:04X}" for i in unaccounted[:60]],
        "note": (
            "replaces the retired count floor "
            f"({LEGACY_PTR_GATE_MIN}/3831); a count necessarily falls as shared "
            "slots are localized, so it measured effort, not safety"
        ),
    }


def gate_for_target(
    jp: Path,
    pre: Path,
    target: Path,
    *,
    tbl: Path | None,
    decode: bool,
    ptr_min: int | None,
    relocation_allowlist: set[str] | None,
    opening_safe_indices: set[int] | None,
    approved_stock_indices: set[int] | None,
    approved_detachment_ranges: Sequence[tuple[int, int, str]] = (),
    baseline_ranges: Sequence[tuple[int, int, str, bytes]] = (),
) -> dict:
    diff = run_diff(
        jp,
        pre,
        target,
        tbl_path=tbl,
        hex_cap=32,
        decode=decode,
        max_per_cat=200,
        baseline_ranges=baseline_ranges,
    )
    raw_counts = dict(diff["counts"])
    raw_unintended = list(diff["unintended"])
    relocation_allowlist = relocation_allowlist or set()

    def is_out_of_band_row(row: dict) -> bool:
        logical = int(row["logical"], 16)
        bank = int(row["bank"], 16)
        return bool(
            row["category"] in ("dialogue_bank_outside_band", "data_table_bank_64_69")
            or logical < DIALOGUE_LO and 0x60 <= bank <= 0x69
        )

    authorized_relocation = [
        row
        for row in raw_unintended
        if is_out_of_band_row(row)
        and str(row["logical"]).upper() in relocation_allowlist
    ]
    relocation_ids = {id(row) for row in authorized_relocation}

    def detachment_owner(row: dict) -> str | None:
        logical = int(row["logical"], 16)
        end = logical + int(row["len"])
        for lo, hi, owner in approved_detachment_ranges:
            if lo <= logical and end <= hi:
                return owner
        return None

    authorized_detachment = [
        {**row, "detachment_owner": detachment_owner(row)}
        for row in raw_unintended
        if id(row) not in relocation_ids and detachment_owner(row) is not None
    ]
    detachment_ids = {
        id(row)
        for row in raw_unintended
        if id(row) not in relocation_ids and detachment_owner(row) is not None
    }
    authorized_ids = relocation_ids | detachment_ids
    unintended = [row for row in raw_unintended if id(row) not in authorized_ids]
    authorized_relocation_bytes = sum(
        int(row["len"]) for row in authorized_relocation
    )
    authorized_detachment_bytes = sum(
        int(row["len"]) for row in authorized_detachment
    )
    authorized_bytes = authorized_relocation_bytes + authorized_detachment_bytes
    authorized_runs = len(authorized_relocation) + len(authorized_detachment)
    counts = dict(raw_counts)
    counts["unintended_runs"] -= authorized_runs
    counts["unintended_bytes"] -= authorized_bytes
    counts["intended_runs"] += authorized_runs
    counts["intended_bytes"] += authorized_bytes

    dict_review = diff["dict_5f_review"]
    ptr_match = int(dict_review["pointers_match_original"])
    ptr_total = int(dict_review["pointer_count"])
    semantic = ptr_semantic_gate(
        jp,
        target,
        opening_safe_indices,
        approved_stock_indices,
    )
    ptr_ok = semantic["ok"]
    legacy_ptr_ok = ptr_min is None or ptr_match >= ptr_min

    # unintended is already filtered for explicitly allowlisted relocation sites.
    by_bank: Dict[str, dict] = {}
    for r in unintended:
        b = by_bank.setdefault(r["bank"], {"runs": 0, "bytes": 0, "sites": []})
        b["runs"] += 1
        b["bytes"] += r["len"]
        b["sites"].append(r["site"])

    out_of_band = [
        r
        for r in unintended
        if r["category"] in ("dialogue_bank_outside_band", "data_table_bank_64_69")
        or int(r["logical"], 16) < DIALOGUE_LO
        and 0x60 <= int(r["bank"], 16) <= 0x69
    ]

    failures: List[str] = []
    if counts["unintended_runs"]:
        failures.append(
            f"{counts['unintended_bytes']} B in {counts['unintended_runs']} "
            "UNINTENDED run(s) in the stock address space"
        )
    if not ptr_ok:
        failures.append(
            f"{semantic['unaccounted_count']} 5F dictionary pointer(s) retargeted "
            f"outside the curated UI set, stored dialogue baseline, "
            f"opening-safe report, and approved stock-spill report: "
            f"{', '.join(semantic['unaccounted'][:12])}"
        )
    if not legacy_ptr_ok:
        failures.append(
            f"[--legacy-ptr-min] 5F pointers matching the original "
            f"{ptr_match}/{ptr_total} < {ptr_min}"
        )

    return {
        "target": str(target),
        "ok": not failures,
        "failures": failures,
        "inputs": diff["inputs"],
        "counts": counts,
        "raw_counts": raw_counts,
        "unintended_by_bank": dict(sorted(by_bank.items())),
        "unintended": unintended,
        "authorized_relocation": authorized_relocation,
        "authorized_relocation_bytes": authorized_relocation_bytes,
        "authorized_detachment": authorized_detachment,
        "authorized_detachment_bytes": authorized_detachment_bytes,
        "approved_detachment_ranges": [
            {
                "logical_start": f"{lo:06X}",
                "logical_end_exclusive": f"{hi:06X}",
                "owner_id": owner,
            }
            for lo, hi, owner in approved_detachment_ranges
        ],
        "relocation_allowlist_size": len(relocation_allowlist),
        "dict_5f_pointer_gate": {
            "mode": "semantic",
            "semantic": semantic,
            "pointers_match_original": ptr_match,
            "pointer_count": ptr_total,
            "legacy_gate_min_match": ptr_min,
            "legacy_ok": legacy_ptr_ok,
            "ok": ptr_ok,
            "pointers_changed_pre_to_target": dict_review[
                "pointers_changed_pre_to_target"
            ],
            "bytes_vs_original": dict_review["bytes"],
            "bytes_changed_pre_to_target": dict_review["bytes_changed_pre_to_target"],
        },
        "out_of_band_dialogue_writes": {
            "note": OUT_OF_BAND_NOTE,
            "runs": len(out_of_band),
            "bytes": sum(r["len"] for r in out_of_band),
            "sites": [
                {
                    "site": r["site"],
                    "len": r["len"],
                    "orig": r["orig_hex"],
                    "target": r["target_hex"],
                    "attribution": r["attribution"],
                    "attributed_tool": r["attributed_tool"],
                }
                for r in out_of_band
            ],
        },
        "by_attribution": diff["by_attribution"],
        "by_attributed_tool": diff["by_attributed_tool"],
        "pre_ext3_to_target": diff["pre_ext3_to_target"],
        "original_to_pre_ext3": diff["original_to_pre_ext3"],
        "_diff": diff,
    }


def print_gate(entry: dict) -> None:
    c = entry["counts"]
    p = entry["dict_5f_pointer_gate"]
    print(f"\n=== gate: {entry['target']} ===")
    print(
        f"  stock diff      : {c['diff_bytes']} B / {c['runs']} runs "
        f"(intended {c['intended_bytes']} B, UNINTENDED {c['unintended_bytes']} B "
        f"/ {c['unintended_runs']} runs)"
    )
    for bank, b in entry["unintended_by_bank"].items():
        print(f"    {bank}: {b['bytes']:>4} B / {b['runs']} runs  {', '.join(b['sites'])}")
    s = p["semantic"]
    print(
        f"  5F pointer gate : {s['pointers_moved']} moved, {s['accounted']} "
        f"accounted ({s['curated_ui_indices']} curated UI + "
        f"{s['dialogue_baseline']} + {s['opening_safe_indices']} opening-safe), "
        f"unaccounted {s['unaccounted_count']} -> "
        f"{'ok' if p['ok'] else 'VIOLATED'}"
    )
    if s["unaccounted"]:
        print(f"    unaccounted idx: {', '.join(s['unaccounted'][:16])}")
    if p["legacy_gate_min_match"] is not None:
        print(
            f"    [legacy count floor] {p['pointers_match_original']}/"
            f"{p['pointer_count']} vs {p['legacy_gate_min_match']} -> "
            f"{'ok' if p['legacy_ok'] else 'VIOLATED'}"
        )
    ob = entry["out_of_band_dialogue_writes"]
    print(
        f"  out-of-band 60-69 (outside {DIALOGUE_LO:06X}-{DIALOGUE_HI:06X}): "
        f"{ob['bytes']} B / {ob['runs']} runs"
    )
    for s in ob["sites"]:
        print(
            f"    {s['site']} len {s['len']} {s['orig'][:16]} -> {s['target'][:16]} "
            f"[{s['attribution']}/{s['attributed_tool']}]"
        )
    if entry["ok"]:
        print("  RESULT          : PASS")
    else:
        print("  RESULT          : FAIL")
        for f in entry["failures"]:
            print(f"    - {f}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE)
    ap.add_argument(
        "--target",
        type=Path,
        action="append",
        default=None,
        help="target ROM (repeatable; default = the tip)",
    )
    ap.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="accepted parent candidate used to inherit a candidate-bound stock "
        "approval only when its approved pointers and payloads are unchanged",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--diff-out",
        type=Path,
        default=None,
        help="optional dump of the full wrapped diff report (never the diff "
        "tool's own default path)",
    )
    ap.add_argument(
        "--reloc-report",
        type=Path,
        default=None,
        help="free-space report whose pointer_allowlist authorizes "
        "out-of-band dialogue-bank pointer writes",
    )
    ap.add_argument(
        "--opening-report",
        type=Path,
        default=None,
        help="successful apply_opening_safe_slots report whose stock slots "
        "are authorized in the semantic 5F pointer gate",
    )
    ap.add_argument(
        "--approved-stock-report",
        type=Path,
        default=None,
        help="candidate-bound P2 stock-spill proof whose union-true-free stock "
        "slots are authorized in the semantic 5F pointer gate",
    )
    ap.add_argument(
        "--approved-detachment-report",
        type=Path,
        default=None,
        help="candidate-bound duplicate-payload detachment proof that authorizes "
        "its exact non-dialogue token ranges and reclaimed stock slot",
    )
    ap.add_argument(
        "--baseline-meta",
        type=Path,
        default=None,
        help="normalized P0 metadata whose record_body ranges are already "
        "part of the accepted main-TIP baseline",
    )
    ap.add_argument("--tbl", type=Path, default=ROOT / "data/monoeye.tbl")
    ap.add_argument(
        "--legacy-ptr-min",
        dest="ptr_min",
        type=int,
        default=None,
        help=f"also enforce the retired count floor (was {LEGACY_PTR_GATE_MIN}). "
        "Off by default: it falls by one per localized shared slot, so it "
        "measures localization effort, not safety. Superseded by the semantic "
        "check that no pointer moves outside the curated set.",
    )
    ap.add_argument("--no-decode", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="also print the full diff summary")
    args = ap.parse_args(argv)

    targets = args.target or [DEFAULT_TARGET]
    for p in (args.out, args.diff_out):
        if p is not None and p.suffix.lower() == ".wsc":
            raise SystemExit("refusing to write a .wsc — this gate is read-only")
    if args.diff_out is not None and args.diff_out.resolve() == DIFF_TOOL_OWN_REPORT.resolve():
        raise SystemExit(
            f"--diff-out must not reuse diff_stock_3way's own report path "
            f"({DIFF_TOOL_OWN_REPORT})"
        )
    if args.reloc_report is not None and args.reloc_report.suffix.lower() == ".wsc":
        raise SystemExit("refusing to read a ROM as --reloc-report")
    relocation_allowlist = load_relocation_allowlist(args.reloc_report)
    opening_safe_indices = load_opening_safe_indices(args.opening_report)
    approved_stock_indices, approved_stock_sha = load_approved_stock_indices(
        args.approved_stock_report
    )
    (
        detached_stock_indices,
        approved_detachment_sha,
        approved_detachment_ranges,
    ) = load_approved_detachment(args.approved_detachment_report)
    approved_stock_indices |= detached_stock_indices
    baseline_ranges = load_stock_approved_ranges(args.baseline_meta)
    for p in [args.jp, args.pre, *targets]:
        if not p.exists():
            raise SystemExit(f"missing input ROM: {p}")
    if args.baseline is not None and not args.baseline.exists():
        raise SystemExit(f"missing baseline ROM: {args.baseline}")

    entries = []
    diffs = {}
    inherited_stock_checks: dict[str, dict] = {}
    for t in targets:
        actual_sha = hashlib.sha256(t.read_bytes()).hexdigest()
        if approved_detachment_sha is not None and actual_sha != approved_detachment_sha:
            raise SystemExit(
                f"detachment approval is bound to {approved_detachment_sha}, "
                f"but {t} is {actual_sha}"
            )
        if approved_stock_sha is not None and actual_sha != approved_stock_sha:
            if args.baseline is None:
                raise SystemExit(
                    "--baseline is required to inherit a parent-bound stock approval"
                )
            baseline_sha = hashlib.sha256(args.baseline.read_bytes()).hexdigest()
            if baseline_sha != approved_stock_sha:
                raise SystemExit(
                    f"stock approval is bound to {approved_stock_sha}, while "
                    f"target={actual_sha} and baseline={baseline_sha}"
                )
            inherited = verify_inherited_stock_approval(
                approved_stock_indices - detached_stock_indices,
                baseline=args.baseline,
                target=t,
            )
            if not inherited["ok"]:
                raise SystemExit(
                    "parent-approved stock slots changed in child candidate: "
                    + ", ".join(inherited["failures"])
                )
            inherited_stock_checks[str(t)] = inherited
        e = gate_for_target(
            args.jp,
            args.pre,
            t,
            tbl=args.tbl,
            decode=not args.no_decode,
            ptr_min=args.ptr_min,
            relocation_allowlist=relocation_allowlist,
            opening_safe_indices=opening_safe_indices,
            approved_stock_indices=approved_stock_indices,
            approved_detachment_ranges=approved_detachment_ranges,
            baseline_ranges=baseline_ranges,
        )
        diffs[str(t)] = e.pop("_diff")
        entries.append(e)

    report = {
        "ok": all(e["ok"] for e in entries),
        "generated_by": "tools/verify_stock_noninvasion.py",
        "wraps": "tools/diff_stock_3way.py (classification, attribution, tool guess)",
        "read_only": True,
        "gates": [
            "unintended_runs == 0 in stock logical banks 00–7F",
            "every moved 5F dictionary pointer is in the curated UI set, the "
            "stored dialogue baseline, the supplied opening-safe report, or a "
            "candidate-bound union-true-free stock-spill approval "
            "(semantic; replaces the retired "
            f"{LEGACY_PTR_GATE_MIN}/3831 count floor)",
        ]
        + (
            [f"[legacy] 5F pointers matching original >= {args.ptr_min}/3831"]
            if args.ptr_min is not None
            else []
        ),
        "original": str(args.jp),
        "pre_ext3": str(args.pre),
        "reloc_report": str(args.reloc_report) if args.reloc_report else None,
        "opening_report": str(args.opening_report) if args.opening_report else None,
        "approved_stock_report": (
            str(args.approved_stock_report) if args.approved_stock_report else None
        ),
        "approved_detachment_report": (
            str(args.approved_detachment_report)
            if args.approved_detachment_report
            else None
        ),
        "approved_detachment_ranges": len(approved_detachment_ranges),
        "inherited_stock_checks": inherited_stock_checks,
        "baseline": str(args.baseline) if args.baseline else None,
        "baseline_meta": str(args.baseline_meta) if args.baseline_meta else None,
        "baseline_approved_ranges": len(baseline_ranges),
        "opening_safe_indices": len(opening_safe_indices),
        "approved_stock_indices": len(approved_stock_indices),
        "relocation_allowlist_size": len(relocation_allowlist),
        "targets": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.diff_out is not None:
        args.diff_out.parent.mkdir(parents=True, exist_ok=True)
        args.diff_out.write_text(
            json.dumps(diffs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if args.verbose:
        for t, d in diffs.items():
            print(f"\n---- full diff summary: {t} ----")
            print_summary(d)
    for e in entries:
        print_gate(e)
    print(f"\n-> {args.out}")
    if args.diff_out is not None:
        print(f"-> {args.diff_out}")
    print(f"ok={report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
