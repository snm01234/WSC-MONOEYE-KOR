#!/usr/bin/env python3
"""Build the remaining reviewed dialogue/prose candidate in two safe stages.

Stage A replaces the 88 records whose bodies can hold an ordinary four-byte
ext3 portal.  Stage B starts from that candidate and handles the 20 shorter
records with ordinary two-byte stock tokens: five reviewed phrases reuse an
already exact stock slot and eleven new phrases use strongly-retired slots.

Both stages preserve every record boundary, prefix, payload length and NUL
terminator.  The current main TIP and SaveRAM are never modified.  Promotion is
intentionally left for a separate user-approved step after visual testing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_current_untranslated_dialogue import (
    classify_text,
    current_strong_retired_slots,
    identity,
    load_json,
    sha256,
)
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from expand_dictionary import write_dictionary_slots_spill
from hangul_marker import marker_code
from mixed_residual_reference_union import (
    _reference_scopes,
    _walk_zstring_range,
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
    load_rom,
    read_encoded_z_safe,
    slice_bank,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import (
    EXP3_SEG0,
    bank_local_for_index,
    token_from_ext3_index,
)

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
AUDIT_PATH = ROOT / "out/patch/current_untranslated_dialogue_audit.json"

STAGE_A_ROM = ROOT / "out/patch/remaining_dialogue_ext3_candidate.wsc"
STAGE_A_SAVE = ROOT / "sram/remaining_dialogue_ext3_candidate.sav"
STAGE_A_REPORT = ROOT / "out/patch/remaining_dialogue_ext3_report.json"

FINAL_ROM = ROOT / "out/patch/remaining_dialogue_complete_candidate.wsc"
FINAL_SAVE = ROOT / "sram/remaining_dialogue_complete_candidate.sav"
FINAL_REPORT = ROOT / "out/patch/remaining_dialogue_complete_report.json"

EXPECTED_PARENT_SHA256 = "31acde8c486b5ba13bc00b74ae019444608051478c5e0b874516e74f4cab8eb6"
EXPECTED_AUDIT_SHA256 = "fb281cf7835647ac400e9e287930c7cebd60ca11e507a1bdba24b1e6cbea9680"
ALLOC_SEG = 0x1C


class BuildError(RuntimeError):
    pass


def write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise BuildError("diff inputs differ in size")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for offset, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = offset
        elif left == right and start is not None:
            runs.append((start, offset))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def covered(run: tuple[int, int], extents: Iterable[tuple[int, int]]) -> bool:
    lo, hi = run
    cursor = lo
    for left, right in sorted(extents):
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= hi:
            return True
    return cursor >= hi


def phrase_cursor(bank: bytes) -> int:
    empty_at = 0x1000 * 2
    cursor = empty_at + 1
    for local in range(0x1000):
        pointer = bank[local * 2] | (bank[local * 2 + 1] << 8)
        if pointer <= empty_at or pointer >= BANK_SIZE:
            continue
        end = pointer
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        if end >= BANK_SIZE:
            raise BuildError(f"unterminated ext3 phrase at {pointer:04X}")
        cursor = max(cursor, end + 1)
    return cursor


def encode_phrase(text: str, tbl: Tbl) -> bytes:
    normalized = normalize_ko_text(text)
    encoded = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot encode reviewed phrase: {text!r}")
    return bytes(encoded)


def load_target_rows() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    if sha256(AUDIT_PATH.read_bytes()) != EXPECTED_AUDIT_SHA256:
        raise BuildError("current untranslated audit identity drifted")
    audit = load_json(AUDIT_PATH)
    if audit.get("ok") is not True:
        raise BuildError("current untranslated audit is not successful")
    rows: list[dict[str, Any]] = []
    for category, category_rows in (audit.get("records") or {}).items():
        for source in category_rows or []:
            row = dict(source)
            row["category"] = category
            row["logical"] = int(str(row["abs"]), 16)
            row["prefix_bytes"] = len(bytes.fromhex(str(row.get("prefix_hex") or "")))
            row["ko"] = normalize_ko_text(str(row.get("ko") or ""))
            if not row["ko"]:
                raise BuildError(f"empty reviewed Korean: {row['record_id']}")
            rows.append(row)
    rows.sort(key=lambda row: int(row["logical"]))
    direct = [row for row in rows if int(row["body_capacity"]) >= 4]
    short = [row for row in rows if int(row["body_capacity"]) < 4]
    if len(rows) != 108 or len(direct) != 88 or len(short) != 20:
        raise BuildError(
            f"target population drifted: total={len(rows)}, direct={len(direct)}, short={len(short)}"
        )
    if len({row["ko"] for row in direct}) != 88:
        raise BuildError("direct ext3 phrases are no longer unique")
    if len({row["ko"] for row in short}) != 16:
        raise BuildError("short phrase population drifted")
    return audit, direct, short


def payload_at(rom: bytes | bytearray, logical: int, *, max_len: int = 256) -> tuple[bytes, int]:
    result = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if result is None:
        raise BuildError(f"unreadable target record at {logical:06X}")
    return bytes(result[0]), int(result[1])


def bind_rows(rom: bytes, rows: Sequence[Mapping[str, Any]]) -> None:
    base = stock_base(rom)
    for row in rows:
        logical = int(row["logical"])
        payload, term_file = payload_at(rom, logical)
        expected = bytes.fromhex(str(row["payload_hex"]))
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        if payload != expected:
            raise BuildError(f"parent payload drifted: {row['record_id']}")
        if not payload.startswith(prefix):
            raise BuildError(f"prefix mismatch: {row['record_id']}")
        if len(payload) - len(prefix) != int(row["body_capacity"]):
            raise BuildError(f"body capacity drifted: {row['record_id']}")
        if term_file != base + logical + len(payload) or rom[term_file] != 0:
            raise BuildError(f"terminator drifted: {row['record_id']}")


def verify_target_renders(
    rom: bytes,
    rows: Sequence[Mapping[str, Any]],
    dictionary: Any,
    tbl: Tbl,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload, term_file = payload_at(rom, logical)
        prefix_len = int(row["prefix_bytes"])
        try:
            rendered = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        except Exception as exc:
            failures.append(
                {"record_id": row["record_id"], "reason": f"decode_failed:{exc}"}
            )
            continue
        expected = str(row["ko"]).rstrip("\u3000 \t")
        classified = classify_text(rendered)
        if rendered != expected:
            failures.append(
                {
                    "record_id": row["record_id"],
                    "reason": "render_mismatch",
                    "expected": expected,
                    "actual": rendered,
                }
            )
        elif int(classified["japanese"]):
            failures.append(
                {
                    "record_id": row["record_id"],
                    "reason": "japanese_residual",
                    "actual": rendered,
                }
            )
        elif rom[term_file] != 0:
            failures.append({"record_id": row["record_id"], "reason": "terminator_changed"})
    return failures


def verify_non_target_invariance(
    before: bytes,
    after: bytes,
    *,
    before_dictionary: Any,
    after_dictionary: Any,
    tbl: Tbl,
    excluded: set[int],
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    checked = 0
    after_base = stock_base(after)
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(
            before, lo, hi, region=region, max_len=max_len
        ):
            if logical in excluded:
                continue
            checked += 1
            result = read_encoded_z_safe(after, after_base + logical, max_len=max_len)
            if result is None:
                failures.append(
                    {"abs": f"{logical:06X}", "region": region, "reason": "after_unreadable"}
                )
                continue
            after_payload = bytes(result[0])
            if after_payload != payload:
                failures.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "kind": kind,
                        "reason": "payload_changed",
                    }
                )
                continue
            try:
                before_text = before_dictionary.expand(payload, tbl)
                after_text = after_dictionary.expand(after_payload, tbl)
            except Exception as exc:
                failures.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "reason": f"decode_failed:{exc}",
                    }
                )
                continue
            if before_text != after_text:
                failures.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "reason": "render_changed",
                        "before": before_text,
                        "after": after_text,
                    }
                )
    return {
        "ok": not failures,
        "records_checked": checked,
        "failure_count": len(failures),
        "failures": failures[:100],
    }


def build_stage_a(
    parent: bytes,
    original: bytes,
    rows: list[dict[str, Any]],
    *,
    tbl: Tbl,
    ext_meta: Mapping[str, Any],
    ext3_meta: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    parent_dictionary = make_dictionary_ext3(parent, dict(ext_meta), dict(ext3_meta))
    bind_rows(parent, rows)
    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    num_banks = int(ext3_meta.get("num_banks") or 0)
    free_indices = sorted(
        index
        for index in inventory.ext3_free
        if bank_local_for_index(index)[0] == ALLOC_SEG
    )
    phrases = sorted(rows, key=lambda row: int(row["logical"]))
    if len(free_indices) < len(phrases):
        raise BuildError("allocation bank lacks free ext3 slots")
    bank_index = ALLOC_SEG - EXP3_SEG0
    available_room = int(inventory.ext3_bank_room.get(bank_index, 0))

    slot_payload: dict[int, bytes] = {}
    assignments: dict[str, int] = {}
    bytes_needed = 0
    for row, index in zip(phrases, free_indices):
        encoded = encode_phrase(str(row["ko"]), tbl)
        slot_payload[index] = encoded
        assignments[str(row["record_id"])] = index
        bytes_needed += len(encoded) + 1
    if bytes_needed > available_room:
        raise BuildError("allocation bank lacks ext3 phrase room")

    candidate = bytearray(parent)
    bank_before = slice_expansion_bank(parent, ALLOC_SEG)
    cursor_before = phrase_cursor(bank_before)
    write_info, guard = write_ext3_slots_guarded(
        candidate,
        slot_payload,
        union=union,
        num_banks=num_banks,
    )
    if write_info.get("written") != len(slot_payload):
        raise BuildError("not all ext3 slots were written")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied_rows: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload, _term = payload_at(parent, logical)
        prefix_len = int(row["prefix_bytes"])
        body_capacity = int(row["body_capacity"])
        index = assignments[str(row["record_id"])]
        token = token_from_ext3_index(index, num_banks=num_banks)
        replacement = token + b"\x01" * (body_capacity - len(token))
        if len(replacement) != body_capacity:
            raise BuildError(f"stage A replacement length drift: {row['record_id']}")
        file_start = base + logical + prefix_len
        candidate[file_start : file_start + body_capacity] = replacement
        target_extents.append((file_start, file_start + body_capacity))
        applied_rows.append(
            {
                "record_id": row["record_id"],
                "abs": row["abs"],
                "category": row["category"],
                "ko": row["ko"],
                "body_capacity": body_capacity,
                "ext3_index": f"{index:05X}",
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(
        candidate_bytes, dict(ext_meta), dict(ext3_meta)
    )
    target_failures = verify_target_renders(candidate_bytes, rows, candidate_dictionary, tbl)
    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )

    bank_after = slice_expansion_bank(candidate_bytes, ALLOC_SEG)
    cursor_after = phrase_cursor(bank_after)
    pointer_extents: list[tuple[int, int]] = []
    bank_file = ALLOC_SEG * BANK_SIZE
    for index in slot_payload:
        _segment, local = bank_local_for_index(index)
        pointer_extents.append((bank_file + local * 2, bank_file + local * 2 + 2))
    phrase_extent = (bank_file + cursor_before, bank_file + cursor_after)
    allowed = target_extents + pointer_extents + [phrase_extent, (len(parent) - 2, len(parent))]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]

    stock_unchanged = slice_bank(parent, SEG_DICT) == slice_bank(candidate_bytes, SEG_DICT)
    other_ext3_unchanged = all(
        slice_expansion_bank(parent, segment)
        == slice_expansion_bank(candidate_bytes, segment)
        for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if segment != ALLOC_SEG
    )
    runtime_lo = stock_base(parent) + 0x7A0600
    runtime_hi = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_lo:runtime_hi] == candidate_bytes[runtime_lo:runtime_hi]

    ok = (
        not target_failures
        and invariance["ok"]
        and not unaccounted
        and stock_unchanged
        and other_ext3_unchanged
        and runtime_unchanged
    )
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_remaining_dialogue_candidate.py",
        "stage": "A_ext3",
        "status": "candidate_static_verified" if ok else "failed",
        "ok": ok,
        "published": False,
        "parent": identity(PARENT, parent),
        "candidate": identity(STAGE_A_ROM, candidate_bytes),
        "candidate_save": identity(STAGE_A_SAVE) if STAGE_A_SAVE.is_file() else None,
        "counts": {
            "targets": len(rows),
            "unique_phrases": len(slot_payload),
            "new_ext3_slots": len(slot_payload),
            "phrase_bytes": cursor_after - cursor_before,
            "target_failures": len(target_failures),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "capacity": {
            "allocation_segment": f"{ALLOC_SEG:02X}",
            "free_slots_before": len(free_indices),
            "phrase_room_before": available_room,
            "phrase_bytes_required": bytes_needed,
            "phrase_room_after": available_room - bytes_needed,
        },
        "guard": guard.as_dict(),
        "ext3_write": write_info,
        "verification": {
            "target_render_exact": not target_failures,
            "non_target_invariance": invariance,
            "diffs_within_approved_extents": not unaccounted,
            "stock_dictionary_unchanged": stock_unchanged,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "record_lengths_preserved": True,
            "prefixes_preserved": True,
            "terminators_preserved": True,
        },
        "target_failures": target_failures,
        "unaccounted_diff_runs": unaccounted,
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "records": applied_rows,
        "promotion": "blocked_pending_visual_verification",
    }
    if not ok:
        raise BuildError("stage A static verification failed")
    return candidate_bytes, report


def exact_two_byte_slots(
    dictionary: Any,
    tbl: Tbl,
    phrases: set[str],
) -> dict[str, list[int]]:
    """Find exact ordinary two-byte phrases in stock or the 0xFxx extension."""
    result: dict[str, list[int]] = {phrase: [] for phrase in phrases}
    for index in range(min(int(dictionary.count), 0x1000)):
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            rendered = dictionary.expand(bytes(dictionary.raw_entry(index)), tbl).rstrip(
                "\u3000 \t"
            )
        except Exception:
            continue
        if rendered in result:
            result[rendered].append(index)
    return result


def build_stage_b(
    parent: bytes,
    original: bytes,
    short_rows: list[dict[str, Any]],
    all_rows: list[dict[str, Any]],
    *,
    tbl: Tbl,
    ext_meta: Mapping[str, Any],
    ext3_meta: Mapping[str, Any],
    stage_a_report: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    parent_dictionary = make_dictionary_ext3(parent, dict(ext_meta), dict(ext3_meta))
    phrases = {str(row["ko"]) for row in short_rows}
    exact = exact_two_byte_slots(parent_dictionary, tbl, phrases)
    reusable = {phrase: slots for phrase, slots in exact.items() if slots}
    if len(reusable) != 5:
        raise BuildError(f"exact stock reuse population drifted: {len(reusable)}")
    new_phrases = sorted(phrases - set(reusable))
    if len(new_phrases) != 11:
        raise BuildError(f"new short phrase population drifted: {len(new_phrases)}")

    assignments: dict[str, int] = {
        phrase: min(slots) for phrase, slots in reusable.items()
    }
    strong_retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected = [index for index in strong_retired if index not in set(assignments.values())][
        : len(new_phrases)
    ]
    if len(selected) != len(new_phrases):
        raise BuildError("not enough strong-retired stock slots")

    wanted = set(selected)
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    current_nested = nested_occurrence_map(
        parent_dictionary, wanted=wanted, ext3_aware=True
    )
    current_raw = _raw_pair_hits(parent, selected)
    if any(current_external.get(index) for index in selected):
        raise BuildError("selected retired stock slot still has an external consumer")
    if any(current_nested.get(index) for index in selected):
        raise BuildError("selected retired stock slot still has a nested parent")
    if any(current_raw.get(index) for index in selected):
        raise BuildError("selected retired stock slot still has a raw token pair")

    slot_payload: dict[int, bytes] = {}
    for phrase, index in zip(new_phrases, selected):
        assignments[phrase] = index
        slot_payload[index] = encode_phrase(phrase, tbl)

    candidate = bytearray(parent)
    current_locs = _working_two_byte_external_refs(parent)
    phrase_start = _stock_phrase_cursor(parent)
    pointers_before = list(Dictionary(parent).ptrs)
    pointers_after_write, phrase_end = write_dictionary_slots_spill(
        candidate,
        slot_payload,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=current_locs,
    )
    dictionary_after_stock = Dictionary(candidate)
    pointers_after = list(dictionary_after_stock.ptrs)
    if pointers_after != pointers_after_write:
        raise BuildError("stock writer pointer result differs from ROM")
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != set(selected):
        raise BuildError("stock pointer change set differs from selected retired slots")
    for index, encoded in slot_payload.items():
        if bytes(dictionary_after_stock.raw_entry(index)) != encoded:
            raise BuildError(f"stock phrase write verification failed: {index:04X}")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied_rows: list[dict[str, Any]] = []
    for row in short_rows:
        logical = int(row["logical"])
        payload, _term = payload_at(parent, logical)
        prefix_len = int(row["prefix_bytes"])
        body_capacity = int(row["body_capacity"])
        phrase = str(row["ko"])
        index = assignments[phrase]
        token = token_from_dict_index(index)
        replacement = token + b"\x01" * (body_capacity - len(token))
        if len(replacement) != body_capacity:
            raise BuildError(f"stage B replacement length drift: {row['record_id']}")
        file_start = base + logical + prefix_len
        candidate[file_start : file_start + body_capacity] = replacement
        target_extents.append((file_start, file_start + body_capacity))
        applied_rows.append(
            {
                "record_id": row["record_id"],
                "abs": row["abs"],
                "category": row["category"],
                "ko": phrase,
                "body_capacity": body_capacity,
                "slot": f"{index:04X}",
                "token_hex": token.hex().upper(),
                "strategy": "existing_exact" if phrase in reusable else "retired_stock_slot",
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(
        candidate_bytes, dict(ext_meta), dict(ext3_meta)
    )
    all_failures = verify_target_renders(candidate_bytes, all_rows, candidate_dictionary, tbl)
    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in short_rows},
    )

    bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    pointer_extents = [
        (
            bank_file + DICT_PTR_START + index * 2,
            bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in selected
    ]
    phrase_extent = (bank_file + phrase_start, bank_file + phrase_end)
    allowed = target_extents + pointer_extents + [phrase_extent, (len(parent) - 2, len(parent))]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]

    num_banks = int(ext3_meta.get("num_banks") or 0)
    ext3_unchanged = all(
        slice_expansion_bank(parent, segment)
        == slice_expansion_bank(candidate_bytes, segment)
        for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
    )
    runtime_lo = stock_base(parent) + 0x7A0600
    runtime_hi = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_lo:runtime_hi] == candidate_bytes[runtime_lo:runtime_hi]

    ok = (
        not all_failures
        and invariance["ok"]
        and not unaccounted
        and ext3_unchanged
        and runtime_unchanged
    )
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_remaining_dialogue_candidate.py",
        "stage": "B_short_stock_complete",
        "status": "candidate_static_verified" if ok else "failed",
        "ok": ok,
        "published": False,
        "parent_stage_a": identity(STAGE_A_ROM, parent),
        "parent_stage_a_report": dict(stage_a_report),
        "candidate": identity(FINAL_ROM, candidate_bytes),
        "candidate_save": identity(FINAL_SAVE) if FINAL_SAVE.is_file() else None,
        "counts": {
            "stage_b_targets": len(short_rows),
            "all_targets_exact": len(all_rows) if not all_failures else len(all_rows) - len(all_failures),
            "short_unique_phrases": len(phrases),
            "existing_exact_phrases": len(reusable),
            "new_stock_phrases": len(slot_payload),
            "strong_retired_available": len(strong_retired),
            "target_failures": len(all_failures),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "stock_allocation": {
            "spill_floor": f"{SPILL_FLOOR:04X}",
            "phrase_start": f"{phrase_start:04X}",
            "phrase_end": f"{phrase_end:04X}",
            "phrase_bytes": phrase_end - phrase_start,
            "selected_retired_slots": [f"{index:04X}" for index in selected],
            "existing_exact_slots": {
                phrase: [f"{index:04X}" for index in slots]
                for phrase, slots in sorted(reusable.items())
            },
        },
        "verification": {
            "all_108_target_renders_exact": not all_failures,
            "all_108_japanese_residuals_zero": not all_failures,
            "non_target_invariance": invariance,
            "diffs_within_approved_extents": not unaccounted,
            "ext3_banks_unchanged_from_stage_a": ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "selected_slots_current_external_zero": True,
            "selected_slots_current_nested_zero": True,
            "selected_slots_current_raw_zero": True,
            "record_lengths_preserved": True,
            "prefixes_preserved": True,
            "terminators_preserved": True,
        },
        "target_failures": all_failures,
        "unaccounted_diff_runs": unaccounted,
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "records": applied_rows,
        "promotion": "blocked_pending_visual_verification",
    }
    if not ok:
        raise BuildError("stage B static verification failed")
    return candidate_bytes, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--parent-save", type=Path, default=PARENT_SAVE)
    parser.add_argument("--stage-a-rom", type=Path, default=STAGE_A_ROM)
    parser.add_argument("--stage-a-save", type=Path, default=STAGE_A_SAVE)
    parser.add_argument("--stage-a-report", type=Path, default=STAGE_A_REPORT)
    parser.add_argument("--final-rom", type=Path, default=FINAL_ROM)
    parser.add_argument("--final-save", type=Path, default=FINAL_SAVE)
    parser.add_argument("--final-report", type=Path, default=FINAL_REPORT)
    args = parser.parse_args(argv)

    parent = bytes(load_rom(args.parent))
    if sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(parent) != 16_777_216:
        raise BuildError("main TIP is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise BuildError("current 32 KiB SaveRAM is missing")

    audit, direct_rows, short_rows = load_target_rows()
    if str((audit.get("current_tip") or {}).get("sha256")) != sha256(parent):
        raise BuildError("audit is not bound to current main TIP")
    bind_rows(parent, direct_rows + short_rows)

    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)

    stage_a_bytes, stage_a_report = build_stage_a(
        parent,
        original,
        direct_rows,
        tbl=tbl,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    atomic_write(args.stage_a_rom, stage_a_bytes)
    shutil.copy2(args.parent_save, args.stage_a_save)
    stage_a_report["candidate"] = identity(args.stage_a_rom, stage_a_bytes)
    stage_a_report["candidate_save"] = identity(args.stage_a_save)
    write_json(args.stage_a_report, stage_a_report)

    all_rows = sorted(direct_rows + short_rows, key=lambda row: int(row["logical"]))
    final_bytes, final_report = build_stage_b(
        stage_a_bytes,
        original,
        short_rows,
        all_rows,
        tbl=tbl,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
        stage_a_report=identity(args.stage_a_report),
    )
    atomic_write(args.final_rom, final_bytes)
    shutil.copy2(args.parent_save, args.final_save)
    final_report["candidate"] = identity(args.final_rom, final_bytes)
    final_report["candidate_save"] = identity(args.final_save)
    final_report["stage_a_candidate"] = identity(args.stage_a_rom, stage_a_bytes)
    final_report["stage_a_report"] = identity(args.stage_a_report)
    final_report["source_audit"] = identity(AUDIT_PATH)
    write_json(args.final_report, final_report)

    print(
        json.dumps(
            {
                "ok": True,
                "stage_a": {
                    "rom": identity(args.stage_a_rom),
                    "save": identity(args.stage_a_save),
                    "report": identity(args.stage_a_report),
                    "targets": len(direct_rows),
                },
                "final": {
                    "rom": identity(args.final_rom),
                    "save": identity(args.final_save),
                    "report": identity(args.final_report),
                    "targets": len(all_rows),
                },
                "main_tip_modified": False,
                "promotion": "blocked_pending_visual_verification",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
