#!/usr/bin/env python3
"""Build the reviewed stage-2A system/UI localization candidate.

Parent: ``broad_residual_stage1_candidate.wsc``.
Target set: 94 reviewed bank-75 system/UI records: 88 coherent records from
75:B2E3–75:B888 plus six exact-text duplicates later in the table, listed
explicitly in ``data/broad_stage2_ui_system_ko.json``.  Five ambiguous format
fragments and all pre-75:B2E3 walker noise are deliberately excluded.

Storage policy:
* body >= 4 bytes: private ext3 phrase, deduplicated by Korean text;
* body 2–3 bytes: existing exact stock phrase or a strongly retired stock slot.

Every record keeps its original prefix, payload length and NUL terminator.  The
main TIP and live SaveRAM are never modified.  Output is a candidate only.
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
PARENT = ROOT / "out/patch/broad_residual_stage1_candidate.wsc"
PARENT_SAVE = ROOT / "sram/broad_residual_stage1_candidate.sav"
PARENT_REPORT = ROOT / "out/patch/broad_residual_stage1_report.json"
CLASSIFICATION = ROOT / "out/patch/broad_japanese_residual_classification.json"
CATALOG = ROOT / "data/broad_stage2_ui_system_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/broad_stage2_ui_system_candidate.wsc"
OUT_SAVE = ROOT / "sram/broad_stage2_ui_system_candidate.sav"
REPORT = ROOT / "out/patch/broad_stage2_ui_system_report.json"

EXPECTED_MAIN_SHA = "279ce819fa63bea6ee52a307dc920714bb530c2f32e344e2543134dfbccbf7f9"
EXPECTED_PARENT_SHA = "7963100edf892bb736da43b71a89b1bc247554f2b43e37d3d83e910b1cb573ba"
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 94
EXPECTED_DIRECT = 84
EXPECTED_SHORT = 10
EXACT_DUPLICATE_ADDRESSES = {
    0x75BAE6,
    0x75BCE3,
    0x75BCEF,
    0x75BCF4,
    0x75BCF8,
    0x75BE30,
}


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
    classification = json.loads(CLASSIFICATION.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if classification.get("ok") is not True:
        raise BuildError("classification report is not successful")
    by_abs = {
        str(row.get("abs") or "").upper(): row
        for row in classification.get("records") or []
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for translation in catalog.get("lines") or []:
        address = str(translation.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate catalog address {address}")
        seen.add(address)
        source = by_abs.get(address)
        if source is None:
            raise BuildError(f"classification row missing for {address}")
        logical = int(address, 16)
        if not (0x75B2E3 <= logical < 0x75B889 or logical in EXACT_DUPLICATE_ADDRESSES):
            raise BuildError(f"catalog address outside approved system UI scope: {address}")
        if str(source.get("region")) != "name75_ui":
            raise BuildError(f"catalog row is not name75_ui: {address}")
        if int(source.get("body_capacity") or 0) < 2:
            raise BuildError(f"catalog contains a one-byte record: {address}")
        jp = str(translation.get("jp") or "")
        if jp != str(source.get("original_text") or ""):
            raise BuildError(f"catalog JP/source mismatch at {address}")
        ko = normalize_ko_text(str(translation.get("ko") or ""))
        if not ko or any(is_japanese_character(character) for character in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        row = dict(source)
        row["logical"] = logical
        row["ko"] = ko
        row["jp"] = jp
        rows.append(row)
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_ROWS:
        raise BuildError(f"catalog population drifted: expected {EXPECTED_ROWS}, got {len(rows)}")
    return classification, catalog, rows


def bind_rows(parent: bytes, rows: list[dict[str, Any]]) -> None:
    base = stock_base(parent)
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(parent, logical)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        body = bytes.fromhex(str(row.get("body_hex") or ""))
        if payload != prefix + body:
            raise BuildError(f"parent payload drifted at {logical:06X}")
        if len(body) != int(row["body_capacity"]):
            raise BuildError(f"body capacity drifted at {logical:06X}")
        if terminator != base + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {logical:06X}")


def verify_targets(
    rom: bytes,
    rows: list[dict[str, Any]],
    dictionary: Any,
    tbl: Tbl,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(rom, logical)
        prefix_len = int(row.get("prefix_bytes") or 0)
        rendered = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            failures.append(
                {
                    "abs": f"{logical:06X}",
                    "reason": "render_mismatch",
                    "expected": expected,
                    "actual": rendered,
                }
            )
        elif any(is_japanese_character(character) for character in rendered):
            failures.append(
                {"abs": f"{logical:06X}", "reason": "japanese_residual", "actual": rendered}
            )
        elif rom[terminator] != 0:
            failures.append({"abs": f"{logical:06X}", "reason": "terminator_changed"})
    return failures


def main() -> int:
    main_bytes = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    parent = PARENT.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(main_bytes) != ROM_SIZE or digest(main_bytes) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("stage-1 parent identity drifted")
    if len(parent_save) != SAVE_SIZE:
        raise BuildError("stage-1 candidate SaveRAM is missing or wrong size")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or wrong size")
    parent_report = json.loads(PARENT_REPORT.read_text(encoding="utf-8"))
    if parent_report.get("ok") is not True:
        raise BuildError("stage-1 parent report is not successful")
    if ((parent_report.get("candidate") or {}).get("sha256")) != EXPECTED_PARENT_SHA:
        raise BuildError("stage-1 parent report identity mismatch")

    classification, catalog, rows = load_rows(parent)
    bind_rows(parent, rows)
    direct = [row for row in rows if int(row["body_capacity"]) >= 4]
    short = [row for row in rows if 2 <= int(row["body_capacity"]) < 4]
    if len(direct) != EXPECTED_DIRECT or len(short) != EXPECTED_SHORT:
        raise BuildError(
            f"strategy population drifted: direct={len(direct)} short={len(short)}"
        )

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)

    direct_phrases = sorted({str(row["ko"]) for row in direct})
    free_ext3 = sorted(
        index
        for index in inventory.ext3_free
        if bank_local_for_index(index)[0] == ALLOC_SEG
    )
    if len(free_ext3) < len(direct_phrases):
        raise BuildError("not enough ext3 slots in allocation bank")
    ext3_assignment = {
        phrase: index for phrase, index in zip(direct_phrases, free_ext3)
    }
    ext3_payloads = {
        index: encode_phrase(phrase, tbl)
        for phrase, index in ext3_assignment.items()
    }
    ext3_need = sum(len(payload) + 1 for payload in ext3_payloads.values())
    bank_index = ALLOC_SEG - EXP3_SEG0
    ext3_room = int(inventory.ext3_bank_room.get(bank_index, 0))
    if ext3_need > ext3_room:
        raise BuildError(f"not enough ext3 phrase bytes: need {ext3_need}, room {ext3_room}")

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
    current_nested = nested_occurrence_map(
        parent_dictionary, wanted=selected_set, ext3_aware=True
    )
    current_raw = _raw_pair_hits(parent, selected_retired)
    if any(
        current_external.get(index)
        or current_nested.get(index)
        or current_raw.get(index)
        for index in selected_retired
    ):
        raise BuildError("a selected retired stock slot is still reachable")
    stock_assignment = {phrase: min(slots) for phrase, slots in reuse.items()}
    stock_payloads: dict[int, bytes] = {}
    for phrase, index in zip(new_phrases, selected_retired):
        stock_assignment[phrase] = index
        stock_payloads[index] = encode_phrase(phrase, tbl)

    candidate = bytearray(parent)
    ext3_bank_before = bytes(slice_expansion_bank(parent, ALLOC_SEG))
    ext3_cursor_before = phrase_cursor(ext3_bank_before)
    ext3_info, ext3_guard = write_ext3_slots_guarded(
        candidate,
        ext3_payloads,
        union=union,
        num_banks=num_banks,
    )
    if int(ext3_info.get("written") or 0) != len(ext3_payloads):
        raise BuildError("ext3 writer did not write every selected phrase")

    stock_cursor_before = _stock_phrase_cursor(candidate)
    stock_before = Dictionary(candidate)
    pointers_before = list(stock_before.ptrs)
    pointers_written, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        stock_payloads,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=_working_two_byte_external_refs(bytes(candidate)),
    )
    stock_after = Dictionary(candidate)
    pointers_after = list(stock_after.ptrs)
    if pointers_after != pointers_written:
        raise BuildError("stock writer pointer result differs from ROM")
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != set(stock_payloads):
        raise BuildError("stock pointer change set differs from new phrase slots")
    for index, payload in stock_payloads.items():
        if bytes(stock_after.raw_entry(index)) != payload:
            raise BuildError(f"stock phrase write failed for slot {index:04X}")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload, _terminator = payload_at(parent, logical)
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
        if len(replacement) != body_capacity:
            raise BuildError(f"replacement length drift at {logical:06X}")
        start = base + logical + prefix_len
        candidate[start : start + body_capacity] = replacement
        target_extents.append((start, start + body_capacity))
        applied.append(
            {
                "record_id": row["record_id"],
                "abs": f"{logical:06X}",
                "jp": row["jp"],
                "before": row["current_text"],
                "after": phrase,
                "body_capacity": body_capacity,
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

    ext3_bank_after = bytes(slice_expansion_bank(candidate_bytes, ALLOC_SEG))
    ext3_cursor_after = phrase_cursor(ext3_bank_after)
    ext3_bank_file = ALLOC_SEG * BANK_SIZE
    pointer_extents: list[tuple[int, int]] = []
    for index in ext3_payloads:
        _segment, local = bank_local_for_index(index)
        pointer_extents.append(
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
        + pointer_extents
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
        if segment != ALLOC_SEG
    )
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = (
        parent[runtime_start:runtime_end]
        == candidate_bytes[runtime_start:runtime_end]
    )

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
        raise BuildError("stage-2A candidate static verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_broad_stage2_ui_system_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "main_tip": identity(MAIN, main_bytes),
        "parent": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "source_catalog": identity(CATALOG),
        "source_classification": identity(CLASSIFICATION),
        "counts": {
            "targets": len(rows),
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
            "ext3_segment": f"{ALLOC_SEG:02X}",
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
            "non_target_invariance": invariance,
            "diffs_bounded": not unaccounted,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save,
            "candidate_saveram_matches_live": OUT_SAVE.read_bytes() == main_save,
            "prefix_length_terminator_preserved": True,
        },
        "diff": {
            "changed_bytes_from_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "excluded_review_items": [
            {"abs": "75B3FD", "text": "近全", "reason": "abbreviated format label; screen evidence required"},
            {"abs": "75B401", "text": "射全", "reason": "abbreviated format label; screen evidence required"},
            {"abs": "75B41E", "text": "一", "reason": "single glyph format/data candidate"},
            {"abs": "75B48A", "text": "一一一一一", "reason": "separator or format data candidate"},
            {"abs": "75B49E", "text": "降す", "reason": "ambiguous truncated label; screen evidence required"}
        ],
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
