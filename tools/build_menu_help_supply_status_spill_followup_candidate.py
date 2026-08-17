#!/usr/bin/env python3
"""Build a spill/pointer follow-up for menu help + broken assignment title.

Scope (2026-08-16):
* Reprocess the 30 menu-help/status records from
  build_menu_help_supply_status_followup_candidate.py without any visible 0x01
  padding in the active records.
* Repair the eight `配属`/assignment title records without reusing stock slot
  0E8D, whose payload has drifted and now renders broken glyphs.
* Keep the current main TIP and live SaveRAM untouched; emit a test ROM only.

Technique:
* Allocate one private ext3 phrase for each distinct Korean help text plus
  `배속`.
* For duplicate help strings, shorten one existing pointer-addressed record to
  `prefix + ext3 token + NUL` and retarget every duplicate pointer to that one
  representative record.  No payload-length padding is needed.
* The now-unreferenced old status-title record at 5F27FA becomes a private
  bank-5F spill record containing only `ext3(배속) + NUL`; the eight assignment
  title pointers are retargeted there.  Stock dictionary slot 0E8D is never
  modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import encode_phrase, phrase_cursor  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    EXT3_SEG0,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_3byte_dict_token import bank_local_for_index, token_from_ext3_index  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "exp_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "menu_help_supply_status_spill_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/menu_help_supply_status_spill_followup_candidate.sav"
REPORT = PATCH / "menu_help_supply_status_spill_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "f62f14d15f3d76ad2eb33e2b55531ab4781d230cb63bcc2348bbd703d8c39be3"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
CTRL_LINE = bytes.fromhex("E62F")
POINTER_SCAN_START = 0x5F3100
POINTER_SCAN_END = 0x5F3662
BROKEN_ASSIGN_SLOT = 0x0E8D
ASSIGN_SPILL_LOGICAL = 0x5F27FA

# Exact vanilla source -> requested Korean display.  These are the complete 30
# records from the first follow-up candidate.
TARGETS: list[tuple[int, str, str, str]] = [
    (0x5F27CB, "ステ－タス表示", "상태　표시", "status_title"),
    (0x5F27E4, "<E62F>Ａ，Ｙ３：詳細ステ－タス", "Ａ，Ｙ３：상세　상태", "status_hotkey"),
    (0x5F27FA, "ステ－タス表示", "상태　표시", "status_title"),
    (0x5F2811, "<E62F>Ａ，Ｙ３：詳細ステ－タス", "Ａ，Ｙ３：상세　상태", "status_hotkey"),
    (0x5F2827, "ステ－タス表示", "상태　표시", "status_title"),
    (0x5F2831, "ユニットのデ－タを表示します", "유닛　데이터를　표시합니다", "unit_data_help"),
    (0x5F2843, "ユニットのデ－タを表示します", "유닛　데이터를　표시합니다", "unit_data_help"),
    (0x5F2855, "ユニットのデ－タを表示します", "유닛　데이터를　표시합니다", "unit_data_help"),
    (0x5F2868, "ユニットのデ－タを表示します", "유닛　데이터를　표시합니다", "unit_data_help"),
    (0x5F2875, "ステ－タス表示", "상태　표시", "status_title"),
    (0x5F28D3, "ステ－タス表示", "상태　표시", "status_title"),
    (0x5F28DD, "キャラクタ－のデ－タを表示します", "캐릭터　데이터를　표시합니다", "character_data_help"),
    (0x5F28EF, "キャラクタ－のデ－タを表示します", "캐릭터　데이터를　표시합니다", "character_data_help"),
    (0x5F2901, "キャラクタ－のデ－タを表示します", "캐릭터　데이터를　표시합니다", "character_data_help"),
    (0x5F2914, "キャラクタ－のデ－タを表示します", "캐릭터　데이터를　표시합니다", "character_data_help"),
    (0x5F2921, "ステ－タス表示", "상태　표시", "status_title"),
    (0x5F2981, "ユニットやパ－ツを売買します。", "유닛과　파츠를　매매합니다。", "supply_buy_sell"),
    (0x5F2992, "ユニットやパ－ツを購入します。", "유닛과　파츠를　구매합니다。", "supply_purchase"),
    (0x5F29B3, "ユニットやパ－ツを売却します", "유닛과　파츠를　판매합니다", "supply_sale"),
    (0x5F29D8, "ステ－タスを表示します", "상태를　표시합니다", "status_help"),
    (0x5F29E8, "ステ－タスを表示します", "상태를　표시합니다", "status_help"),
    (0x5F2567, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F25B0, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F272A, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F274D, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F27B0, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F299E, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F29C0, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F3073, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
    (0x5F30A7, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시", "y3_status"),
]

# Proven pointer-table owner for every record above on the exact current main.
TARGET_POINTERS: dict[int, int] = {
    0x5F27CB: 0x5F32A2,
    0x5F27E4: 0x5F32A6,
    0x5F27FA: 0x5F32AC,
    0x5F2811: 0x5F32B0,
    0x5F2827: 0x5F32B6,
    0x5F2831: 0x5F32B8,
    0x5F2843: 0x5F32C2,
    0x5F2855: 0x5F32CC,
    0x5F2868: 0x5F32D6,
    0x5F2875: 0x5F32DE,
    0x5F28D3: 0x5F3306,
    0x5F28DD: 0x5F3308,
    0x5F28EF: 0x5F3312,
    0x5F2901: 0x5F331C,
    0x5F2914: 0x5F3326,
    0x5F2921: 0x5F332E,
    0x5F2981: 0x5F336C,
    0x5F2992: 0x5F3376,
    0x5F29B3: 0x5F3380,
    0x5F29D8: 0x5F3394,
    0x5F29E8: 0x5F339E,
    0x5F2567: 0x5F3142,
    0x5F25B0: 0x5F3166,
    0x5F272A: 0x5F3238,
    0x5F274D: 0x5F3242,
    0x5F27B0: 0x5F3292,
    0x5F299E: 0x5F3378,
    0x5F29C0: 0x5F3382,
    0x5F3073: 0x5F3618,
    0x5F30A7: 0x5F3622,
}

ASSIGN_TITLE_POINTERS: dict[int, int] = {
    0x5F2AEF: 0x5F33F6,
    0x5F2B58: 0x5F3428,
    0x5F2B7B: 0x5F3432,
    0x5F2BA0: 0x5F343C,
    0x5F2BD5: 0x5F3446,
    0x5F2BE4: 0x5F3450,
    0x5F2C16: 0x5F345A,
    0x5F2E6B: 0x5F3536,
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def payload_at(rom: bytes | bytearray, logical: int, max_len: int = 256) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"unreadable zstring at {logical:06X}")
    return bytes(got[0]), int(got[1])


def render_payload(dictionary: Any, tbl: Tbl, payload: bytes) -> str:
    body = payload[len(CTRL_LINE) :] if payload.startswith(CTRL_LINE) else payload
    return dictionary.expand(body, tbl)


def read_le16_logical(rom: bytes | bytearray, logical: int) -> int:
    base = stock_base(rom) + logical
    return int(rom[base]) | (int(rom[base + 1]) << 8)


def write_le16_logical(rom: bytearray, logical: int, value: int) -> None:
    base = stock_base(rom) + logical
    rom[base] = value & 0xFF
    rom[base + 1] = (value >> 8) & 0xFF


def pointer_hits_in_table(rom: bytes | bytearray, target_logical: int) -> list[int]:
    off = target_logical & 0xFFFF
    low, high = off & 0xFF, (off >> 8) & 0xFF
    base = stock_base(rom)
    hits: list[int] = []
    for logical in range(POINTER_SCAN_START, POINTER_SCAN_END - 1):
        pos = base + logical
        if rom[pos] == low and rom[pos + 1] == high:
            hits.append(logical)
    return hits


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    pos = 0
    while pos < len(before):
        if before[pos] == after[pos]:
            pos += 1
            continue
        start = pos
        while pos < len(before) and before[pos] != after[pos]:
            pos += 1
        out.append((start, pos))
    return out


def covered(run: tuple[int, int], intervals: list[tuple[int, int]]) -> bool:
    start, end = run
    cursor = start
    for lo, hi in sorted(intervals):
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end


def choose_safe_ext3_slots(
    parent: bytes,
    union: Any,
    ext_meta: dict[str, Any],
    ext3_meta: dict[str, Any],
    count: int,
    phrase_bytes: int,
) -> tuple[int, list[int], dict[str, Any]]:
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    choices: list[tuple[int, int, list[int]]] = []
    for seg in range(EXT3_SEG0, EXT3_SEG0 + num_banks):
        slots: list[int] = []
        for index in inventory.ext3_free:
            if not dict_token_safe_in_zstring(index):
                continue
            try:
                if dictionary._ext3_is_alias(index):
                    continue
                physical_seg, _local = dictionary._ext3_bank_local(index)
                canonical_seg, _ = bank_local_for_index(index)
            except Exception:
                continue
            if physical_seg == seg and canonical_seg == seg:
                slots.append(index)
        room = int(inventory.ext3_bank_room.get(seg - EXT3_SEG0, 0))
        if len(slots) >= count and room >= phrase_bytes:
            choices.append((room, seg, sorted(slots)))
    if not choices:
        raise BuildError(f"no safe canonical ext3 bank fits count={count} bytes={phrase_bytes}")
    room, seg, slots = max(choices, key=lambda row: (row[0], len(row[2])))
    return seg, slots[:count], {"inventory": inventory.as_dict(), "selected_room": room}


def main() -> int:
    parent = bytes(MAIN.read_bytes())
    live_save = bytes(MAIN_SAVE.read_bytes())
    original = bytes(ORIGINAL.read_bytes())
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing/wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    if ext3_meta.get("compact3") is True:
        raise BuildError("compact3 unexpectedly enabled")
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 metadata has no banks")
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    stock_dictionary = Dictionary(parent)

    # Diagnose the exact assignment regression first.  The eight title records
    # still contain FE8D, but slot 0E8D no longer contains `배속`.
    broken_token = token_from_dict_index(BROKEN_ASSIGN_SLOT)
    broken_raw = bytes(stock_dictionary.raw_entry(BROKEN_ASSIGN_SLOT))
    broken_render = parent_dictionary.expand(broken_token, tbl)
    if broken_token != bytes.fromhex("FE8D"):
        raise BuildError(f"unexpected assignment token encoding: {broken_token.hex().upper()}")
    if broken_render.rstrip("　 \t") == "배속":
        raise BuildError("slot 0E8D unexpectedly renders 배속 again; diagnosis drifted")

    # Verify all 30 source records and their single pointer-table owners.
    target_rows: OrderedDict[int, dict[str, Any]] = OrderedDict()
    for logical, jp, ko, group in TARGETS:
        if logical not in TARGET_POINTERS:
            raise BuildError(f"missing target pointer mapping {logical:06X}")
        original_payload, _ = payload_at(original, logical)
        original_render = original_dictionary.expand(original_payload, tbl)
        if original_render != jp:
            raise BuildError(f"vanilla source drift at {logical:06X}: {original_render!r} != {jp!r}")
        current_payload, current_term = payload_at(parent, logical)
        expected_ptr = TARGET_POINTERS[logical]
        if read_le16_logical(parent, expected_ptr) != (logical & 0xFFFF):
            raise BuildError(f"pointer drift at {expected_ptr:06X} for {logical:06X}")
        hits = pointer_hits_in_table(parent, logical)
        if hits != [expected_ptr]:
            raise BuildError(
                f"pointer ownership drift at {logical:06X}: "
                f"expected={[f'{expected_ptr:06X}']} actual={[f'{x:06X}' for x in hits]}"
            )
        prefix = CTRL_LINE if current_payload.startswith(CTRL_LINE) else b""
        target_rows[logical] = {
            "abs": f"{logical:06X}",
            "group": group,
            "jp": jp,
            "ko": ko,
            "before": render_payload(parent_dictionary, tbl, current_payload),
            "prefix_hex": prefix.hex().upper(),
            "payload_len": len(current_payload),
            "old_term_logical": f"{current_term - stock_base(parent):06X}",
            "pointer": f"{expected_ptr:06X}",
            "old_payload_hex": current_payload.hex().upper(),
        }

    # Verify the eight assignment-title records and their owners.
    assign_before: list[dict[str, Any]] = []
    for logical, pointer in ASSIGN_TITLE_POINTERS.items():
        payload, term = payload_at(parent, logical)
        if payload != broken_token:
            raise BuildError(f"assignment title drifted at {logical:06X}: {payload.hex().upper()}")
        if read_le16_logical(parent, pointer) != (logical & 0xFFFF):
            raise BuildError(f"assignment pointer drift at {pointer:06X}")
        hits = pointer_hits_in_table(parent, logical)
        if hits != [pointer]:
            raise BuildError(
                f"assignment pointer ownership drift at {logical:06X}: "
                f"expected={[f'{pointer:06X}']} actual={[f'{x:06X}' for x in hits]}"
            )
        assign_before.append(
            {
                "abs": f"{logical:06X}",
                "pointer": f"{pointer:06X}",
                "payload_hex": payload.hex().upper(),
                "render": render_payload(parent_dictionary, tbl, payload),
                "term_logical": f"{term - stock_base(parent):06X}",
            }
        )

    # One private ext3 phrase per distinct requested display plus 배속.
    phrases = sorted({row[2] for row in TARGETS} | {"배속"})
    encoded = {text: encode_phrase(text, tbl) for text in phrases}
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    phrase_bytes = sum(len(blob) + 1 for blob in encoded.values())
    alloc_seg, selected_slots, alloc_info = choose_safe_ext3_slots(
        parent, union, ext_meta, ext3_meta, len(phrases), phrase_bytes
    )
    phrase_indices = {text: index for text, index in zip(phrases, selected_slots)}
    slot_payloads = {phrase_indices[text]: blob for text, blob in encoded.items()}

    candidate = bytearray(parent)
    ext3_before = bytes(slice_expansion_bank(parent, alloc_seg))
    cursor_before = phrase_cursor(ext3_before)
    ext3_info, ext3_guard = write_ext3_slots_guarded(
        candidate, slot_payloads, union=union, num_banks=num_banks
    )
    if int(ext3_info.get("written") or 0) != len(slot_payloads):
        raise BuildError("ext3 writer did not commit every phrase")

    # Pick one representative for each exact (prefix, Korean text) shape.
    representatives: OrderedDict[tuple[bytes, str], int] = OrderedDict()
    for logical, _jp, ko, _group in TARGETS:
        payload, _ = payload_at(parent, logical)
        prefix = CTRL_LINE if payload.startswith(CTRL_LINE) else b""
        representatives.setdefault((prefix, ko), logical)
    if len(representatives) != 9:
        raise BuildError(f"unexpected representative count: {len(representatives)}")
    if representatives.get((b"", "상태　표시")) != 0x5F27CB:
        raise BuildError("status-title representative drifted")
    if ASSIGN_SPILL_LOGICAL in representatives.values():
        raise BuildError("assignment spill record unexpectedly selected as a representative")

    base = stock_base(candidate)
    modified_record_extents: list[tuple[int, int]] = []
    pointer_extents: list[tuple[int, int]] = []
    representative_rows: list[dict[str, Any]] = []

    # Rewrite only the nine representatives as compact active records.
    for (prefix, ko), representative in representatives.items():
        old, old_term = payload_at(parent, representative)
        token = token_from_ext3_index(phrase_indices[ko], num_banks=num_banks)
        active_payload = prefix + token
        blob = active_payload + b"\x00"
        span = len(old) + 1
        if len(blob) > span:
            raise BuildError(
                f"representative {representative:06X} cannot fit compact record: "
                f"need={len(blob)} span={span}"
            )
        start = base + representative
        candidate[start : start + span] = blob + bytes(span - len(blob))
        modified_record_extents.append((start, start + span))
        representative_rows.append(
            {
                "abs": f"{representative:06X}",
                "ko": ko,
                "prefix_hex": prefix.hex().upper(),
                "ext3_index": f"{phrase_indices[ko]:05X}",
                "old_span": span,
                "new_payload_hex": active_payload.hex().upper(),
                "new_term_logical": f"{representative + len(active_payload):06X}",
                "visible_01_padding": active_payload.count(0x01),
            }
        )

    # Retarget every help/status pointer to its compact representative.
    retarget_rows: list[dict[str, Any]] = []
    for logical, _jp, ko, _group in TARGETS:
        parent_payload, _ = payload_at(parent, logical)
        prefix = CTRL_LINE if parent_payload.startswith(CTRL_LINE) else b""
        representative = representatives[(prefix, ko)]
        pointer = TARGET_POINTERS[logical]
        old_value = read_le16_logical(parent, pointer)
        write_le16_logical(candidate, pointer, representative & 0xFFFF)
        pointer_extents.append((base + pointer, base + pointer + 2))
        retarget_rows.append(
            {
                "source_abs": f"{logical:06X}",
                "pointer": f"{pointer:06X}",
                "old_off16": f"{old_value:04X}",
                "new_abs": f"{representative:06X}",
            }
        )

    # 5F27FA is now unreferenced by the status-title group.  Reuse its old
    # pointer-addressed record extent as a private spill for 배속.  This avoids
    # every stock/retired-slot dependency.
    assign_old, _assign_old_term = payload_at(parent, ASSIGN_SPILL_LOGICAL)
    assign_token = token_from_ext3_index(phrase_indices["배속"], num_banks=num_banks)
    assign_payload = assign_token
    assign_blob = assign_payload + b"\x00"
    assign_span = len(assign_old) + 1
    if len(assign_blob) > assign_span:
        raise BuildError("freed status-title record too small for assignment spill")
    assign_start = base + ASSIGN_SPILL_LOGICAL
    candidate[assign_start : assign_start + assign_span] = assign_blob + bytes(assign_span - len(assign_blob))
    modified_record_extents.append((assign_start, assign_start + assign_span))

    assign_retargets: list[dict[str, Any]] = []
    for logical, pointer in ASSIGN_TITLE_POINTERS.items():
        old_value = read_le16_logical(parent, pointer)
        write_le16_logical(candidate, pointer, ASSIGN_SPILL_LOGICAL & 0xFFFF)
        pointer_extents.append((base + pointer, base + pointer + 2))
        assign_retargets.append(
            {
                "source_abs": f"{logical:06X}",
                "pointer": f"{pointer:06X}",
                "old_off16": f"{old_value:04X}",
                "new_abs": f"{ASSIGN_SPILL_LOGICAL:06X}",
            }
        )

    checksum = update_ws_checksum(candidate)
    final = bytes(candidate)
    final_dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    visible_01_total = 0

    # Verify every active help/status route through its patched pointer.
    for logical, _jp, ko, _group in TARGETS:
        pointer = TARGET_POINTERS[logical]
        off16 = read_le16_logical(final, pointer)
        active_logical = 0x5F0000 | off16
        payload, _term = payload_at(final, active_logical)
        actual = render_payload(final_dictionary, tbl, payload)
        visible_01 = payload.count(0x01)
        visible_01_total += visible_01
        if actual != ko:
            failures.append(
                {
                    "source_abs": f"{logical:06X}",
                    "pointer": f"{pointer:06X}",
                    "reason": "render_mismatch",
                    "expected": ko,
                    "actual": actual,
                    "active_abs": f"{active_logical:06X}",
                }
            )
        if visible_01 != 0:
            failures.append(
                {
                    "source_abs": f"{logical:06X}",
                    "reason": "visible_01_padding",
                    "count": visible_01,
                }
            )

    # Verify all assignment routes now resolve to the private spill and render
    # exactly 배속, while stock slot 0E8D remains byte-identical.
    for logical, pointer in ASSIGN_TITLE_POINTERS.items():
        off16 = read_le16_logical(final, pointer)
        active_logical = 0x5F0000 | off16
        payload, _term = payload_at(final, active_logical)
        actual = render_payload(final_dictionary, tbl, payload)
        visible_01 = payload.count(0x01)
        visible_01_total += visible_01
        if active_logical != ASSIGN_SPILL_LOGICAL or actual != "배속" or visible_01 != 0:
            failures.append(
                {
                    "source_abs": f"{logical:06X}",
                    "reason": "assignment_route",
                    "active_abs": f"{active_logical:06X}",
                    "actual": actual,
                    "visible_01": visible_01,
                }
            )

    final_stock_dictionary = Dictionary(final)
    final_broken_raw = bytes(final_stock_dictionary.raw_entry(BROKEN_ASSIGN_SLOT))
    if final_broken_raw != broken_raw:
        failures.append({"reason": "stock_0E8D_modified"})
    if visible_01_total != 0:
        failures.append({"reason": "active_visible_01_total", "count": visible_01_total})

    # Account for every byte-level change: representatives/private spill,
    # pointer entries, private ext3 slot pointers/phrases, and checksum.
    ext3_after = bytes(slice_expansion_bank(final, alloc_seg))
    cursor_after = phrase_cursor(ext3_after)
    ext3_bank_file = alloc_seg * BANK_SIZE
    ext3_pointer_extents: list[tuple[int, int]] = []
    for index in slot_payloads:
        _seg, local = bank_local_for_index(index)
        ext3_pointer_extents.append((ext3_bank_file + local * 2, ext3_bank_file + local * 2 + 2))
    ext3_phrase_extent = (ext3_bank_file + cursor_before, ext3_bank_file + cursor_after)
    allowed = (
        modified_record_extents
        + pointer_extents
        + ext3_pointer_extents
        + [ext3_phrase_extent, (len(parent) - 2, len(parent))]
    )
    runs = diff_runs(parent, final)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    if unaccounted:
        failures.append({"reason": "unaccounted_diff_runs", "runs": unaccounted[:20]})

    # Runtime hook/code area must remain untouched by this data-only candidate.
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == final[runtime_start:runtime_end]
    if not runtime_unchanged:
        failures.append({"reason": "runtime_hook_changed"})

    if sha(MAIN.read_bytes()) != EXPECTED_MAIN_SHA or MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("live main ROM/SAV changed during candidate build")
    if failures:
        raise BuildError(f"verification failed: {failures[:8]!r}")

    atomic_bytes(OUT_ROM, final)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_menu_help_supply_status_spill_followup_candidate.py",
        "ok": True,
        "published": False,
        "status": "static_verified_pending_user_runtime_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, final),
        "candidate_save": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "diagnosis": {
            "assignment_title_records": len(ASSIGN_TITLE_POINTERS),
            "assignment_old_token_hex": broken_token.hex().upper(),
            "assignment_stock_slot": f"{BROKEN_ASSIGN_SLOT:04X}",
            "assignment_stock_slot_raw_hex": broken_raw.hex().upper(),
            "assignment_stock_slot_render": broken_render,
            "assignment_stock_slot_reused_by_candidate": False,
            "assignment_private_spill_abs": f"{ASSIGN_SPILL_LOGICAL:06X}",
            "help_target_records": len(TARGETS),
            "help_distinct_active_records": len(representatives),
            "active_visible_0x01_padding": visible_01_total,
        },
        "allocation": {
            "ext3_segment": f"{alloc_seg:02X}",
            "phrase_indices": {text: f"{index:05X}" for text, index in phrase_indices.items()},
            "cursor_before": f"{cursor_before:04X}",
            "cursor_after": f"{cursor_after:04X}",
            "guard": ext3_guard.as_dict(),
            **alloc_info,
        },
        "representatives": representative_rows,
        "help_pointer_retargets": retarget_rows,
        "assignment_before": assign_before,
        "assignment_pointer_retargets": assign_retargets,
        "verification": {
            "all_30_help_routes_render_exact": True,
            "all_8_assignment_routes_render_exact": True,
            "all_active_visible_0x01_padding_zero": True,
            "stock_0E8D_untouched": True,
            "all_pointer_owners_verified_before_patch": True,
            "private_ext3_only": True,
            "runtime_hook_unchanged": runtime_unchanged,
            "diffs_bounded": not unaccounted,
            "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == live_save,
        },
        "runtime_gate": [
            "배속 메뉴 제목 8개 경로에서 깨진 글리프/큰 공백 없이 '배속' 표시",
            "유닛/캐릭터 데이터 및 상태 도움말에서 '표시합니다' 뒤 큰 공백 없음",
            "보급 구매/판매/매매 설명에서 trailing 큰 공백 없음",
            "Y3 상태 표시 / 상세 상태 안내에서 trailing 큰 공백 없음",
            "메뉴 이동 -> 상태창 진입 -> 복귀를 반복해도 정상",
        ],
        "promotion": "blocked_pending_user_runtime_visual_verification",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "checksum": report["checksum"],
                "diagnosis": report["diagnosis"],
                "verification": report["verification"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
