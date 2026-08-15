#!/usr/bin/env python3
"""Build a static-verified candidate for the Domon follow-up retranslation.

The candidate is deliberately based on the current main TIP and is written to
separate ROM/SaveRAM paths.  It covers the 779 scenario dialogue records from
63CF2D through 63FDD2, the 72 untranslated ID-effect descriptions, and the
four repeated ID-command help records.  The shared ``覚醒`` dictionary entry
is also replaced with ``각성`` so dynamically composed ID descriptions do not
leave the Japanese term on screen.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3
from expand_dictionary import (
    AUX_TOKEN_BANKS,
    NAME75_RANGES,
    SCRIPT_TOKEN_BANKS,
    _walk_zstring_range,
)
from extract_script import extract_records, split_prefix_body
from hangul_marker import marker_code
from mixed_residual_reference_union import (
    _reference_scopes,
    _walk_zstring_range as walk_reference_range,
    build_free_slot_inventory,
    build_reference_union,
    iter_token_refs_with_offsets,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_exp_dictionary import write_exp_dictionary_slots
from patch_3byte_dict_token import (
    EXP3_SEG0,
    bank_local_for_index,
    list_free_ext3_indices,
    token_from_ext3_index,
)

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
MANUAL_PATH = ROOT / "data/domon_followup_manual_ko.json"
OUT_ROM = ROOT / "out/patch/domon_followup_retranslation_candidate.wsc"
OUT_SAVE = ROOT / "sram/domon_followup_retranslation_candidate.sav"
REPORT = ROOT / "out/patch/domon_followup_retranslation_report.json"

EXPECTED_PARENT_SHA256 = "79083106361d471138392e78ccfd9698781d0f03b8f4b61ad1e61ba2d373d8be"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}
SCENARIO_START = 0x63CF2D
SCENARIO_END = 0x63FDD2
ID_EFFECT_START = 0x5CBD14
ID_EFFECT_END = 0x5D0000
ID_SHARED_AWAKENING_INDEX = 0x03B3
AWAKENING_TEXT = "\uac01\uc131"
AWAKENING_JP = "\u899a\u9192"
NAME_STANDARD_TEXT = "\ub3c4\ubaac \uce87\uc288"
NAME_VARIANTS = (
    "\ub3c4\ubaac\u30fb\uce74\uc288",
    "\ub3c4\ubaac\u30fb\uce90\uc2dc",
    "\ub3c4\ubaac\u30fb\uce74\uc2dc",
    "\ub3c4\ubaac\u3000\uce74\uc288",
    "\ub3c4\ubaac\u3000\uce90\uc2dc",
    "\ub3c4\ubaac\u3000\uce74\uc2dc",
    "\ub3c4\ubaac \uce74\uc288",
    "\ub3c4\ubaac \uce90\uc2dc",
    "\ub3c4\ubaac \uce74\uc2dc",
)
GENERIC_ID_HELP = (0x5F287F, 0x5F2895, 0x5F28AB, 0x5F28C2)
GENERIC_ID_SOURCE = "ＩＤコマンド等の効果を表示します"
GENERIC_ID_TARGET = "ＩＤ 커맨드 등의 효과를 표시합니다"
ID_EFFECT_NEEDLE = "１Ｔの間"


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise BuildError("ROM sizes differ")
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


def japanese_residual(text: str) -> bool:
    return any(
        "\u3040" <= ch <= "\u30ff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\u4e00" <= ch <= "\u9fff"
        for ch in text
    )


def choose_marker(tbl: Tbl) -> tuple[int, str]:
    """Resolve the installed pad3 marker even when the generated map is absent.

    This checkout does not retain ``hangul_char_map.json``.  The active
    pad3 table and current ext3 phrases identify EC8D as the installed marker;
    E3DB is a visible glyph in the same table and is therefore rejected.
    """
    installed = marker_code()
    if tbl.code_to_char.get(installed) == "":
        return installed, "hangul_marker.marker_code"
    if tbl.code_to_char.get(0xEC8D) == "":
        return 0xEC8D, "pad3_tbl_empty_marker_and_current_ROM_evidence"
    raise BuildError(
        f"cannot resolve installed Hangul marker: fallback={installed:04X}"
    )


def encode_phrase(text: str, tbl: Tbl, marker: int) -> bytes:
    normalized = normalize_ko_text(text)
    encoded = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker,
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot encode Korean target: {text!r}")
    return bytes(encoded)


def current_payload(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable current record at {logical:06X}")
    return bytes(got[0]), int(got[1])


def standardize_name(text: str) -> str:
    for old in NAME_VARIANTS:
        text = text.replace(old, NAME_STANDARD_TEXT)
    return text


def standardize_name_render(text: str) -> str:
    canonical = normalize_ko_text(NAME_STANDARD_TEXT)
    for old in NAME_VARIANTS:
        text = text.replace(old, canonical)
        text = text.replace(normalize_ko_text(old), canonical)
    return text


def load_old_targets() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted((ROOT / "data/dialogue_20cell_llm_batches").glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("translation_source") != "llm":
            raise BuildError(f"unexpected source provenance: {path.name}")
        if document.get("review_status") != "approved_for_candidate":
            raise BuildError(f"unapproved batch: {path.name}")
        result.update({str(k).upper(): standardize_name(str(v)) for k, v in (document.get("targets") or {}).items()})
    return result


def scenario_targets(
    original: bytes,
    parent: bytes,
    tbl: Tbl,
    manual: Mapping[str, str],
    old: Mapping[str, str],
) -> list[dict[str, Any]]:
    original_dictionary = Dictionary(original)
    records = [
        row
        for row in extract_records(bytearray(original), tbl, original_dictionary)
        if row.kind == "dialogue" and SCENARIO_START <= row.abs <= SCENARIO_END
    ]
    if len(records) != 779:
        raise BuildError(f"scenario population drifted: {len(records)}")
    result: list[dict[str, Any]] = []
    for row in records:
        address = f"{row.abs:06X}"
        target = manual.get(address) or old.get(address)
        if not target:
            raise BuildError(f"missing scenario translation at {address}")
        target = standardize_name(target)
        if japanese_residual(target):
            raise BuildError(f"Japanese residual in scenario target {address}: {target!r}")
        prefix = bytes.fromhex(row.prefix_hex)
        payload, terminator = current_payload(parent, row.abs)
        if not payload.startswith(prefix):
            raise BuildError(f"scenario prefix drift at {address}")
        if terminator != stock_base(parent) + row.abs + len(payload) or parent[terminator] != 0:
            raise BuildError(f"scenario terminator drift at {address}")
        result.append(
            {
                "group": "scenario",
                "logical": row.abs,
                "abs": address,
                "jp": row.jp,
                "prefix_hex": prefix.hex().upper(),
                "prefix_bytes": len(prefix),
                "body_capacity": len(payload) - len(prefix),
                "target": target,
                "source_type": "manual_direct" if address in manual else "approved_existing_retranslation",
                "payload_hex": payload.hex().upper(),
            }
        )
    return result


ID_EFFECT_TRANSLATIONS = {
    "１Ｔの間、自分の防御と回避が上昇します": "1턴 동안, 자신의 방어와 회피가 상승합니다",
    "１Ｔの間、スタックの戦闘力が上昇します": "1턴 동안, 스택의 전투력이 상승합니다",
    "１Ｔの間、指揮範囲内の回避を上昇させます": "1턴 동안, 지휘 범위 내의 회피를 상승시킵니다",
    "１Ｔの間、指揮範囲内の防御を上昇させます": "1턴 동안, 지휘 범위 내의 방어를 상승시킵니다",
    "１Ｔの間、スタックの移動と反応が上昇": "1턴 동안, 스택의 이동과 반응이 상승",
    "１Ｔの間、指揮範囲内の攻撃力と回避が上昇": "1턴 동안, 지휘 범위 내의 공격력과 회피가 상승",
    "１Ｔの間、相手の攻撃を全て自分で受けます": "1턴 동안, 상대의 공격을 모두 자신이 받습니다",
    "１Ｔの間、指揮範囲内の命中と回避が上昇": "1턴 동안, 지휘 범위 내의 명중과 회피가 상승",
    "１Ｔの間、自分の攻撃力、命中、回避が上昇": "1턴 동안, 자신의 공격력, 명중, 회피가 상승",
    "１Ｔの間、指揮範囲内の回避が上昇します": "1턴 동안, 지휘 범위 내의 회피가 상승합니다",
    "１Ｔの間、スタックの回避が上昇します": "1턴 동안, 스택의 회피가 상승합니다",
    "１Ｔの間、自分の攻撃力と反応が上昇します": "1턴 동안, 자신의 공격력과 반응이 상승합니다",
    "１Ｔの間、指揮範囲内の戦闘力が上昇します": "1턴 동안, 지휘 범위 내의 전투력이 상승합니다",
    "１Ｔの間、自分の戦闘力が上昇します": "1턴 동안, 자신의 전투력이 상승합니다",
    "１Ｔの間、スタックの命中と回避が上昇": "1턴 동안, 스택의 명중과 회피가 상승",
    "１Ｔの間、自分の攻撃力と防御が上昇します": "1턴 동안, 자신의 공격력과 방어가 상승합니다",
    "１Ｔの間、自分の命中と回避が上昇します": "1턴 동안, 자신의 명중과 회피가 상승합니다",
    "１Ｔの間、先制攻撃を発生しやすくします": "1턴 동안, 선제 공격이 발생하기 쉬워집니다",
    "１Ｔの間、指揮範囲内の防御と回避が上昇": "1턴 동안, 지휘 범위 내의 방어와 회피가 상승",
    "１Ｔの間、指揮範囲内の攻撃力と命中が上昇": "1턴 동안, 지휘 범위 내의 공격력과 명중이 상승",
    "１Ｔの間、指揮範囲内の攻撃力が上昇します": "1턴 동안, 지휘 범위 내의 공격력이 상승합니다",
    "１Ｔの間、指揮範囲内命中が上昇します": "1턴 동안, 지휘 범위 내 명중이 상승합니다",
    "１Ｔの間、捕獲成功率が上昇します": "1턴 동안, 포획 성공률이 상승합니다",
    "１Ｔの間、自分の攻撃力、防御、反応が上昇": "1턴 동안, 자신의 공격력, 방어, 반응이 상승",
    "１Ｔの間、自分の命中と防御が上昇します": "1턴 동안, 자신의 명중과 방어가 상승합니다",
    "１Ｔの間、自分の回避が上昇します": "1턴 동안, 자신의 회피가 상승합니다",
    "１Ｔの間、自分の防御と反応が上昇します": "1턴 동안, 자신의 방어와 반응이 상승합니다",
    "１Ｔの間、自分の反応が上昇します": "1턴 동안, 자신의 반응이 상승합니다",
    "１Ｔの間、スタックの防御と回避が上昇": "1턴 동안, 스택의 방어와 회피가 상승",
    "１Ｔの間、指揮範囲内の命中が上昇します": "1턴 동안, 지휘 범위 내의 명중이 상승합니다",
    "１Ｔの間、指揮範囲内の防御が上昇します": "1턴 동안, 지휘 범위 내의 방어가 상승합니다",
}


def id_targets(original: bytes, parent: bytes, tbl: Tbl) -> list[dict[str, Any]]:
    original_dictionary = Dictionary(original)
    result: list[dict[str, Any]] = []
    for logical, payload, _kind in _walk_zstring_range(
        original,
        ID_EFFECT_START,
        ID_EFFECT_END,
        region="id_effect",
        max_len=256,
    ):
        source = original_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        if ID_EFFECT_NEEDLE not in source:
            continue
        target = ID_EFFECT_TRANSLATIONS.get(source)
        if target is None:
            raise BuildError(f"missing ID effect translation for {source!r} at {logical:06X}")
        target = standardize_name(target)
        if japanese_residual(target):
            raise BuildError(f"Japanese residual in ID effect target {logical:06X}")
        prefix, _body, _kind = split_prefix_body(payload)
        current, terminator = current_payload(parent, logical)
        if not current.startswith(prefix):
            raise BuildError(f"ID effect prefix drift at {logical:06X}")
        if terminator != stock_base(parent) + logical + len(current) or parent[terminator] != 0:
            raise BuildError(f"ID effect terminator drift at {logical:06X}")
        result.append(
            {
                "group": "id_effect",
                "logical": logical,
                "abs": f"{logical:06X}",
                "jp": source,
                "prefix_hex": prefix.hex().upper(),
                "prefix_bytes": len(prefix),
                "body_capacity": len(current) - len(prefix),
                "target": target,
                "source_type": "manual_id_effect",
                "payload_hex": current.hex().upper(),
            }
        )
    if len(result) != 72:
        raise BuildError(f"ID effect population drifted: {len(result)}")
    return result


def generic_id_targets(original: bytes, parent: bytes, tbl: Tbl) -> list[dict[str, Any]]:
    original_dictionary = Dictionary(original)
    result: list[dict[str, Any]] = []
    for logical in GENERIC_ID_HELP:
        source_payload, _ = read_encoded_z_safe(original, logical, max_len=256) or (None, None)
        if source_payload is None:
            raise BuildError(f"generic ID help missing at {logical:06X}")
        source = original_dictionary.expand(source_payload, tbl).rstrip("\u3000 \t")
        if source != GENERIC_ID_SOURCE:
            raise BuildError(f"generic ID source drift at {logical:06X}: {source!r}")
        current, terminator = current_payload(parent, logical)
        if terminator != stock_base(parent) + logical + len(current) or parent[terminator] != 0:
            raise BuildError(f"generic ID terminator drift at {logical:06X}")
        result.append(
            {
                "group": "id_help",
                "logical": logical,
                "abs": f"{logical:06X}",
                "jp": source,
                "prefix_hex": "",
                "prefix_bytes": 0,
                "body_capacity": len(current),
                "target": GENERIC_ID_TARGET,
                "source_type": "manual_id_help",
                "payload_hex": current.hex().upper(),
            }
        )
    return result


def ext3_phrase_map(parent: bytes, dictionary: Dictionary) -> dict[bytes, int]:
    result: dict[bytes, int] = {}
    end = 0x1000 + int(EXT3_META["num_banks"]) * 0x1000
    for index in range(0x1000, end):
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            phrase = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        if phrase:
            result.setdefault(phrase, index)
    return result


def exact_stock_slots(parent: bytes, tbl: Tbl, targets: set[str]) -> dict[str, list[int]]:
    dictionary = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    normalized = {normalize_ko_text(text).rstrip("\u3000 \t"): text for text in targets}
    result: dict[str, list[int]] = {text: [] for text in targets}
    for index in range(min(dictionary.count, 0x1000)):
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            rendered = dictionary.expand(dictionary.raw_entry(index), tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        original = normalized.get(rendered)
        if original is not None:
            result[original].append(index)
    return result


def assign_ext3(
    parent: bytes,
    all_rows: list[dict[str, Any]],
    encoded_by_text: Mapping[str, bytes],
    *,
    original: bytes,
    reference_working: bytes,
    extra_phrases: Iterable[str] = (),
) -> tuple[dict[str, int], dict[int, bytes], Any, dict[str, Any]]:
    parent_dictionary = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    existing = ext3_phrase_map(parent, parent_dictionary)
    assignments: dict[str, int] = {}
    new_phrases: list[str] = []
    for row in all_rows:
        if int(row["body_capacity"]) < 4:
            continue
        text = str(row["target"])
        if text in assignments:
            continue
        encoded = encoded_by_text[text]
        if encoded in existing:
            assignments[text] = existing[encoded]
        else:
            new_phrases.append(text)
    for text in extra_phrases:
        if text in assignments:
            continue
        encoded = encoded_by_text[text]
        if encoded in existing:
            assignments[text] = existing[encoded]
        else:
            new_phrases.append(text)

    union = build_reference_union(original, reference_working, ext_meta=EXT_META, ext3_meta=EXT3_META)
    inventory = build_free_slot_inventory(
        parent,
        union=union,
        ext_meta=EXT_META,
        ext3_meta=EXT3_META,
    )
    free_by_segment: dict[int, list[int]] = defaultdict(list)
    for index in inventory.ext3_free:
        segment, local = bank_local_for_index(index)
        page = segment - EXP3_SEG0
        # Page aliases 0..4 use the promoted 0x21..0x25 banks for locals >=600.
        # Allocate new phrases only in unaliased physical pages.
        if page < 5 and local >= 0x600:
            continue
        free_by_segment[segment].append(index)
    for values in free_by_segment.values():
        values.sort()

    room = {
        segment: max(0, int(inventory.ext3_bank_room.get(segment - EXP3_SEG0, 0)))
        for segment in free_by_segment
    }
    used_bytes = {segment: 0 for segment in free_by_segment}
    slot_payload: dict[int, bytes] = {}
    for text in new_phrases:
        encoded = encoded_by_text[text]
        chosen: tuple[int, int] | None = None
        for segment in sorted(free_by_segment):
            if free_by_segment[segment] and used_bytes[segment] + len(encoded) + 1 <= room[segment]:
                chosen = (segment, free_by_segment[segment].pop(0))
                break
        if chosen is None:
            raise BuildError(
                f"not enough true-free ext3 room for {text!r}; remaining={len(new_phrases)}"
            )
        segment, index = chosen
        assignments[text] = index
        slot_payload[index] = encoded
        used_bytes[segment] += len(encoded) + 1

    candidate = bytearray(parent)
    if slot_payload:
        ext3_write, guard = write_ext3_slots_guarded(
            candidate,
            slot_payload,
            union=union,
            num_banks=int(EXT3_META["num_banks"]),
            justification="new true-free ext3 phrases for Domon/ID direct retranslation",
        )
    else:
        ext3_write = {"written": 0, "by_bank": {}}
        guard = None
    if int(ext3_write.get("written", 0)) != len(slot_payload):
        raise BuildError(f"ext3 allocation write count drifted: {ext3_write}")
    return assignments, slot_payload, union, {
        "inventory": inventory.as_dict(),
        "existing_exact_phrases": len(assignments) - len(slot_payload),
        "new_phrases": len(slot_payload),
        "slot_payload": {f"{i:05X}": blob.hex().upper() for i, blob in sorted(slot_payload.items())},
        "write": ext3_write,
        "guard": guard.as_dict() if guard is not None else None,
    }, candidate


def ext3_cursors(rom: bytes, segments: Iterable[int]) -> dict[int, int]:
    cursors: dict[int, int] = {}
    for segment in segments:
        bank = rom[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        cursor = 0x2001
        for local in range(0x1000):
            pointer = int.from_bytes(bank[local * 2 : local * 2 + 2], "little")
            if pointer < 0x2000 or pointer >= BANK_SIZE:
                continue
            end = pointer
            while end < BANK_SIZE and bank[end] != 0:
                end += 1
            cursor = max(cursor, end + 1)
        cursors[segment] = cursor
    return cursors


def ext_dictionary_cursor(rom: bytes) -> int:
    bank = rom[0x10 * BANK_SIZE : 0x11 * BANK_SIZE]
    cursor = 265 * 2
    for local in range(265):
        pointer = int.from_bytes(bank[local * 2 : local * 2 + 2], "little")
        if pointer < cursor or pointer >= BANK_SIZE:
            continue
        end = pointer
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        cursor = max(cursor, end + 1)
    return cursor


def assign_ext_dictionary(
    parent: bytes,
    candidate: bytearray,
    rows: list[dict[str, Any]],
    encoded_by_text: Mapping[str, bytes],
    *,
    tbl: Tbl,
    union: Any,
) -> tuple[
    dict[str, int],
    dict[str, list[int]],
    list[str],
    int,
    dict[int, bytes],
    dict[str, Any],
    dict[int, int],
]:
    short_phrases = {str(row["target"]) for row in rows if int(row["body_capacity"]) < 4}
    exact = exact_stock_slots(parent, tbl, short_phrases | {AWAKENING_TEXT})
    stock_assignment = {
        phrase: min(slots) for phrase, slots in exact.items() if slots and phrase != AWAKENING_TEXT
    }
    awakening_hits = exact.get(AWAKENING_TEXT) or []
    new_short = sorted(short_phrases - set(stock_assignment))
    requested_new = list(new_short)
    if not awakening_hits:
        requested_new.append(AWAKENING_TEXT)

    inventory = build_free_slot_inventory(
        parent,
        union=union,
        ext_meta=EXT_META,
        ext3_meta=EXT3_META,
    )
    ext_payload: dict[int, bytes] = {}
    assignment = dict(stock_assignment)
    slot_reassignments: dict[int, int] = {}

    # The expanded two-byte page is full by design: every slot is still
    # referenced somewhere in the Original+Working union. A duplicate phrase
    # is nevertheless reclaimable when all current tokens for the candidate
    # slot are redirected to its identical keeper. This preserves every
    # existing render while creating a two-byte destination for a short phrase.
    dictionary = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    duplicate_groups: dict[bytes, list[int]] = defaultdict(list)
    ext_lo = int(EXT_META["stock_count"])
    ext_hi = ext_lo + int(EXT_META["slot_count"])
    for index in range(ext_lo, ext_hi):
        try:
            raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        if raw:
            duplicate_groups[raw].append(index)
    duplicate_candidates: list[tuple[int, int, int]] = []
    for indices in duplicate_groups.values():
        if len(indices) < 2:
            continue
        ordered = sorted(
            indices,
            key=lambda index: (len(union.consumers_for(index)), index),
        )
        victim, keeper = ordered[0], ordered[1]
        if not dict_token_safe_in_zstring(victim) or not dict_token_safe_in_zstring(keeper):
            continue
        if union.parents_of(victim) or union.parents_of(keeper):
            continue
        duplicate_candidates.append((len(union.consumers_for(victim)), victim, keeper))
    duplicate_candidates.sort()
    if len(duplicate_candidates) < len(requested_new):
        raise BuildError(
            "not enough duplicate extended dictionary slots to reclaim: "
            f"need={len(requested_new)} have={len(duplicate_candidates)}"
        )
    for phrase, (_consumer_count, victim, keeper) in zip(requested_new, duplicate_candidates):
        assignment[phrase] = victim
        ext_payload[victim] = encoded_by_text[phrase]
        slot_reassignments[victim] = keeper
    if awakening_hits:
        awakening_index = min(awakening_hits)
    else:
        awakening_index = assignment[AWAKENING_TEXT]
    cursor_before = ext_dictionary_cursor(bytes(candidate))
    if ext_payload:
        write_info = write_exp_dictionary_slots(
            candidate,
            ext_payload,
            ext_ptr_off=0,
            stock_count=int(EXT_META["stock_count"]),
            slot_count=int(EXT_META["slot_count"]),
            allow_aux_consumers=True,
        )
    else:
        write_info = {"written": 0, "phrase_end": cursor_before}
    if int(write_info.get("written", 0)) != len(ext_payload):
        raise BuildError(f"extended dictionary write count drifted: {write_info}")
    cursor_after = ext_dictionary_cursor(bytes(candidate))
    return assignment, exact, new_short, awakening_index, ext_payload, {
        "inventory": inventory.as_dict(),
        "new_slots": {f"{i:04X}": blob.hex().upper() for i, blob in sorted(ext_payload.items())},
        "reclaimed_slots": {
            f"{victim:04X}": f"{keeper:04X}"
            for victim, keeper in sorted(slot_reassignments.items())
        },
        "write": write_info,
        "cursor_before": f"{cursor_before:04X}",
        "cursor_after": f"{cursor_after:04X}",
        "awakening_index": f"{awakening_index:04X}",
    }, slot_reassignments


def active_reference_records(rom: bytes) -> Iterable[tuple[int, bytes, str]]:
    for segment in SCRIPT_TOKEN_BANKS:
        yield from _walk_zstring_range(
            rom,
            segment * BANK_SIZE,
            (segment + 1) * BANK_SIZE,
            region="script",
            max_len=256,
        )
    for lo, hi in NAME75_RANGES:
        yield from _walk_zstring_range(rom, lo, hi, region="name75", max_len=64)
    for segment in AUX_TOKEN_BANKS:
        yield from _walk_zstring_range(
            rom,
            segment * BANK_SIZE,
            (segment + 1) * BANK_SIZE,
            region="aux",
            max_len=128,
        )


COMPOSED_NAME_SOURCE = bytes.fromhex("F5892AFA53")


def name_payload_replacements(
    tbl: Tbl,
    marker: int,
    name_ext3_token: bytes | None = None,
) -> dict[bytes, bytes]:
    canonical = encode_phrase(NAME_STANDARD_TEXT, tbl, marker)
    replacements = {
        encode_phrase(variant, tbl, marker): canonical
        for variant in NAME_VARIANTS
    }
    if name_ext3_token is not None:
        # The source is a five-byte composed name (two stock tokens plus the
        # middle-dot glyph). End the shorter ext3 replacement explicitly so it
        # does not render a visible padding space.
        replacements[COMPOSED_NAME_SOURCE] = name_ext3_token + b"\x00"
    return replacements


def replace_name_sequences(
    payload: bytes,
    replacements: Mapping[bytes, bytes],
) -> bytes:
    output = payload
    for source, target in replacements.items():
        output = output.replace(source, target)
    if len(output) != len(payload):
        raise BuildError(
            f"name standardization changed payload length: "
            f"{len(payload)} -> {len(output)}"
        )
    return output


def standardize_active_names(
    parent: bytes,
    candidate: bytearray,
    *,
    tbl: Tbl,
    marker: int,
    name_ext3_token: bytes,
) -> dict[str, Any]:
    replacements = name_payload_replacements(tbl, marker, name_ext3_token)
    base = stock_base(parent)
    external_sites: list[int] = []
    external_extents: list[tuple[int, int]] = []
    for logical, payload, _kind in active_reference_records(parent):
        start = base + logical
        current = bytes(candidate[start : start + len(payload)])
        if len(current) != len(payload):
            raise BuildError(f"name standardization read drift at {logical:06X}")
        replacement = replace_name_sequences(current, replacements)
        if replacement == current:
            continue
        candidate[start : start + len(payload)] = replacement
        external_sites.append(logical)
        external_extents.append((start, start + len(payload)))

    dictionary = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    dictionary_extents: list[tuple[int, int]] = []
    dictionary_sites: list[int] = []
    seen: set[tuple[int, int]] = set()
    indices = list(range(dictionary.count)) + list(
        range(0x1000, 0x1000 + int(EXT3_META["num_banks"]) * 0x1000)
    )
    for index in indices:
        try:
            raw = bytes(dictionary.raw_entry(index))
            location = dictionary.entry_abs(index)
        except Exception:
            continue
        key = (location, len(raw))
        if not raw or key in seen:
            continue
        current = bytes(candidate[location : location + len(raw)])
        if len(current) != len(raw):
            continue
        replacement = replace_name_sequences(current, replacements)
        if replacement == current:
            continue
        seen.add(key)
        candidate[location : location + len(raw)] = replacement
        dictionary_extents.append((location, location + len(raw)))
        dictionary_sites.append(index)

    return {
        "canonical": NAME_STANDARD_TEXT,
        "variants": list(NAME_VARIANTS),
        "external_sites": [f"{logical:06X}" for logical in sorted(set(external_sites))],
        "external_site_count": len(set(external_sites)),
        "dictionary_sites": [f"{index:05X}" for index in sorted(set(dictionary_sites))],
        "dictionary_site_count": len(set(dictionary_sites)),
        "external_extents": external_extents,
        "dictionary_extents": dictionary_extents,
        "composed_name_source": COMPOSED_NAME_SOURCE.hex().upper(),
        "name_ext3_token": name_ext3_token.hex().upper(),
    }


def replace_runtime_two_byte_tokens(
    payload: bytes,
    replacements: Mapping[int, bytes],
) -> bytes:
    """Replace only parser-recognized 2-byte dictionary tokens.

    Raw ``bytes.replace`` is unsafe here: sequences such as ``FF FF 09`` have
    overlapping byte pairs, but the runtime consumes them as one ``FF FF``
    token followed by the next stream byte. The reference walker already knows
    the runtime precedence, so use its offsets for every rewrite.
    """
    output = bytearray(payload)
    for index, length, offset in iter_token_refs_with_offsets(
        payload,
        ext3_aware=True,
    ):
        target = replacements.get(index)
        if target is None:
            continue
        if length != 2:
            raise BuildError(f"cannot replace non-2-byte token at {offset}: {index:04X}")
        output[offset : offset + 2] = target
    return bytes(output)


def remap_shared_awaken_token(
    parent: bytes,
    candidate: bytearray,
    *,
    new_index: int,
    additional_reassignments: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    old_token = token_from_dict_index(ID_SHARED_AWAKENING_INDEX)
    new_token = token_from_dict_index(new_index)
    reassignments = dict(additional_reassignments or {})
    # Reclaimed duplicate slots must be redirected first. The shared Japanese
    # awakening token is redirected last because its new destination can itself
    # be one of the reclaimed victim slots.
    token_pairs = [
        (
            token_from_dict_index(old_index),
            token_from_dict_index(keeper),
            old_index,
            keeper,
        )
        for old_index, keeper in sorted(reassignments.items())
    ]
    token_pairs.append((old_token, new_token, ID_SHARED_AWAKENING_INDEX, new_index))
    token_replacements = {
        old_index: target_token
        for source_token, target_token, old_index, _target_index in token_pairs
    }
    base = stock_base(parent)
    external_sites: list[int] = []
    external_extents: list[tuple[int, int]] = []
    for logical, payload, _kind in active_reference_records(parent):
        replacement = replace_runtime_two_byte_tokens(payload, token_replacements)
        if replacement == payload:
            continue
        start = base + logical
        if len(replacement) != len(payload):
            raise BuildError(
                f"external remap length drift at {logical:06X}: "
                f"{len(payload)} -> {len(replacement)}"
            )
        candidate[start : start + len(payload)] = replacement
        if len(candidate) != len(parent):
            raise BuildError(
                f"external remap ROM size drift at {logical:06X}: "
                f"{len(candidate):#x} start={start:#x} span={len(payload)}"
            )
        external_sites.append(logical)
        external_extents.append((start, start + len(payload)))

    dictionary = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    dictionary_extents: list[tuple[int, int]] = []
    dictionary_sites: list[int] = []
    seen: set[tuple[int, int]] = set()
    indices = list(range(dictionary.count)) + list(
        range(0x1000, 0x1000 + int(EXT3_META["num_banks"]) * 0x1000)
    )
    for index in indices:
        try:
            raw = bytes(dictionary.raw_entry(index))
            location = dictionary.entry_abs(index)
        except Exception:
            continue
        replacement = replace_runtime_two_byte_tokens(raw, token_replacements)
        if replacement == raw or (location, len(raw)) in seen:
            continue
        if parent[location : location + len(raw)] != raw:
            continue
        seen.add((location, len(raw)))
        if len(replacement) != len(raw):
            raise BuildError(
                f"dictionary remap length drift at {index:05X}: "
                f"{len(raw)} -> {len(replacement)}"
            )
        candidate[location : location + len(raw)] = replacement
        if len(candidate) != len(parent):
            raise BuildError(
                f"dictionary remap ROM size drift at {index:05X}: "
                f"{len(candidate):#x} location={location:#x} span={len(raw)}"
            )
        dictionary_extents.append((location, location + len(raw)))
        dictionary_sites.append(index)
    return {
        "old_token": old_token.hex().upper(),
        "new_token": new_token.hex().upper(),
        "new_index": f"{new_index:04X}",
        "external_sites": [f"{logical:06X}" for logical in sorted(set(external_sites))],
        "external_site_count": len(set(external_sites)),
        "dictionary_sites": [f"{index:04X}" for index in sorted(set(dictionary_sites))],
        "dictionary_site_count": len(set(dictionary_sites)),
        "external_extents": external_extents,
        "dictionary_extents": dictionary_extents,
        "reassignments": [
            {
                "old_index": f"{old_index:04X}",
                "new_index": f"{new_index_value:04X}",
                "old_token": source_token.hex().upper(),
                "new_token": target_token.hex().upper(),
            }
            for source_token, target_token, old_index, new_index_value in token_pairs
        ],
    }


def neutralized_reference_working(parent: bytes, rows: list[dict[str, Any]]) -> bytes:
    """Remove only the soon-to-be-replaced record bodies before free-slot scan."""
    candidate = bytearray(parent)
    base = stock_base(parent)
    for row in rows:
        start = base + int(row["logical"]) + int(row["prefix_bytes"])
        capacity = int(row["body_capacity"])
        candidate[start : start + capacity] = b"\x01" * capacity
    return bytes(candidate)


def build_candidate() -> tuple[bytes, dict[str, Any]]:
    parent = bytes(load_rom(PARENT))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("current main TIP identity drifted")
    if not PARENT_SAVE.is_file() or PARENT_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("current main SaveRAM missing")
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    marker, marker_source = choose_marker(tbl)
    manual_document = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
    manual = {str(k).upper(): standardize_name(str(v)) for k, v in (manual_document.get("targets") or {}).items()}
    old = load_old_targets()
    scenario = scenario_targets(original, parent, tbl, manual, old)
    if len(set(manual) & {row["abs"] for row in scenario}) != 250:
        raise BuildError("manual scenario population is not exactly 250 rows")
    effects = id_targets(original, parent, tbl)
    generic = generic_id_targets(original, parent, tbl)
    rows = scenario + effects + generic
    if len(rows) != 855 or len({int(row["logical"]) for row in rows}) != len(rows):
        raise BuildError("combined target population drifted")

    encoded_by_text: dict[str, bytes] = {}
    for row in rows:
        text = str(row["target"])
        encoded_by_text.setdefault(text, encode_phrase(text, tbl, marker))
    encoded_by_text[AWAKENING_TEXT] = encode_phrase(AWAKENING_TEXT, tbl, marker)
    encoded_by_text[NAME_STANDARD_TEXT] = encode_phrase(NAME_STANDARD_TEXT, tbl, marker)
    reference_working = neutralized_reference_working(parent, rows)

    assignments, ext3_payload, union, ext3_report, candidate = assign_ext3(
        parent,
        rows,
        encoded_by_text,
        original=original,
        reference_working=reference_working,
        extra_phrases=(NAME_STANDARD_TEXT,),
    )
    if len(candidate) != ROM_SIZE:
        raise BuildError(f"candidate size drift after ext3 allocation: {len(candidate):#x}")

    (
        stock_assignment,
        exact,
        new_short,
        awakening_index,
        ext_dict_payload,
        ext_dict_report,
        slot_reassignments,
    ) = assign_ext_dictionary(
        parent,
        candidate,
        rows,
        encoded_by_text,
        tbl=tbl,
        union=union,
    )
    if len(candidate) != ROM_SIZE:
        raise BuildError(f"candidate size drift after extended dictionary allocation: {len(candidate):#x}")
    remap_report = remap_shared_awaken_token(
        parent,
        candidate,
        new_index=awakening_index,
        additional_reassignments=slot_reassignments,
    )
    if len(candidate) != ROM_SIZE:
        raise BuildError(f"candidate size drift after token remap: {len(candidate):#x}")
    name_report = standardize_active_names(
        parent,
        candidate,
        tbl=tbl,
        marker=marker,
        name_ext3_token=token_from_ext3_index(
            assignments[NAME_STANDARD_TEXT],
            num_banks=int(EXT3_META["num_banks"]),
        ),
    )
    if len(candidate) != ROM_SIZE:
        raise BuildError(f"candidate size drift after name standardization: {len(candidate):#x}")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        prefix_len = int(row["prefix_bytes"])
        capacity = int(row["body_capacity"])
        phrase = str(row["target"])
        if capacity >= 4:
            index = assignments[phrase]
            token = token_from_ext3_index(index, num_banks=int(EXT3_META["num_banks"]))
            strategy = "existing_ext3_exact" if index not in ext3_payload or ext3_payload.get(index) != encoded_by_text[phrase] else "new_ext3_true_free"
            allocation = {"ext3_index": f"{index:05X}"}
        else:
            index = stock_assignment[phrase]
            token = token_from_dict_index(index)
            strategy = "existing_stock_exact" if phrase in exact and exact[phrase] else "retired_stock_true_free"
            allocation = {"stock_index": f"{index:04X}"}
        replacement = token + b"\x01" * (capacity - len(token))
        if len(replacement) != capacity:
            raise BuildError(f"replacement length drift at {row['abs']}")
        file_start = base + logical + prefix_len
        candidate[file_start : file_start + capacity] = replacement
        if len(candidate) != ROM_SIZE:
            raise BuildError(f"candidate size drift after target {row['abs']}: {len(candidate):#x}")
        target_extents.append((file_start, file_start + capacity))
        applied.append(
            {
                "group": row["group"],
                "abs": row["abs"],
                "jp": row["jp"],
                "ko": phrase,
                "body_capacity": capacity,
                "source_type": row["source_type"],
                "strategy": strategy,
                "token_hex": token.hex().upper(),
                **allocation,
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    before_dictionary = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    after_dictionary = make_dictionary_ext3(candidate_bytes, EXT_META, EXT3_META)
    target_failures: list[dict[str, Any]] = []
    excluded = {int(row["logical"]) for row in rows}
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = current_payload(candidate_bytes, logical)
        prefix_len = int(row["prefix_bytes"])
        expected = normalize_ko_text(str(row["target"])).rstrip("\u3000 \t")
        actual = after_dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload != bytes.fromhex(str(row["payload_hex"])):
            # The target body is intentionally changed, so only compare shape.
            if len(payload) != int(row["prefix_bytes"]) + int(row["body_capacity"]):
                reasons.append("payload_length_changed")
        if actual != expected:
            reasons.append("render_mismatch")
        if japanese_residual(actual):
            reasons.append("japanese_residual")
        if terminator != base + logical + len(payload) or candidate_bytes[terminator] != 0:
            reasons.append("terminator_changed")
        if reasons:
            target_failures.append(
                {"abs": row["abs"], "expected": expected, "actual": actual, "reasons": reasons}
            )

    remapped_logicals = {
        int(value, 16) for value in remap_report.get("external_sites", [])
    }
    shared_changes: list[dict[str, Any]] = []
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, _kind in walk_reference_range(
            parent,
            lo,
            hi,
            region=region,
            max_len=max_len,
        ):
            if logical in excluded:
                continue
            got = read_encoded_z_safe(candidate_bytes, base + logical, max_len=max_len)
            if got is None:
                shared_changes.append({"abs": f"{logical:06X}", "reason": "non_target_payload_changed"})
                continue
            current_payload_bytes = bytes(got[0])
            if current_payload_bytes != payload:
                expected_payload = replace_runtime_two_byte_tokens(
                    payload,
                    {
                        int(str(remap["old_index"]), 16): bytes.fromhex(
                            str(remap["new_token"])
                        )
                        for remap in remap_report.get("reassignments", [])
                    },
                )
                expected_payload = replace_name_sequences(
                    expected_payload,
                    name_payload_replacements(
                        tbl,
                        marker,
                        bytes.fromhex(str(name_report["name_ext3_token"])),
                    ),
                )
                allowed_payload_sites = remapped_logicals | {
                    int(value, 16)
                    for value in name_report.get("external_sites", [])
                }
                expected_payload_without_terminator = expected_payload.rstrip(b"\x00")
                if logical not in allowed_payload_sites or (
                    current_payload_bytes != expected_payload
                    and current_payload_bytes != expected_payload_without_terminator
                ):
                    shared_changes.append(
                        {
                            "abs": f"{logical:06X}",
                            "reason": "non_target_payload_changed",
                            "before_payload": payload.hex().upper(),
                            "after_payload": current_payload_bytes.hex().upper(),
                            "expected_payload": expected_payload.hex().upper(),
                            "remapped": logical in remapped_logicals,
                        }
                    )
                    continue
            before_text = before_dictionary.expand(payload, tbl)
            after_text = after_dictionary.expand(current_payload_bytes, tbl)
            if before_text == after_text:
                continue
            expected_shared = standardize_name_render(before_text).replace(
                AWAKENING_JP,
                AWAKENING_TEXT,
            )
            if after_text != expected_shared:
                shared_changes.append(
                    {
                        "abs": f"{logical:06X}",
                        "reason": "unexpected_shared_dictionary_render_change",
                        "before": before_text,
                        "after": after_text,
                        "payload": payload.hex().upper(),
                    }
                )

    changed_segments = sorted(
        {
            (offset // BANK_SIZE)
            for offset, value in enumerate(zip(parent, candidate_bytes))
            if value[0] != value[1] and offset < 0x800000
        }
    )
    new_segments = sorted({bank_local_for_index(index)[0] for index in ext3_payload})
    ext3_before = ext3_cursors(parent, new_segments)
    ext3_after = ext3_cursors(candidate_bytes, new_segments)
    allowed: list[tuple[int, int]] = list(target_extents)
    allowed.extend(tuple(extent) for extent in remap_report.get("external_extents", []))
    allowed.extend(tuple(extent) for extent in remap_report.get("dictionary_extents", []))
    allowed.extend(tuple(extent) for extent in name_report.get("external_extents", []))
    allowed.extend(tuple(extent) for extent in name_report.get("dictionary_extents", []))
    ext_bank_file = 0x10 * BANK_SIZE
    allowed.extend(
        (ext_bank_file + (index - int(EXT_META["stock_count"])) * 2,
         ext_bank_file + (index - int(EXT_META["stock_count"])) * 2 + 2)
        for index in ext_dict_payload
    )
    ext_cursor_before = int(ext_dict_report["cursor_before"], 16)
    ext_cursor_after = int(ext_dict_report["cursor_after"], 16)
    if ext_cursor_after > ext_cursor_before:
        allowed.append((ext_bank_file + ext_cursor_before, ext_bank_file + ext_cursor_after))
    for segment in new_segments:
        for index in ext3_payload:
            seg, local = bank_local_for_index(index)
            if seg == segment:
                allowed.append((segment * BANK_SIZE + local * 2, segment * BANK_SIZE + local * 2 + 2))
        if ext3_after[segment] > ext3_before[segment]:
            allowed.append((segment * BANK_SIZE + ext3_before[segment], segment * BANK_SIZE + ext3_after[segment]))
    allowed.append((len(parent) - 2, len(parent)))
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]
    runtime_lo = base + 0x7A0600
    runtime_hi = base + 0x7A1000
    runtime_unchanged = parent[runtime_lo:runtime_hi] == candidate_bytes[runtime_lo:runtime_hi]
    ok = not target_failures and not shared_changes and not unaccounted and runtime_unchanged
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_domon_followup_retranslation_candidate.py",
        "status": "candidate_static_verified" if ok else "failed",
        "ok": ok,
        "published": False,
        "promotion": "blocked_pending_runtime_visual_validation",
        "parent": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE) if OUT_SAVE.is_file() else None,
        "marker": {"code": f"{marker:04X}", "source": marker_source},
        "scope": {
            "scenario_start": f"{SCENARIO_START:06X}",
            "scenario_end": f"{SCENARIO_END:06X}",
            "scenario_records": len(scenario),
            "manual_direct_records": sum(1 for row in scenario if row["source_type"] == "manual_direct"),
            "approved_existing_retranslation_records": sum(1 for row in scenario if row["source_type"] != "manual_direct"),
            "id_effect_records": len(effects),
            "id_help_records": len(generic),
            "all_records": len(rows),
            "shared_awaking_slot": f"{ID_SHARED_AWAKENING_INDEX:04X}",
        },
        "allocation": {
            "ext3": ext3_report,
            "extended_dictionary": ext_dict_report,
            "short_exact": {phrase: [f"{i:04X}" for i in slots] for phrase, slots in exact.items() if slots},
            "short_new": {phrase: f"{stock_assignment[phrase]:04X}" for phrase in new_short},
            "shared_awaken_remap": remap_report,
            "name_standardization": name_report,
            "ext3_new_segments": [f"{s:02X}" for s in new_segments],
        },
        "verification": {
            "target_render_exact": not target_failures,
            "target_japanese_residuals_zero": not target_failures,
            "shared_dictionary_and_name_changes_only": not shared_changes,
            "name_standardization_applied": bool(
                name_report.get("external_site_count")
                or name_report.get("dictionary_site_count")
            ),
            "diffs_within_approved_extents": not unaccounted,
            "runtime_hook_unchanged": runtime_unchanged,
            "record_lengths_preserved": True,
            "prefixes_preserved": True,
            "terminators_preserved": True,
        },
        "target_failures": target_failures,
        "unexpected_shared_changes": shared_changes[:100],
        "unaccounted_diff_runs": unaccounted,
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
            "changed_segments": [f"{segment:02X}" for segment in changed_segments],
        },
        "records": applied,
    }
    if not ok:
        raise BuildError(
            json.dumps(
                {
                    "target_failures": target_failures[:5],
                    "shared_changes": shared_changes[:5],
                    "unaccounted": unaccounted[:5],
                    "runtime_unchanged": runtime_unchanged,
                    "reassignments": remap_report.get("reassignments", []),
                },
                ensure_ascii=False,
            )
        )
    return candidate_bytes, report


def main() -> int:
    candidate, report = build_candidate()
    atomic_bytes(OUT_ROM, candidate)
    shutil.copy2(PARENT_SAVE, OUT_SAVE)
    report["candidate"] = identity(OUT_ROM, candidate)
    report["candidate_save"] = identity(OUT_SAVE)
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "candidate": identity(OUT_ROM, candidate), "save": identity(OUT_SAVE), "report": identity(REPORT), "main_tip_modified": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
