#!/usr/bin/env python3
"""Build a main-TIP follow-up for the user-reported mixed Japanese menu help.

Scope (2026-08-16):
* Supply help shown in the first screenshot (purchase / buy-sell / sale).
* Character/unit status help from the added screenshot and its duplicate routes.
* Remaining identical Y3/status-display help strings in the same bank-5F UI family.

The builder is fail-closed against the exact current main TIP and vanilla source.
It preserves every target payload extent and NUL terminator, preserves E6 2F line
control prefixes byte-exactly, and uses private safe ext3 phrases plus a zero-width
ext3 filler.  It never overwrites the live main TIP or live SaveRAM.
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
    EXT3_INDEX_BASE,
    EXT3_SEG0,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
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
OUT_ROM = PATCH / "menu_help_supply_status_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/menu_help_supply_status_followup_candidate.sav"
REPORT = PATCH / "menu_help_supply_status_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "f62f14d15f3d76ad2eb33e2b55531ab4781d230cb63bcc2348bbd703d8c39be3"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
CTRL_LINE = bytes.fromhex("E62F")

# Exact vanilla source -> requested Korean display.  Duplicate routes are kept
# explicit so the audit can prove that every currently reachable copy changed.
TARGETS: list[tuple[int, str, str, str]] = [
    # Status titles / status descriptions around unit & character data help.
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

    # Supply screen shown by the first screenshot.
    (0x5F2981, "ユニットやパ－ツを売買します。", "유닛과　파츠를　매매합니다。", "supply_buy_sell"),
    (0x5F2992, "ユニットやパ－ツを購入します。", "유닛과　파츠를　구매합니다。", "supply_purchase"),
    (0x5F29B3, "ユニットやパ－ツを売却します", "유닛과　파츠를　판매합니다", "supply_sale"),
    (0x5F29D8, "ステ－タスを表示します", "상태를　표시합니다", "status_help"),
    (0x5F29E8, "ステ－タスを表示します", "상태를　표시합니다", "status_help"),

    # Identical Y3 status help still left Japanese in the current main TIP.
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
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def payload_at(rom: bytes | bytearray, logical: int, max_len: int = 256) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"unreadable zstring at {logical:06X}")
    return bytes(got[0]), int(got[1])


def render_payload(dictionary: Any, tbl: Tbl, payload: bytes) -> str:
    body = payload[len(CTRL_LINE) :] if payload.startswith(CTRL_LINE) else payload
    return dictionary.expand(body, tbl)


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
    current_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)

    rows: OrderedDict[int, dict[str, Any]] = OrderedDict()
    for logical, jp, ko, group in TARGETS:
        if logical in rows:
            raise BuildError(f"duplicate target address {logical:06X}")
        original_payload, _ = payload_at(original, logical)
        original_render = original_dictionary.expand(original_payload, tbl)
        if original_render != jp:
            raise BuildError(
                f"vanilla source drift at {logical:06X}: {original_render!r} != {jp!r}"
            )
        current_payload, current_term = payload_at(parent, logical)
        prefix = CTRL_LINE if current_payload.startswith(CTRL_LINE) else b""
        body_capacity = len(current_payload) - len(prefix)
        if body_capacity < 4:
            raise BuildError(f"target body too short for ext3 at {logical:06X}: {body_capacity}")
        rows[logical] = {
            "abs": f"{logical:06X}",
            "group": group,
            "jp": jp,
            "ko": ko,
            "before": render_payload(current_dictionary, tbl, current_payload),
            "payload_len": len(current_payload),
            "body_capacity": body_capacity,
            "prefix_hex": prefix.hex().upper(),
            "terminator": current_term,
        }

    phrases = sorted({row["ko"] for row in rows.values()})
    encoded = {text: encode_phrase(text, tbl) for text in phrases}
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    phrase_bytes = sum(len(blob) + 1 for blob in encoded.values()) + 1  # + empty phrase NUL
    alloc_seg, selected_slots, alloc_info = choose_safe_ext3_slots(
        parent, union, ext_meta, ext3_meta, len(phrases) + 1, phrase_bytes
    )
    empty_index = selected_slots[0]
    phrase_indices = {text: index for text, index in zip(phrases, selected_slots[1:])}
    slot_payloads: dict[int, bytes] = {empty_index: b""}
    slot_payloads.update({phrase_indices[text]: blob for text, blob in encoded.items()})
    for index in slot_payloads:
        if not dict_token_safe_in_zstring(index):
            raise BuildError(f"unsafe ext3 token selected: {index:05X}")

    candidate = bytearray(parent)
    ext3_before = bytes(slice_expansion_bank(parent, alloc_seg))
    cursor_before = phrase_cursor(ext3_before)
    ext3_info, ext3_guard = write_ext3_slots_guarded(
        candidate, slot_payloads, union=union, num_banks=num_banks
    )
    if int(ext3_info.get("written") or 0) != len(slot_payloads):
        raise BuildError("ext3 writer did not commit every phrase")
    empty_token = token_from_ext3_index(empty_index, num_banks=num_banks)

    base = stock_base(candidate)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for logical, row in rows.items():
        old, old_term = payload_at(candidate, logical)
        prefix = CTRL_LINE if old.startswith(CTRL_LINE) else b""
        body_capacity = len(old) - len(prefix)
        index = phrase_indices[row["ko"]]
        token = token_from_ext3_index(index, num_banks=num_banks)
        remaining = body_capacity - len(token)
        if remaining < 0:
            raise BuildError(f"negative padding at {logical:06X}")
        body = token + empty_token * (remaining // 4) + b"\x01" * (remaining % 4)
        replacement = prefix + body
        if len(replacement) != len(old):
            raise BuildError(f"payload length drift at {logical:06X}")
        start = base + logical
        candidate[start : start + len(replacement)] = replacement
        target_extents.append((start, start + len(replacement)))
        if payload_at(candidate, logical)[1] != old_term:
            raise BuildError(f"terminator moved at {logical:06X}")
        applied.append(
            {
                **row,
                "ext3_index": f"{index:05X}",
                "old_payload_hex": old.hex().upper(),
                "new_payload_hex": replacement.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        payload, term = payload_at(candidate_bytes, logical)
        actual = render_payload(candidate_dictionary, tbl, payload).rstrip("　 ")
        expected = str(row["ko"]).rstrip("　 ")
        if actual != expected:
            failures.append(
                {"abs": row["abs"], "reason": "render_mismatch", "expected": expected, "actual": actual}
            )
        if term != int(row["terminator"]):
            failures.append({"abs": row["abs"], "reason": "terminator_changed"})
    if candidate_dictionary.expand(empty_token, tbl) != "":
        failures.append({"reason": "empty_filler_not_zero_width"})

    ext3_after = bytes(slice_expansion_bank(candidate_bytes, alloc_seg))
    cursor_after = phrase_cursor(ext3_after)
    ext3_bank_file = alloc_seg * BANK_SIZE
    ext3_pointer_extents: list[tuple[int, int]] = []
    for index in slot_payloads:
        _seg, local = bank_local_for_index(index)
        ext3_pointer_extents.append((ext3_bank_file + local * 2, ext3_bank_file + local * 2 + 2))
    ext3_phrase_extent = (ext3_bank_file + cursor_before, ext3_bank_file + cursor_after)
    allowed = target_extents + ext3_pointer_extents + [
        ext3_phrase_extent,
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    # Core runtime hook area is deliberately untouched by this pure data patch.
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]
    if failures or unaccounted or not runtime_unchanged:
        raise BuildError(
            f"verification failed: failures={failures[:5]!r} "
            f"unaccounted={unaccounted[:5]!r} runtime_unchanged={runtime_unchanged}"
        )
    if sha(MAIN.read_bytes()) != EXPECTED_MAIN_SHA or MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("live main ROM/SAV changed during candidate build")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_menu_help_supply_status_followup_candidate.py",
        "ok": True,
        "published": False,
        "status": "static_verified_pending_user_runtime_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "counts": {
            "targets": len(applied),
            "unique_phrases": len(phrases),
            "groups": {group: sum(1 for row in applied if row["group"] == group) for group in sorted({row["group"] for row in applied})},
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segment": f"{alloc_seg:02X}",
            "empty_ext3_index": f"{empty_index:05X}",
            "empty_token_hex": empty_token.hex().upper(),
            "cursor_before": f"{cursor_before:04X}",
            "cursor_after": f"{cursor_after:04X}",
            "guard": ext3_guard.as_dict(),
            **alloc_info,
        },
        "verification": {
            "all_targets_render_exact": not failures,
            "all_payload_extents_preserved": True,
            "all_terminators_preserved": True,
            "line_control_prefixes_preserved": True,
            "safe_ext3_tokens_only": all(dict_token_safe_in_zstring(i) for i in slot_payloads),
            "runtime_hook_unchanged": runtime_unchanged,
            "diffs_bounded": not unaccounted,
            "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == live_save,
        },
        "applied": applied,
        "failures": failures,
        "unaccounted_diff_runs": unaccounted,
        "runtime_gate": [
            "보급 구매 화면: '유닛과 파츠를 구매합니다。'가 한글로만 표시",
            "보급 판매/매매 설명도 일본어 조사·동사 없이 한글로 표시",
            "추가 캡처의 캐릭터 데이터 설명이 '캐릭터 데이터를 표시합니다'로 표시",
            "Y3 안내가 'Ｙ３：상태 표시'로 표시되고 메뉴 전환/상태 화면 진입이 정상",
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
                "counts": report["counts"],
                "allocation": {
                    "ext3_segment": report["allocation"]["ext3_segment"],
                    "empty_ext3_index": report["allocation"]["empty_ext3_index"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
