#!/usr/bin/env python3
"""Build the cumulative broad stage-2C title/map/communication UI candidate."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

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
    verify_targets,
)
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, phrase_cursor, verify_non_target_invariance
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs, build_free_slot_inventory, build_reference_union, write_ext3_slots_guarded
from monoeye_rom import BANK_SIZE, DICT_PTR_START, SEG_DICT, Dictionary, Tbl, read_encoded_z_safe, slice_expansion_bank, stock_base, token_from_dict_index, update_ws_checksum
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/broad_stage2_dialogue_voice_candidate.wsc"
SOURCE_AUDIT = ROOT / "out/patch/broad_stage2_dialogue_voice_residual_audit.json"
CATALOG = ROOT / "data/broad_stage2_title_ui_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/broad_stage2_title_ui_candidate.wsc"
OUT_SAVE = ROOT / "sram/broad_stage2_title_ui_candidate.sav"
REPORT = ROOT / "out/patch/broad_stage2_title_ui_report.json"

EXPECTED_MAIN_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
EXPECTED_PARENT_SHA = "1a950ebc0090adffbf04dd5e5667482ed3b08be38f98cf387398e043e8a786b9"
EXPECTED_SOURCE_SHA = "60a8ad9a44a5e758334f5208149a4e7094271dd22977dbe747103cb245d7c9a5"
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 127
EXPECTED_DIRECT = 121
EXPECTED_SHORT = 6


class BuildError(RuntimeError):
    pass


def load_rows(parent: bytes) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    source = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if source.get("ok") is not True or digest(SOURCE_AUDIT.read_bytes()) != EXPECTED_SOURCE_SHA:
        raise BuildError("source residual audit is not accepted")
    source_rows = []
    for bucket in (source.get("records") or {}).values():
        source_rows.extend(dict(row) for row in (bucket or []))
    by_abs = {str(row.get("abs") or "").upper(): row for row in source_rows}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in catalog.get("lines") or []:
        address = str(item.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate catalog address {address}")
        seen.add(address)
        source_row = by_abs.get(address)
        if source_row is None or str(source_row.get("region") or "") != "name75_ui":
            raise BuildError(f"source UI row missing at {address}")
        if int(address, 16) < 0x75B8DA:
            raise BuildError(f"catalog includes excluded UI noise at {address}")
        if str(item.get("record_id") or "") != str(source_row.get("record_id") or ""):
            raise BuildError(f"record id mismatch at {address}")
        if str(item.get("jp") or "") != str(source_row.get("original_text") or ""):
            raise BuildError(f"JP mismatch at {address}")
        if str(item.get("current") or "") != str(source_row.get("current_text") or ""):
            raise BuildError(f"current render mismatch at {address}")
        if str(item.get("body_hex") or "").upper() != str(source_row.get("body_hex") or "").upper():
            raise BuildError(f"body mismatch at {address}")
        if int(item.get("body_capacity") or 0) != int(source_row.get("body_capacity") or 0):
            raise BuildError(f"capacity mismatch at {address}")
        ko = normalize_ko_text(str(item.get("ko") or ""))
        if not ko or any(is_japanese_character(character) for character in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        row = dict(source_row)
        row["logical"] = int(address, 16)
        row["ko"] = ko
        row["jp"] = str(item.get("jp") or "")
        rows.append(row)
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_ROWS:
        raise BuildError(f"catalog population drifted: {len(rows)}")
    return source, catalog, rows


def bind_rows(parent: bytes, rows: list[dict[str, Any]]) -> None:
    base = stock_base(parent)
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(parent, logical)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        body = bytes.fromhex(str(row.get("body_hex") or ""))
        if payload != prefix + body or len(body) != int(row["body_capacity"]):
            raise BuildError(f"parent payload drifted at {logical:06X}")
        if terminator != base + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {logical:06X}")


def main() -> int:
    main_bytes = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    parent = PARENT.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(main_bytes) != ROM_SIZE or digest(main_bytes) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("dialogue parent identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or wrong size")

    source, catalog, rows = load_rows(parent)
    bind_rows(parent, rows)
    direct = [row for row in rows if int(row["body_capacity"]) >= 4]
    short = [row for row in rows if 2 <= int(row["body_capacity"]) < 4]
    if len(direct) != EXPECTED_DIRECT or len(short) != EXPECTED_SHORT:
        raise BuildError(f"strategy population drifted: direct={len(direct)} short={len(short)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)

    direct_phrases = sorted({str(row["ko"]) for row in direct})
    free_ext3 = sorted(index for index in inventory.ext3_free if bank_local_for_index(index)[0] == ALLOC_SEG)
    if len(free_ext3) < len(direct_phrases):
        raise BuildError("not enough ext3 slots")
    ext3_assignment = {phrase: index for phrase, index in zip(direct_phrases, free_ext3)}
    ext3_payloads = {index: encode_phrase(phrase, tbl) for phrase, index in ext3_assignment.items()}
    ext3_need = sum(len(payload) + 1 for payload in ext3_payloads.values())
    ext3_room = int(inventory.ext3_bank_room.get(ALLOC_SEG - EXP3_SEG0, 0))
    if ext3_need > ext3_room:
        raise BuildError(f"not enough ext3 phrase bytes: need {ext3_need}, room {ext3_room}")

    short_phrases = {str(row["ko"]) for row in short}
    exact = exact_slots(parent_dictionary, tbl, short_phrases)
    reuse = {phrase: slots for phrase, slots in exact.items() if slots}
    new_phrases = sorted(short_phrases - set(reuse))
    retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected_retired = retired[:len(new_phrases)]
    if len(selected_retired) != len(new_phrases):
        raise BuildError("not enough strong-retired stock slots")
    selected_set = set(selected_retired)
    if any(
        external_occurrence_map(parent, ext3_aware=True, wanted=selected_set).get(index)
        or nested_occurrence_map(parent_dictionary, wanted=selected_set, ext3_aware=True).get(index)
        or _raw_pair_hits(parent, selected_retired).get(index)
        for index in selected_retired
    ):
        raise BuildError("selected retired slot remains reachable")
    stock_assignment = {phrase: min(slots) for phrase, slots in reuse.items()}
    stock_payloads: dict[int, bytes] = {}
    for phrase, index in zip(new_phrases, selected_retired):
        stock_assignment[phrase] = index
        stock_payloads[index] = encode_phrase(phrase, tbl)

    candidate = bytearray(parent)
    ext3_cursor_before = phrase_cursor(bytes(slice_expansion_bank(parent, ALLOC_SEG)))
    ext3_info, ext3_guard = write_ext3_slots_guarded(candidate, ext3_payloads, union=union, num_banks=num_banks)
    if int(ext3_info.get("written") or 0) != len(ext3_payloads):
        raise BuildError("ext3 writer count mismatch")

    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_before = list(Dictionary(candidate).ptrs)
    pointers_written, stock_cursor_after = write_dictionary_slots_spill(candidate, stock_payloads, spill_start=SPILL_FLOOR, allow_aux_consumers=False, locs=_working_two_byte_external_refs(bytes(candidate)))
    dictionary_after_stock = Dictionary(candidate)
    if list(dictionary_after_stock.ptrs) != pointers_written:
        raise BuildError("stock pointer result mismatch")
    changed_indices = {index for index, (before, after) in enumerate(zip(pointers_before, pointers_written)) if before != after}
    if changed_indices != set(stock_payloads):
        raise BuildError("stock pointer change set mismatch")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        prefix_len = int(row.get("prefix_bytes") or 0)
        body_capacity = int(row["body_capacity"])
        phrase = str(row["ko"])
        if body_capacity >= 4:
            index = ext3_assignment[phrase]
            token = token_from_ext3_index(index, num_banks=num_banks)
            strategy = "private_ext3"
            allocation = {"ext3_index": f"{index:05X}"}
        else:
            index = stock_assignment[phrase]
            token = token_from_dict_index(index)
            strategy = "existing_exact_stock" if phrase in reuse else "strong_retired_stock"
            allocation = {"stock_index": f"{index:04X}"}
        replacement = token + b"\x01" * (body_capacity - len(token))
        start = base + logical + prefix_len
        candidate[start:start + body_capacity] = replacement
        target_extents.append((start, start + body_capacity))
        applied.append({"record_id": row["record_id"], "abs": f"{logical:06X}", "jp": row["jp"], "before": row["current_text"], "after": phrase, "body_capacity": body_capacity, "strategy": strategy, **allocation, "token_hex": token.hex().upper()})

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    target_failures = verify_targets(candidate_bytes, rows, candidate_dictionary, tbl)
    invariance = verify_non_target_invariance(parent, candidate_bytes, before_dictionary=parent_dictionary, after_dictionary=candidate_dictionary, tbl=tbl, excluded={int(row["logical"]) for row in rows})

    ext3_cursor_after = phrase_cursor(bytes(slice_expansion_bank(candidate_bytes, ALLOC_SEG)))
    ext3_bank_file = ALLOC_SEG * BANK_SIZE
    pointer_extents = []
    for index in ext3_payloads:
        _segment, local = bank_local_for_index(index)
        pointer_extents.append((ext3_bank_file + local * 2, ext3_bank_file + local * 2 + 2))
    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [(stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2) for index in stock_payloads]
    allowed = target_extents + pointer_extents + stock_pointer_extents + [
        (ext3_bank_file + ext3_cursor_before, ext3_bank_file + ext3_cursor_after),
        (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [{"start": f"{left:08X}", "end_exclusive": f"{right:08X}"} for left, right in runs if not covered((left, right), allowed)]
    other_ext3_unchanged = all(bytes(slice_expansion_bank(parent, segment)) == bytes(slice_expansion_bank(candidate_bytes, segment)) for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks) if segment != ALLOC_SEG)
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]

    ok = not target_failures and invariance.get("ok") is True and not unaccounted and other_ext3_unchanged and runtime_unchanged and digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA and MAIN_SAVE.read_bytes() == main_save
    if not ok:
        raise BuildError("stage-2C title/UI candidate verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_broad_stage2_title_ui_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "main_tip": identity(MAIN, main_bytes),
        "parent_dialogue_candidate": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "source_audit": identity(SOURCE_AUDIT),
        "source_catalog": identity(CATALOG),
        "counts": {"targets": len(rows), "ext3_records": len(direct), "short_stock_records": len(short), "ext3_unique_phrases": len(ext3_payloads), "short_unique_phrases": len(short_phrases), "existing_exact_stock_phrases": len(reuse), "new_retired_stock_phrases": len(stock_payloads), "target_failures": len(target_failures), "non_target_failures": int(invariance.get("failure_count") or 0), "unaccounted_diff_runs": len(unaccounted)},
        "allocation": {"ext3_segment": f"{ALLOC_SEG:02X}", "ext3_cursor_before": f"{ext3_cursor_before:04X}", "ext3_cursor_after": f"{ext3_cursor_after:04X}", "ext3_phrase_bytes": ext3_cursor_after - ext3_cursor_before, "ext3_room_before": ext3_room, "stock_cursor_before": f"{stock_cursor_before:04X}", "stock_cursor_after": f"{stock_cursor_after:04X}", "stock_phrase_bytes": stock_cursor_after - stock_cursor_before, "selected_retired_slots": [f"{index:04X}" for index in selected_retired], "existing_exact_slots": {phrase: [f"{index:04X}" for index in slots] for phrase, slots in sorted(reuse.items())}},
        "guards": {"ext3": ext3_guard.as_dict(), "selected_retired_current_external_zero": True, "selected_retired_current_nested_zero": True, "selected_retired_current_raw_zero": True},
        "verification": {"all_targets_render_exact": not target_failures, "target_japanese_residuals_zero": not target_failures, "non_target_invariance": invariance, "diffs_bounded": not unaccounted, "other_ext3_banks_unchanged": other_ext3_unchanged, "runtime_hook_unchanged": runtime_unchanged, "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA, "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save, "candidate_saveram_matches_live": OUT_SAVE.read_bytes() == main_save, "prefix_length_terminator_preserved": True},
        "diff": {"changed_bytes_from_parent": sum(right-left for left,right in runs), "runs": len(runs), "checksum": f"{checksum:04X}"},
        "excluded_ui_records": catalog.get("excluded") or [],
        "records": applied,
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["candidate"], "counts": report["counts"], "allocation": report["allocation"], "diff": report["diff"], "report": str(REPORT.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
