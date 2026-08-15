#!/usr/bin/env python3
"""Repair the terrain-info popup names 우주 / 아 바오아 쿠.

The map-terrain HUD expands F0 dictionary tokens but plots the result through
the compact font path.  That path does not honour the Hangul-run marker EC8D,
so slot 008F (우주, marker + E761 E764) shows as あ, and the E5 18 encoding of
ア・バオア・クー is drawn as ordinary glyphs (詩…).  명중 보정 on the same
window uses the hooked 75B path and is already correct.

This candidate:

* steals six text-unused 2-byte compact-font codes below the Hangul window;
* copies the Hangul 우/주/아/바/오/쿠 bitmaps onto those compact glyphs;
* allocates two retired stock slots whose payloads are those compact codes
  (no EC8D marker), matching how original 宇宙 was two compact kanji;
* retargets only 75B3CE/75E59A (우주) and 75E58C/75BD77
  (아 바오아쿠; the 7-byte field holds one fullwidth space).

Candidate only.  It never overwrites the main TIP or main SaveRAM.
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
from expand_dictionary import _walk_zstring_range  # noqa: E402
from hangul_allocator import HANGUL_PRIMARY_START  # noqa: E402
from mixed_residual_reference_union import _reference_scopes  # noqa: E402
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
OUT_ROM = ROOT / "out/patch/terrain_space_abaoaqu_compact_glyph_candidate.wsc"
OUT_SAVE = ROOT / "sram/terrain_space_abaoaqu_compact_glyph_candidate.sav"
OUT_REPORT = ROOT / "out/patch/terrain_space_abaoaqu_compact_glyph_report.json"

EXPECTED_MAIN_SHA256 = (
    "0ff2bc7398c5b677d02bc1d81df21d12dc7731d2d16d62c3cc7cd25b1c74ca11"
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
STEAL_ORDER = ("우", "주", "아", "바", "오", "쿠")
SPACE_TEXT = "우주"
ABAOA_TEXT = "아　바오아쿠"
ABAOA_FRAGMENTS = ("아", "바오", "아쿠")
FRAGMENT_TEXTS = (SPACE_TEXT,) + ABAOA_FRAGMENTS
SPACE_SITES = (0x75B3CE, 0x75E59A)
ABAOA_SITES = (0x75E58C, 0x75BD77)
SPACE_BEFORE = bytes.fromhex("F08F")
ABAOA_BEFORE = {
    0x75E58C: bytes.fromhex("E518EC41010101"),
    0x75BD77: bytes.fromhex("E518B445F0A901"),
}
STEAL_RANGES = (
    (0xE500, 0xE600),
    (0xE000, 0xE200),
    (0xE400, 0xE500),
    (0xE300, 0xE400),
    (0xE200, 0xE300),
    (0xE600, 0xE740),
)
UNSAFE_TRAILS = {0x00, 0x18, 0x19}
UNSAFE_CODES = {0xE518, 0xE519}


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
    """Compact-font file offset using this ROM's size, not the global stock base."""
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


def walk_kanji_codes(payload: bytes, *, ext3_aware: bool) -> Iterable[int]:
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
            cursor += 2
            continue
        if is_kanji_lead(lead) and cursor + 1 < size:
            yield (lead << 8) | payload[cursor + 1]
            cursor += 2
            continue
        cursor += 1


def kanji_usage(rom: bytes, dictionary: Dictionary, *, ext3_aware: bool) -> Counter[int]:
    counts: Counter[int] = Counter()
    for region, lo, hi, max_len in _reference_scopes():
        for _logical, payload, _kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            counts.update(walk_kanji_codes(payload, ext3_aware=ext3_aware))
    for index in range(dictionary.count):
        try:
            raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        counts.update(walk_kanji_codes(raw, ext3_aware=ext3_aware))
    return counts


