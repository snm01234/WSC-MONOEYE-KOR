#!/usr/bin/env python3
"""Build one non-promotable candidate containing all 1,893 uncovered rows.

The 35 previously approved/candidate rows are preserved from the draft sheet;
the other 1,858 rows are explicitly unreviewed draft translations (LLM literal
or legacy fresh MT). This tool is a test candidate builder only. It never
modifies the live main TIP or live SaveRAM.

Sheet payloads remain bound to the pre-uncovered parent TIP
``898f3b82…``. After that parent was once promoted, rebuilds read the bound
parent from backup while leaving the live tip untouched until a separate
promotion step.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3
from build_encyclopedia_character_five_bank_batch02_candidate import PAGES
from build_encyclopedia_ms_batch01_candidate import exact_slots, payload_at
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    verify_non_target_invariance,
)
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text

LIVE_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = (
    ROOT
    / "out/patch/backup/20260804_211641_pre_uncovered_auto_draft_all/monoeye_ko_expanded.wsc"
)
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SHEET = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
DRAFT_MANIFEST = ROOT / "out/patch/uncovered_auto_draft_batch_manifest.json"
TRANSLATION_REPORT = ROOT / "out/patch/uncovered_auto_draft_translation_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/uncovered_auto_draft_all_candidate.wsc"
OUT_SAVE = ROOT / "sram/uncovered_auto_draft_all_candidate.sav"
REPORT = ROOT / "out/patch/uncovered_auto_draft_all_candidate_report.json"

PARENT_SHA256 = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 1_893
EXPECTED_DRAFT_ROWS = 1_858
EXPECTED_PRESERVED_ROWS = 35
EXPECTED_BATCHES = 49
EXPECTED_SHORT_ROWS = 27
DRAFT_WORKFLOWS = {"draft_auto", "draft_llm_literal"}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def load_rows(parent: bytes, tbl: Tbl) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    translation_report = load_object(TRANSLATION_REPORT)
    manifest = load_object(DRAFT_MANIFEST)
    if translation_report.get("ok") is not True or manifest.get("ok") is not True:
        raise BuildError("draft translation sheet or manifest is not complete")
    if translation_report.get("promotion_allowed") is not False or manifest.get("promotion_allowed") is not False:
        raise BuildError("draft assets lost their non-promotable flag")
    if int((translation_report.get("counts") or {}).get("fresh_auto_draft") or -1) != EXPECTED_DRAFT_ROWS:
        raise BuildError("draft translation population drifted")
    if int((manifest.get("counts") or {}).get("batches") or len(manifest.get("batches") or [])) not in {-1, EXPECTED_BATCHES}:
        raise BuildError("draft batch count drifted")

    with SHEET.open(encoding="utf-8-sig", newline="") as stream:
        sources = [dict(row) for row in csv.DictReader(stream)]
    if len(sources) != EXPECTED_ROWS or len({str(row.get("abs") or "").upper() for row in sources}) != EXPECTED_ROWS:
        raise BuildError("draft sheet population drifted")

    sb = stock_base(parent)
    prepared: list[dict[str, Any]] = []
    provenance_counts: Counter[str] = Counter()
    for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source["payload_capacity"])
        body_capacity = int(source["body_capacity"])
        expected_payload = bytes.fromhex(str(source["current_payload_hex"]))
        expected_body_digest = str(source["source_body_sha256"]).lower()
        if payload_capacity != len(expected_payload) or body_capacity != payload_capacity - len(prefix):
            raise BuildError(f"sheet boundary drifted at {address}")
        if body_capacity < 2:
            raise BuildError(f"body cannot fit any safe token at {address}")
        payload, terminator = payload_at(parent, logical)
        if payload != expected_payload or not payload.startswith(prefix):
            raise BuildError(f"parent payload drifted at {address}")
        if terminator != sb + logical + payload_capacity or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {address}")
        if sha256(payload[len(prefix):]) != expected_body_digest:
            raise BuildError(f"body digest drifted at {address}")

        workflow = str(source.get("workflow_status") or "")
        translation_source = str(source.get("translation_source") or "")
        review_status = str(source.get("review_status") or "")
        if workflow in DRAFT_WORKFLOWS:
            if review_status != "unreviewed_draft":
                raise BuildError(f"draft provenance drifted at {address}")
            if workflow == "draft_auto" and translation_source != "google_translate_fresh_draft":
                raise BuildError(f"legacy draft provenance drifted at {address}")
            if workflow == "draft_llm_literal" and translation_source != "llm":
                raise BuildError(f"llm draft provenance drifted at {address}")
            provenance_counts["fresh_auto_draft"] += 1
        elif workflow in {"candidate_pending", "approved"}:
            if translation_source != "llm" or review_status != "approved":
                raise BuildError(f"preserved provenance drifted at {address}")
            provenance_counts["preserved_approved_or_candidate"] += 1
        else:
            raise BuildError(f"unsupported workflow status at {address}: {workflow!r}")

        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or any(is_japanese_character(ch) for ch in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        encoded = encode_phrase(ko, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"invalid encoded phrase at {address}")
        prepared.append(
            {
                "abs": address,
                "logical": logical,
                "batch_id": str(source.get("batch_id") or ""),
                "batch_order": int(source.get("batch_order") or 0),
                "scope": str(source.get("scope") or ""),
                "gap": str(source.get("gap") or ""),
                "jp": str(source.get("original_jp") or ""),
                "before": str(source.get("current_text") or ""),
                "ko": ko,
                "encoded": encoded,
                "prefix": prefix,
                "prefix_len": len(prefix),
                "payload_capacity": payload_capacity,
                "body_capacity": body_capacity,
                "workflow_status": workflow,
                "translation_source": translation_source,
                "review_status": review_status,
            }
        )

    if provenance_counts != Counter(
        {
            "fresh_auto_draft": EXPECTED_DRAFT_ROWS,
            "preserved_approved_or_candidate": EXPECTED_PRESERVED_ROWS,
        }
    ):
        raise BuildError(f"provenance population drifted: {dict(provenance_counts)}")
    ext3_rows = [row for row in prepared if int(row["body_capacity"]) >= 4]
    short_rows = [row for row in prepared if int(row["body_capacity"]) < 4]
    if len(short_rows) != EXPECTED_SHORT_ROWS or any(int(row["body_capacity"]) != 3 for row in short_rows):
        raise BuildError("short-body population drifted")
    return ext3_rows, short_rows, {
        "provenance_counts": dict(provenance_counts),
        "batch_counts": dict(Counter(row["batch_id"] for row in prepared)),
        "scope_counts": dict(Counter(row["scope"] for row in prepared)),
    }


def main() -> int:
    if not PARENT.is_file():
        raise BuildError(f"sheet-bound parent backup missing: {PARENT}")
    parent = bytes(load_rom(PARENT))
    live_tip_before = LIVE_TIP.read_bytes()
    save_snapshot = LIVE_SAVE.read_bytes()
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha256(parent) != PARENT_SHA256:
        raise BuildError("sheet-bound parent TIP identity drifted")
    if len(live_tip_before) != ROM_SIZE:
        raise BuildError("live main TIP missing or wrong size")
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")
    live_tip_sha_before = sha256(live_tip_before)

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    ext3_rows, short_rows, population = load_rows(parent, tbl)

    assignments, states = allocate_ext3(parent, ext3_rows)
    candidate = bytearray(parent)
    ext3_pointer_extents: list[tuple[int, int]] = []
    ext3_phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        start = int(state["start"])
        candidate[start : start + BANK_SIZE] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        }
        ext3_pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2)
            for local in sorted(new_locals)
        )
        if int(state["cursor"]) > int(state["cursor_before"]):
            ext3_phrase_extents.append(
                (start + int(state["cursor_before"]), start + int(state["cursor"]))
            )

    short_phrases = {str(row["ko"]) for row in short_rows}
    exact = exact_slots(parent_dictionary, tbl, short_phrases)
    reusable = {phrase: slots for phrase, slots in exact.items() if slots}
    new_short_phrases = sorted(short_phrases - set(reusable))
    retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected_retired = retired[: len(new_short_phrases)]
    if len(selected_retired) != len(new_short_phrases):
        raise BuildError("insufficient strong-retired stock slots")
    selected_set = set(selected_retired)
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_set)
    current_nested = nested_occurrence_map(parent_dictionary, wanted=selected_set, ext3_aware=True)
    current_raw = _raw_pair_hits(parent, selected_retired)
    if any(current_external.get(i) or current_nested.get(i) or current_raw.get(i) for i in selected_retired):
        raise BuildError("selected retired stock slot is still reachable")

    stock_assignment = {phrase: min(slots) for phrase, slots in reusable.items()}
    stock_payloads: dict[int, bytes] = {}
    for phrase, index in zip(new_short_phrases, selected_retired):
        stock_assignment[phrase] = index
        stock_payloads[index] = encode_phrase(phrase, tbl)

    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_before = list(Dictionary(candidate).ptrs)
    if stock_payloads:
        pointers_written, stock_cursor_after = write_dictionary_slots_spill(
            candidate,
            stock_payloads,
            spill_start=SPILL_FLOOR,
            allow_aux_consumers=False,
            locs=_working_two_byte_external_refs(bytes(candidate)),
        )
    else:
        pointers_written = list(Dictionary(candidate).ptrs)
        stock_cursor_after = stock_cursor_before
    pointers_after = list(Dictionary(candidate).ptrs)
    if pointers_after != pointers_written:
        raise BuildError("stock writer pointer result differs from ROM")
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != set(stock_payloads):
        raise BuildError("stock pointer change set differs from selected retired slots")

    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in ext3_rows + short_rows:
        phrase = str(row["ko"])
        if int(row["body_capacity"]) >= 4:
            info = assignments[phrase]
            token = bytes(info["token"])
            strategy = "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new"
            allocation: dict[str, Any] = {
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
            }
        else:
            index = int(stock_assignment[phrase])
            token = token_from_dict_index(index)
            strategy = "existing_exact_stock" if phrase in reusable else "strong_retired_stock"
            allocation = {"stock_index": f"{index:04X}"}
        replacement = token + b"\x01" * (int(row["body_capacity"]) - len(token))
        if len(replacement) != int(row["body_capacity"]):
            raise BuildError(f"replacement length drift at {row['abs']}")
        body_start = sb + int(row["logical"]) + int(row["prefix_len"])
        candidate[body_start : body_start + int(row["body_capacity"])] = replacement
        target_extents.append((body_start, body_start + int(row["body_capacity"])))
        applied.append(
            {
                "abs": row["abs"],
                "batch_id": row["batch_id"],
                "batch_order": row["batch_order"],
                "scope": row["scope"],
                "gap": row["gap"],
                "jp": row["jp"],
                "before": row["before"],
                "after": phrase,
                "workflow_status": row["workflow_status"],
                "translation_source": row["translation_source"],
                "review_status": row["review_status"],
                "prefix_hex": bytes(row["prefix"]).hex().upper(),
                "payload_capacity": int(row["payload_capacity"]),
                "body_capacity": int(row["body_capacity"]),
                "strategy": strategy,
                **allocation,
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        payload, terminator = payload_at(candidate_bytes, logical)
        prefix_len = len(bytes.fromhex(str(row["prefix_hex"])))
        actual = candidate_dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[:prefix_len].hex().upper() != row["prefix_hex"]:
            reasons.append("prefix_changed")
        if actual != row["after"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")
        if len(payload) != int(row["payload_capacity"]):
            reasons.append("payload_length_changed")
        if terminator != sb + logical + int(row["payload_capacity"]) or candidate_bytes[terminator] != 0:
            reasons.append("terminator_changed")
        if reasons:
            target_failures.append(
                {
                    "abs": row["abs"],
                    "expected": row["after"],
                    "actual": actual,
                    "reasons": reasons,
                }
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in ext3_rows + short_rows},
    )

    candidate_external = external_occurrence_map(candidate_bytes, ext3_aware=True, wanted=selected_set)
    candidate_nested = nested_occurrence_map(candidate_dictionary, wanted=selected_set, ext3_aware=True)
    expected_new_sites: dict[int, set[str]] = defaultdict(set)
    for row in applied:
        if row["strategy"] == "strong_retired_stock":
            expected_new_sites[int(str(row["stock_index"]), 16)].add(str(row["abs"]))
    retired_reference_failures: list[dict[str, Any]] = []
    for index in selected_retired:
        actual_sites = {
            str(ref.get("record_abs") or "").upper()
            for ref in candidate_external.get(index, [])
        }
        if actual_sites != expected_new_sites.get(index, set()) or candidate_nested.get(index):
            retired_reference_failures.append(
                {
                    "stock_index": f"{index:04X}",
                    "expected_sites": sorted(expected_new_sites.get(index, set())),
                    "actual_sites": sorted(actual_sites),
                    "nested": candidate_nested.get(index, []),
                }
            )

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (
            stock_bank_file + DICT_PTR_START + index * 2,
            stock_bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in stock_payloads
    ]
    stock_phrase_extents = (
        [(stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)]
        if stock_cursor_after > stock_cursor_before
        else []
    )
    runs = diff_runs(parent, candidate_bytes)
    allowed = (
        target_extents
        + ext3_pointer_extents
        + ext3_phrase_extents
        + stock_pointer_extents
        + stock_phrase_extents
        + [(len(parent) - 2, len(parent))]
    )
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        == candidate_bytes[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    page_hits_parent = {page: five.scan_range_hits(parent, page) for page in range(PAGES)}
    page_hits_candidate = {page: five.scan_range_hits(candidate_bytes, page) for page in range(PAGES)}
    expected_page_counts = {
        page: len(page_hits_parent[page])
        + sum(
            int(row.get("page", -1)) == page
            for row in applied
            if str(row["strategy"]).startswith("five_bank")
        )
        for page in range(PAGES)
    }
    page_counts_exact = all(
        len(page_hits_candidate[page]) == expected_page_counts[page]
        for page in range(PAGES)
    )

    batch_counts = Counter(str(row["batch_id"]) for row in applied)
    manifest = load_object(DRAFT_MANIFEST)
    expected_batches = {
        str(batch["batch_id"]): int(batch["records"])
        for batch in manifest.get("batches") or []
    }
    draft_applied = sum(row["workflow_status"] in DRAFT_WORKFLOWS for row in applied)
    preserved_applied = sum(row["workflow_status"] not in DRAFT_WORKFLOWS for row in applied)
    checks = {
        "parent_tip_exact": sha256(parent) == PARENT_SHA256,
        "targets_exactly_1893": len(applied) == EXPECTED_ROWS,
        "draft_rows_exactly_1858": draft_applied == EXPECTED_DRAFT_ROWS,
        "preserved_rows_exactly_35": preserved_applied == EXPECTED_PRESERVED_ROWS,
        "all_49_batches_applied": dict(batch_counts) == expected_batches and len(batch_counts) == EXPECTED_BATCHES,
        "short_records_exactly_27": len(short_rows) == EXPECTED_SHORT_ROWS,
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "retired_stock_references_exact": not retired_reference_failures,
        "page_reference_counts_exact": page_counts_exact,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "parent_backup_unchanged": sha256(PARENT.read_bytes()) == PARENT_SHA256,
        "live_tip_unchanged": sha256(LIVE_TIP.read_bytes()) == live_tip_sha_before,
        "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save_snapshot,
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": target_failures[:50],
                    "retired_reference_failures": retired_reference_failures,
                    "unaccounted": unaccounted[:50],
                },
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save_snapshot)
    page_usage = {
        f"{page}": {
            "physical_bank": f"{0x21 + page:02X}",
            "used_before": len(states[page]["used_before"]),
            "used_after": len(states[page]["used_before"])
            + sum(
                int(info["page"]) == page and not bool(info["reused"])
                for info in assignments.values()
            ),
            "cursor_before": f"{int(states[page]['cursor_before']):04X}",
            "cursor_after": f"{int(states[page]['cursor']):04X}",
            "phrase_bytes_added": int(states[page]["cursor"]) - int(states[page]["cursor_before"]),
        }
        for page in range(PAGES)
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_uncovered_auto_draft_all_candidate.py",
        "ok": True,
        "canonical": False,
        "promotion_allowed": False,
        "status": "all_batches_static_verified_unreviewed_draft_pending_user_runtime_test",
        "warning": (
            "1,858 rows are unreviewed draft translations (LLM literal or legacy MT); "
            "this ROM must not be promoted as a production TIP without review and runtime validation."
        ),
        "parent": identity(PARENT, parent),
        "live_tip_at_build": identity(LIVE_TIP, live_tip_before),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE, save_snapshot),
            "policy": "test-only snapshot of current main SaveRAM; never promote SaveRAM",
        },
        "sheet": identity(SHEET),
        "manifest": identity(DRAFT_MANIFEST),
        "checksum": f"{checksum:04X}",
        "counts": {
            "targets": len(applied),
            "batches": len(batch_counts),
            "fresh_auto_draft": draft_applied,
            "preserved_approved_or_candidate": preserved_applied,
            "direct_ext3_records": len(ext3_rows),
            "short_stock_records": len(short_rows),
            "unique_ext3_phrases": len(assignments),
            "short_unique_phrases": len(short_phrases),
            "short_exact_reuse_phrases": len(reusable),
            "short_new_retired_phrases": len(stock_payloads),
            "target_failures": len(target_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "population": population,
        "batch_counts": dict(sorted(batch_counts.items())),
        "scope_counts": dict(Counter(str(row["scope"]) for row in applied)),
        "allocation": {
            "page_usage": page_usage,
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
            "retired_stock_indices": [f"{index:04X}" for index in selected_retired],
            "exact_stock_reuse": {phrase: [f"{index:04X}" for index in slots] for phrase, slots in reusable.items()},
        },
        "checks": checks,
        "diff": {
            "changed_bytes": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "applied": applied,
        "test_scope": [
            "all 49 batches are included in one cumulative candidate",
            "sample event dialogue in each E batch",
            "sample pilot/ship voice in each V batch",
            "all 27 short-token records in C000/S001/S002/S003",
            "battle transition, indirect fire, ID command, save, full restart, and reload",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps({key: report[key] for key in ("ok", "status", "candidate", "counts", "checks", "diff")}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
