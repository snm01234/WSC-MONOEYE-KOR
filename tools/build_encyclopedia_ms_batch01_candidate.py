#!/usr/bin/env python3
"""Build the first reviewed MS-encyclopedia localization candidate.

Scope: bank 5C records 5C34CA-5C524C, including the encyclopedia-only
``ゲルググＪＧ`` label that must display the canonical Korean unit name
``겔구그 예거``.  The parent main TIP and live SaveRAM are never modified.

Storage policy:
* payload >= 4 bytes: private ext3 phrase token plus 0x01 padding;
* payload 2-3 bytes: existing exact stock phrase or a strongly retired slot;
* record length and the original NUL terminator stay fixed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    phrase_cursor,
    verify_non_target_invariance,
)
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
    dict_token_safe_in_zstring,
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
WORKLIST = ROOT / "out/patch/encyclopedia_ms_batch01_worklist.json"
CATALOG = ROOT / "data/encyclopedia_ms_batch01_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/encyclopedia_ms_batch01_candidate.wsc"
OUT_SAVE = ROOT / "sram/encyclopedia_ms_batch01_candidate.sav"
REPORT = ROOT / "out/patch/encyclopedia_ms_batch01_report.json"

EXPECTED_PARENT_SHA = "abd9c29656cef765960c1a7d9220b7cfe862f36ebff7c48a46e9893cc14770f3"
EXPECTED_ROWS = 395
EXPECTED_SHORT = 4
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": digest(payload)}


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


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    result = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if result is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(result[0]), int(result[1])


def exact_slots(dictionary: Any, tbl: Tbl, phrases: set[str]) -> dict[str, list[int]]:
    result = {phrase: [] for phrase in phrases}
    for index in range(min(int(dictionary.count), 0x1000)):
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            text = dictionary.expand(bytes(dictionary.raw_entry(index)), tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        if text in result:
            result[text].append(index)
    return result


def load_rows(parent: bytes) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    input_tip = ((worklist.get("inputs") or {}).get("tip") or {})
    if str(input_tip.get("sha256") or "").lower() != EXPECTED_PARENT_SHA:
        raise BuildError("worklist is not bound to the current parent TIP")
    if int(((catalog.get("scope") or {}).get("expected_records") or 0)) != EXPECTED_ROWS:
        raise BuildError("catalog expected-record count drifted")
    provenance = catalog.get("provenance") or {}
    if provenance.get("translation_source") not in {"llm", "human", "user_verified", "curated_project_data"}:
        raise BuildError("catalog translation source is not approved")
    if provenance.get("review_status") not in {"approved", "user_verified"}:
        raise BuildError("catalog review status is not approved")
    if provenance.get("legacy_machine_translation_used") is not False:
        raise BuildError("legacy machine-translation provenance is forbidden")

    source_rows = [
        dict(row)
        for row in (worklist.get("records") or [])
        if row.get("status") in {"japanese_residual", "name_alias_mismatch"}
    ]
    by_abs = {str(row.get("abs") or "").upper(): row for row in source_rows}
    translations = list(catalog.get("lines") or [])
    if len(source_rows) != EXPECTED_ROWS or len(translations) != EXPECTED_ROWS:
        raise BuildError(
            f"population drifted: worklist={len(source_rows)} catalog={len(translations)}"
        )
    if len(by_abs) != EXPECTED_ROWS:
        raise BuildError("duplicate worklist address")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for translation in translations:
        address = str(translation.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate catalog address {address}")
        seen.add(address)
        source = by_abs.get(address)
        if source is None:
            raise BuildError(f"catalog address is outside the audited target set: {address}")
        ko = normalize_ko_text(str(translation.get("ko") or ""))
        if not ko or any(is_japanese_character(character) for character in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        if len(ko) > 13:
            raise BuildError(f"encyclopedia line exceeds 13 visual cells at {address}: {len(ko)} {ko!r}")
        logical = int(address, 16)
        payload, terminator = payload_at(parent, logical)
        expected_payload = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        if payload != expected_payload:
            raise BuildError(f"parent payload drifted at {address}")
        if len(payload) != int(source.get("payload_len") or 0):
            raise BuildError(f"payload length drifted at {address}")
        if terminator != stock_base(parent) + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {address}")
        if len(payload) < 2:
            raise BuildError(f"unsupported one-byte target at {address}")
        rows.append(
            {
                **source,
                "logical": logical,
                "ko": ko,
                "payload": payload,
                "payload_len": len(payload),
                "strategy": "private_ext3" if len(payload) >= 4 else "short_stock",
            }
        )

    if seen != set(by_abs):
        missing = sorted(set(by_abs) - seen)
        raise BuildError(f"catalog misses audited targets: {missing[:8]}")
    rows.sort(key=lambda row: int(row["logical"]))
    if sum(row["strategy"] == "short_stock" for row in rows) != EXPECTED_SHORT:
        raise BuildError("short-record population drifted")
    return worklist, catalog, rows


def verify_targets(rom: bytes, rows: list[dict[str, Any]], dictionary: Any, tbl: Tbl) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(rom, logical)
        rendered = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            failures.append(
                {"abs": f"{logical:06X}", "reason": "render_mismatch", "expected": expected, "actual": rendered}
            )
        elif any(is_japanese_character(character) for character in rendered):
            failures.append({"abs": f"{logical:06X}", "reason": "japanese_residual", "actual": rendered})
        elif len(payload) != int(row["payload_len"]):
            failures.append({"abs": f"{logical:06X}", "reason": "payload_length_changed"})
        elif rom[terminator] != 0:
            failures.append({"abs": f"{logical:06X}", "reason": "terminator_changed"})
    return failures


def choose_ext3_segment(
    inventory: Any,
    *,
    phrase_count: int,
    phrase_bytes: int,
    num_banks: int,
) -> tuple[int, list[int], int]:
    options: list[tuple[int, int, list[int]]] = []
    for bank_index in range(num_banks):
        segment = EXP3_SEG0 + bank_index
        slots = sorted(
            index for index in inventory.ext3_free if bank_local_for_index(index)[0] == segment
        )
        room = int(inventory.ext3_bank_room.get(bank_index, 0))
        if len(slots) >= phrase_count and room >= phrase_bytes:
            options.append((room, segment, slots))
    if not options:
        raise BuildError(
            f"no ext3 bank can hold phrases: count={phrase_count} bytes={phrase_bytes}"
        )
    room, segment, slots = max(options, key=lambda item: (item[0], len(item[2])))
    return segment, slots, room


def main() -> int:
    parent = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("main TIP identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("main SaveRAM is missing or has the wrong size")

    worklist, catalog, rows = load_rows(parent)
    direct = [row for row in rows if row["strategy"] == "private_ext3"]
    short = [row for row in rows if row["strategy"] == "short_stock"]

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)

    direct_phrases = sorted({str(row["ko"]) for row in direct})
    encoded_by_phrase = {phrase: encode_phrase(phrase, tbl) for phrase in direct_phrases}
    ext3_need = sum(len(payload) + 1 for payload in encoded_by_phrase.values())
    alloc_segment, free_ext3, ext3_room = choose_ext3_segment(
        inventory,
        phrase_count=len(direct_phrases),
        phrase_bytes=ext3_need,
        num_banks=num_banks,
    )
    ext3_assignment = {
        phrase: index for phrase, index in zip(direct_phrases, free_ext3)
    }
    ext3_payloads = {
        ext3_assignment[phrase]: payload for phrase, payload in encoded_by_phrase.items()
    }

    short_phrases = {str(row["ko"]) for row in short}
    exact = exact_slots(parent_dictionary, tbl, short_phrases)
    reuse = {phrase: slots for phrase, slots in exact.items() if slots}
    new_phrases = sorted(short_phrases - set(reuse))
    retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected_retired = retired[: len(new_phrases)]
    if len(selected_retired) != len(new_phrases):
        raise BuildError("not enough strong-retired stock slots")
    selected_set = set(selected_retired)
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_set)
    current_nested = nested_occurrence_map(parent_dictionary, wanted=selected_set, ext3_aware=True)
    current_raw = _raw_pair_hits(parent, selected_retired)
    if any(
        current_external.get(index) or current_nested.get(index) or current_raw.get(index)
        for index in selected_retired
    ):
        raise BuildError("a selected retired stock slot is still reachable")
    stock_assignment = {phrase: min(slots) for phrase, slots in reuse.items()}
    stock_payloads: dict[int, bytes] = {}
    for phrase, index in zip(new_phrases, selected_retired):
        stock_assignment[phrase] = index
        stock_payloads[index] = encode_phrase(phrase, tbl)

    candidate = bytearray(parent)
    ext3_bank_before = bytes(slice_expansion_bank(parent, alloc_segment))
    ext3_cursor_before = phrase_cursor(ext3_bank_before)
    ext3_info, ext3_guard = write_ext3_slots_guarded(
        candidate, ext3_payloads, union=union, num_banks=num_banks
    )
    if int(ext3_info.get("written") or 0) != len(ext3_payloads):
        raise BuildError("ext3 writer did not write every phrase")

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
        pointers_written = list(pointers_before)
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
        raise BuildError("stock pointer change set differs from selected new slots")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        capacity = int(row["payload_len"])
        phrase = str(row["ko"])
        if capacity >= 4:
            index = ext3_assignment[phrase]
            token = token_from_ext3_index(index, num_banks=num_banks)
            strategy = "private_ext3"
            allocation = {"ext3_index": f"{index:05X}"}
        else:
            index = stock_assignment[phrase]
            token = token_from_dict_index(index)
            strategy = "existing_exact_stock" if phrase in reuse else "strong_retired_stock"
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
                "jp": row.get("jp"),
                "before": row.get("current"),
                "after": phrase,
                "payload_len": capacity,
                "strategy": strategy,
                **allocation,
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    target_failures = verify_targets(candidate_bytes, rows, candidate_dictionary, tbl)
    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )

    ext3_bank_after = bytes(slice_expansion_bank(candidate_bytes, alloc_segment))
    ext3_cursor_after = phrase_cursor(ext3_bank_after)
    ext3_bank_file = alloc_segment * BANK_SIZE
    ext3_pointer_extents = []
    for index in ext3_payloads:
        _segment, local = bank_local_for_index(index)
        ext3_pointer_extents.append(
            (ext3_bank_file + local * 2, ext3_bank_file + local * 2 + 2)
        )
    ext3_phrase_extent = (
        ext3_bank_file + ext3_cursor_before,
        ext3_bank_file + ext3_cursor_after,
    )

    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (
            stock_bank_file + DICT_PTR_START + index * 2,
            stock_bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in stock_payloads
    ]
    stock_phrase_extent = (
        stock_bank_file + stock_cursor_before,
        stock_bank_file + stock_cursor_after,
    )
    allowed = (
        target_extents
        + ext3_pointer_extents
        + stock_pointer_extents
        + [ext3_phrase_extent, stock_phrase_extent, (len(parent) - 2, len(parent))]
    )
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    other_ext3_unchanged = all(
        bytes(slice_expansion_bank(parent, segment))
        == bytes(slice_expansion_bank(candidate_bytes, segment))
        for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if segment != alloc_segment
    )
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = (
        parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]
    )

    ok = (
        not target_failures
        and invariance.get("ok") is True
        and not unaccounted
        and other_ext3_unchanged
        and runtime_unchanged
        and digest(MAIN.read_bytes()) == EXPECTED_PARENT_SHA
        and MAIN_SAVE.read_bytes() == main_save
    )
    if not ok:
        raise BuildError("encyclopedia candidate static verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_encyclopedia_ms_batch01_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "source_worklist": identity(WORKLIST),
        "source_catalog": identity(CATALOG),
        "provenance": catalog.get("provenance"),
        "counts": {
            "targets": len(rows),
            "japanese_residual_targets": sum(row.get("status") == "japanese_residual" for row in rows),
            "name_alias_mismatch_targets": sum(row.get("status") == "name_alias_mismatch" for row in rows),
            "ext3_records": len(direct),
            "short_stock_records": len(short),
            "ext3_unique_phrases": len(ext3_payloads),
            "short_unique_phrases": len(short_phrases),
            "existing_exact_stock_phrases": len(reuse),
            "new_retired_stock_phrases": len(stock_payloads),
            "target_failures": len(target_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segment": f"{alloc_segment:02X}",
            "ext3_cursor_before": f"{ext3_cursor_before:04X}",
            "ext3_cursor_after": f"{ext3_cursor_after:04X}",
            "ext3_phrase_bytes": ext3_cursor_after - ext3_cursor_before,
            "ext3_room_before": ext3_room,
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
            "stock_phrase_bytes": stock_cursor_after - stock_cursor_before,
            "selected_retired_slots": [f"{index:04X}" for index in selected_retired],
            "existing_exact_slots": {
                phrase: [f"{index:04X}" for index in slots]
                for phrase, slots in sorted(reuse.items())
            },
        },
        "guards": {
            "ext3": ext3_guard.as_dict(),
            "selected_retired_current_external_zero": True,
            "selected_retired_current_nested_zero": True,
            "selected_retired_current_raw_zero": True,
        },
        "verification": {
            "all_targets_render_exact": not target_failures,
            "target_japanese_residuals_zero": not target_failures,
            "canonical_gelgoog_jager_exact": any(
                row["abs"] == "5C515C" and row["after"].rstrip("　 ") == "겔구그　예거"
                for row in applied
            ),
            "non_target_invariance": invariance,
            "diffs_bounded": not unaccounted,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_PARENT_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save,
            "candidate_saveram_matches_live": OUT_SAVE.read_bytes() == main_save,
            "all_targets_within_13_visual_cells": all(len(str(row["ko"])) <= 13 for row in rows),
            "record_length_and_terminator_preserved": True,
        },
        "diff": {
            "changed_bytes_from_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "applied": applied,
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