def select_steal_codes(
    parent: bytes,
    original: bytes,
    d_parent: Dictionary,
    d_original: Dictionary,
    tbl: Tbl,
) -> list[dict[str, Any]]:
    usage = kanji_usage(parent, d_parent, ext3_aware=True)
    usage.update(kanji_usage(original, d_original, ext3_aware=False))
    chosen: list[dict[str, Any]] = []
    for start, end in STEAL_RANGES:
        for code in range(start, end):
            if (code & 0xFF) in UNSAFE_TRAILS:
                continue
            if code in UNSAFE_CODES:
                continue
            if usage[code]:
                continue
            offset = compact_glyph_offset(parent, code)
            record = bytes(parent[offset : offset + 16])
            if len(record) != 16 or record == b"\xFF" * 16:
                continue
            ink = sum(sum(row) for row in decode_compact_font_record(record))
            if ink <= 0:
                continue
            chosen.append(
                {
                    "code": code,
                    "code_hex": f"{code:04X}",
                    "old_char": tbl.decode_char(code),
                    "old_glyph_hex": record.hex().upper(),
                    "compact_offset": f"{offset:06X}",
                    "ink": ink,
                }
            )
            if len(chosen) >= len(STEAL_ORDER):
                return chosen
    raise BuildError(
        f"need {len(STEAL_ORDER)} unused compact codes, found {len(chosen)}"
    )


def encode_stolen(text: str, char_to_code: dict[str, int]) -> bytes:
    out = bytearray()
    for char in text:
        if char == "　":
            out.append(0x01)
            continue
        code = char_to_code.get(char)
        if code is None:
            raise BuildError(f"no stolen code for {char!r}")
        out.extend(u16(code))
    if not out or 0x00 in out:
        raise BuildError(f"stolen payload unsafe: {text!r}")
    return bytes(out)


