#!/usr/bin/env python3
"""Build the follow-up UI/weapon candidate from menu_help_weapon_padding_candidate.

User-verified follow-up scope (2026-08-09):
1. bank75 75B49E `降す` -> `내림`.
2. Repair eight bank5F `配属` title records that still point at reclaimed stock
   slot 0021 (`티탄즈가`); they must render `배속` while the real dialogue
   consumers of slot 0021 remain untouched.
3. Restore Despada weapon 75C3C7 from compact `대형런처` to
   `대형　미사일　런처`.  Preserve the 11-byte field and original terminator.
   The 7 old visible padding bytes are replaced by one proven zero-width ext3
   token (4 B), one private zero-width retired-stock token (2 B), and one final
   0x01 pad (1 B).  Thus the full 9-cell name has only one trailing visible cell
   (10 total), safely below the former 11-cell fixed field.

This is candidate-only.  Main TIP and live SaveRAM are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_menu_help_weapon_padding_candidate import covered, diff_runs, encode_phrase, phrase_cursor
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from mixed_residual_reference_union import (
    _working_two_byte_external_refs,
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    SEG_DICT,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_3byte_dict_token import bank_local_for_index, token_from_ext3_index

PARENT_ROM = ROOT / "out/patch/menu_help_weapon_padding_candidate.wsc"
PARENT_SAVE = ROOT / "sram/menu_help_weapon_padding_candidate.sav"
MAIN_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/menu_help_weapon_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/menu_help_weapon_followup_candidate.sav"
SRAM_MIRROR = ROOT / "sram/menu_help_weapon_followup_candidate.sav"
REPORT = ROOT / "out/patch/menu_help_weapon_followup_report.json"

EXPECTED_PARENT_SHA = "4b08d0a94d6082881c7663a73a56963b33597c2ad3ed168f38ea96cd4a12c6bc"
EXPECTED_MAIN_SHA = "cfb1905aa8f19eb94b92bd23cb96b2657d05b7d18e7b3426b435ef41cb345f5f"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Proven retired stock slots on this exact parent.  Their original payloads are
# unreachable in the accepted current consumer set; the builder re-proves this
# before writing.
ASSIGN_SLOT = 0x0E8D
LOWER_SLOT = 0x0EB1
EMPTY_STOCK_SLOT = 0x00A9
EXPECTED_RETIRED_SLOTS = (ASSIGN_SLOT, LOWER_SLOT, EMPTY_STOCK_SLOT)

# Existing zero-width ext3 phrase allocated by the parent candidate.  It
# statically expands to zero cells; name75 runtime behavior is still a visual
# gate and is not treated as proven until the user confirms it in-emulator.
EMPTY_EXT3_INDEX = 0x0A48E

ASSIGN_TITLE_RECORDS = (
    0x5F2AEF,
    0x5F2B58,
    0x5F2B7B,
    0x5F2BA0,
    0x5F2BD5,
    0x5F2BE4,
    0x5F2C16,
    0x5F2E6B,
)
RECLAIMED_TITANS_SLOT = 0x0021
LOWER_LOGICAL = 0x75B49E
DESPADA_LOGICAL = 0x75C3C7
DESPADA_OLD_TOKEN = bytes.fromhex("E518EFAB")
DESPADA_FULL_TEXT = "대형　미사일　런처"


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.relative_to(ROOT)), "size": len(payload), "sha256": sha(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def payload_at(rom: bytes | bytearray, logical: int, max_len: int = 128) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"unreadable zstring {logical:06X}")
    return bytes(got[0]), int(got[1])


def render(dictionary: Any, tbl: Tbl, payload: bytes) -> str:
    return dictionary.expand(payload, tbl)


def choose_ext3_slot(inventory: Any, num_banks: int, phrase_bytes: int) -> int:
    # Prefer the bank with the most remaining phrase room, then the lowest safe
    # free slot in that bank.  This keeps the allocation deterministic.
    choices: list[tuple[int, int, list[int]]] = []
    for bank_i in range(num_banks):
        seg = 0x11 + bank_i
        slots = [i for i in inventory.ext3_free if bank_local_for_index(i)[0] == seg]
        room = int(inventory.ext3_bank_room.get(bank_i, 0))
        if slots and room >= phrase_bytes + 1:
            choices.append((room, seg, slots))
    if not choices:
        raise BuildError("no ext3 bank has room for Despada full name")
    _room, _seg, slots = max(choices, key=lambda row: (row[0], len(row[2]), -row[1]))
    return min(slots)


def bounded(run: tuple[int, int], extents: Iterable[tuple[int, int]]) -> bool:
    return covered(run, extents)


def main() -> int:
    parent = PARENT_ROM.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent candidate identity drifted: {sha(parent)}")
    if not PARENT_SAVE.is_file() or PARENT_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("parent candidate SaveRAM missing/wrong size")
    parent_save = PARENT_SAVE.read_bytes()
    main_before = MAIN_ROM.read_bytes()
    main_save_before = MAIN_SAVE.read_bytes()
    if sha(main_before) != EXPECTED_MAIN_SHA or len(main_save_before) != SAVE_SIZE:
        raise BuildError("main TIP/SaveRAM identity drifted")

    original = ORIGINAL.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 runtime metadata unavailable")
    parent_dict = make_dictionary_ext3(parent, ext_meta, ext3_meta)

    # Parent zero-width ext3 filler must still be exactly empty.
    empty_ext3_token = token_from_ext3_index(EMPTY_EXT3_INDEX, num_banks=num_banks)
    if render(parent_dict, tbl, empty_ext3_token) != "":
        raise BuildError("parent empty ext3 filler no longer expands to zero cells")

    # Re-prove retired stock slots on this exact parent, including raw-pair and
    # nested scans.  No historical assumption is accepted silently.
    retired = current_strong_retired_slots(original, parent, parent_dict)
    if not set(EXPECTED_RETIRED_SLOTS).issubset(set(retired)):
        raise BuildError(
            "required retired slots drifted: "
            f"required={[f'{x:04X}' for x in EXPECTED_RETIRED_SLOTS]} "
            f"available={[f'{x:04X}' for x in retired]}"
        )
    wanted = set(EXPECTED_RETIRED_SLOTS)
    ext_before = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested_before = nested_occurrence_map(parent_dict, wanted=wanted, ext3_aware=True)
    raw_before = _raw_pair_hits(parent, list(EXPECTED_RETIRED_SLOTS))
    if any(ext_before.get(i) or nested_before.get(i) or raw_before.get(i) for i in wanted):
        raise BuildError("one selected retired stock slot became reachable")

    # Prove the eight missed bank5F titles are exactly the reclaimed 티탄즈가
    # token and preserve the actual dialogue consumers of slot 0021.
    titans_token = token_from_dict_index(RECLAIMED_TITANS_SLOT)
    if parent_dict.expand(titans_token, tbl).rstrip("　 \t") != "티탄즈가":
        raise BuildError("slot 0021 no longer renders 티탄즈가")
    title_terms: dict[int, int] = {}
    for logical in ASSIGN_TITLE_RECORDS:
        old, term = payload_at(parent, logical)
        if old != titans_token:
            raise BuildError(f"배속 title drifted at {logical:06X}: {old.hex().upper()}")
        title_terms[logical] = term

    dialogue_refs_before = tuple(
        sorted(
            int(str(row["token_abs"]), 16)
            for row in external_occurrence_map(parent, ext3_aware=True, wanted={RECLAIMED_TITANS_SLOT}).get(RECLAIMED_TITANS_SLOT, [])
        )
    )
    expected_dialogue = (0x601DFA, 0x61AC6C, 0x6292D0, 0x62938F, 0x63F686)
    if dialogue_refs_before != expected_dialogue:
        raise BuildError(f"티탄즈가 dialogue consumer set drifted: {dialogue_refs_before}")

    lower_old, lower_term = payload_at(parent, LOWER_LOGICAL)
    if lower_old != bytes.fromhex("E10A1E"):
        raise BuildError(f"75B49E 降す drifted: {lower_old.hex().upper()}")

    despada_old, despada_term = payload_at(parent, DESPADA_LOGICAL)
    if despada_old != DESPADA_OLD_TOKEN + b"\x01" * 7:
        raise BuildError(f"Despada compact field drifted: {despada_old.hex().upper()}")

    candidate = bytearray(parent)

    # 1) Reuse three proven-retired stock slots in place.  The stock spill is
    # already full on this parent, so we deliberately avoid pointer relocation.
    # 0E8D/0EB1 each have an exclusive 6-byte payload, exactly the encoded size
    # of 배속/내림.  00A9 has an exclusive 2-byte payload; writing a NUL at its
    # first byte turns that retired slot into a zero-width phrase.
    stock_dict_before = Dictionary(candidate)
    pointers_before = list(stock_dict_before.ptrs)
    assign_encoded = encode_phrase("배속", tbl)
    lower_encoded = encode_phrase("내림", tbl)
    if len(assign_encoded) != 6 or len(lower_encoded) != 6:
        raise BuildError("expected 6-byte encoded 배속/내림 phrases")
    stock_phrase_extents: list[tuple[int, int]] = []
    stock_before_rows: dict[int, dict[str, Any]] = {}
    for slot, encoded, expected_len in (
        (ASSIGN_SLOT, assign_encoded, 6),
        (LOWER_SLOT, lower_encoded, 6),
        (EMPTY_STOCK_SLOT, b"", 2),
    ):
        pointer = int(stock_dict_before.ptrs[slot])
        raw = bytes(stock_dict_before.raw_entry(slot))
        if len(raw) != expected_len:
            raise BuildError(f"retired slot {slot:04X} payload length drifted: {len(raw)}")
        aliases = [i for i, value in enumerate(stock_dict_before.ptrs) if i != slot and int(value) == pointer]
        interiors = [
            i for i, value in enumerate(stock_dict_before.ptrs)
            if i != slot and pointer < int(value) < pointer + len(raw) + 1
        ]
        if aliases or interiors:
            raise BuildError(
                f"retired slot {slot:04X} storage is shared/overlapped: aliases={aliases[:8]} interiors={interiors[:8]}"
            )
        file_start = stock_dict_before.base + pointer
        if bytes(candidate[file_start : file_start + len(raw)]) != raw or candidate[file_start + len(raw)] != 0:
            raise BuildError(f"retired slot {slot:04X} storage identity drifted")
        stock_before_rows[slot] = {
            "pointer": pointer,
            "raw_hex": raw.hex().upper(),
            "file_start": file_start,
        }
        if slot == EMPTY_STOCK_SLOT:
            candidate[file_start] = 0
            stock_phrase_extents.append((file_start, file_start + 1))
        else:
            candidate[file_start : file_start + len(encoded)] = encoded
            stock_phrase_extents.append((file_start, file_start + len(encoded)))

    stock_dict_after_write = Dictionary(candidate)
    if list(stock_dict_after_write.ptrs) != pointers_before:
        raise BuildError("retired-stock in-place rewrite changed dictionary pointers")

    # 2) Allocate the restored full Despada name in one private ext3 phrase.
    union = build_reference_union(original, bytes(candidate), ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(bytes(candidate), union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    full_encoded = encode_phrase(DESPADA_FULL_TEXT, tbl)
    full_index = choose_ext3_slot(inventory, num_banks, len(full_encoded))
    full_seg, full_local = bank_local_for_index(full_index)
    ext3_before_bank = bytes(slice_expansion_bank(candidate, full_seg))
    ext3_cursor_before = phrase_cursor(ext3_before_bank)
    ext3_info, ext3_guard = write_ext3_slots_guarded(
        candidate, {full_index: full_encoded}, union=union, num_banks=num_banks
    )
    if int(ext3_info.get("written") or 0) != 1:
        raise BuildError("Despada ext3 phrase was not written")
    full_token = token_from_ext3_index(full_index, num_banks=num_banks)

    base = stock_base(candidate)
    target_extents: list[tuple[int, int]] = []

    # Repair every bank5F title that should be 配属 -> 배속.
    assign_token = token_from_dict_index(ASSIGN_SLOT)
    for logical in ASSIGN_TITLE_RECORDS:
        start = base + logical
        candidate[start : start + 2] = assign_token
        target_extents.append((start, start + 2))

    # User-provided screen evidence resolves the previous ambiguous `降す` as
    # the menu label `내림`.  Keep the 3-byte field by one 01 pad.
    lower_token = token_from_dict_index(LOWER_SLOT)
    lower_new = lower_token + b"\x01"
    candidate[base + LOWER_LOGICAL : base + LOWER_LOGICAL + 3] = lower_new
    target_extents.append((base + LOWER_LOGICAL, base + LOWER_LOGICAL + 3))

    # Restore full Despada weapon while consuming six of the seven old visible
    # pads with zero-width tokens.  One final 01 remains solely because the field
    # is odd-length (11 B); 9+1 visual cells stays inside the old 11-cell field.
    empty_stock_token = token_from_dict_index(EMPTY_STOCK_SLOT)
    despada_new = full_token + empty_ext3_token + empty_stock_token + b"\x01"
    if len(despada_new) != len(despada_old):
        raise BuildError(f"Despada replacement length mismatch: {len(despada_new)}")
    candidate[base + DESPADA_LOGICAL : base + DESPADA_LOGICAL + len(despada_new)] = despada_new
    target_extents.append((base + DESPADA_LOGICAL, base + DESPADA_LOGICAL + len(despada_new)))

    checksum = update_ws_checksum(candidate)
    final = bytes(candidate)
    final_dict = make_dictionary_ext3(final, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    if final_dict.expand(assign_token, tbl).rstrip("　 \t") != "배속":
        failures.append({"reason": "배속_stock_render"})
    if final_dict.expand(lower_token, tbl).rstrip("　 \t") != "내림":
        failures.append({"reason": "내림_stock_render"})
    if final_dict.expand(empty_stock_token, tbl) != "":
        failures.append({"reason": "empty_stock_not_zero_width"})
    if final_dict.expand(empty_ext3_token, tbl) != "":
        failures.append({"reason": "empty_ext3_not_zero_width"})
    if final_dict.expand(full_token, tbl) != DESPADA_FULL_TEXT:
        failures.append({"reason": "despada_full_phrase_render"})

    for logical in ASSIGN_TITLE_RECORDS:
        payload, term = payload_at(final, logical)
        actual = final_dict.expand(payload, tbl).rstrip("　 \t")
        if payload != assign_token or actual != "배속" or term != title_terms[logical]:
            failures.append({"abs": f"{logical:06X}", "reason": "assignment_title", "actual": actual, "payload": payload.hex().upper()})

    lower_payload, lower_term_after = payload_at(final, LOWER_LOGICAL)
    lower_render = final_dict.expand(lower_payload, tbl)
    if lower_payload != lower_new or lower_term_after != lower_term or lower_render.rstrip("　 \t") != "내림":
        failures.append({"abs": f"{LOWER_LOGICAL:06X}", "reason": "lower_label", "render": lower_render})

    despada_payload, despada_term_after = payload_at(final, DESPADA_LOGICAL)
    despada_render = final_dict.expand(despada_payload, tbl)
    despada_trim = despada_render.rstrip("　 \t")
    trailing_cells = len(despada_render) - len(despada_trim)
    visual_cells = len(despada_render)
    if (
        despada_payload != despada_new
        or despada_term_after != despada_term
        or despada_trim != DESPADA_FULL_TEXT
        or trailing_cells != 1
        or visual_cells > 11
    ):
        failures.append({
            "abs": f"{DESPADA_LOGICAL:06X}",
            "reason": "despada_field",
            "render": despada_render,
            "trailing_cells": trailing_cells,
            "visual_cells": visual_cells,
            "payload": despada_payload.hex().upper(),
        })

    dialogue_refs_after = tuple(
        sorted(
            int(str(row["token_abs"]), 16)
            for row in external_occurrence_map(final, ext3_aware=True, wanted={RECLAIMED_TITANS_SLOT}).get(RECLAIMED_TITANS_SLOT, [])
        )
    )
    if dialogue_refs_after != expected_dialogue:
        failures.append({"reason": "titans_dialogue_consumer_drift", "after": [f"{x:06X}" for x in dialogue_refs_after]})

    # Bound all changes: three visible target families, three exclusive retired
    # stock phrase extents, one ext3 pointer + phrase range, checksum.
    ext3_after_bank = bytes(slice_expansion_bank(final, full_seg))
    ext3_cursor_after = phrase_cursor(ext3_after_bank)
    ext3_bank_file = full_seg * BANK_SIZE
    ext3_pointer_extent = (ext3_bank_file + full_local * 2, ext3_bank_file + full_local * 2 + 2)
    ext3_phrase_extent = (ext3_bank_file + ext3_cursor_before, ext3_bank_file + ext3_cursor_after)

    allowed = target_extents + stock_phrase_extents + [
        ext3_pointer_extent,
        ext3_phrase_extent,
        (len(final) - 2, len(final)),
    ]
    runs = diff_runs(parent, final)
    unaccounted = [
        {"start": f"{a:08X}", "end_exclusive": f"{b:08X}"}
        for a, b in runs if not bounded((a, b), allowed)
    ]
    if unaccounted:
        failures.append({"reason": "unaccounted_diff", "sample": unaccounted[:8]})

    # Runtime code and all unrelated ext3 banks remain byte-identical.
    runtime_lo = stock_base(parent) + 0x7A0600
    runtime_hi = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_lo:runtime_hi] == final[runtime_lo:runtime_hi]
    if not runtime_unchanged:
        failures.append({"reason": "runtime_hook_changed"})
    other_ext3_unchanged = all(
        bytes(slice_expansion_bank(parent, seg)) == bytes(slice_expansion_bank(final, seg))
        for seg in range(0x11, 0x11 + num_banks)
        if seg != full_seg
    )
    if not other_ext3_unchanged:
        failures.append({"reason": "unrelated_ext3_bank_changed"})

    if MAIN_ROM.read_bytes() != main_before or MAIN_SAVE.read_bytes() != main_save_before:
        failures.append({"reason": "main_mutated"})
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        failures.append({"reason": "parent_mutated"})

    if failures:
        raise BuildError(f"follow-up candidate verification failed: {failures[:8]!r}")

    atomic_bytes(OUT_ROM, final)
    # Project policy: every newly generated test ROM is paired with the current
    # live main SaveRAM, not an older candidate's saved snapshot.
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    SRAM_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MAIN_SAVE, SRAM_MIRROR)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_menu_help_weapon_followup_candidate.py",
        "ok": True,
        "published": False,
        "status": "static_verified_pending_user_runtime_visual_test",
        "parent": identity(PARENT_ROM, parent),
        "main_tip_unchanged": identity(MAIN_ROM, main_before),
        "candidate": identity(OUT_ROM, final),
        "candidate_save": identity(OUT_SAVE),
        "sram_mirror": identity(SRAM_MIRROR),
        "checksum": f"{checksum:04X}",
        "assignment_titles": {
            "count": len(ASSIGN_TITLE_RECORDS),
            "addresses": [f"{a:06X}" for a in ASSIGN_TITLE_RECORDS],
            "before_token": titans_token.hex().upper(),
            "after_token": assign_token.hex().upper(),
            "rendered": "배속",
            "titans_dialogue_consumers_preserved": [f"{a:06X}" for a in dialogue_refs_after],
        },
        "lower_label": {
            "abs": f"{LOWER_LOGICAL:06X}",
            "before_hex": lower_old.hex().upper(),
            "after_hex": lower_new.hex().upper(),
            "rendered": lower_render.rstrip("　 \t"),
            "terminator_preserved": lower_term_after == lower_term,
            "provenance": "user_screen_evidence_2026-08-09",
        },
        "despada": {
            "abs": f"{DESPADA_LOGICAL:06X}",
            "before_hex": despada_old.hex().upper(),
            "after_hex": despada_new.hex().upper(),
            "full_name": DESPADA_FULL_TEXT,
            "rendered": despada_render,
            "trimmed_render": despada_trim,
            "visual_cells_with_tail": visual_cells,
            "trailing_visible_spaces": trailing_cells,
            "former_field_cells": 11,
            "old_padding_cells": 7,
            "removed_padding_cells": 6,
            "terminator_preserved": despada_term_after == despada_term,
            "full_name_ext3_index": f"{full_index:05X}",
            "empty_ext3_index": f"{EMPTY_EXT3_INDEX:05X}",
            "empty_stock_slot": f"{EMPTY_STOCK_SLOT:04X}",
            "runtime_visual_gate": "full name visible and no white right-side protrusion",
        },
        "stock_allocation": {
            "배속": f"{ASSIGN_SLOT:04X}",
            "내림": f"{LOWER_SLOT:04X}",
            "zero_width": f"{EMPTY_STOCK_SLOT:04X}",
            "mode": "exclusive_retired_payload_in_place_no_pointer_change",
            "before": {f"{slot:04X}": row for slot, row in stock_before_rows.items()},
            "retired_slots_reproved": True,
            "dictionary_pointers_unchanged": list(Dictionary(final).ptrs) == pointers_before,
        },
        "ext3_allocation": {
            "segment": f"{full_seg:02X}",
            "index": f"{full_index:05X}",
            "token": full_token.hex().upper(),
            "cursor_before": f"{ext3_cursor_before:04X}",
            "cursor_after": f"{ext3_cursor_after:04X}",
            "guard": ext3_guard.as_dict(),
            "writer": ext3_info,
        },
        "verification": {
            "target_failures": 0,
            "unaccounted_diff_runs": len(unaccounted),
            "runtime_hook_unchanged": runtime_unchanged,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "compact3_added": False,
            "main_tip_unchanged": MAIN_ROM.read_bytes() == main_before,
            "main_saveram_unchanged": MAIN_SAVE.read_bytes() == main_save_before,
            "parent_candidate_unchanged": PARENT_ROM.read_bytes() == parent,
            "parent_saveram_unchanged": PARENT_SAVE.read_bytes() == parent_save,
        },
        "diff": {
            "changed_bytes": sum(b - a for a, b in runs),
            "runs": len(runs),
            "unaccounted": unaccounted,
        },
        "promotion": "blocked_pending_user_runtime_visual_verification",
        "runtime_gate": [
            "75B49E menu label displays 내림 instead of 降す",
            "배속 위치 선택 help title displays 배속, never 티탄즈가",
            "Despada weapon displays 대형 미사일 런처 without right-side white protrusion",
            "트리플 메가소닉 포 remains unchanged from the parent candidate",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "checksum": report["checksum"],
        "assignment_titles": report["assignment_titles"],
        "lower_label": report["lower_label"],
        "despada": report["despada"],
        "verification": report["verification"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
