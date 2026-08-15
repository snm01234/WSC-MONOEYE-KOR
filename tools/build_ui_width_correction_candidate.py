#!/usr/bin/env python3
"""Build a screen-width correction candidate on top of broad stage-2.

The correction layer changes only 13 screen-verified UI records.  Long records
receive private ext3 phrases.  The two existing exact stock phrases ``사용`` and
``미사용`` are reused, while ``뒤로`` receives one newly proven strong-retired
stock slot.  Record length, NUL position, and all non-target renderings are
preserved.  The main TIP is never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_broad_stage2_dialogue_voice_candidate import (
    atomic_bytes,
    atomic_json,
    digest,
    exact_slots,
    identity,
    payload_at,
)
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, phrase_cursor, verify_non_target_invariance
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import (
    _working_two_byte_external_refs,
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/broad_stage2_placeholder_candidate.wsc"
SPEC = ROOT / "data/ui_width_corrections_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ui_width_correction_candidate.wsc"
OUT_SAVE = ROOT / "sram/ui_width_correction_candidate.sav"
REPORT = ROOT / "out/patch/ui_width_correction_report.json"

EXPECTED_MAIN_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
EXPECTED_PARENT_SHA = "c9f7049873d6040c63d99144db709c80a163ba1ff679f58f139e8eadea47635c"
EXPECTED_TARGETS = 13
EXPECTED_LONG = 9
EXPECTED_SHORT = 4
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def visual_cells(text: str) -> int:
    """Count renderer cells for this plain-text correction set.

    Every character in these records is rendered as one 8-pixel cell.  The
    leading ideographic spaces in the two confirmation prompts deliberately
    count as one cell.
    """
    if "<" in text or ">" in text:
        raise BuildError(f"control markup is not allowed in width spec: {text!r}")
    return len(text)


def load_rows(parent: bytes, dictionary: Any, tbl: Tbl) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    base = stock_base(parent)
    for item in spec.get("records") or []:
        address = str(item.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate target address {address}")
        seen.add(address)
        logical = int(address, 16)
        result = read_encoded_z_safe(parent, base + logical, max_len=256)
        if result is None:
            raise BuildError(f"unreadable parent record {address}")
        payload, terminator = bytes(result[0]), int(result[1])
        before = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        expected_before = normalize_ko_text(str(item.get("before") or "")).rstrip("\u3000 \t")
        after = normalize_ko_text(str(item.get("after") or ""))
        max_cells = int(item.get("max_visual_cells") or 0)
        if before != expected_before:
            raise BuildError(f"parent render drift at {address}: expected {expected_before!r}, got {before!r}")
        if not after or any(is_japanese_character(character) for character in after):
            raise BuildError(f"invalid Korean correction at {address}: {after!r}")
        if visual_cells(after) > max_cells:
            raise BuildError(f"visual width exceeds limit at {address}: {visual_cells(after)} > {max_cells}")
        if terminator != base + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"record boundary drift at {address}")
        rows.append(
            {
                "abs": address,
                "logical": logical,
                "before": before,
                "after": after,
                "body_capacity": len(payload),
                "max_visual_cells": max_cells,
                "screen": item.get("screen"),
                "reason": item.get("reason"),
                "parent_payload_hex": payload.hex().upper(),
            }
        )
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_TARGETS:
        raise BuildError(f"target population drifted: expected {EXPECTED_TARGETS}, got {len(rows)}")
    long_rows = [row for row in rows if int(row["body_capacity"]) >= 4]
    short_rows = [row for row in rows if 2 <= int(row["body_capacity"]) < 4]
    if len(long_rows) != EXPECTED_LONG or len(short_rows) != EXPECTED_SHORT:
        raise BuildError(f"strategy population drifted: long={len(long_rows)}, short={len(short_rows)}")
    return spec, rows


def main() -> int:
    main_rom = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    parent = PARENT.read_bytes()
    if len(main_rom) != ROM_SIZE or digest(main_rom) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("broad-stage parent identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    spec, rows = load_rows(parent, parent_dictionary, tbl)
    long_rows = [row for row in rows if int(row["body_capacity"]) >= 4]
    short_rows = [row for row in rows if 2 <= int(row["body_capacity"]) < 4]

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    long_phrases = sorted({str(row["after"]) for row in long_rows})
    free_ext3 = sorted(index for index in inventory.ext3_free if bank_local_for_index(index)[0] == ALLOC_SEG)
    if len(free_ext3) < len(long_phrases):
        raise BuildError("not enough ext3 slots in allocation bank")
    ext3_assignment = {phrase: index for phrase, index in zip(long_phrases, free_ext3)}
    ext3_payloads = {index: encode_phrase(phrase, tbl) for phrase, index in ext3_assignment.items()}
    ext3_need = sum(len(payload) + 1 for payload in ext3_payloads.values())
    ext3_room = int(inventory.ext3_bank_room.get(ALLOC_SEG - EXP3_SEG0, 0))
    if ext3_need > ext3_room:
        raise BuildError(f"not enough ext3 phrase room: need {ext3_need}, room {ext3_room}")

    short_phrases = {str(row["after"]) for row in short_rows}
    exact = exact_slots(parent_dictionary, tbl, short_phrases)
    exact_assignment: dict[str, int] = {}
    for phrase in sorted(short_phrases - {"뒤로"}):
        slots = exact.get(phrase) or []
        if not slots:
            raise BuildError(f"required exact stock phrase is absent: {phrase!r}")
        exact_assignment[phrase] = min(slots)
    if exact.get("뒤로"):
        exact_assignment["뒤로"] = min(exact["뒤로"])
        new_stock_payloads: dict[int, bytes] = {}
        selected_retired: list[int] = []
    else:
        retired = current_strong_retired_slots(original, parent, parent_dictionary)
        if not retired:
            raise BuildError("no strong-retired stock slot is available for 뒤로")
        selected_retired = [retired[0]]
        selected = set(selected_retired)
        external = external_occurrence_map(parent, ext3_aware=True, wanted=selected)
        nested = nested_occurrence_map(parent_dictionary, wanted=selected, ext3_aware=True)
        raw = _raw_pair_hits(parent, selected_retired)
        index = selected_retired[0]
        if external.get(index) or nested.get(index) or raw.get(index):
            raise BuildError("selected retired stock slot remains reachable")
        exact_assignment["뒤로"] = index
        new_stock_payloads = {index: encode_phrase("뒤로", tbl)}

    candidate = bytearray(parent)
    ext3_cursor_before = phrase_cursor(bytes(slice_expansion_bank(parent, ALLOC_SEG)))
    ext3_info, ext3_guard = write_ext3_slots_guarded(candidate, ext3_payloads, union=union, num_banks=num_banks)
    if int(ext3_info.get("written") or 0) != len(ext3_payloads):
        raise BuildError("ext3 writer did not write every phrase")

    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_before = list(Dictionary(candidate).ptrs)
    if new_stock_payloads:
        pointers_written, stock_cursor_after = write_dictionary_slots_spill(
            candidate,
            new_stock_payloads,
            spill_start=SPILL_FLOOR,
            allow_aux_consumers=False,
            locs=_working_two_byte_external_refs(bytes(candidate)),
        )
        if list(Dictionary(candidate).ptrs) != pointers_written:
            raise BuildError("stock pointer result differs from ROM")
        changed_indices = {
            index
            for index, (before, after) in enumerate(zip(pointers_before, pointers_written))
            if before != after
        }
        if changed_indices != set(new_stock_payloads):
            raise BuildError("stock pointer change set differs from selected slots")
    else:
        stock_cursor_after = stock_cursor_before

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        capacity = int(row["body_capacity"])
        phrase = str(row["after"])
        if capacity >= 4:
            index = ext3_assignment[phrase]
            token = token_from_ext3_index(index, num_banks=num_banks)
            strategy = "private_ext3"
            allocation = {"ext3_index": f"{index:05X}"}
        else:
            index = exact_assignment[phrase]
            token = token_from_dict_index(index)
            strategy = "new_strong_retired_stock" if index in new_stock_payloads else "existing_exact_stock"
            allocation = {"stock_index": f"{index:04X}"}
        replacement = token + b"\x01" * (capacity - len(token))
        if len(replacement) != capacity:
            raise BuildError(f"replacement length drift at {logical:06X}")
        start = base + logical
        candidate[start : start + capacity] = replacement
        target_extents.append((start, start + capacity))
        applied.append(
            {
                "abs": f"{logical:06X}",
                "before": row["before"],
                "after": phrase,
                "visual_cells": visual_cells(phrase),
                "max_visual_cells": row["max_visual_cells"],
                "body_capacity": capacity,
                "screen": row["screen"],
                "reason": row["reason"],
                "strategy": strategy,
                **allocation,
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    target_failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(candidate_bytes, logical)
        rendered = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        expected = str(row["after"]).rstrip("\u3000 \t")
        width = visual_cells(rendered)
        if rendered != expected:
            target_failures.append({"abs": f"{logical:06X}", "reason": "render_mismatch", "expected": expected, "actual": rendered})
        elif width > int(row["max_visual_cells"]):
            target_failures.append({"abs": f"{logical:06X}", "reason": "visual_width_exceeded", "width": width, "limit": row["max_visual_cells"]})
        elif any(is_japanese_character(character) for character in rendered):
            target_failures.append({"abs": f"{logical:06X}", "reason": "japanese_residual", "actual": rendered})
        elif terminator != base + logical + int(row["body_capacity"]) or candidate_bytes[terminator] != 0:
            target_failures.append({"abs": f"{logical:06X}", "reason": "record_boundary_changed"})

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )

    ext3_cursor_after = phrase_cursor(bytes(slice_expansion_bank(candidate_bytes, ALLOC_SEG)))
    ext3_bank_file = ALLOC_SEG * BANK_SIZE
    ext3_pointer_extents = []
    for index in ext3_payloads:
        _segment, local = bank_local_for_index(index)
        ext3_pointer_extents.append((ext3_bank_file + local * 2, ext3_bank_file + local * 2 + 2))
    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2)
        for index in new_stock_payloads
    ]
    allowed = target_extents + ext3_pointer_extents + stock_pointer_extents + [
        (ext3_bank_file + ext3_cursor_before, ext3_bank_file + ext3_cursor_after),
        (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    other_ext3_unchanged = all(
        bytes(slice_expansion_bank(parent, segment)) == bytes(slice_expansion_bank(candidate_bytes, segment))
        for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if segment != ALLOC_SEG
    )
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]

    ok = (
        not target_failures
        and invariance.get("ok") is True
        and not unaccounted
        and other_ext3_unchanged
        and runtime_unchanged
        and digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA
        and MAIN_SAVE.read_bytes() == main_save
    )
    if not ok:
        raise BuildError("UI width correction candidate static verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ui_width_correction_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "main_tip": identity(MAIN, main_rom),
        "parent": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "spec": identity(SPEC),
        "counts": {
            "targets": len(rows),
            "long_ext3_records": len(long_rows),
            "short_stock_records": len(short_rows),
            "ext3_unique_phrases": len(ext3_payloads),
            "existing_exact_stock_phrases": len(exact_assignment) - len(new_stock_payloads),
            "new_retired_stock_phrases": len(new_stock_payloads),
            "target_failures": len(target_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segment": f"{ALLOC_SEG:02X}",
            "ext3_cursor_before": f"{ext3_cursor_before:04X}",
            "ext3_cursor_after": f"{ext3_cursor_after:04X}",
            "ext3_phrase_bytes": ext3_cursor_after - ext3_cursor_before,
            "ext3_room_before": ext3_room,
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
            "stock_phrase_bytes": stock_cursor_after - stock_cursor_before,
            "selected_retired_slots": [f"{index:04X}" for index in selected_retired],
            "short_assignments": {phrase: f"{index:04X}" for phrase, index in sorted(exact_assignment.items())},
        },
        "guards": {
            "ext3": ext3_guard.as_dict(),
            "selected_retired_external_zero": True,
            "selected_retired_nested_zero": True,
            "selected_retired_raw_zero": True,
        },
        "verification": {
            "all_targets_render_exact": not target_failures,
            "all_targets_within_visual_cell_limits": not target_failures,
            "target_japanese_residuals_zero": not target_failures,
            "non_target_invariance": invariance,
            "diffs_bounded": not unaccounted,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save,
            "candidate_saveram_matches_live_at_build": OUT_SAVE.read_bytes() == main_save,
            "record_length_and_terminator_preserved": True,
        },
        "diff": {
            "changed_bytes_from_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "records": applied,
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "counts": report["counts"],
                "allocation": report["allocation"],
                "diff": report["diff"],
                "report": str(REPORT.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
