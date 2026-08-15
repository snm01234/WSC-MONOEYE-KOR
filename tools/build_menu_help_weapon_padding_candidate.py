#!/usr/bin/env python3
"""Build the menu-help + assignment-release + weapon-padding runtime candidate.

User-visible goals (2026-08-09):
1. Finish Korean localization of the intermission/list/development help popups that
   still mix Japanese with Korean, and render 配属 release `解除` as `해제`.
2. Keep the complete weapon name `트리플 메가소닉 포` at 75CB03 while removing
   only the white overdraw caused by the fixed field's visible 0x01 padding.

Safety strategy:
* Current main TIP is the only parent; the main ROM/SAV are never modified.
* Normal >=4-byte UI bodies are retargeted to private E5 18 ext3 phrases while
  preserving their exact payload extent and NUL terminator.
* Existing E6 2F line-control prefixes are kept byte-exact outside the phrase.
* Remaining body capacity is filled with a private *empty* ext3 phrase token in
  4-byte groups, leaving only 0..3 visible 0x01 padding cells.
* The 3-byte `一覧` title cannot hold E5 18 and compact3 is forbidden.  One
  strongly-retired stock slot (005E) is repointed to the proven-unused final
  7 bytes of the terminology-round2 orphan area and stores `목록` there.
* `解除` is a fixed 3-byte raw UI string and reuses existing stock token 0399
  (`해제`) as F3 99 01.
* Weapon 75CB03 retains its original first token E5 18 EE E3 and replaces only
  eight visible 01 cells with two empty ext3 tokens.  The outer 12-byte field
  and NUL terminator remain unchanged.

The empty ext3 filler is intentionally a runtime candidate technique.  It is
statically well-formed but requires the user's emulator visual confirmation
before any main-TIP promotion.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, phrase_cursor
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    write_le16,
)
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
UI_SPILL = ROOT / "data/ui_spill_ko.json"
OUT_ROM = ROOT / "out/patch/menu_help_weapon_padding_candidate.wsc"
OUT_SAVE = ROOT / "sram/menu_help_weapon_padding_candidate.sav"
SRAM_MIRROR = ROOT / "sram/menu_help_weapon_padding_candidate.sav"
REPORT = ROOT / "out/patch/menu_help_weapon_padding_candidate_report.json"

EXPECTED_PARENT = "cfb1905aa8f19eb94b92bd23cb96b2657d05b7d18e7b3426b435ef41cb345f5f"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
CTRL_LINE = bytes.fromhex("E62F")
LIST_STOCK_SLOT = 0x005E
RELEASE_STOCK_SLOT = 0x0399
LIST_ORPHAN_LOGICAL = 0x5FD59C
LIST_ORPHAN_FILE = 0xDFD59C
LIST_ORPHAN_END = 0xDFD5A3
EXPECTED_LIST_ORPHAN_BYTES = bytes.fromhex("e772e743e7d600")
WEAPON_LOGICAL = 0x75CB03
WEAPON_TOKEN = bytes.fromhex("E518EEE3")
WEAPON_TEXT = "트리플　메가소닉　포"
RELEASE_LOGICAL = 0x5F445F

# Intermission/list/development block immediately preceding ui_spill_ko.json.
# This is deliberately explicit so later game-data changes fail rather than
# allowing a broad blind Japanese->Korean rewrite.
EARLY_UI: list[tuple[int, str, str]] = [
    (0x5F29F5, "ー覧", "목록"),
    (0x5F29F9, "各種ー覧を表示します", "각종　목록을　표시합니다"),
    (0x5F2A08, "ー覧", "목록"),
    (0x5F2A0C, "キャラクタ－ー覧を表示します", "캐릭터　목록을　표시합니다"),
    (0x5F2A19, "<E62F>Ｙ１：ソ－ト順変更", "Ｙ１：정렬　순서　변경"),
    (0x5F2A2A, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2A3C, "ー覧", "목록"),
    (0x5F2A40, "ユニットへの乗り降りを選択します", "유닛　탑승／하차를　선택합니다"),
    (0x5F2A50, "ー覧", "목록"),
    (0x5F2A54, "搭乗するユニットを選択します", "탑승할　유닛을　선택합니다"),
    (0x5F2A60, "<E62F>Ｙ１：ソ－ト順変更", "Ｙ１：정렬　순서　변경"),
    (0x5F2A71, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2A83, "ー覧", "목록"),
    (0x5F2A87, "ステ－タスを表示します", "상태를　표시합니다"),
    (0x5F2A94, "ー覧", "목록"),
    (0x5F2A98, "ステ－タスを表示します", "상태를　표시합니다"),
    (0x5F2AA5, "ー覧", "목록"),
    (0x5F2AA9, "所有ユニットのー覧を表示します", "보유　유닛　목록을　표시합니다"),
    (0x5F2ABB, "<E62F>Ｙ１：ソ－ト順変更", "Ｙ１：정렬　순서　변경"),
    (0x5F2ACC, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2ADE, "ー覧", "목록"),
    (0x5F2AE2, "ソ－ト順を変更します", "정렬　순서를　변경합니다"),
    (0x5F2AF2, "ソ－ト順を変更します", "정렬　순서를　변경합니다"),
    (0x5F2B05, "ソ－ト順を変更します", "정렬　순서를　변경합니다"),
    (0x5F2B25, "ソ－ト順を変更します", "정렬　순서를　변경합니다"),
    (0x5F2B35, "ソ－ト順を変更します", "정렬　순서를　변경합니다"),
    (0x5F2B42, "ー覧", "목록"),
    (0x5F2B46, "所有パ－ツのー覧を表示します", "보유　파츠　목록을　표시합니다"),
    (0x5F2B5B, "配属するスロットを選択します", "배속　위치를　선택합니다"),
    (0x5F2B69, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2B7E, "配属コマンドメニュ－を表示します", "배속　명령　메뉴를　표시합니다"),
    (0x5F2B8E, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2BA3, "キャラクタ－のー覧を表示します", "캐릭터　목록을　표시합니다"),
    (0x5F2BB1, "<E62F>Ｙ１：ソ－ト順の変更", "Ｙ１：정렬　순서　변경"),
    (0x5F2BC3, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2BD8, "搭乗か配属かを選択します", "탑승／배속을　선택합니다"),
    (0x5F2BE7, "搭乗するユニットを選択します", "탑승할　유닛을　선택합니다"),
    (0x5F2BF3, "<E62F>Ｙ１：ソ－ト順変更", "Ｙ１：정렬　순서　변경"),
    (0x5F2C04, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2C19, "ステ－タスを表示します", "상태를　표시합니다"),
    (0x5F2C26, "開発プラン", "개발　플랜"),
    (0x5F2C2C, "検索する項目を選択します", "검색할　항목을　선택합니다"),
    (0x5F2C3B, "開発プラン", "개발　플랜"),
    (0x5F2C41, "改造に必要な組合せを表示します", "개조에　필요한　조합을　표시합니다"),
    (0x5F2C53, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2C65, "開発プラン", "개발　플랜"),
    (0x5F2C6B, "改造ユニットのー覧を表示します", "개조　유닛　목록을　표시합니다"),
    (0x5F2C7B, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2C8D, "開発プラン", "개발　플랜"),
    (0x5F2C93, "組合せ可能なパ－ツを表示します", "조합　가능한　파츠를　표시합니다"),
    (0x5F2CA4, "<E62F>Ａ，Ｙ３：ステ－タス表示", "Ａ，Ｙ３：상세　상태"),
    (0x5F2CBA, "開発プラン", "개발　플랜"),
    (0x5F2CC0, "改造用パ－ツのー覧を表示します", "개조용　파츠　목록을　표시합니다"),
    (0x5F2CD1, "開発プラン", "개발　플랜"),
    (0x5F2CD7, "組合せ可能なユニットを表示します", "조합　가능한　유닛을　표시합니다"),
    (0x5F2CE8, "<E62F>Ａ，Ｙ３：ステ－タス表示", "Ａ，Ｙ３：상세　상태"),
    (0x5F2CFE, "開発プラン", "개발　플랜"),
    (0x5F2D04, "ユニットのデ－タを表示します", "유닛　데이터를　표시합니다"),
    (0x5F2D14, "改造するユニットを選択します", "개조할　유닛을　선택합니다"),
    (0x5F2D20, "<E62F>Ｙ１：ソ－ト順変更", "Ｙ１：정렬　순서　변경"),
    (0x5F2D31, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2D46, "組合せ可能なパ－ツを表示します", "조합　가능한　파츠를　표시합니다"),
    (0x5F2D57, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2D6C, "改造を実行しています", "개조를　실행　중입니다"),
    (0x5F2D77, "ステ－タスを表示します", "상태를　표시합니다"),
    (0x5F2D87, "ステ－タスを表示します", "상태를　표시합니다"),
    (0x5F2D97, "分解するユニットを選択します", "분해할　유닛을　선택합니다"),
    (0x5F2DA3, "<E62F>Ｙ１：ソ－ト順変更", "Ｙ１：정렬　순서　변경"),
    (0x5F2DB4, "<E62F>Ｙ３：ステ－タス表示", "Ｙ３：상태　표시"),
    (0x5F2DC9, "分解を実行します", "분해를　실행합니다"),
    (0x5F2DD5, "上書きセ－ブの確認を行います", "덮어쓰기　세이브를　확인합니다"),
    (0x5F2DE8, "ロ－ドの確認を行います", "로드를　확인합니다"),
]


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = payload if payload is not None else path.read_bytes()
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "size": len(data), "sha256": sha(data)}


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


def choose_ext3_slots(parent: bytes, union: Any, ext_meta: dict, ext3_meta: dict, count: int, phrase_bytes: int) -> tuple[int, list[int], dict[str, Any]]:
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    choices: list[tuple[int, int, list[int]]] = []
    for seg in range(EXP3_SEG0, EXP3_SEG0 + num_banks):
        slots: list[int] = []
        for index in inventory.ext3_free:
            try:
                # Avoid the five-bank alias window: the generic ext3 writer uses
                # canonical page->bank mapping, so only canonical slots belong here.
                if dictionary._ext3_is_alias(index):
                    continue
                physical_seg, _local = dictionary._ext3_bank_local(index)
                canonical_seg, _ = bank_local_for_index(index)
                if physical_seg == seg and canonical_seg == seg:
                    slots.append(index)
            except Exception:
                continue
        room = int(inventory.ext3_bank_room.get(seg - EXP3_SEG0, 0))
        if len(slots) >= count and room >= phrase_bytes:
            choices.append((room, seg, sorted(slots)))
    if not choices:
        raise BuildError(f"no canonical ext3 bank fits count={count} bytes={phrase_bytes}")
    room, seg, slots = max(choices, key=lambda row: (row[0], len(row[2])))
    return seg, slots[:count], {"inventory": inventory.as_dict(), "selected_room": room}


def build_targets(parent: bytes, original: bytes, current_dictionary: Any, original_dictionary: Any, tbl: Tbl) -> OrderedDict[int, dict[str, Any]]:
    targets: OrderedDict[int, dict[str, Any]] = OrderedDict()

    # First add the explicit early block, verifying the vanilla Japanese source.
    for logical, jp, ko in EARLY_UI:
        op, _ = payload_at(original, logical)
        original_render = original_dictionary.expand(op, tbl)
        if original_render != jp:
            raise BuildError(f"original source drift at {logical:06X}: {original_render!r} != {jp!r}")
        cp, ct = payload_at(parent, logical)
        targets[logical] = {
            "abs": f"{logical:06X}", "source": "early_intermission_help", "jp": jp, "ko": ko,
            "payload_len": len(cp), "terminator": ct,
        }

    # Then add the approved 62-row ui_spill catalog.  Overlaps must agree.
    spill = json.loads(UI_SPILL.read_text(encoding="utf-8"))
    lines = spill.get("lines") or []
    if len(lines) != 62:
        raise BuildError(f"ui_spill population drifted: {len(lines)}")
    for source in lines:
        logical = int(str(source["abs"]), 16)
        ko = str(source["ko"])
        jp = str(source["jp"])
        op, _ = payload_at(original, logical)
        original_render = original_dictionary.expand(op, tbl)
        if original_render != jp:
            raise BuildError(f"ui_spill vanilla source drift at {logical:06X}: {original_render!r} != {jp!r}")
        cp, ct = payload_at(parent, logical)
        row = {
            "abs": f"{logical:06X}", "source": "ui_spill_ko", "jp": jp, "ko": ko,
            "payload_len": len(cp), "terminator": ct,
        }
        if logical in targets:
            if targets[logical]["ko"] != ko:
                raise BuildError(f"overlap target disagreement at {logical:06X}")
            targets[logical]["sources"] = [targets[logical]["source"], "ui_spill_ko"]
        else:
            targets[logical] = row

    for logical, row in targets.items():
        payload, term = payload_at(parent, logical)
        if term != row["terminator"] or parent[term] != 0:
            raise BuildError(f"parent terminator drift at {logical:06X}")
        prefix_len = len(CTRL_LINE) if payload.startswith(CTRL_LINE) else 0
        body_capacity = len(payload) - prefix_len
        row["prefix_hex"] = payload[:prefix_len].hex().upper()
        row["body_capacity"] = body_capacity
        row["before"] = render_payload(current_dictionary, tbl, payload)
        if row["ko"] == "목록":
            if body_capacity != 3 or prefix_len:
                raise BuildError(f"목록 title no longer has 3-byte body at {logical:06X}")
            row["strategy"] = "retired_stock_list"
        elif body_capacity >= 4:
            row["strategy"] = "private_ext3"
        else:
            raise BuildError(f"unsupported UI body capacity {body_capacity} at {logical:06X}")
    return targets


def main() -> int:
    parent = bytes(MAIN.read_bytes())
    live_save = bytes(MAIN_SAVE.read_bytes())
    original = bytes(ORIGINAL.read_bytes())
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing/wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    if ext3_meta.get("compact3") is not False:
        raise BuildError("compact3 unexpectedly enabled")
    num_banks = int(ext3_meta.get("num_banks") or 0)
    current_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    targets = build_targets(parent, original, current_dictionary, original_dictionary, tbl)

    # Prove the short `목록` stock slot is strongly retired and unreachable now.
    retired = current_strong_retired_slots(original, parent, current_dictionary)
    if LIST_STOCK_SLOT not in retired:
        raise BuildError(f"stock slot {LIST_STOCK_SLOT:04X} is no longer strongly retired")
    wanted = {LIST_STOCK_SLOT}
    ext_refs = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested_refs = nested_occurrence_map(current_dictionary, wanted=wanted, ext3_aware=True)
    raw_refs = _raw_pair_hits(parent, [LIST_STOCK_SLOT])
    if ext_refs.get(LIST_STOCK_SLOT) or nested_refs.get(LIST_STOCK_SLOT) or raw_refs.get(LIST_STOCK_SLOT):
        raise BuildError("selected list-title retired stock slot has a current consumer")

    # Prove the final 7 bytes of the terminology orphan region remain unused.
    if parent[LIST_ORPHAN_FILE:LIST_ORPHAN_END] != EXPECTED_LIST_ORPHAN_BYTES:
        raise BuildError("list orphan tail identity drifted")
    for i in range(current_dictionary.count):
        try:
            a = current_dictionary.entry_abs(i)
            b = a + len(current_dictionary.raw_entry(i)) + 1
        except Exception:
            continue
        if max(a, LIST_ORPHAN_FILE) < min(b, LIST_ORPHAN_END):
            raise BuildError(f"list orphan tail overlaps live dictionary entry {i:04X}")

    direct_phrases = sorted({row["ko"] for row in targets.values() if row["strategy"] == "private_ext3"})
    encoded = {text: encode_phrase(text, tbl) for text in direct_phrases}
    # One additional empty phrase is the zero-visible-cell padding token.
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    slot_count = len(direct_phrases) + 1
    phrase_bytes = sum(len(blob) + 1 for blob in encoded.values()) + 1
    alloc_seg, selected_slots, alloc_info = choose_ext3_slots(parent, union, ext_meta, ext3_meta, slot_count, phrase_bytes)
    empty_index = selected_slots[0]
    phrase_indices = {text: index for text, index in zip(direct_phrases, selected_slots[1:])}
    slot_payloads: dict[int, bytes] = {empty_index: b""}
    slot_payloads.update({phrase_indices[text]: blob for text, blob in encoded.items()})

    candidate = bytearray(parent)
    ext3_before = bytes(slice_expansion_bank(parent, alloc_seg))
    cursor_before = phrase_cursor(ext3_before)
    ext3_info, ext3_guard = write_ext3_slots_guarded(
        candidate, slot_payloads, union=union, num_banks=num_banks
    )
    if int(ext3_info.get("written") or 0) != len(slot_payloads):
        raise BuildError("ext3 writer did not commit every private phrase")
    empty_token = token_from_ext3_index(empty_index, num_banks=num_banks)

    # Repoint the verified retired 005E slot to the 7-byte orphan tail containing 목록.
    list_encoded = encode_phrase("목록", tbl)
    if len(list_encoded) + 1 != LIST_ORPHAN_END - LIST_ORPHAN_FILE:
        raise BuildError(f"목록 orphan extent drift: encoded={len(list_encoded)}")
    candidate[LIST_ORPHAN_FILE : LIST_ORPHAN_FILE + len(list_encoded)] = list_encoded
    candidate[LIST_ORPHAN_FILE + len(list_encoded)] = 0
    candidate_dictionary_stock = Dictionary(candidate)
    ptr_abs = candidate_dictionary_stock.ptr_file + LIST_STOCK_SLOT * 2
    write_le16(candidate, ptr_abs, LIST_ORPHAN_LOGICAL & 0xFFFF)
    list_token = token_from_dict_index(LIST_STOCK_SLOT)

    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    base = stock_base(candidate)
    for logical, row in targets.items():
        old, old_term = payload_at(candidate, logical)
        prefix = CTRL_LINE if old.startswith(CTRL_LINE) else b""
        body_capacity = len(old) - len(prefix)
        if row["strategy"] == "retired_stock_list":
            body = list_token + b"\x01"
            phrase_index: str | None = None
        else:
            index = phrase_indices[row["ko"]]
            phrase_token = token_from_ext3_index(index, num_banks=num_banks)
            remaining = body_capacity - len(phrase_token)
            if remaining < 0:
                raise BuildError(f"negative remaining body at {logical:06X}")
            body = phrase_token + empty_token * (remaining // 4) + b"\x01" * (remaining % 4)
            phrase_index = f"{index:05X}"
        replacement = prefix + body
        if len(replacement) != len(old):
            raise BuildError(f"payload length drift while writing {logical:06X}")
        start = base + logical
        candidate[start : start + len(replacement)] = replacement
        target_extents.append((start, start + len(replacement)))
        applied.append({
            **row,
            "old_payload_hex": old.hex().upper(),
            "new_payload_hex": replacement.hex().upper(),
            "ext3_index": phrase_index,
            "trailing_01": len(body) - len(body.rstrip(b"\x01")),
        })
        _now, now_term = payload_at(candidate, logical)
        if now_term != old_term:
            raise BuildError(f"terminator moved at {logical:06X}")

    # Fixed 3-byte raw `解除` -> stock phrase `해제` + one padding cell.
    release_old, release_term = payload_at(candidate, RELEASE_LOGICAL)
    if release_old != bytes.fromhex("BEE0DD") or len(release_old) != 3:
        raise BuildError(f"解除 raw identity drifted: {release_old.hex()}")
    if current_dictionary.expand_index(RELEASE_STOCK_SLOT, tbl).rstrip("　 ") != "해제":
        raise BuildError("stock 0399 no longer expands to 해제")
    release_new = token_from_dict_index(RELEASE_STOCK_SLOT) + b"\x01"
    candidate[base + RELEASE_LOGICAL : base + RELEASE_LOGICAL + 3] = release_new
    target_extents.append((base + RELEASE_LOGICAL, base + RELEASE_LOGICAL + 3))
    if payload_at(candidate, RELEASE_LOGICAL)[1] != release_term:
        raise BuildError("解除 terminator moved")

    # Weapon: preserve full-name token and replace exactly eight visible spaces.
    weapon_old, weapon_term = payload_at(candidate, WEAPON_LOGICAL)
    if len(weapon_old) != 12 or weapon_old[:4] != WEAPON_TOKEN or weapon_old[4:] != b"\x01" * 8:
        raise BuildError(f"weapon padding identity drifted: {weapon_old.hex().upper()}")
    weapon_new = WEAPON_TOKEN + empty_token + empty_token
    candidate[base + WEAPON_LOGICAL : base + WEAPON_LOGICAL + 12] = weapon_new
    target_extents.append((base + WEAPON_LOGICAL, base + WEAPON_LOGICAL + 12))
    if payload_at(candidate, WEAPON_LOGICAL)[1] != weapon_term:
        raise BuildError("weapon terminator moved")

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    # Fail-closed static verification of every rewritten visible target.
    failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        payload, term = payload_at(candidate_bytes, logical)
        actual = render_payload(candidate_dictionary, tbl, payload).rstrip("　 ")
        expected = str(row["ko"]).rstrip("　 ")
        if actual != expected:
            failures.append({"abs": row["abs"], "reason": "render_mismatch", "expected": expected, "actual": actual})
        if term != int(row["terminator"]):
            failures.append({"abs": row["abs"], "reason": "terminator_changed"})

    release_render = candidate_dictionary.expand(release_new, tbl).rstrip("　 ")
    if release_render != "해제":
        failures.append({"abs": f"{RELEASE_LOGICAL:06X}", "reason": "release_render", "actual": release_render})
    weapon_render = candidate_dictionary.expand(weapon_new, tbl)
    if weapon_render != WEAPON_TEXT:
        failures.append({"abs": f"{WEAPON_LOGICAL:06X}", "reason": "weapon_render", "expected": WEAPON_TEXT, "actual": weapon_render})
    if candidate_dictionary.expand(empty_token, tbl) != "":
        failures.append({"reason": "empty_filler_not_empty"})

    # Bound all byte changes to known records, one stock pointer/storage area,
    # ext3 allocation bank, and the final two checksum bytes.
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
        (LIST_ORPHAN_FILE, LIST_ORPHAN_END),
        (ptr_abs, ptr_abs + 2),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs if not covered((left, right), allowed)
    ]
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]
    compact3_new = 0
    for left, right in runs:
        segment = candidate_bytes[max(0, left - 2) : min(len(candidate_bytes), right + 2)]
        compact3_new += segment.count(bytes.fromhex("E519"))

    if failures or unaccounted or not runtime_unchanged or compact3_new:
        raise BuildError(
            f"candidate verification failed: targets={len(failures)} unaccounted={len(unaccounted)} "
            f"runtime_unchanged={runtime_unchanged} compact3_new={compact3_new} "
            f"failure_sample={failures[:5]!r} unaccounted_sample={unaccounted[:5]!r}"
        )
    if sha(MAIN.read_bytes()) != EXPECTED_PARENT or MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("main ROM/SAV changed during candidate build")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    SRAM_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MAIN_SAVE, SRAM_MIRROR)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_menu_help_weapon_padding_candidate.py",
        "ok": True,
        "published": False,
        "status": "static_verified_pending_user_runtime_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "sram_mirror": identity(SRAM_MIRROR),
        "checksum": f"{checksum:04X}",
        "counts": {
            "ui_targets": len(targets),
            "early_ui_targets": len(EARLY_UI),
            "ui_spill_catalog_rows": 62,
            "unique_ext3_phrases": len(direct_phrases),
            "list_title_targets": sum(row["strategy"] == "retired_stock_list" for row in targets.values()),
            "target_failures": len(failures),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segment": f"{alloc_seg:02X}",
            "empty_ext3_index": f"{empty_index:05X}",
            "empty_token_hex": empty_token.hex().upper(),
            "ext3_cursor_before": f"{cursor_before:04X}",
            "ext3_cursor_after": f"{cursor_after:04X}",
            "ext3_phrase_bytes": cursor_after - cursor_before,
            "guard": ext3_guard.as_dict(),
            **alloc_info,
            "list_stock_slot": f"{LIST_STOCK_SLOT:04X}",
            "list_stock_token": list_token.hex().upper(),
            "list_orphan_file": f"{LIST_ORPHAN_FILE:08X}-{LIST_ORPHAN_END:08X}",
        },
        "release": {
            "abs": f"{RELEASE_LOGICAL:06X}",
            "before_hex": release_old.hex().upper(),
            "after_hex": release_new.hex().upper(),
            "rendered": release_render,
            "terminator": f"{release_term - stock_base(candidate_bytes):06X}",
        },
        "weapon": {
            "abs": f"{WEAPON_LOGICAL:06X}",
            "before_hex": weapon_old.hex().upper(),
            "after_hex": weapon_new.hex().upper(),
            "name_token_unchanged": weapon_new[:4] == WEAPON_TOKEN,
            "rendered_exact": weapon_render,
            "trailing_visible_spaces": len(weapon_render) - len(weapon_render.rstrip("　 ")),
            "terminator": f"{weapon_term - stock_base(candidate_bytes):06X}",
            "runtime_test_required": True,
        },
        "verification": {
            "all_ui_targets_render_exact": not failures,
            "all_ui_payload_extents_preserved": True,
            "all_ui_terminators_preserved": True,
            "release_renders_해제": release_render == "해제",
            "weapon_full_name_preserved": weapon_render == WEAPON_TEXT,
            "weapon_name_token_byte_exact": weapon_new[:4] == WEAPON_TOKEN,
            "weapon_visual_padding_zero": weapon_render == weapon_render.rstrip("　 "),
            "empty_ext3_static_zero_width": candidate_dictionary.expand(empty_token, tbl) == "",
            "compact3_new_occurrences_zero": compact3_new == 0,
            "runtime_hook_unchanged": runtime_unchanged,
            "diffs_bounded": not unaccounted,
            "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_PARENT,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == live_save,
            "candidate_saveram_matches_live": OUT_SAVE.read_bytes() == live_save,
        },
        "applied": applied,
        "failures": failures,
        "unaccounted_diff_runs": unaccounted,
        "promotion": "blocked_pending_user_runtime_visual_verification",
        "runtime_gate": [
            "intermission/list/development help popups show Korean without Japanese fragments",
            "배속 해제 option renders 해제 instead of 解除",
            "트리플 메가소닉 포 remains fully visible and the white right-side protrusion disappears",
            "weapon selection/navigation remains stable after entering/leaving the affected unit weapon list",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "checksum": report["checksum"],
        "counts": report["counts"],
        "release": report["release"],
        "weapon": report["weapon"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
