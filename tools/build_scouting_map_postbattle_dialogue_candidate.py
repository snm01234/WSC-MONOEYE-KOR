#!/usr/bin/env python3
"""Build the corrected scouting-map first-battle follow-up candidate.

Runtime testing disproved the v1 target set: it patched the four scouting-stage
introduction lines before the battle, while the captured broken windows are the
following experience/capture tutorial lines at 60:65CE-65F9.  Their ext3 tokens
render literally as 詩フ軍 / 詩フ連 / 詩フへ / 詩フゥ on the legacy post-battle
path.  The actual four records are detached to dedicated legacy stock slots.

Parent: cumulative battle UI action-label candidate.
Main TIP and live SaveRAM are never modified.
"""
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
from build_broad_stage2_dialogue_voice_candidate import atomic_bytes, atomic_json, digest, identity
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/battle_ui_action_labels_candidate.wsc"
SPEC = ROOT / "data/scouting_map_postbattle_dialogue_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/scouting_map_postbattle_dialogue_candidate.wsc"
OUT_SAVE = ROOT / "sram/scouting_map_postbattle_dialogue_candidate.sav"
REPORT = ROOT / "out/patch/scouting_map_postbattle_dialogue_report.json"

EXPECTED_MAIN_SHA = "29f790238e02eb228db97619b9c55bc900495bfef2f76eb819d1abcfb5d430f0"
EXPECTED_PARENT_SHA = "6e63e4830e0391f00e0ccdf7d07c6b3b3309e5e3fb797cd934d20900b050e33f"
EXPECTED_ROWS = 4
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXT3_MARKER = b"\xE5\x18"


class BuildError(RuntimeError):
    pass