def decode_stolen(
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
            parts.append(decode_stolen(raw, code_to_char, dictionary, tbl))
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
        parts.append(tbl.decode_char(lead))
        cursor += 1
    return "".join(parts)


def select_retired_slots(
    parent: bytes,
    original: bytes,
    d_parent: Dictionary,
    d_original: Dictionary,
    needed: list[int],
) -> list[dict[str, Any]]:
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
    prelim_caps = sorted({len(row["old_payload"]) for row in preliminary}, reverse=True)
    strong.sort(
        key=lambda row: (len(row["historical"]), -len(row["old_payload"]), row["index"])
    )
    picked: list[dict[str, Any]] = []
    used: set[int] = set()
    for need in needed:
        for row in strong:
            if row["index"] in used:
                continue
            if len(row["old_payload"]) < need:
                continue
            picked.append(row)
            used.add(row["index"])
            break
        else:
            capacities = sorted({len(row["old_payload"]) for row in strong}, reverse=True)
            raise BuildError(
                f"no retired in-place slot with capacity {need}; "
                f"strong={len(strong)} capacities={capacities} "
                f"preliminary={len(preliminary)} prelim_caps={prelim_caps}"
            )
    return picked


def write_slots_inplace(
    rom: bytearray,
    assignments: list[dict[str, Any]],
) -> None:
    bank = bytearray(slice_bank(rom, SEG_DICT))
    for item in assignments:
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
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
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
        raise BuildError("slot 008F 우주 payload drifted; leaving it untouched requires the known bytes")

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

    steal_rows = select_steal_codes(parent, original, d_parent, d_original, tbl)
    char_to_code = {
        char: int(row["code"]) for char, row in zip(STEAL_ORDER, steal_rows)
    }
    code_to_char = {code: char for char, code in char_to_code.items()}
    for row, char in zip(steal_rows, STEAL_ORDER):
        row["hangul"] = char
        row["hangul_source_hex"] = f"{HANGUL_SOURCE[char]:04X}"

    fragment_payloads = {
        text: encode_stolen(text, char_to_code) for text in FRAGMENT_TEXTS
    }

    retired = select_retired_slots(
        parent,
        original,
        d_parent,
        d_original,
        [len(fragment_payloads[text]) for text in FRAGMENT_TEXTS],
    )
    slot_payload: dict[int, bytes] = {}
    phrase_to_slot: dict[str, int] = {}
    retired_proof: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    dict_bank = sb + SEG_DICT * BANK_SIZE
    for evidence, phrase in zip(retired, FRAGMENT_TEXTS):
        index = int(evidence["index"])
        encoded = fragment_payloads[phrase]
        phrase_to_slot[phrase] = index
        slot_payload[index] = encoded
        pointer = int(evidence["old_pointer"])
        old_payload = bytes(evidence["old_payload"])
        assignments.append(
            {
                "index": index,
                "old_pointer": pointer,
                "old_payload": old_payload,
                "new_payload": encoded,
            }
        )
        retired_proof.append(
            {
                "index": f"{index:04X}",
                "token_hex": token_from_dict_index(index).hex().upper(),
                "phrase": phrase,
                "old_pointer": f"{pointer:04X}",
                "old_payload_hex": old_payload.hex().upper(),
                "new_payload_hex": encoded.hex().upper(),
                "inplace": True,
                "old_capacity": len(old_payload),
                "phrase_file_start": f"{dict_bank + pointer:06X}",
                "phrase_file_end": f"{dict_bank + pointer + len(old_payload) + 1:06X}",
                "historical_original_consumers": [
                    f"{historical_abs(value):06X}"
                    for value in evidence["historical"]
                ],
                "current_external_consumers": 0,
                "current_nested_consumers": 0,
                "current_raw_pair_hits": 0,
            }
        )

    hangul_glyphs = {
        char: read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        for char in STEAL_ORDER
    }

    candidate = bytearray(parent)
    allow: list[tuple[int, int]] = []
    glyph_writes: list[dict[str, Any]] = []
    for row in steal_rows:
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
                "compact_offset": f"{offset:06X}",
                "source_hangul_code": row["hangul_source_hex"],
                "glyph_hex": glyph.hex().upper(),
            }
        )

    write_slots_inplace(candidate, assignments)
    for row in retired_proof:
        start = int(row["phrase_file_start"], 16)
        end = int(row["phrase_file_end"], 16)
        allow.append((start, end))

    space_token = token_from_dict_index(phrase_to_slot[SPACE_TEXT])
    a_token = token_from_dict_index(phrase_to_slot["아"])
    bao_token = token_from_dict_index(phrase_to_slot["바오"])
    aku_token = token_from_dict_index(phrase_to_slot["아쿠"])
    abaoa_body = a_token + b"\x01" + bao_token + aku_token
    if len(abaoa_body) != 7:
        raise BuildError(f"아 바오아 쿠 body length {len(abaoa_body)} != 7")
    applied: list[dict[str, Any]] = []
    for logical in SPACE_SITES:
        old, terminator = payload_at(parent, logical)
        new = space_token
        if len(new) != len(old):
            raise BuildError(f"{logical:06X} 우주 length changed")
        start = sb + logical
        candidate[start : start + len(new)] = new
        allow.append((start, start + len(new)))
        if candidate[sb + terminator] != 0:
            raise BuildError(f"{logical:06X} terminator changed")
        applied.append(
            {
                "abs": f"{logical:06X}",
                "phrase": SPACE_TEXT,
                "before_hex": old.hex().upper(),
                "after_hex": new.hex().upper(),
                "slot": f"{phrase_to_slot[SPACE_TEXT]:04X}",
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
                "slots": {
                    "아": f"{phrase_to_slot['아']:04X}",
                    "바오": f"{phrase_to_slot['바오']:04X}",
                    "아쿠": f"{phrase_to_slot['아쿠']:04X}",
                },
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

    for logical in SPACE_SITES:
        payload, terminator = payload_at(result, logical)
        rendered = decode_stolen(payload, code_to_char, d_result, tbl)
        if payload != space_token or rendered != SPACE_TEXT:
            raise BuildError(f"{logical:06X} 우주 render {payload.hex()} {rendered!r}")
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator moved")
    for logical in ABAOA_SITES:
        payload, terminator = payload_at(result, logical)
        rendered = decode_stolen(payload, code_to_char, d_result, tbl)
        if payload != abaoa_body or rendered != ABAOA_TEXT:
            raise BuildError(
                f"{logical:06X} 아 바오아 쿠 render {payload.hex()} {rendered!r}"
            )
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator moved")

    for index, expected in slot_payload.items():
        if bytes(d_result.raw_entry(index)) != expected:
            raise BuildError(f"slot {index:04X} payload mismatch")
        if d_result.ptrs[index] != d_parent.ptrs[index]:
            raise BuildError(f"slot {index:04X} pointer moved")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terrain_space_abaoaqu_compact_glyph_candidate.py",
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
        "cause": {
            "hud": "terrain-info popup top line uses compact-font blit after F0 expand",
            "space": "F08F → slot 008F EC8D+우주, marker plots as あ",
            "abaoa": "E518 ext3 is not expanded on that blit and plots as 詩…",
            "fix": "dedicated F0 tokens whose payloads are compact Hangul glyphs without EC8D",
        },
        "glyphs": glyph_writes,
        "dictionary": {
            "selected_retired_slots": len(slot_payload),
            "write_mode": "inplace_retired_payload",
            "slot_008F_preserved": True,
            "proof": retired_proof,
        },
        "records": applied,
        "verification": {
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "범용_preserved": True,
            "명중_보정_preserved": True,
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
                "slots": [row["index"] for row in retired_proof],
                "steals": [row["stolen_code"] for row in glyph_writes],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
