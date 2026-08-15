#!/usr/bin/env python3
"""Build a minimal, candidate-only E5 18 bank-0x21 alias probe.

This does not introduce a new text token or a second parser.  The accepted E5 18
walkers remain byte-exact.  Only the accepted ext3 leaf is redirected to a new
leaf that preserves the old mapping and adds one previously-unused alias range:

    E5 18 06 01 .. E5 18 0F FF -> expansion bank 0x21, local 0x0001..0x09FF

The parent ROM has no raw references in that range.  The probe stores a freshly
reviewed Korean rendering of the actual first A Baoa Qu stage line in bank
0x21/local 1 and rewrites only record 590005 while preserving its `17 34 18`
event/dialogue prefix, fixed payload length and terminator.  This exercises the
new bank mapping in the exact user-captured scene without using legacy machine
translation text.

Candidate only: never overwrites the main TIP or main SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from hangul_marker import marker_code
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import (
    BANK_MAP_OFF,
    BANK_MAP_SEG,
    BANK_SAVE_OFF,
    CODE_SEG_7A,
    EXP3_PTR_OFF,
    EXT_CAVE_SEG,
    FAD0_OFF,
    FAD0_SEG,
    HANGUL_FAR_STUB,
    INDEX_BASE,
    LEAF,
    LEAF_CONTINUE,
    LEAF_EXPECT,
    SITE1,
    SITE2_FIXED,
    far_jmp,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_ROM = ROOT / "out/patch/ext3_bank21_probe_candidate.wsc"
OUT_SAVE = ROOT / "sram/ext3_bank21_probe_candidate.sav"
OUT_REPORT = ROOT / "out/patch/ext3_bank21_probe_report.json"

EXPECTED_MAIN_SHA256 = "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825"
ROM_SIZE = 16_777_216

# Accepted runtime layout in the current TIP.  The two appended walkers include
# the previously accepted narrow padding-skip fix and must not change.
OLD_LEAF_START = 0x7FFD91
OLD_LEAF_END = 0x7FFDF8
WALKER1_START = 0x7FFDF8
WALKER2_START = 0x7FFE4A
FREE_CAVE_START = 0x7FFE9D
FREE_CAVE_END = 0x7FFFF0  # exact end of the verified contiguous FF run
EXPECTED_OLD_LEAF_SHA256 = "f5151b796364d731de0e61bf1049e0e3c2b2a0697fc3a42414d3aa0f9addc1de"
EXPECTED_WALKER1_SHA256 = "62bd35edfaac534b14d2a11de843eb2ada86920dab849528fcefbdffb87f9099"
EXPECTED_WALKER2_SHA256 = "95e2a524111e9a5a7191141d386b862f87d0aa865ffdfd109dcee04b0cb30652"
EXPECTED_SITE1_HOOK = bytes.fromhex("EAF8FD00F0")
EXPECTED_SITE2_HOOK = bytes.fromhex("EA4AFE00F0")
EXPECTED_LEAF_HOOK = bytes.fromhex("EA91FD00F090")

# Actual first A Baoa Qu stage line shown in the user's screenshot.
# 17 34 18 is the event/dialogue prefix; the following 17-byte body renders
# `……思ったより連邦の兵力が少ないな。`.
TARGET_ABS = 0x590005
TARGET_PREFIX = bytes.fromhex("173418")
TARGET_BODY_CAPACITY = 17
TARGET_CAPACITY = len(TARGET_PREFIX) + TARGET_BODY_CAPACITY
EXPECTED_TARGET_PAYLOAD = bytes.fromhex(
    "173418F19194F70E27F42505E036571788F7F90A"
)
EXPECTED_TARGET_TERMINATOR = 0x00
PROBE_TEXT = "……생각보다 연방군이 적구나。"
PROBE_TOKEN = bytes.fromhex("E5180601")
PROBE_LOCAL = 0x0001

BANK21_SEG = 0x21
BANK21_ALIAS_RAW_START = 0x0600
BANK21_ALIAS_RAW_END_EXCLUSIVE = 0x1000
BANK21_LOCAL_COUNT = BANK21_ALIAS_RAW_END_EXCLUSIVE - BANK21_ALIAS_RAW_START
BANK21_POINTER_COUNT = 0x1000
BANK21_EMPTY_AT = BANK21_POINTER_COUNT * 2


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def encode_probe_phrase(tbl: Tbl) -> bytes:
    normalized = normalize_ko_text(PROBE_TEXT)
    payload = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or not payload or b"\x00" in payload:
        raise BuildError("probe Korean phrase is not safely encodable")
    return payload


def patch_rel8(buf: bytearray, at: int, target: int) -> None:
    displacement = target - (at + 2)
    if not -128 <= displacement <= 127:
        raise BuildError(f"rel8 out of range: {displacement}")
    buf[at + 1] = displacement & 0xFF


def build_bank21_leaf() -> bytes:
    """Accepted ext3 leaf plus one bank21 alias branch; no new WRAM state."""
    out = bytearray()

    # Preserve the accepted flag gate and stock fallback behavior.
    out += b"\x80\x3E\xFA\x19\x01"  # cmp byte ptr [19FA],1
    not_flag_at = len(out)
    out += b"\x75\x00"  # jne fallback
    out += b"\xC6\x06\xFA\x19\x00"  # mov byte ptr [19FA],0
    out += LEAF_EXPECT
    out += b"\x51\x52\x56\x57"
    out += b"\x89\x46\xFC\x89\x5E\xFE"
    out += b"\x8B\xFA"  # mov di,dx
    out += b"\x8B\x36\xF8\x19"  # mov si,[19F8]
    out += b"\x9A" + struct.pack("<HH", BANK_SAVE_OFF, BANK_MAP_SEG)
    out += b"\x50"  # save previous ROM bank

    # BX = raw xx:yy value.  The old path is unchanged outside 0600..0FFF.
    out += b"\x89\xF0"  # mov ax,si
    out += b"\x2D" + struct.pack("<H", INDEX_BASE)  # sub ax,1000
    out += b"\x89\xC3"  # mov bx,ax
    out += b"\x81\xFB" + struct.pack("<H", BANK21_ALIAS_RAW_START)
    below_alias_at = len(out)
    out += b"\x72\x00"  # jb normal
    out += b"\x81\xFB" + struct.pack("<H", BANK21_ALIAS_RAW_END_EXCLUSIVE)
    above_alias_at = len(out)
    out += b"\x73\x00"  # jae normal

    # Reserved range: rebase local 0600..0FFF to 0000..09FF, map bank 21.
    out += b"\x81\xEB" + struct.pack("<H", BANK21_ALIAS_RAW_START)
    out += b"\xB8" + struct.pack("<H", BANK21_SEG)
    mapped_at = len(out)
    jump_mapped_at = len(out)
    out += b"\xEB\x00"

    # Original mapping: bank = 11 + (raw >> 12), local = raw & 0FFF.
    normal_at = len(out)
    patch_rel8(out, below_alias_at, normal_at)
    patch_rel8(out, above_alias_at, normal_at)
    out += b"\x89\xD8"  # mov ax,bx
    out += b"\xB1\x0C\xD3\xE8"  # mov cl,12; shr ax,cl
    out += b"\x04\x11"  # add al,11

    map_bank_at = len(out)
    patch_rel8(out, jump_mapped_at, map_bank_at)
    out += b"\x53"  # preserve local across bank mapper
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xBB\x00\x30\x8E\xC3"  # es=3000
    out += b"\x5E"  # pop si
    out += b"\x81\xE6\xFF\x0F"  # local &= 0FFF
    out += b"\xD1\xE6"  # pointer index * 2
    out += b"\x26\x8B\x84" + struct.pack("<H", EXP3_PTR_OFF)
    out += b"\x9A" + struct.pack("<HH", FAD0_OFF, FAD0_SEG)
    out += b"\x89\x46\xF8\x89\x5E\xFA"
    out += far_jmp(0x0743, CODE_SEG_7A)

    fallback_at = len(out)
    patch_rel8(out, not_flag_at, fallback_at)
    out += LEAF_EXPECT
    out += far_jmp(LEAF_CONTINUE & 0xFFFF, CODE_SEG_7A)

    if FREE_CAVE_START + len(out) > FREE_CAVE_END:
        raise BuildError(f"new leaf does not fit: {len(out)} bytes")
    if mapped_at <= 0:
        raise AssertionError("unreachable assembler guard")
    return bytes(out)


def format_bank21(raw_phrase: bytes) -> tuple[bytes, dict[str, Any]]:
    if not raw_phrase or b"\x00" in raw_phrase:
        raise BuildError("source phrase is empty or contains an embedded NUL")
    bank = bytearray([0xFF] * BANK_SIZE)
    for local in range(BANK21_POINTER_COUNT):
        struct.pack_into("<H", bank, local * 2, BANK21_EMPTY_AT)
    bank[BANK21_EMPTY_AT] = 0
    phrase_at = BANK21_EMPTY_AT + 1
    phrase_end = phrase_at + len(raw_phrase)
    if phrase_end + 1 > BANK_SIZE:
        raise BuildError("probe phrase does not fit bank21")
    bank[phrase_at:phrase_end] = raw_phrase
    bank[phrase_end] = 0
    struct.pack_into("<H", bank, PROBE_LOCAL * 2, phrase_at)
    return bytes(bank), {
        "segment": f"{BANK21_SEG:02X}",
        "pointer_count": BANK21_POINTER_COUNT,
        "alias_local_count": BANK21_LOCAL_COUNT,
        "empty_at": f"{BANK21_EMPTY_AT:04X}",
        "probe_local": f"{PROBE_LOCAL:04X}",
        "probe_phrase_at": f"{phrase_at:04X}",
        "probe_phrase_end_exclusive": f"{phrase_end:04X}",
        "phrase_room_after": BANK_SIZE - (phrase_end + 1),
    }


def diff_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(before):
        if before[cursor] == after[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < len(before) and before[cursor] != after[cursor]:
            cursor += 1
        rows.append(
            {
                "start": f"{start:07X}",
                "end_exclusive": f"{cursor:07X}",
                "length": cursor - start,
                "before_hex": before[start:min(cursor, start + 32)].hex().upper(),
                "after_hex": after[start:min(cursor, start + 32)].hex().upper(),
            }
        )
    return rows


def in_any(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def reserved_token_hits(data: bytes) -> list[int]:
    hits: list[int] = []
    for pos in range(len(data) - 3):
        if data[pos] != 0xE5 or data[pos + 1] != 0x18:
            continue
        raw = (data[pos + 2] << 8) | data[pos + 3]
        if BANK21_ALIAS_RAW_START <= raw < BANK21_ALIAS_RAW_END_EXCLUSIVE and data[pos + 3] != 0:
            hits.append(pos)
    return hits


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file():
        raise BuildError("main SaveRAM is missing")
    # SaveRAM is mutable validation state.  Always pair the candidate with the
    # latest current main SaveRAM; never gate it on a historical fixed hash.
    main_save = MAIN_SAVE.read_bytes()
    main_save_sha256 = sha256(main_save)

    sb = stock_base(parent)
    old_leaf = parent[sb + OLD_LEAF_START:sb + OLD_LEAF_END]
    walker1 = parent[sb + WALKER1_START:sb + WALKER2_START]
    walker2 = parent[sb + WALKER2_START:sb + FREE_CAVE_START]
    if sha256(old_leaf) != EXPECTED_OLD_LEAF_SHA256:
        raise BuildError("accepted ext3 leaf drifted")
    if sha256(walker1) != EXPECTED_WALKER1_SHA256:
        raise BuildError("accepted walker1 drifted")
    if sha256(walker2) != EXPECTED_WALKER2_SHA256:
        raise BuildError("accepted walker2 drifted")
    if parent[sb + SITE1:sb + SITE1 + 5] != EXPECTED_SITE1_HOOK:
        raise BuildError("site1 hook drifted")
    if parent[sb + SITE2_FIXED:sb + SITE2_FIXED + 5] != EXPECTED_SITE2_HOOK:
        raise BuildError("site2 hook drifted")
    if parent[sb + LEAF:sb + LEAF + 6] != EXPECTED_LEAF_HOOK:
        raise BuildError("leaf hook drifted")
    if not all(byte == 0xFF for byte in parent[sb + FREE_CAVE_START:sb + FREE_CAVE_END]):
        raise BuildError("new leaf cave is not empty")

    bank21_file = BANK21_SEG * BANK_SIZE
    if not all(byte == 0xFF for byte in parent[bank21_file:bank21_file + BANK_SIZE]):
        raise BuildError("expansion bank21 is not empty")
    parent_reserved_hits = reserved_token_hits(parent)
    if parent_reserved_hits:
        raise BuildError(f"reserved E5 18 alias range is already referenced: {parent_reserved_hits[:8]}")

    target_file = sb + TARGET_ABS
    target_payload = parent[target_file:target_file + TARGET_CAPACITY]
    target_terminator = parent[target_file + TARGET_CAPACITY]
    if target_payload != EXPECTED_TARGET_PAYLOAD or target_terminator != EXPECTED_TARGET_TERMINATOR:
        raise BuildError("A Baoa Qu probe record drifted")

    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    if ext3_meta.get("compact3") is not False:
        raise BuildError("accepted metadata no longer has compact3 disabled")
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    tbl = Tbl.load(TBL_PATH)
    raw_phrase = encode_probe_phrase(tbl)
    rendered_phrase = dictionary.expand(raw_phrase, tbl)
    if rendered_phrase != normalize_ko_text(PROBE_TEXT):
        raise BuildError("probe Korean phrase rendering drifted")

    candidate = bytearray(parent)
    new_leaf = build_bank21_leaf()
    candidate[sb + FREE_CAVE_START:sb + FREE_CAVE_START + len(new_leaf)] = new_leaf
    candidate[sb + LEAF:sb + LEAF + 6] = far_jmp(FREE_CAVE_START & 0xFFFF, EXT_CAVE_SEG) + b"\x90"

    bank21, bank_meta = format_bank21(raw_phrase)
    candidate[bank21_file:bank21_file + BANK_SIZE] = bank21
    candidate[target_file:target_file + TARGET_CAPACITY] = (
        TARGET_PREFIX
        + PROBE_TOKEN
        + bytes([0x01]) * (TARGET_BODY_CAPACITY - len(PROBE_TOKEN))
    )
    if candidate[target_file + TARGET_CAPACITY] != EXPECTED_TARGET_TERMINATOR:
        raise BuildError("probe terminator changed")

    checksum = update_ws_checksum(candidate)

    # Self-audit the intended change envelope before writing anything.
    allowed = [
        (bank21_file, bank21_file + BANK_SIZE),
        (sb + FREE_CAVE_START, sb + FREE_CAVE_START + len(new_leaf)),
        (sb + LEAF, sb + LEAF + 6),
        (target_file, target_file + TARGET_CAPACITY),
        (len(parent) - 2, len(parent)),
    ]
    unaccounted = [
        offset
        for offset, (before, after) in enumerate(zip(parent, candidate))
        if before != after and not in_any(offset, allowed)
    ]
    if unaccounted:
        raise BuildError(f"unaccounted changed bytes: {unaccounted[:16]}")
    if candidate[sb + WALKER1_START:sb + FREE_CAVE_START] != parent[sb + WALKER1_START:sb + FREE_CAVE_START]:
        raise BuildError("accepted walkers changed")
    if candidate[sb + OLD_LEAF_START:sb + OLD_LEAF_END] != old_leaf:
        raise BuildError("accepted old leaf body changed")
    for seg in range(0x11, 0x21):
        start = seg * BANK_SIZE
        if candidate[start:start + BANK_SIZE] != parent[start:start + BANK_SIZE]:
            raise BuildError(f"accepted ext3 bank {seg:02X} changed")

    candidate_reserved_hits = reserved_token_hits(bytes(candidate))
    expected_probe_hit = target_file + len(TARGET_PREFIX)
    if candidate_reserved_hits != [expected_probe_hit]:
        raise BuildError(
            f"candidate alias references differ: {candidate_reserved_hits[:8]} != {[expected_probe_hit]}"
        )

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(candidate)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ext3_bank21_probe_candidate.py",
        "status": "candidate_static_verified",
        "ok": True,
        "parent": {
            "path": str(MAIN.relative_to(ROOT)),
            "size": len(parent),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "size": len(candidate),
            "sha256": sha256(candidate),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "main_path": str(MAIN_SAVE.relative_to(ROOT)),
            "candidate_path": str(OUT_SAVE.relative_to(ROOT)),
            "main_sha256": main_save_sha256,
            "candidate_sha256": sha256(OUT_SAVE.read_bytes()),
            "copied_byte_exact": OUT_SAVE.read_bytes() == main_save,
            "policy": "candidate SaveRAM is test-only and must never be promoted back to main",
        },
        "runtime": {
            "existing_token": "E5 18 xx yy",
            "new_token_added": False,
            "existing_walkers_byte_exact": True,
            "existing_old_leaf_body_byte_exact": True,
            "old_leaf": f"{OLD_LEAF_START:06X}",
            "new_leaf": f"{FREE_CAVE_START:06X}",
            "new_leaf_length": len(new_leaf),
            "new_leaf_sha256": sha256(new_leaf),
            "leaf_hook_before": EXPECTED_LEAF_HOOK.hex().upper(),
            "leaf_hook_after": bytes(candidate[sb + LEAF:sb + LEAF + 6]).hex().upper(),
            "alias": {
                "raw_start": f"{BANK21_ALIAS_RAW_START:04X}",
                "raw_end_exclusive": f"{BANK21_ALIAS_RAW_END_EXCLUSIVE:04X}",
                "token_start": "E5 18 06 01",
                "token_end": "E5 18 0F FF",
                "expansion_bank": f"{BANK21_SEG:02X}",
                "local_start": "0001",
                "local_end": "09FF",
                "usable_tokens": 2550,
                "parent_raw_reference_count": len(parent_reserved_hits),
                "candidate_raw_reference_count": len(candidate_reserved_hits),
            },
            "forbidden_designs_not_used": [
                "E5 2F character portal",
                "second text parser",
                "new WRAM flag/index",
                "compact3 E5 19",
                "bank21 direct character dispatcher",
            ],
        },
        "bank21": bank_meta,
        "probe": {
            "record_abs": f"{TARGET_ABS:06X}",
            "scenario": "A Baoa Qu stage first dialogue in logical bank59",
            "capacity": TARGET_CAPACITY,
            "prefix_hex": TARGET_PREFIX.hex().upper(),
            "body_capacity": TARGET_BODY_CAPACITY,
            "source_payload_before": EXPECTED_TARGET_PAYLOAD.hex().upper(),
            "probe_token": PROBE_TOKEN.hex().upper(),
            "padding_preserved": True,
            "terminator_preserved": True,
            "translation_source": "fresh_llm_reviewed_for_user_captured_line",
            "raw_phrase_sha256": sha256(raw_phrase),
            "raw_phrase_hex": raw_phrase.hex().upper(),
            "rendered_text": rendered_phrase,
            "fresh_direct_encoding": True,
        },
        "invariance": {
            "ext3_banks_11_20_byte_exact": True,
            "site1_hook_byte_exact": True,
            "site2_hook_byte_exact": True,
            "old_leaf_body_byte_exact": True,
            "walkers_byte_exact": True,
            "main_rom_untouched": sha256(MAIN.read_bytes()) == EXPECTED_MAIN_SHA256,
            "main_save_untouched": sha256(MAIN_SAVE.read_bytes()) == main_save_sha256,
            "unaccounted_changed_bytes": len(unaccounted),
        },
        "diff_runs": diff_runs(parent, bytes(candidate)),
        "test_requirements": [
            "Enter the A Baoa Qu stage and confirm the first line `……思ったより連邦の兵力が少ないな。` renders as `……생각보다 연방군이 적구나。`.",
            "Advance through multiple dialogue windows and into battle without Event Error or glyph corruption.",
            "Save, fully restart the emulator, reload, and confirm progress and text remain normal.",
            "Do not copy the candidate .sav over the main .sav after testing.",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
