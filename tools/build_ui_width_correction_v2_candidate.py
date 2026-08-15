#!/usr/bin/env python3
"""Build the second screen-width correction candidate.

Parent: ``out/patch/ui_width_correction_candidate.wsc``.
Targets: four screen-verified UI strings that still overlapped after the first
width pass.  Every target receives a private ext3 phrase.  The main TIP and
live SaveRAM are never modified.
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
from build_broad_stage2_dialogue_voice_candidate import atomic_bytes, atomic_json, digest, identity, payload_at
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, phrase_cursor, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union, write_ext3_slots_guarded
from monoeye_rom import BANK_SIZE, Tbl, read_encoded_z_safe, slice_expansion_bank, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/ui_width_correction_candidate.wsc"
SPEC = ROOT / "data/ui_width_corrections_v2_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ui_width_correction_v2_candidate.wsc"
OUT_SAVE = ROOT / "sram/ui_width_correction_v2_candidate.sav"
REPORT = ROOT / "out/patch/ui_width_correction_v2_report.json"

EXPECTED_MAIN_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
EXPECTED_PARENT_SHA = "f1d2352a4384250df3e55fdf9ee507f366a11f12ab477cb07f4ee9a909c46c45"
EXPECTED_TARGETS = 4
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def visual_cells(text: str) -> int:
    if "<" in text or ">" in text:
        raise BuildError(f"control markup is not allowed: {text!r}")
    return len(text)


def load_rows(parent: bytes, dictionary: Any, tbl: Tbl) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    base = stock_base(parent)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in spec.get("records") or []:
        address = str(item.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate target {address}")
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
        prefix_cells = int(item.get("dynamic_prefix_cells") or 0)
        max_combined = int(item.get("max_combined_cells") or 0)
        if before != expected_before:
            raise BuildError(f"parent render drift at {address}: expected {expected_before!r}, got {before!r}")
        if not after or any(is_japanese_character(character) for character in after):
            raise BuildError(f"invalid Korean correction at {address}: {after!r}")
        if visual_cells(after) > max_cells:
            raise BuildError(f"visual width exceeds limit at {address}")
        if max_combined and prefix_cells + visual_cells(after) > max_combined:
            raise BuildError(f"combined prompt width exceeds limit at {address}")
        if terminator != base + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"record boundary drift at {address}")
        if len(payload) < 4:
            raise BuildError(f"target does not support private ext3 at {address}")
        rows.append({
            "abs": address,
            "logical": logical,
            "before": before,
            "after": after,
            "body_capacity": len(payload),
            "max_visual_cells": max_cells,
            "dynamic_prefix_cells": prefix_cells,
            "max_combined_cells": max_combined,
            "screen": item.get("screen"),
            "reason": item.get("reason"),
            "parent_payload_hex": payload.hex().upper(),
        })
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_TARGETS:
        raise BuildError(f"target population drifted: {len(rows)}")
    return spec, rows


def main() -> int:
    main_rom = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    parent = PARENT.read_bytes()
    if len(main_rom) != ROM_SIZE or digest(main_rom) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("first width-correction parent identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is invalid")

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
        raise BuildError("not enough ext3 slots")
    assignment = {phrase: index for phrase, index in zip(phrases, free_ext3)}
    payloads = {index: encode_phrase(phrase, tbl) for phrase, index in assignment.items()}
    phrase_bytes = sum(len(payload) + 1 for payload in payloads.values())
    room = int(inventory.ext3_bank_room.get(ALLOC_SEG - EXP3_SEG0, 0))
    if phrase_bytes > room:
        raise BuildError(f"not enough ext3 phrase room: need {phrase_bytes}, room {room}")

    candidate = bytearray(parent)
    cursor_before = phrase_cursor(bytes(slice_expansion_bank(parent, ALLOC_SEG)))
    info, guard = write_ext3_slots_guarded(candidate, payloads, union=union, num_banks=num_banks)
    if int(info.get("written") or 0) != len(payloads):
        raise BuildError("ext3 writer count mismatch")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        capacity = int(row["body_capacity"])
        phrase = str(row["after"])
        index = assignment[phrase]
        token = token_from_ext3_index(index, num_banks=num_banks)
        replacement = token + b"\x01" * (capacity - len(token))
        start = base + logical
        candidate[start:start + capacity] = replacement
        target_extents.append((start, start + capacity))
        applied.append({
            "abs": f"{logical:06X}",
            "before": row["before"],
            "after": phrase,
            "visual_cells": visual_cells(phrase),
            "max_visual_cells": row["max_visual_cells"],
            "dynamic_prefix_cells": row["dynamic_prefix_cells"],
            "combined_cells": row["dynamic_prefix_cells"] + visual_cells(phrase),
            "max_combined_cells": row["max_combined_cells"],
            "body_capacity": capacity,
            "screen": row["screen"],
            "reason": row["reason"],
            "strategy": "private_ext3",
            "ext3_index": f"{index:05X}",
            "token_hex": token.hex().upper(),
        })

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
        combined = int(row["dynamic_prefix_cells"]) + width
        if rendered != expected:
            target_failures.append({"abs": f"{logical:06X}", "reason": "render_mismatch", "expected": expected, "actual": rendered})
        elif width > int(row["max_visual_cells"]):
            target_failures.append({"abs": f"{logical:06X}", "reason": "visual_width_exceeded", "width": width})
        elif int(row["max_combined_cells"]) and combined > int(row["max_combined_cells"]):
            target_failures.append({"abs": f"{logical:06X}", "reason": "combined_width_exceeded", "combined": combined})
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

    cursor_after = phrase_cursor(bytes(slice_expansion_bank(candidate_bytes, ALLOC_SEG)))
    bank_file = ALLOC_SEG * BANK_SIZE
    pointer_extents = []
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
        not target_failures
        and invariance.get("ok") is True
        and not unaccounted
        and other_ext3_unchanged
        and runtime_unchanged
        and digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA
        and MAIN_SAVE.read_bytes() == main_save
    )
    if not ok:
        raise BuildError("second UI width correction static verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ui_width_correction_v2_candidate.py",
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
            "ext3_unique_phrases": len(payloads),
            "target_failures": len(target_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segment": f"{ALLOC_SEG:02X}",
            "ext3_cursor_before": f"{cursor_before:04X}",
            "ext3_cursor_after": f"{cursor_after:04X}",
            "ext3_phrase_bytes": cursor_after - cursor_before,
            "ext3_room_before": room,
        },
        "guards": {"ext3": guard.as_dict()},
        "verification": {
            "all_targets_render_exact": not target_failures,
            "all_targets_within_visual_limits": not target_failures,
            "non_target_invariance": invariance,
            "diffs_bounded": not unaccounted,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save,
            "candidate_saveram_matches_live_at_build": OUT_SAVE.read_bytes() == main_save,
            "record_lengths_and_terminators_preserved": True,
        },
        "diff": {
            "changed_bytes_from_parent": sum(right-left for left,right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "records": applied,
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["candidate"], "counts": report["counts"], "allocation": report["allocation"], "diff": report["diff"], "report": str(REPORT.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
