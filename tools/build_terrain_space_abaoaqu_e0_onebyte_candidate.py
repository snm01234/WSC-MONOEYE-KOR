#!/usr/bin/env python3
"""Repair terrain-info 우주 / 아 바오아 쿠 using the HUD's proven code widths.

The compact-glyph candidate wrote unused E5xx kanji (E511…) into retired F0
slots.  Binary recheck shows those bytes landed on the right records, but the
user still did not see 우주 / 아 바오아쿠.  Original 宇宙 expanded to E078 E046
(E0 lead).  Original ア・バオア・クー is a 1-byte kana walker after F0 expand.
Runtime special-cases E5 as the ext3/compact3 portal lead, so E5xx payloads are
the likely miss.  This candidate:

* steals two main-unused E0xx compact glyphs (never E5xx / E519) for 우/주;
* puts those two codes in one retired stock slot (no EC8D marker) and retargets
  only 75B3CE / 75E59A;
* steals four HUD+dict-unused 1-byte compact codes (same path as 공/분) and
  writes the 7-byte stream 아 01 바 오 아 01 쿠 at 75E58C / 75BD77.

Slot 008F (색적우주), 75B3CA 범용, 75B457 명중 보정, the main TIP, and live
SaveRAM are not modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import (  # noqa: E402
    external_occurrence_map,
    nested_occurrence_map,
)
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from expand_dictionary import NAME75_RANGES, _walk_zstring_range, guard_hangul_slot_writes  # noqa: E402
from hangul_allocator import HANGUL_PRIMARY_START  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    NAME75_UI_TABLE_RANGES,
    _reference_scopes,
    build_reference_union,
    guard_slot_writes,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    COMPACT_FONT_RECORD_SIZE,
    COMPACT_FONT_SEGMENT,
    COMPACT_FONT_TABLE,
    SEG_DICT,
    Dictionary,
    Tbl,
    decode_compact_font_record,
    dict_index_from_token,
    dict_token_safe_in_zstring,
    is_compact3_magic,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    load_rom,
    logical_bank_offset,
    patch_bank,
    read_encoded_z_safe,
    slice_bank,
    stock_base,
    text_code_to_glyph_index,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_font_hangul_hook import PAD1_FILE, PAD1_SLOTS, PAD2_FILE  # noqa: E402
from patch_pad3_expansion import PAD12_SLOTS, pad3_file_offset  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/terrain_space_abaoaqu_e0_onebyte_candidate.wsc"
OUT_SAVE = ROOT / "sram/terrain_space_abaoaqu_e0_onebyte_candidate.sav"
OUT_REPORT = ROOT / "out/patch/terrain_space_abaoaqu_e0_onebyte_report.json"

EXPECTED_MAIN_SHA256 = (
    "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EMPTY_STOCK = 0x00A9

HANGUL_SOURCE = {
    "우": 0xE761,
    "주": 0xE764,
    "아": 0xE79E,
    "바": 0xE7C3,
    "오": 0xE754,
    "쿠": 0xE7F1,
}
SPACE_TEXT = "우주"
ABAOA_TEXT = "아　바오아　쿠"
SPACE_SITES = (0x75B3CE, 0x75E59A)
ABAOA_SITES = (0x75E58C, 0x75BD77)
SPACE_BEFORE = bytes.fromhex("F08F")
ABAOA_BEFORE = {
    0x75E58C: bytes.fromhex("E518EC41010101"),
    0x75BD77: bytes.fromhex("E518B445F0A901"),
}
TAKEN_ONEBYTE = {0x00, 0x01, 0x18, 0x19, 0x1F, 0x26, 0x2A, 0xC6, 0xDF}
UNSAFE_TRAILS = {0x00, 0x18, 0x19}
UNSAFE_KANJI = {0xE078, 0xE518, 0xE519}
KANA_CHARS = set(
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとど"
    "なにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをん"
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトド"
    "ナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヲンヴ"
)
HUD_RANGES = list(NAME75_RANGES) + list(NAME75_UI_TABLE_RANGES)
GUARD_JUSTIFICATION = (
    "terrain HUD 우주 dedicated E0xx stock token; no Hangul marker; "
    "name75 keepers 75B3CE/75E59A after retarget; slot is retired "
    "(current working external/nested 0; historical original consumers already migrated)"
)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def compact_glyph_offset(rom: bytes | bytearray, code: int) -> int:
    index = text_code_to_glyph_index(code)
    return (
        stock_base(rom)
        + logical_bank_offset(COMPACT_FONT_SEGMENT, COMPACT_FONT_TABLE)
        + index * COMPACT_FONT_RECORD_SIZE
    )


def u16(code: int) -> bytes:
    return bytes([(code >> 8) & 0xFF, code & 0xFF])


def historical_abs(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("record_abs", value.get("abs"))
    if isinstance(value, str):
        return int(value, 16)
    return int(value)


def hangul_pad_slot(code: int) -> int:
    slot = code - HANGUL_PRIMARY_START
    if slot < 0:
        raise BuildError(f"not a primary Hangul code: {code:04X}")
    return slot


def hangul_glyph_offset(rom: bytes | bytearray, code: int) -> int:
    slot = hangul_pad_slot(code)
    base = stock_base(rom)
    if slot < PAD1_SLOTS:
        return base + PAD1_FILE + slot * 16
    if slot < PAD12_SLOTS:
        return base + PAD2_FILE + (slot - PAD1_SLOTS) * 16
    return pad3_file_offset(rom, slot)


def read_glyph(rom: bytes | bytearray, offset: int) -> bytes:
    record = bytes(rom[offset : offset + 16])
    if len(record) != 16:
        raise BuildError(f"truncated glyph at {offset:06X}")
    ink = sum(sum(row) for row in decode_compact_font_record(record))
    if ink <= 0 or record == b"\xFF" * 16:
        raise BuildError(f"empty glyph at {offset:06X}")
    return record


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=64)
    if got is None:
        raise BuildError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - stock_base(rom)


def walk_units(payload: bytes, *, ext3_aware: bool) -> Iterable[tuple[str, int]]:
    cursor = 0
    size = len(payload)
    while cursor < size:
        lead = payload[cursor]
        if (
            ext3_aware
            and cursor + 2 < size
            and is_compact3_magic(lead, payload[cursor + 1])
        ):
            cursor += 3
            continue
        if (
            ext3_aware
            and cursor + 3 < size
            and is_ext3_magic(lead, payload[cursor + 1])
        ):
            cursor += 4
            continue
        if is_dict_token(lead) and cursor + 1 < size:
            yield ("dict", (lead << 8) | payload[cursor + 1])
            cursor += 2
            continue
        if is_kanji_lead(lead) and cursor + 1 < size:
            yield ("kanji", (lead << 8) | payload[cursor + 1])
            cursor += 2
            continue
        yield ("one", lead)
        cursor += 1


def parent_kanji_usage(rom: bytes, dictionary: Dictionary) -> Counter[int]:
    counts: Counter[int] = Counter()
    for region, lo, hi, max_len in _reference_scopes():
        for _logical, payload, _kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            for kind, value in walk_units(payload, ext3_aware=True):
                if kind == "kanji":
                    counts[value] += 1
    for index in range(dictionary.count):
        try:
            raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        for kind, value in walk_units(raw, ext3_aware=True):
            if kind == "kanji":
                counts[value] += 1
    return counts


def glyph_usable(rom: bytes, code: int) -> tuple[bool, int, int]:
    offset = compact_glyph_offset(rom, code)
    record = bytes(rom[offset : offset + 16])
    if len(record) != 16 or record == b"\xFF" * 16:
        return False, 0, offset
    ink = sum(sum(row) for row in decode_compact_font_record(record))
    return ink > 0, ink, offset


def select_e0_codes(
    parent: bytes,
    d_parent: Dictionary,
    tbl: Tbl,
    needed: int,
) -> list[dict[str, Any]]:
    usage = parent_kanji_usage(parent, d_parent)
    chosen: list[dict[str, Any]] = []
    ranges = (range(0xE000, 0xE500), range(0xE600, 0xE740))
    for span in ranges:
        for code in span:
            if (code >> 8) == 0xE5:
                continue
            if (code & 0xFF) in UNSAFE_TRAILS or code in UNSAFE_KANJI:
                continue
            if usage[code]:
                continue
            ok, ink, offset = glyph_usable(parent, code)
            if not ok:
                continue
            chosen.append(
                {
                    "code": code,
                    "code_hex": f"{code:04X}",
                    "old_char": tbl.decode_char(code),
                    "old_glyph_hex": bytes(parent[offset : offset + 16]).hex().upper(),
                    "compact_offset": f"{offset:06X}",
                    "ink": ink,
                    "kind": "e0_kanji",
                }
            )
            if len(chosen) >= needed:
                return chosen
    raise BuildError(f"need {needed} unused E0xx codes, found {len(chosen)}")


def select_onebyte_codes(
    parent: bytes,
    d_parent: Dictionary,
    tbl: Tbl,
    needed: int,
) -> list[dict[str, Any]]:
    dict_one: Counter[int] = Counter()
    hud_one: Counter[int] = Counter()
    script_one: Counter[int] = Counter()
    for index in range(d_parent.count):
        try:
            raw = bytes(d_parent.raw_entry(index))
        except Exception:
            continue
        for kind, value in walk_units(raw, ext3_aware=True):
            if kind == "one":
                dict_one[value] += 1
    for lo, hi in HUD_RANGES:
        for _logical, payload, _kind in _walk_zstring_range(
            parent, lo, hi, region="name75", max_len=64
        ):
            for kind, value in walk_units(payload, ext3_aware=True):
                if kind == "one":
                    hud_one[value] += 1
    for region, lo, hi, max_len in _reference_scopes():
        if region != "script":
            continue
        for _logical, payload, _kind in _walk_zstring_range(
            parent, lo, hi, region=region, max_len=max_len
        ):
            for kind, value in walk_units(payload, ext3_aware=True):
                if kind == "one":
                    script_one[value] += 1
    ranked: list[dict[str, Any]] = []
    for code in range(0x02, 0xE0):
        if code in TAKEN_ONEBYTE:
            continue
        if dict_one[code] or hud_one[code]:
            continue
        char = tbl.decode_char(code)
        if char in KANA_CHARS:
            continue
        ok, ink, offset = glyph_usable(parent, code)
        if not ok:
            continue
        ranked.append(
            {
                "code": code,
                "code_hex": f"{code:02X}",
                "old_char": char,
                "old_glyph_hex": bytes(parent[offset : offset + 16]).hex().upper(),
                "compact_offset": f"{offset:06X}",
                "ink": ink,
                "kind": "onebyte",
                "script_collateral": script_one[code],
                "dict_hits": dict_one[code],
                "hud_hits": hud_one[code],
            }
        )
    ranked.sort(key=lambda row: (int(row["script_collateral"]), int(row["code"])))
    if len(ranked) < needed:
        raise BuildError(f"need {needed} HUD/dict-unused 1-byte codes, found {len(ranked)}")
    return ranked[:needed]


def decode_mapped(
    payload: bytes,
    code_to_char: dict[int, str],
    dictionary: Dictionary,
    tbl: Tbl,
) -> str:
    parts: list[str] = []
    cursor = 0
    while cursor < len(payload):
        lead = payload[cursor]
        if is_dict_token(lead) and cursor + 1 < len(payload):
            index = dict_index_from_token(lead, payload[cursor + 1])
            raw = bytes(dictionary.raw_entry(index))
            parts.append(decode_mapped(raw, code_to_char, dictionary, tbl))
            cursor += 2
            continue
        if is_kanji_lead(lead) and cursor + 1 < len(payload):
            code = (lead << 8) | payload[cursor + 1]
            parts.append(code_to_char.get(code, tbl.decode_char(code)))
            cursor += 2
            continue
        if lead == 0x01:
            parts.append("　")
            cursor += 1
            continue
        parts.append(code_to_char.get(lead, tbl.decode_char(lead)))
        cursor += 1
    return "".join(parts)


def select_retired_slot(
    parent: bytes,
    original: bytes,
    d_parent: Dictionary,
    d_original: Dictionary,
    need: int,
) -> dict[str, Any]:
    wanted = {
        index
        for index in range(min(d_original.stock_count, 0xF00))
        if dict_token_safe_in_zstring(index)
    }
    original_external = external_occurrence_map(
        original, ext3_aware=False, wanted=wanted
    )
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    original_nested = nested_occurrence_map(
        d_original, wanted=wanted, ext3_aware=False
    )
    current_nested = nested_occurrence_map(d_parent, wanted=wanted, ext3_aware=True)
    preliminary: list[dict[str, Any]] = []
    for index in sorted(wanted):
        if current_external.get(index) or current_nested.get(index) or original_nested.get(index):
            continue
        historical = list(original_external.get(index) or [])
        if not historical:
            continue
        try:
            vanilla_payload = bytes(d_original.raw_entry(index))
            current_payload = bytes(d_parent.raw_entry(index))
        except Exception:
            continue
        if d_original.ptrs[index] != d_parent.ptrs[index]:
            continue
        if vanilla_payload != current_payload:
            continue
        if index == EMPTY_STOCK:
            continue
        if len(current_payload) < need:
            continue
        if b"\xE5\x19" in current_payload or b"\xF0\x8F" in current_payload:
            continue
        preliminary.append(
            {
                "index": index,
                "old_pointer": d_parent.ptrs[index],
                "old_payload": current_payload,
                "historical": historical,
            }
        )
    raw_hits = _raw_pair_hits(parent, [row["index"] for row in preliminary])
    strong = [row for row in preliminary if not raw_hits.get(row["index"])]
    strong.sort(
        key=lambda row: (len(row["historical"]), -len(row["old_payload"]), row["index"])
    )
    if not strong:
        raise BuildError("no retired in-place slot with capacity for E0xx 우주")
    return strong[0]


def write_slot_inplace(rom: bytearray, item: dict[str, Any]) -> None:
    bank = bytearray(slice_bank(rom, SEG_DICT))
    pointer = int(item["old_pointer"])
    old = bytes(item["old_payload"])
    new = bytes(item["new_payload"])
    if len(new) > len(old):
        raise BuildError(
            f"slot {int(item['index']):04X} capacity {len(old)} < {len(new)}"
        )
    if pointer + len(old) + 1 > BANK_SIZE:
        raise BuildError(f"slot {int(item['index']):04X} phrase overflows bank")
    bank[pointer : pointer + len(new)] = new
    fill = len(old) - len(new) + 1
    bank[pointer + len(new) : pointer + len(old) + 1] = b"\x00" * fill
    patch_bank(rom, SEG_DICT, bank)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    live_save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file() or len(live_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or not 32 KiB")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_original = Dictionary(original)
    sb = stock_base(parent)
    empty = token_from_dict_index(EMPTY_STOCK)
    if empty != bytes.fromhex("F0A9"):
        raise BuildError("00A9 token drifted")
    if d_parent.expand_index(EMPTY_STOCK, tbl) != "":
        raise BuildError("00A9 is no longer zero-width")
    if bytes(d_parent.raw_entry(0x008F)) != bytes.fromhex("EC8DE761E764"):
        raise BuildError("slot 008F 우주 payload drifted")

    for logical, expected in (
        *((site, SPACE_BEFORE) for site in SPACE_SITES),
        *ABAOA_BEFORE.items(),
    ):
        payload, terminator = payload_at(parent, logical)
        if payload != expected:
            raise BuildError(
                f"{logical:06X} drifted: {payload.hex().upper()} != {expected.hex().upper()}"
            )
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator drifted")

    e0_rows = select_e0_codes(parent, d_parent, tbl, 2)
    one_rows = select_onebyte_codes(parent, d_parent, tbl, 4)
    for row, char in zip(e0_rows, ("우", "주")):
        row["hangul"] = char
        row["hangul_source_hex"] = f"{HANGUL_SOURCE[char]:04X}"
        if (int(row["code"]) >> 8) == 0xE5:
            raise BuildError("refusing E5xx steal")
    for row, char in zip(one_rows, ("아", "바", "오", "쿠")):
        row["hangul"] = char
        row["hangul_source_hex"] = f"{HANGUL_SOURCE[char]:04X}"
        if int(row["code"]) > 0xDF:
            raise BuildError("1-byte steal escaped 02-DF")

    char_to_code = {
        str(row["hangul"]): int(row["code"]) for row in e0_rows + one_rows
    }
    code_to_char = {code: char for char, code in char_to_code.items()}
    space_payload = u16(char_to_code["우"]) + u16(char_to_code["주"])
    if space_payload[0] == 0xE5 or space_payload[2] == 0xE5:
        raise BuildError("우주 payload used E5 lead")
    if b"\xE5\x19" in space_payload:
        raise BuildError("compact3 E519 in 우주 payload")
    abaoa_body = bytes(
        [
            char_to_code["아"],
            0x01,
            char_to_code["바"],
            char_to_code["오"],
            char_to_code["아"],
            0x01,
            char_to_code["쿠"],
        ]
    )
    if len(abaoa_body) != 7:
        raise BuildError("아 바오아 쿠 body is not 7 bytes")
    if 0xE5 in abaoa_body or 0x00 in abaoa_body:
        raise BuildError("아 바오아 쿠 body contains E5 or NUL")

    retired = select_retired_slot(parent, original, d_parent, d_original, len(space_payload))
    slot_index = int(retired["index"])
    space_token = token_from_dict_index(slot_index)
    union = build_reference_union(
        original,
        parent,
        regions=("script", "name75", "aux"),
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    live_working = [
        consumer
        for consumer in union.consumers_for(slot_index)
        if "working" in consumer.seen_in
    ]
    if live_working:
        raise BuildError(
            f"slot {slot_index:04X} still has working consumers: "
            + ", ".join(f"{c.abs:06X}/{c.region}" for c in live_working[:6])
        )
    slot_payload = {slot_index: space_payload}
    guard_hangul_slot_writes(
        parent,
        slot_payload,
        allow_aux_consumers=False,
        locs=union.as_locs(),
    )
    outcome = guard_slot_writes(
        parent,
        slot_payload,
        union=union,
        require_free=False,
        allow_aux_consumers=True,
        justification=GUARD_JUSTIFICATION,
    )
    if not outcome.ok:
        raise BuildError(f"slot guard refused: {outcome.outcome} {outcome.refuse_reasons}")

    hangul_glyphs = {
        char: read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        for char in ("우", "주", "아", "바", "오", "쿠")
    }

    candidate = bytearray(parent)
    allow: list[tuple[int, int]] = []
    glyph_writes: list[dict[str, Any]] = []
    for row in e0_rows + one_rows:
        char = str(row["hangul"])
        offset = compact_glyph_offset(candidate, int(row["code"]))
        glyph = hangul_glyphs[char]
        if bytes(candidate[offset : offset + 16]) == glyph:
            raise BuildError(f"{row['code_hex']} already holds {char}")
        candidate[offset : offset + 16] = glyph
        allow.append((offset, offset + 16))
        glyph_writes.append(
            {
                "hangul": char,
                "stolen_code": row["code_hex"],
                "old_char": row["old_char"],
                "kind": row["kind"],
                "compact_offset": f"{offset:06X}",
                "source_hangul_code": row["hangul_source_hex"],
                "glyph_hex": glyph.hex().upper(),
                "script_collateral": row.get("script_collateral"),
            }
        )

    assignment = {
        "index": slot_index,
        "old_pointer": int(retired["old_pointer"]),
        "old_payload": bytes(retired["old_payload"]),
        "new_payload": space_payload,
    }
    write_slot_inplace(candidate, assignment)
    dict_bank = sb + SEG_DICT * BANK_SIZE
    pointer = int(retired["old_pointer"])
    old_payload = bytes(retired["old_payload"])
    phrase_start = dict_bank + pointer
    phrase_end = dict_bank + pointer + len(old_payload) + 1
    allow.append((phrase_start, phrase_end))
    retired_proof = {
        "index": f"{slot_index:04X}",
        "token_hex": space_token.hex().upper(),
        "phrase": SPACE_TEXT,
        "old_pointer": f"{pointer:04X}",
        "old_payload_hex": old_payload.hex().upper(),
        "new_payload_hex": space_payload.hex().upper(),
        "inplace": True,
        "old_capacity": len(old_payload),
        "phrase_file_start": f"{phrase_start:06X}",
        "phrase_file_end": f"{phrase_end:06X}",
        "historical_original_consumers": [
            f"{historical_abs(value):06X}" for value in retired["historical"]
        ],
        "current_working_consumers_before": 0,
        "guard_outcome": outcome.as_dict(),
    }

    applied: list[dict[str, Any]] = []
    for logical in SPACE_SITES:
        old, terminator = payload_at(parent, logical)
        if len(space_token) != len(old):
            raise BuildError(f"{logical:06X} 우주 length changed")
        start = sb + logical
        candidate[start : start + len(space_token)] = space_token
        allow.append((start, start + len(space_token)))
        if candidate[sb + terminator] != 0:
            raise BuildError(f"{logical:06X} terminator changed")
        applied.append(
            {
                "abs": f"{logical:06X}",
                "phrase": SPACE_TEXT,
                "before_hex": old.hex().upper(),
                "after_hex": space_token.hex().upper(),
                "slot": f"{slot_index:04X}",
                "token_hex": space_token.hex().upper(),
            }
        )

    for logical in ABAOA_SITES:
        old, terminator = payload_at(parent, logical)
        if len(abaoa_body) != len(old):
            raise BuildError(f"{logical:06X} 아 바오아 쿠 length changed")
        start = sb + logical
        candidate[start : start + len(abaoa_body)] = abaoa_body
        allow.append((start, start + len(abaoa_body)))
        if candidate[sb + terminator] != 0:
            raise BuildError(f"{logical:06X} terminator changed")
        applied.append(
            {
                "abs": f"{logical:06X}",
                "phrase": ABAOA_TEXT,
                "before_hex": old.hex().upper(),
                "after_hex": abaoa_body.hex().upper(),
                "encoding": "inline_onebyte_plus_spaces",
                "token_hex": abaoa_body.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    allow.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allow)]
    if unexpected:
        raise BuildError(
            "diff outside allowlist: "
            + ", ".join(f"{lo:08X}-{hi:08X}" for lo, hi in unexpected)
        )

    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)
    if bytes(d_result.raw_entry(0x008F)) != bytes(d_parent.raw_entry(0x008F)):
        raise BuildError("slot 008F changed")
    if d_result.expand_index(EMPTY_STOCK, tbl) != "":
        raise BuildError("00A9 collateral")
    if payload_at(result, 0x75B3CA)[0] != payload_at(parent, 0x75B3CA)[0]:
        raise BuildError("범용 record changed")
    if payload_at(result, 0x75B457)[0] != payload_at(parent, 0x75B457)[0]:
        raise BuildError("명중 보정 record changed")
    if b"\xE5\x19" in bytes(d_result.raw_entry(slot_index)):
        raise BuildError("E519 in 우주 slot")

    result_union = build_reference_union(
        original,
        result,
        regions=("script", "name75", "aux"),
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    keepers = set(SPACE_SITES)
    unexpected_working = [
        consumer
        for consumer in result_union.consumers_for(slot_index)
        if "working" in consumer.seen_in and consumer.abs not in keepers
    ]
    if unexpected_working:
        raise BuildError(
            "우주 slot leaked to non-keeper working consumers: "
            + ", ".join(f"{c.abs:06X}/{c.region}" for c in unexpected_working[:8])
        )
    aux_name75 = result_union.aux_or_name75_consumers(slot_index)
    working_aux = [c for c in aux_name75 if "working" in c.seen_in]
    if any(c.abs not in keepers for c in working_aux):
        raise BuildError("우주 slot has unexpected working aux/name75 consumers")

    for logical in SPACE_SITES:
        payload, terminator = payload_at(result, logical)
        rendered = decode_mapped(payload, code_to_char, d_result, tbl)
        if payload != space_token or rendered != SPACE_TEXT:
            raise BuildError(f"{logical:06X} 우주 render {payload.hex()} {rendered!r}")
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator moved")
    for logical in ABAOA_SITES:
        payload, terminator = payload_at(result, logical)
        rendered = decode_mapped(payload, code_to_char, d_result, tbl)
        if payload != abaoa_body or rendered != ABAOA_TEXT:
            raise BuildError(
                f"{logical:06X} 아 바오아 쿠 render {payload.hex()} {rendered!r}"
            )
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator moved")
    if bytes(d_result.raw_entry(slot_index)) != space_payload:
        raise BuildError("우주 slot payload mismatch")
    if d_result.ptrs[slot_index] != d_parent.ptrs[slot_index]:
        raise BuildError("우주 slot pointer moved")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if MAIN.read_bytes() != parent:
        raise BuildError("main TIP mutated")
    if MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("live SaveRAM mutated")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terrain_space_abaoaqu_e0_onebyte_candidate.py",
        "status": "candidate_static_verified_needs_target_screen_check",
        "ok": True,
        "main_tip_modified": False,
        "parent": identity(MAIN, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "source": str(MAIN_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "policy": "copy_live_main_sav_at_build_time",
            "copied_latest": True,
            "hash_verification_skipped": True,
            "sha256": sha256(OUT_SAVE.read_bytes()),
        },
        "failed_previous_candidate": {
            "path": "out/patch/terrain_space_abaoaqu_compact_glyph_candidate.wsc",
            "why": (
                "wrote unused E5xx compact codes (E511…) after F0 expand; "
                "this HUD special-cases E5 as ext3/compact3, unlike original "
                "宇宙 E078 E046 (E0) and ア・バオア・クー 1-byte kana"
            ),
            "records_were_correct": True,
        },
        "cause": {
            "hud": "terrain-info top line expands F0 then blits compact glyphs",
            "space": "keep 2-byte field; use E0xx compact kanji like original 宇宙",
            "abaoa": "keep 7-byte field; inline 1-byte glyphs plus two 01 spaces",
            "spelling": "아　바오아　쿠 fits because 1-byte encoding has room for both spaces",
            "e078_not_stolen": "aux 5ED6A1 宇人質 remains 宇; leftover E078 is not overwritten",
        },
        "glyphs": glyph_writes,
        "dictionary": {
            "selected_retired_slots": 1,
            "write_mode": "inplace_retired_payload",
            "slot_008F_preserved": True,
            "proof": [retired_proof],
        },
        "records": applied,
        "verification": {
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "범용_preserved": True,
            "명중_보정_preserved": True,
            "compact3_e519_absent": True,
            "unaccounted_changed_bytes": 0,
            "diff_runs": len(runs),
            "diff_bytes": sum(hi - lo for lo, hi in runs),
        },
    }
    atomic_json(OUT_REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate_sha256": report["candidate"]["sha256"],
                "checksum": report["candidate"]["checksum"],
                "diff_bytes": report["verification"]["diff_bytes"],
                "space_slot": retired_proof["index"],
                "space_payload": space_payload.hex().upper(),
                "abaoa_hex": abaoa_body.hex().upper(),
                "steals": [
                    {"hangul": row["hangul"], "code": row["stolen_code"], "kind": row["kind"]}
                    for row in glyph_writes
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
