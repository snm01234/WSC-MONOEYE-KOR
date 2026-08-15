#!/usr/bin/env python3
"""Build a screen-proven battle dialogue / ID-command follow-up candidate.

The promoted main TIP already contains translations for the canonical Name75
and damage-line records shown in the screenshots, but exact raw duplicates
remain in bank 5C and one bank-59 battle line remains mixed Japanese/Korean.
This builder patches 19 screen-proven duplicate records with three private ext3
phrases.  Prefix bytes, payload capacities, NUL terminators, runtime hooks, the
main TIP, and live SaveRAM are preserved.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_broad_stage2_dialogue_voice_candidate import atomic_bytes, atomic_json, digest, identity
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, phrase_cursor, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union, write_ext3_slots_guarded
from monoeye_rom import BANK_SIZE, Tbl, read_encoded_z_safe, slice_expansion_bank, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/battle_id_command_followup_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/battle_id_command_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_id_command_followup_candidate.sav"
REPORT = ROOT / "out/patch/battle_id_command_followup_report.json"

EXPECTED_PARENT_SHA = "29f790238e02eb228db97619b9c55bc900495bfef2f76eb819d1abcfb5d430f0"
EXPECTED_TARGETS = 19
EXPECTED_UNIQUE_PHRASES = 3
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def load_rows(parent: bytes, dictionary: Any, tbl: Tbl) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen_records: set[int] = set()
    seen_bodies: set[int] = set()
    base = stock_base(parent)
    for item in spec.get("records") or []:
        record_start = int(str(item.get("record_start") or "0"), 16)
        body_start = int(str(item.get("body_start") or "0"), 16)
        if record_start in seen_records or body_start in seen_bodies:
            raise BuildError(f"duplicate record/body address: {record_start:06X}/{body_start:06X}")
        seen_records.add(record_start)
        seen_bodies.add(body_start)
        prefix = bytes.fromhex(str(item.get("prefix_hex") or ""))
        body = bytes.fromhex(str(item.get("body_hex") or ""))
        if body_start != record_start + len(prefix):
            raise BuildError(f"body start does not follow prefix at {record_start:06X}")
        got = read_encoded_z_safe(parent, base + record_start, max_len=128)
        if got is None:
            raise BuildError(f"unreadable parent record {record_start:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        if payload != prefix + body:
            raise BuildError(
                f"parent payload drift at {record_start:06X}: expected {(prefix+body).hex().upper()}, got {payload.hex().upper()}"
            )
        if terminator != base + record_start + len(payload) or parent[terminator] != 0:
            raise BuildError(f"record terminator drift at {record_start:06X}")
        after = normalize_ko_text(str(item.get("ko") or ""))
        if not after or any(is_japanese_character(character) for character in after):
            raise BuildError(f"invalid Korean phrase at {record_start:06X}: {after!r}")
        before = dictionary.expand(body, tbl).rstrip("\u3000 \t")
        rows.append(
            {
                "record_start": record_start,
                "body_start": body_start,
                "prefix": prefix,
                "body": body,
                "capacity": len(body),
                "jp": str(item.get("jp") or ""),
                "before": before,
                "after": after,
                "category": str(item.get("category") or ""),
                "evidence": item.get("evidence"),
                "terminator": terminator,
            }
        )
    rows.sort(key=lambda row: int(row["record_start"]))
    if len(rows) != EXPECTED_TARGETS:
        raise BuildError(f"target population drifted: expected {EXPECTED_TARGETS}, got {len(rows)}")
    if len({str(row["after"]) for row in rows}) != EXPECTED_UNIQUE_PHRASES:
        raise BuildError("unique phrase population drifted")
    if any(int(row["capacity"]) < 4 for row in rows):
        raise BuildError("all dedicated targets must support a four-byte ext3 token")
    return spec, rows


def main() -> int:
    parent = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("promoted main TIP identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    spec, rows = load_rows(parent, parent_dictionary, tbl)

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    phrases = sorted({str(row["after"]) for row in rows})
    free_ext3 = sorted(index for index in inventory.ext3_free if bank_local_for_index(index)[0] == ALLOC_SEG)
    if len(free_ext3) < len(phrases):
        raise BuildError("not enough free ext3 slots in allocation bank")
    assignment = {phrase: index for phrase, index in zip(phrases, free_ext3)}
    payloads = {index: encode_phrase(phrase, tbl) for phrase, index in assignment.items()}
    required_bytes = sum(len(payload) + 1 for payload in payloads.values())
    room = int(inventory.ext3_bank_room.get(ALLOC_SEG - EXP3_SEG0, 0))
    if required_bytes > room:
        raise BuildError(f"not enough ext3 phrase room: need {required_bytes}, room {room}")

    candidate = bytearray(parent)
    cursor_before = phrase_cursor(bytes(slice_expansion_bank(parent, ALLOC_SEG)))
    write_info, guard = write_ext3_slots_guarded(candidate, payloads, union=union, num_banks=num_banks)
    if int(write_info.get("written") or 0) != len(payloads):
        raise BuildError("ext3 writer did not write every selected phrase")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        phrase = str(row["after"])
        index = assignment[phrase]
        token = token_from_ext3_index(index, num_banks=num_banks)
        capacity = int(row["capacity"])
        replacement = token + b"\x01" * (capacity - len(token))
        if len(replacement) != capacity:
            raise BuildError(f"replacement length drift at {int(row['body_start']):06X}")
        start = base + int(row["body_start"])
        candidate[start : start + capacity] = replacement
        target_extents.append((start, start + capacity))
        applied.append(
            {
                "record_start": f"{int(row['record_start']):06X}",
                "body_start": f"{int(row['body_start']):06X}",
                "category": row["category"],
                "jp": row["jp"],
                "before": row["before"],
                "after": phrase,
                "prefix_hex": bytes(row["prefix"]).hex().upper(),
                "body_capacity": capacity,
                "strategy": "private_ext3",
                "ext3_index": f"{index:05X}",
                "token_hex": token.hex().upper(),
                "evidence": row["evidence"],
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []
    for row in rows:
        record_start = int(row["record_start"])
        got = read_encoded_z_safe(candidate_bytes, base + record_start, max_len=128)
        if got is None:
            failures.append({"record_start": f"{record_start:06X}", "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        prefix = bytes(row["prefix"])
        rendered = candidate_dictionary.expand(payload[len(prefix):], tbl).rstrip("\u3000 \t")
        expected = str(row["after"]).rstrip("\u3000 \t")
        ok = (
            payload[: len(prefix)] == prefix
            and len(payload) == len(prefix) + int(row["capacity"])
            and terminator == int(row["terminator"])
            and candidate_bytes[terminator] == 0
            and rendered == expected
            and not any(is_japanese_character(character) for character in rendered)
        )
        if not ok:
            failures.append(
                {
                    "record_start": f"{record_start:06X}",
                    "expected": expected,
                    "actual": rendered,
                    "prefix_ok": payload[: len(prefix)] == prefix,
                    "terminator_ok": terminator == int(row["terminator"]),
                }
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["record_start"]) for row in rows},
    )

    cursor_after = phrase_cursor(bytes(slice_expansion_bank(candidate_bytes, ALLOC_SEG)))
    bank_file = ALLOC_SEG * BANK_SIZE
    pointer_extents: list[tuple[int, int]] = []
    for index in payloads:
        _segment, local = bank_local_for_index(index)
        pointer_extents.append((bank_file + local * 2, bank_file + local * 2 + 2))
    allowed = target_extents + pointer_extents + [
        (bank_file + cursor_before, bank_file + cursor_after),
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
        not failures
        and invariance.get("ok") is True
        and not unaccounted
        and other_ext3_unchanged
        and runtime_unchanged
        and digest(MAIN.read_bytes()) == EXPECTED_PARENT_SHA
        and MAIN_SAVE.read_bytes() == main_save
    )
    if not ok:
        raise BuildError("battle/ID follow-up candidate static verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_id_command_followup_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "spec": identity(SPEC),
        "counts": {
            "targets": len(rows),
            "battle_dialogue_duplicates": sum(row["category"] == "battle_dialogue_duplicate" for row in rows),
            "id_command_activation_duplicates": sum(row["category"] == "id_command_activation_duplicate" for row in rows),
            "unique_phrases": len(payloads),
            "target_failures": len(failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segment": f"{ALLOC_SEG:02X}",
            "ext3_cursor_before": f"{cursor_before:04X}",
            "ext3_cursor_after": f"{cursor_after:04X}",
            "ext3_phrase_bytes": cursor_after - cursor_before,
            "ext3_room_before": room,
            "assignments": {phrase: f"{index:05X}" for phrase, index in sorted(assignment.items())},
        },
        "guard": guard.as_dict(),
        "verification": {
            "all_targets_render_exact": not failures,
            "target_japanese_residuals_zero": not failures,
            "prefix_length_terminator_preserved": not failures,
            "non_target_invariance": invariance,
            "diffs_bounded": not unaccounted,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_PARENT_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save,
            "candidate_saveram_matches_live_at_build": OUT_SAVE.read_bytes() == main_save,
        },
        "diff": {
            "changed_bytes_from_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "already_translated_verified": spec.get("already_translated_verified") or [],
        "records": applied,
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["candidate"], "counts": report["counts"], "allocation": report["allocation"], "diff": report["diff"], "report": str(REPORT.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