def load_rows(
    parent: bytes,
    original: bytes,
    parent_dictionary: Any,
    original_dictionary: Any,
    tbl: Tbl,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = json.loads(SPEC.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    parent_base = stock_base(parent)
    original_base = stock_base(original)

    for item in document.get("records") or []:
        logical = int(str(item.get("abs") or "0"), 16)
        if logical in seen:
            raise BuildError(f"duplicate target {logical:06X}")
        seen.add(logical)

        prefix = bytes.fromhex(str(item.get("prefix_hex") or ""))
        expected_parent = bytes.fromhex(str(item.get("parent_payload_hex") or ""))
        expected_original = bytes.fromhex(str(item.get("original_payload_hex") or ""))
        parent_terminator = int(str(item.get("parent_terminator") or "0"), 16)
        target_terminator = int(str(item.get("target_terminator") or "0"), 16)
        target_capacity = int(item.get("target_body_capacity") or 0)

        parent_read = read_encoded_z_safe(parent, parent_base + logical, max_len=96)
        original_read = read_encoded_z_safe(original, original_base + logical, max_len=96)
        if parent_read is None or original_read is None:
            raise BuildError(f"unreadable source record {logical:06X}")
        parent_payload, parent_term_file = bytes(parent_read[0]), int(parent_read[1])
        original_payload, original_term_file = bytes(original_read[0]), int(original_read[1])
        if parent_payload != expected_parent:
            raise BuildError(
                f"parent payload drift at {logical:06X}: expected {expected_parent.hex().upper()}, got {parent_payload.hex().upper()}"
            )
        if original_payload != expected_original:
            raise BuildError(
                f"original payload drift at {logical:06X}: expected {expected_original.hex().upper()}, got {original_payload.hex().upper()}"
            )
        if parent_term_file - parent_base != parent_terminator:
            raise BuildError(f"parent terminator drift at {logical:06X}")
        if original_term_file - original_base != target_terminator:
            raise BuildError(f"original terminator no longer matches target at {logical:06X}")
        if not parent_payload.startswith(prefix) or not original_payload.startswith(prefix):
            raise BuildError(f"prefix drift at {logical:06X}")
        if target_capacity != len(original_payload) - len(prefix) or target_capacity < 2:
            raise BuildError(f"target body capacity drift at {logical:06X}")

        parent_body = parent_payload[len(prefix) :]
        if not parent_body.startswith(EXT3_MARKER):
            raise BuildError(f"target is no longer ext3-backed at {logical:06X}")
        parent_render = parent_dictionary.expand(parent_body, tbl).rstrip("\u3000 \t")
        original_render = original_dictionary.expand(original_payload[len(prefix) :], tbl)
        korean = normalize_ko_text(str(item.get("ko") or ""))
        if not korean or any(is_japanese_character(character) for character in korean):
            raise BuildError(f"invalid Korean phrase at {logical:06X}: {korean!r}")
        encode_phrase(korean, tbl)

        rows.append(
            {
                "record_id": str(item.get("record_id") or ""),
                "logical": logical,
                "prefix": prefix,
                "parent_payload": parent_payload,
                "parent_terminator": parent_terminator,
                "target_capacity": target_capacity,
                "target_terminator": target_terminator,
                "jp": str(item.get("jp") or ""),
                "original_render": original_render,
                "parent_render": parent_render,
                "after": korean,
                "reason": str(item.get("reason") or ""),
            }
        )

    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_ROWS:
        raise BuildError(f"target population drifted: expected {EXPECTED_ROWS}, got {len(rows)}")
    if {int(row["logical"]) for row in rows} != {0x6065CE, 0x6065DF, 0x6065ED, 0x6065F9}:
        raise BuildError("corrected post-battle target address set drifted")
    if len({str(row["after"]) for row in rows}) != EXPECTED_ROWS:
        raise BuildError("each target must have a private phrase")
    return document, rows


def main() -> int:
    main_rom = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    parent = PARENT.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(main_rom) != ROM_SIZE or digest(main_rom) != EXPECTED_MAIN_SHA:
        raise BuildError("promoted main TIP identity drifted")
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("cumulative parent candidate identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    _document, rows = load_rows(parent, original, parent_dictionary, original_dictionary, tbl)

    retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected = retired[: len(rows)]
    if len(selected) != len(rows):
        raise BuildError("not enough strong-retired stock slots")
    selected_set = set(selected)
    parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_set)
    parent_nested = nested_occurrence_map(parent_dictionary, wanted=selected_set, ext3_aware=True)
    parent_raw = _raw_pair_hits(parent, selected)
    for index in selected:
        if parent_external.get(index) or parent_nested.get(index) or parent_raw.get(index):
            raise BuildError(f"selected stock slot {index:04X} remains reachable")

    phrase_to_slot = {str(row["after"]): index for row, index in zip(rows, selected)}
    stock_payloads = {
        phrase_to_slot[str(row["after"])]: encode_phrase(str(row["after"]), tbl)
        for row in rows
    }

    candidate = bytearray(parent)
    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_before = list(Dictionary(candidate).ptrs)
    pointers_written, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        stock_payloads,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=_working_two_byte_external_refs(bytes(candidate)),
    )
    if list(Dictionary(candidate).ptrs) != pointers_written:
        raise BuildError("stock pointer writer result mismatch")
    changed_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_written))
        if before != after
    }
    if changed_indices != selected_set:
        raise BuildError(f"stock pointer change set mismatch: {changed_indices}")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        prefix = bytes(row["prefix"])
        body_start = logical + len(prefix)
        capacity = int(row["target_capacity"])
        index = phrase_to_slot[str(row["after"])]
        token = token_from_dict_index(index)
        replacement = token + b"\x01" * (capacity - len(token))
        if len(replacement) != capacity:
            raise BuildError(f"replacement capacity mismatch at {logical:06X}")

        candidate[base + body_start : base + body_start + capacity] = replacement
        candidate[base + int(row["target_terminator"])] = 0
        # Keep support for a boundary restoration in the generic transaction,
        # although the corrected v2 target set preserves all four boundaries.
        if int(row["parent_terminator"]) > int(row["target_terminator"]):
            candidate[
                base + int(row["target_terminator"]) : base + int(row["parent_terminator"]) + 1
            ] = b"\x00" * (
                int(row["parent_terminator"]) - int(row["target_terminator"]) + 1
            )

        target_extents.append(
            (
                base + body_start,
                base + max(int(row["parent_terminator"]), int(row["target_terminator"])) + 1,
            )
        )
        applied.append(
            {
                "record_id": row["record_id"],
                "abs": f"{logical:06X}",
                "jp": row["jp"],
                "before": row["parent_render"],
                "after": row["after"],
                "prefix_hex": prefix.hex().upper(),
                "target_body_capacity": capacity,
                "parent_terminator": f"{int(row['parent_terminator']):06X}",
                "target_terminator": f"{int(row['target_terminator']):06X}",
                "strategy": "dedicated_strong_retired_legacy_stock",
                "stock_index": f"{index:04X}",
                "token_hex": token.hex().upper(),
                "reason": row["reason"],
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    legacy_dictionary = Dictionary(candidate_bytes)

    target_failures: list[dict[str, Any]] = []
    expected_token_offsets: dict[int, int] = {}
    for row in rows:
        logical = int(row["logical"])
        prefix = bytes(row["prefix"])
        capacity = int(row["target_capacity"])
        index = phrase_to_slot[str(row["after"])]
        token = token_from_dict_index(index)
        expected_payload = prefix + token + b"\x01" * (capacity - 2)
        got = read_encoded_z_safe(candidate_bytes, base + logical, max_len=96)
        if got is None:
            target_failures.append({"abs": f"{logical:06X}", "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        body = payload[len(prefix) :]
        ext3_render = candidate_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        legacy_render = legacy_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        expected = str(row["after"]).rstrip("\u3000 \t")
        old_shifted_zero_ok = True
        if int(row["parent_terminator"]) > int(row["target_terminator"]):
            old_shifted_zero_ok = candidate_bytes[base + int(row["parent_terminator"])] == 0
        ok = (
            payload == expected_payload
            and terminator - base == int(row["target_terminator"])
            and candidate_bytes[terminator] == 0
            and old_shifted_zero_ok
            and EXT3_MARKER not in body
            and ext3_render == expected
            and legacy_render == expected
            and not any(is_japanese_character(character) for character in legacy_render)
        )
        if not ok:
            target_failures.append(
                {
                    "abs": f"{logical:06X}",
                    "payload": payload.hex().upper(),
                    "expected_payload": expected_payload.hex().upper(),
                    "terminator": f"{terminator - base:06X}",
                    "expected_terminator": f"{int(row['target_terminator']):06X}",
                    "ext3_render": ext3_render,
                    "legacy_render": legacy_render,
                    "old_shifted_zero_ok": old_shifted_zero_ok,
                }
            )
        expected_token_offsets[index] = logical + len(prefix)

    final_external = external_occurrence_map(candidate_bytes, ext3_aware=True, wanted=selected_set)
    final_nested = nested_occurrence_map(candidate_dictionary, wanted=selected_set, ext3_aware=True)
    reference_failures: list[dict[str, Any]] = []
    for index in selected:
        occurrences = final_external.get(index, [])
        actual_token_offsets = {int(str(item.get("token_abs") or "0"), 16) for item in occurrences}
        expected_offset = expected_token_offsets[index]
        phrase = legacy_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        expected_phrase = next(text for text, slot in phrase_to_slot.items() if slot == index)
        if (
            actual_token_offsets != {expected_offset}
            or len(occurrences) != 1
            or final_nested.get(index)
            or phrase != expected_phrase.rstrip("\u3000 \t")
        ):
            reference_failures.append(
                {
                    "index": f"{index:04X}",
                    "actual_token_offsets": [f"{value:06X}" for value in sorted(actual_token_offsets)],
                    "expected_token_offset": f"{expected_offset:06X}",
                    "occurrences": occurrences,
                    "nested": final_nested.get(index) or [],
                    "actual_phrase": phrase,
                    "expected_phrase": expected_phrase,
                }
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )

    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    allowed = list(target_extents)
    for index in selected:
        allowed.append(
            (
                stock_bank_file + DICT_PTR_START + index * 2,
                stock_bank_file + DICT_PTR_START + index * 2 + 2,
            )
        )
    allowed.extend(
        [
            (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after),
            (len(parent) - 2, len(parent)),
        ]
    )
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]
    main_unchanged = digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA
    save_untouched = MAIN_SAVE.read_bytes() == main_save

    ok = (
        not target_failures
        and not reference_failures
        and invariance.get("ok") is True
        and not unaccounted
        and runtime_unchanged
        and main_unchanged
        and save_untouched
    )
    if not ok:
        raise BuildError("scouting-map post-battle dialogue candidate verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scouting_map_postbattle_dialogue_candidate.py",
        "ok": True,
        "published": False,
        "status": "corrected_v2_candidate_static_verified_pending_independent_audit_and_visual_test",
        "main_tip": identity(MAIN, main_rom),
        "parent_battle_ui_candidate": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "source_spec": identity(SPEC),
        "counts": {
            "targets": len(rows),
            "dedicated_legacy_stock_phrases": len(stock_payloads),
            "restored_original_terminators": sum(
                int(row["parent_terminator"]) != int(row["target_terminator"]) for row in rows
            ),
            "target_failures": len(target_failures),
            "reference_failures": len(reference_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "selected_retired_slots": [f"{index:04X}" for index in selected],
            "assignments": {phrase: f"{index:04X}" for phrase, index in phrase_to_slot.items()},
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
            "stock_phrase_bytes": stock_cursor_after - stock_cursor_before,
            "stock_tail_room_after": BANK_SIZE - stock_cursor_after,
        },
        "verification": {
            "all_targets_render_exact_with_ext3_decoder": not target_failures,
            "all_targets_render_exact_with_legacy_decoder": not target_failures,
            "target_ext3_markers_removed": not target_failures,
            "all_original_boundaries_preserved": not target_failures,
            "dedicated_stock_reference_sets_exact": not reference_failures,
            "non_target_invariance": invariance,
            "diffs_bounded": not unaccounted,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": main_unchanged,
            "main_saveram_untouched": save_untouched,
            "candidate_saveram_matches_live_at_build": OUT_SAVE.read_bytes() == main_save,
        },
        "diff": {
            "changed_bytes_from_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "records": applied,
        "runtime_evidence": {
            "v1_candidate_failed": True,
            "v1_candidate_sha256": "492A2EF547B2A886CBC998E64E296F0086AFEA9DA79A68864009E75625BBB741",
            "corrected_target_set": ["6065CE", "6065DF", "6065ED", "6065F9"],
            "legacy_glitch_sequence": ["詩フ軍", "詩フ連", "詩フへ", "詩フゥ"],
        },
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
