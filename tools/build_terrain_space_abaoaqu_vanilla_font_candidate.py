#!/usr/bin/env python3
"""Repair terrain-info 우주 / 아 바오아 쿠 on vanilla compact-font slots.

This HUD top line does not honour ext3/Hangul markers.  Original 宇宙 is
F08F → dict[008F] = E078 E046, and ア・バオア・クー is a 7-byte
1-byte + nakaguro + F0 + 1-byte + F0 stream.  Previous candidates stole
E5xx or retargeted F08F→F050 / 1-byte 59/BC/B2/C2 and failed on-screen.

This candidate keeps F08F, restores slot 008F to E078 E046 via spill,
paints 우/주 onto those original compact glyphs, preserves aux 宇人質 by
copying 宇 off E078 first, and rebuilds 아 바오아 쿠 on the original
7-byte F0 skeleton (inline 1-byte fallback if two retired slots cannot
be claimed).  Parent is live main B0438B51….  Main TIP and live SaveRAM
are never overwritten.
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
from expand_dictionary import (  # noqa: E402
    NAME75_RANGES,
    _walk_zstring_range,
    guard_hangul_slot_writes,
    write_dictionary_slots_spill,
)
from hangul_allocator import HANGUL_PRIMARY_START  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    NAME75_UI_TABLE_RANGES,
    _reference_scopes,
    build_reference_union,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    COMPACT_FONT_RECORD_SIZE,
    COMPACT_FONT_SEGMENT,
    COMPACT_FONT_TABLE,
    DICT_PTR_START,
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
    set_stock_base,
    slice_bank,
    stock_base,
    text_code_to_glyph_index,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_font_hangul_hook import PAD1_FILE, PAD1_SLOTS, PAD2_FILE  # noqa: E402
from patch_pad3_expansion import PAD12_SLOTS, pad3_file_offset  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PINNED_PARENT_FALLBACK = (
    ROOT
    / "out/patch/backup/20260815_015949_pre_name_mapping_spirit_combined"
    / "monoeye_ko_expanded.wsc"
)
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/terrain_space_abaoaqu_vanilla_font_candidate.wsc"
OUT_SAVE = ROOT / "sram/terrain_space_abaoaqu_vanilla_font_candidate.sav"
OUT_REPORT = ROOT / "out/patch/terrain_space_abaoaqu_vanilla_font_report.json"

EXPECTED_MAIN_SHA256 = (
    "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"
)
EXPECTED_SAVE_SHA256 = (
    "c0056b393cc669032ae19b88c33d8cffa861b49ef7de69d402569b72f11326dd"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EMPTY_STOCK = 0x00A9
SLOT_SPACE = 0x008F

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
SEKI_SITES = (0x75B75E, 0x75BE2B)
SPACE_BEFORE = bytes.fromhex("F08F")
SLOT_008F_BEFORE = bytes.fromhex("EC8DE761E764")
SLOT_008F_AFTER = bytes.fromhex("E078E046")
ABAOA_BEFORE = {
    0x75E58C: bytes.fromhex("E518EC41010101"),
    0x75BD77: bytes.fromhex("E518B445F0A901"),
}
AUX_U_SITE = 0x5ED6A1
AUX_U_BEFORE = bytes.fromhex("E078F078")
VANILLA_U = 0xE078
VANILLA_JU = 0xE046
TAKEN_ONEBYTE = {0x00, 0x01, 0x18, 0x19, 0x1F, 0x26, 0x2A, 0xC6, 0xDF}
FAILED_ONEBYTE = {0x59, 0xBC, 0xB2, 0xC2}
UNSAFE_TRAILS = {0x00, 0x18, 0x19}
UNSAFE_KANJI = {0xE078, 0xE046, 0xE518, 0xE519}
KANA_CHARS = set(
    "ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとど"
    "なにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをん"
    "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトド"
    "ナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヲンヴ"
)
HUD_RANGES = list(NAME75_RANGES) + list(NAME75_UI_TABLE_RANGES)
F58CE5 = bytes.fromhex("F58CE5")
PTR_BANKS = range(0x64, 0x67)


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
    """File offset of a compact glyph.  Uses *this* ROM's length, not the
    global stock base (load_rom(original) would otherwise zero the base)."""
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


def select_preserve_e0(
    parent: bytes,
    d_parent: Dictionary,
    tbl: Tbl,
) -> dict[str, Any]:
    usage = parent_kanji_usage(parent, d_parent)
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
            return {
                "code": code,
                "code_hex": f"{code:04X}",
                "old_char": tbl.decode_char(code),
                "old_glyph_hex": bytes(parent[offset : offset + 16]).hex().upper(),
                "compact_offset": f"{offset:06X}",
                "ink": ink,
                "kind": "e0_preserve_u",
            }
    raise BuildError("no unused E0xx to preserve 宇 from E078")


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
        if code in TAKEN_ONEBYTE or code in FAILED_ONEBYTE:
            continue
        if hud_one[code]:
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
    ranked.sort(
        key=lambda row: (
            int(row["dict_hits"]),
            int(row["script_collateral"]),
            int(row["code"]),
        )
    )
    if len(ranked) < needed:
        raise BuildError(
            f"need {needed} HUD-unused 1-byte codes excluding "
            f"59/BC/B2/C2, found {len(ranked)}"
        )
    chosen = ranked[:needed]
    if int(chosen[0]["dict_hits"]) != 0:
        raise BuildError("no HUD+dict unused 1-byte remains for inline 아")
    return chosen


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
        if index in {EMPTY_STOCK, SLOT_SPACE}:
            continue
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
            raise BuildError(
                f"no retired in-place slot with capacity {need}; "
                f"strong={len(strong)} preliminary={len(preliminary)}"
            )
    return picked


def refuse_working_consumers(
    union: Any,
    index: int,
    keepers: set[int],
) -> None:
    working = [
        consumer
        for consumer in union.consumers_for(index)
        if "working" in consumer.seen_in
    ]
    leak = [c for c in working if c.abs not in keepers]
    aux_name75 = [c for c in leak if c.region != "script"]
    script_leak = [c for c in leak if c.region == "script"]
    if aux_name75:
        raise BuildError(
            f"slot {index:04X} has working aux/name75 consumers: "
            + ", ".join(f"{c.abs:06X}/{c.region}" for c in aux_name75[:6])
        )
    if script_leak:
        raise BuildError(
            f"slot {index:04X} has non-keeper working script consumers: "
            + ", ".join(f"{c.abs:06X}" for c in script_leak[:6])
        )


def write_slots_inplace(rom: bytearray, assignments: list[dict[str, Any]]) -> None:
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


def resolve_pinned_parent() -> tuple[Path, bytes]:
    """Load the SHA-pinned parent without touching live main.

    Live ``monoeye_ko_expanded.wsc`` may already be a later promotion
    (name-mapping + spirit).  This candidate must not mix with that ROM.
    """
    if MAIN.is_file():
        live = MAIN.read_bytes()
        if sha256(live) == EXPECTED_MAIN_SHA256:
            return MAIN, live
    if PINNED_PARENT_FALLBACK.is_file():
        pinned = PINNED_PARENT_FALLBACK.read_bytes()
        if sha256(pinned) == EXPECTED_MAIN_SHA256:
            return PINNED_PARENT_FALLBACK, pinned
    raise BuildError(
        "pinned parent B0438B51 not found; live main drifted and rollback missing"
    )


def find_f58ce5(rom: bytes) -> list[int]:
    sb = stock_base(rom)
    hits: list[int] = []
    for bank in PTR_BANKS:
        start = sb + bank * BANK_SIZE
        chunk = rom[start : start + BANK_SIZE]
        pos = 0
        while True:
            found = chunk.find(F58CE5, pos)
            if found < 0:
                break
            hits.append(start + found)
            pos = found + 1
    return hits


def paint_glyph(
    rom: bytearray,
    code: int,
    glyph: bytes,
    *,
    hangul: str,
    old_char: str,
    kind: str,
    source_hex: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    offset = compact_glyph_offset(rom, code)
    old = bytes(rom[offset : offset + 16])
    if old == glyph:
        raise BuildError(f"{code:04X} already holds {hangul}")
    rom[offset : offset + 16] = glyph
    row = {
        "hangul": hangul,
        "stolen_code": f"{code:04X}" if code > 0xFF else f"{code:02X}",
        "old_char": old_char,
        "kind": kind,
        "compact_offset": f"{offset:06X}",
        "source_hangul_code": source_hex,
        "glyph_hex": glyph.hex().upper(),
    }
    if extra:
        row.update(extra)
    return row


def main() -> int:
    parent_path, parent = resolve_pinned_parent()
    load_rom(parent_path)
    original = bytes(load_rom(ORIGINAL))
    set_stock_base(stock_base(parent))
    live_main_before = MAIN.read_bytes() if MAIN.is_file() else b""
    live_save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("pinned parent identity drifted")
    if not MAIN_SAVE.is_file() or len(live_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or not 32 KiB")
    if sha256(live_save) != EXPECTED_SAVE_SHA256:
        raise BuildError("live SaveRAM SHA drifted; refusing to copy a different sav")
    if compact_glyph_offset(parent, VANILLA_U) < 0x800000:
        raise BuildError("compact_glyph_offset(parent) used 8MB base")
    if compact_glyph_offset(original, VANILLA_U) >= 0x800000:
        raise BuildError("compact_glyph_offset(original) used 16MB base")

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
    if bytes(d_parent.raw_entry(SLOT_SPACE)) != SLOT_008F_BEFORE:
        raise BuildError("slot 008F 우주 payload drifted")
    if bytes(d_original.raw_entry(SLOT_SPACE)) != SLOT_008F_AFTER:
        raise BuildError("original slot 008F is not E078E046")

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
    aux_old, aux_term = payload_at(parent, AUX_U_SITE)
    if aux_old != AUX_U_BEFORE or aux_term != AUX_U_SITE + len(aux_old):
        raise BuildError("5ED6A1 宇人質 drifted")
    orig_aux, _orig_aux_term = payload_at(original, AUX_U_SITE)
    if orig_aux != AUX_U_BEFORE:
        raise BuildError("original 5ED6A1 is not E078F078")

    parent_u_glyph = read_glyph(parent, compact_glyph_offset(parent, VANILLA_U))
    orig_u_glyph = read_glyph(original, compact_glyph_offset(original, VANILLA_U))
    if parent_u_glyph != orig_u_glyph:
        raise BuildError("parent E078 glyph is not original 宇")
    parent_ju_glyph = read_glyph(parent, compact_glyph_offset(parent, VANILLA_JU))
    orig_ju_glyph = read_glyph(original, compact_glyph_offset(original, VANILLA_JU))
    if parent_ju_glyph != orig_ju_glyph:
        raise BuildError("parent E046 glyph is not original 宙")

    preserve = select_preserve_e0(parent, d_parent, tbl)
    one_rows = select_onebyte_codes(parent, d_parent, tbl, 4)
    for row, char in zip(one_rows, ("아", "바", "오", "쿠")):
        row["hangul"] = char
        row["hangul_source_hex"] = f"{HANGUL_SOURCE[char]:04X}"
        if int(row["code"]) in FAILED_ONEBYTE:
            raise BuildError("refusing failed e0 1-byte steal")
        if int(row["code"]) > 0xDF:
            raise BuildError("1-byte steal escaped 02-DF")
    if int(one_rows[0]["dict_hits"]) != 0 or int(one_rows[0]["hud_hits"]) != 0:
        raise BuildError("아 is not HUD+dict unused")

    char_to_code = {str(row["hangul"]): int(row["code"]) for row in one_rows}
    char_to_code["우"] = VANILLA_U
    char_to_code["주"] = VANILLA_JU
    char_to_code["宇"] = int(preserve["code"])
    code_to_char = {code: char for char, code in char_to_code.items()}

    baoa_payload = bytes(
        [char_to_code["바"], char_to_code["오"], char_to_code["아"]]
    )
    ku_payload = bytes([char_to_code["쿠"]])
    if len(baoa_payload) != 3 or len(ku_payload) != 1:
        raise BuildError("바오아/쿠 payload width drifted")
    if 0xE5 in baoa_payload or 0xE5 in ku_payload:
        raise BuildError("E5 in 바오아/쿠 payload")

    union = build_reference_union(
        original,
        parent,
        regions=("script", "name75", "aux"),
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    abaoa_path = "f0_skeleton"
    retired_rows: list[dict[str, Any]] = []
    try:
        retired_rows = select_retired_slots(
            parent, original, d_parent, d_original, [len(baoa_payload), len(ku_payload)]
        )
        for row in retired_rows:
            refuse_working_consumers(union, int(row["index"]), set())
        retired_payloads = {
            int(retired_rows[0]["index"]): baoa_payload,
            int(retired_rows[1]["index"]): ku_payload,
        }
        if any(b"\xE5\x19" in blob for blob in retired_payloads.values()):
            raise BuildError("compact3 E519 in retired payload")
        guard_hangul_slot_writes(
            parent,
            retired_payloads,
            allow_aux_consumers=False,
            locs=union.as_locs(),
        )
    except BuildError:
        abaoa_path = "inline_fallback"
        retired_rows = []
        retired_payloads = {}

    space_payload = SLOT_008F_AFTER
    if b"\xEC\x8D" in space_payload:
        raise BuildError("Hangul marker in restored 008F")
    guard_hangul_slot_writes(
        parent,
        {SLOT_SPACE: space_payload},
        allow_aux_consumers=False,
        locs=union.as_locs(),
    )

    hangul_glyphs = {
        char: read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        for char in ("우", "주", "아", "바", "오", "쿠")
    }

    candidate = bytearray(parent)
    allow: list[tuple[int, int]] = []
    glyph_writes: list[dict[str, Any]] = []

    preserve_code = int(preserve["code"])
    glyph_writes.append(
        paint_glyph(
            candidate,
            preserve_code,
            orig_u_glyph,
            hangul="宇",
            old_char=str(preserve["old_char"]),
            kind="e0_preserve_u",
            source_hex=f"{VANILLA_U:04X}",
            extra={"note": "copy original 宇 off E078 before painting 우"},
        )
    )
    allow.append(
        (
            compact_glyph_offset(candidate, preserve_code),
            compact_glyph_offset(candidate, preserve_code) + 16,
        )
    )

    aux_new = u16(preserve_code) + aux_old[2:]
    if len(aux_new) != 4 or aux_new[0:2] == u16(VANILLA_U):
        raise BuildError("5ED6A1 retarget failed")
    aux_start = sb + AUX_U_SITE
    candidate[aux_start : aux_start + 2] = u16(preserve_code)
    allow.append((aux_start, aux_start + 2))
    if candidate[sb + aux_term] != 0:
        raise BuildError("5ED6A1 terminator changed")

    for code, char in ((VANILLA_U, "우"), (VANILLA_JU, "주")):
        glyph_writes.append(
            paint_glyph(
                candidate,
                code,
                hangul_glyphs[char],
                hangul=char,
                old_char="宇" if code == VANILLA_U else "宙",
                kind="vanilla_e0",
                source_hex=f"{HANGUL_SOURCE[char]:04X}",
            )
        )
        allow.append(
            (compact_glyph_offset(candidate, code), compact_glyph_offset(candidate, code) + 16)
        )

    for row in one_rows:
        char = str(row["hangul"])
        glyph_writes.append(
            paint_glyph(
                candidate,
                int(row["code"]),
                hangul_glyphs[char],
                hangul=char,
                old_char=str(row["old_char"]),
                kind="onebyte",
                source_hex=str(row["hangul_source_hex"]),
                extra={
                    "script_collateral": row["script_collateral"],
                    "dict_hits": row["dict_hits"],
                    "hud_hits": row["hud_hits"],
                },
            )
        )
        allow.append(
            (
                compact_glyph_offset(candidate, int(row["code"])),
                compact_glyph_offset(candidate, int(row["code"])) + 16,
            )
        )

    ptrs_before = list(d_parent.ptrs)
    spill_ptrs, _cursor = write_dictionary_slots_spill(
        candidate,
        {SLOT_SPACE: space_payload},
        allow_aux_consumers=False,
        locs=union.as_locs(),
    )
    new_ptr = int(spill_ptrs[SLOT_SPACE])
    if new_ptr == int(ptrs_before[SLOT_SPACE]):
        raise BuildError("008F spill did not move the pointer; inplace shrink is forbidden")
    dict_bank = sb + SEG_DICT * BANK_SIZE
    phrase_start = dict_bank + new_ptr
    phrase_end = phrase_start + len(space_payload) + 1
    ptr_file = dict_bank + DICT_PTR_START + SLOT_SPACE * 2
    allow.append((phrase_start, phrase_end))
    allow.append((ptr_file, ptr_file + 2))
    spill_proof = {
        "index": f"{SLOT_SPACE:04X}",
        "token_hex": token_from_dict_index(SLOT_SPACE).hex().upper(),
        "phrase": SPACE_TEXT,
        "old_pointer": f"{int(ptrs_before[SLOT_SPACE]):04X}",
        "new_pointer": f"{new_ptr:04X}",
        "old_payload_hex": SLOT_008F_BEFORE.hex().upper(),
        "new_payload_hex": space_payload.hex().upper(),
        "write_mode": "spill_restore_vanilla_e0",
        "phrase_file_start": f"{phrase_start:06X}",
        "phrase_file_end": f"{phrase_end:06X}",
        "pointer_file": f"{ptr_file:06X}",
        "shared_with_색적우주": ["75B75E", "75BE2B"],
        "allow_aux_consumers": False,
        "consumer_retarget": False,
        "hangul_marker": False,
    }

    retired_proof: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    if abaoa_path == "f0_skeleton":
        phrases = (baoa_payload, ku_payload)
        names = ("바오아", "쿠")
        for evidence, encoded, name in zip(retired_rows, phrases, names):
            index = int(evidence["index"])
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
            start = dict_bank + pointer
            end = start + len(old_payload) + 1
            allow.append((start, end))
            retired_proof.append(
                {
                    "index": f"{index:04X}",
                    "token_hex": token_from_dict_index(index).hex().upper(),
                    "phrase": name,
                    "old_pointer": f"{pointer:04X}",
                    "old_payload_hex": old_payload.hex().upper(),
                    "new_payload_hex": encoded.hex().upper(),
                    "inplace": True,
                    "old_capacity": len(old_payload),
                    "phrase_file_start": f"{start:06X}",
                    "phrase_file_end": f"{end:06X}",
                    "historical_original_consumers": [
                        f"{historical_abs(value):06X}" for value in evidence["historical"]
                    ],
                    "current_working_consumers_before": 0,
                }
            )
        write_slots_inplace(candidate, assignments)
        baoa_token = token_from_dict_index(int(retired_rows[0]["index"]))
        ku_token = token_from_dict_index(int(retired_rows[1]["index"]))
        abaoa_body = bytes([char_to_code["아"], 0x01]) + baoa_token + bytes([0x01]) + ku_token
    else:
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
    for code in FAILED_ONEBYTE:
        if code in abaoa_body:
            raise BuildError("failed e0 1-byte code in 아 바오아 쿠 body")

    applied: list[dict[str, Any]] = []
    for logical in SPACE_SITES:
        old, terminator = payload_at(parent, logical)
        if old != SPACE_BEFORE:
            raise BuildError(f"{logical:06X} 우주 token drifted before write")
        applied.append(
            {
                "abs": f"{logical:06X}",
                "phrase": SPACE_TEXT,
                "before_hex": old.hex().upper(),
                "after_hex": SPACE_BEFORE.hex().upper(),
                "slot": f"{SLOT_SPACE:04X}",
                "token_hex": SPACE_BEFORE.hex().upper(),
                "token_unchanged": True,
            }
        )
        if candidate[sb + terminator] != 0:
            raise BuildError(f"{logical:06X} terminator changed")

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
                "encoding": abaoa_path,
                "token_hex": abaoa_body.hex().upper(),
            }
        )

    parent_ptrs = find_f58ce5(parent)
    if not parent_ptrs:
        raise BuildError("no F58CE5 pointers in banks 64-66")
    checksum = update_ws_checksum(candidate)
    allow.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    if find_f58ce5(result) != parent_ptrs:
        raise BuildError("bank 64-66 F58CE5 pointers changed")
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allow)]
    if unexpected:
        raise BuildError(
            "diff outside allowlist: "
            + ", ".join(f"{lo:08X}-{hi:08X}" for lo, hi in unexpected)
        )

    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)
    if bytes(d_result.raw_entry(SLOT_SPACE)) != space_payload:
        raise BuildError("slot 008F not restored to E078E046")
    if d_result.expand_index(EMPTY_STOCK, tbl) != "":
        raise BuildError("00A9 collateral")
    if payload_at(result, 0x75B3CA)[0] != payload_at(parent, 0x75B3CA)[0]:
        raise BuildError("범용 record changed")
    if payload_at(result, 0x75B457)[0] != payload_at(parent, 0x75B457)[0]:
        raise BuildError("명중 보정 record changed")
    for logical in SEKI_SITES:
        if payload_at(result, logical)[0] != payload_at(parent, logical)[0]:
            raise BuildError(f"색적우주 {logical:06X} site changed")
    if bytes(result[compact_glyph_offset(result, VANILLA_U) : compact_glyph_offset(result, VANILLA_U) + 16]) != hangul_glyphs["우"]:
        raise BuildError("E078 glyph is not 우")
    if bytes(result[compact_glyph_offset(result, VANILLA_JU) : compact_glyph_offset(result, VANILLA_JU) + 16]) != hangul_glyphs["주"]:
        raise BuildError("E046 glyph is not 주")
    if bytes(result[compact_glyph_offset(result, preserve_code) : compact_glyph_offset(result, preserve_code) + 16]) != orig_u_glyph:
        raise BuildError("preserved 宇 glyph mismatch")
    aux_got, aux_got_term = payload_at(result, AUX_U_SITE)
    if aux_got[0:2] == u16(VANILLA_U):
        raise BuildError("5ED6A1 still uses E078")
    if aux_got != aux_new or aux_got_term != aux_term:
        raise BuildError("5ED6A1 retarget mismatch")
    if decode_mapped(aux_got, code_to_char, d_result, tbl) != "宇人質":
        raise BuildError(
            f"5ED6A1 decode {decode_mapped(aux_got, code_to_char, d_result, tbl)!r}"
        )

    result_union = build_reference_union(
        original,
        result,
        regions=("script", "name75", "aux"),
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    if abaoa_path == "f0_skeleton":
        keepers = set(ABAOA_SITES)
        for row in retired_proof:
            refuse_working_consumers(result_union, int(row["index"], 16), keepers)
        if bytes(d_result.raw_entry(int(retired_rows[0]["index"]))) != baoa_payload:
            raise BuildError("바오아 slot payload mismatch")
        if bytes(d_result.raw_entry(int(retired_rows[1]["index"]))) != ku_payload:
            raise BuildError("쿠 slot payload mismatch")

    for logical in SPACE_SITES:
        payload, terminator = payload_at(result, logical)
        rendered = decode_mapped(payload, code_to_char, d_result, tbl)
        if payload != SPACE_BEFORE or rendered != SPACE_TEXT:
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
        if terminator != {0x75E58C: 0x75E593, 0x75BD77: 0x75BD7E}[logical]:
            raise BuildError(f"{logical:06X} terminator not original")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if MAIN.is_file() and MAIN.read_bytes() != live_main_before:
        raise BuildError("live main TIP mutated")
    if MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("live SaveRAM mutated")
    if sha256(OUT_SAVE.read_bytes()) != EXPECTED_SAVE_SHA256:
        raise BuildError("pair sav SHA mismatch")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terrain_space_abaoaqu_vanilla_font_candidate.py",
        "status": "candidate_static_verified_needs_target_screen_check",
        "ok": True,
        "main_tip_modified": False,
        "parent": {
            **identity(parent_path, parent),
            "live_main_path": str(MAIN.relative_to(ROOT)).replace("\\", "/"),
            "live_main_sha256": sha256(live_main_before) if live_main_before else None,
            "live_main_is_pinned_parent": sha256(live_main_before) == EXPECTED_MAIN_SHA256
            if live_main_before
            else False,
        },
        "candidate": {
            **identity(OUT_ROM, result),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "source": str(MAIN_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "policy": "copy_live_main_sav_at_build_time",
            "copied_latest": True,
            "sha256": sha256(OUT_SAVE.read_bytes()),
            "matches_live_pin": True,
        },
        "failed_previous_candidates": [
            {
                "path": "out/patch/terrain_space_abaoaqu_compact_glyph_candidate.wsc",
                "why": "E5xx compact steal; this HUD special-cases E5 as ext3/compact3",
            },
            {
                "path": "out/patch/terrain_space_abaoaqu_e0_onebyte_candidate.wsc",
                "why": "unused E009+F050 retarget and 1-byte 59/BC/B2/C2; do not reuse",
            },
        ],
        "cause": {
            "hud": "terrain-info top line expands F0 then blits 1-byte kana / E0 kanji",
            "space": "keep F08F; restore 008F to E078 E046; paint 우/주 on those glyphs",
            "abaoa": (
                "7-byte 아 01 F0(바오아) 01 F0(쿠) with fullwidth 01, "
                f"path={abaoa_path}"
            ),
            "색적우주": "75B75E/75BE2B keep F506 F08F; glyph paint makes 색적우주",
            "u_hostage": "5ED6A1 E078 retargeted to unused E0xx holding original 宇",
        },
        "glyphs": glyph_writes,
        "dictionary": {
            "slot_008F": spill_proof,
            "abaoa_path": abaoa_path,
            "selected_retired_slots": len(retired_proof),
            "proof": retired_proof,
        },
        "preserve_u": {
            "site": f"{AUX_U_SITE:06X}",
            "before_hex": AUX_U_BEFORE.hex().upper(),
            "after_hex": aux_new.hex().upper(),
            "preserve_code": f"{preserve_code:04X}",
            "old_char": preserve["old_char"],
        },
        "records": applied,
        "onebyte_codes": {
            str(row["hangul"]): {
                "code": row["code_hex"],
                "old_char": row["old_char"],
                "dict_hits": row["dict_hits"],
                "hud_hits": row["hud_hits"],
                "script_collateral": row["script_collateral"],
            }
            for row in one_rows
        },
        "verification": {
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "F08F_unchanged": True,
            "범용_preserved": True,
            "명중_보정_preserved": True,
            "색적우주_sites_preserved": True,
            "F58CE5_pointers_preserved": True,
            "compact3_e519_absent": True,
            "failed_onebyte_absent": True,
            "unaccounted_changed_bytes": 0,
            "diff_runs": len(runs),
            "diff_bytes": sum(hi - lo for lo, hi in runs),
            "f58ce5_hits": [f"{off - sb:06X}" for off in parent_ptrs],
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
                "abaoa_path": abaoa_path,
                "space_payload": space_payload.hex().upper(),
                "abaoa_hex": abaoa_body.hex().upper(),
                "preserve_u": f"{preserve_code:04X}",
                "onebyte": [
                    {"hangul": row["hangul"], "code": row["code_hex"]}
                    for row in one_rows
                ],
                "retired": [row["index"] for row in retired_proof],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
