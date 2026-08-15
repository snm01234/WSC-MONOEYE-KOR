#!/usr/bin/env python3
"""Candidate-only E5 2F character dictionary dispatcher.

This module deliberately leaves the accepted E5 18 ext3 cave byte-exact.  New
hooks first test E5 2F in a separate free code run; non-matching input far-jumps
to the original ext3 walkers/leaf without altering their state or code.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from patch_3byte_dict_token import (
    BANK_MAP_OFF,
    BANK_MAP_SEG,
    BANK_SAVE_OFF,
    CODE_SEG_7A,
    EXT_CAVE_SEG,
    FAD0_OFF,
    FAD0_SEG,
    HANGUL_FAR_STUB,
    HOOK_LEN,
    LEAF,
    LEAF_CLEANUP,
    LEAF_EXPECT,
    LEAF_FAR_TRAMP,
    SITE1,
    SITE1_MOVES,
    SITE1_RETURN,
    SITE2_MOVES,
    WRAM_FLAG,
    WRAM_INDEX,
    _patch_rel8,
    far_jmp,
    find_site2,
    sab,
)

CHAR_MAGIC = 0xE52F
CHAR_SEG = 0x21
CHAR_CAVE = 0x7FFE9D
CHAR_CAVE_END = 0x7FFFF0
CHAR_CAVE_MAX = CHAR_CAVE_END - CHAR_CAVE


class CharacterPortalRuntimeError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _emit_dispatcher(
    out: bytearray,
    *,
    moves: bytes,
    return_ip: int,
    old_walker: int,
    name: str,
    parts: dict[str, int],
) -> None:
    parts[name] = len(out)
    out += b"\x81\xFA" + struct.pack("<H", CHAR_MAGIC)
    char_je = len(out)
    out += b"\x74\x00"
    out += far_jmp(old_walker & 0xFFFF, EXT_CAVE_SEG)

    char_path = len(out)
    _patch_rel8(out, char_je, char_path)
    out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x27"
    out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x07"
    out += b"\x89\xC3"
    out += b"\x89\x1E" + struct.pack("<H", WRAM_INDEX)
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x02"
    out += b"\xBA\x00\xF0"
    out += moves
    out += b"\x9A" + struct.pack("<HH", HANGUL_FAR_STUB & 0xFFFF, CODE_SEG_7A)
    out += far_jmp(return_ip & 0xFFFF, CODE_SEG_7A)


def build_dispatch_handlers(
    *,
    site2_return: int,
    old_walker1: int,
    old_walker2: int,
    old_leaf: int,
) -> tuple[bytes, dict[str, int]]:
    out = bytearray()
    parts: dict[str, int] = {}
    _emit_dispatcher(
        out,
        moves=SITE1_MOVES,
        return_ip=SITE1_RETURN,
        old_walker=old_walker1,
        name="walker1_dispatch",
        parts=parts,
    )
    _emit_dispatcher(
        out,
        moves=SITE2_MOVES,
        return_ip=site2_return,
        old_walker=old_walker2,
        name="walker2_dispatch",
        parts=parts,
    )

    parts["leaf_dispatch"] = len(out)
    out += b"\x80\x3E" + struct.pack("<H", WRAM_FLAG) + b"\x02"
    char_je = len(out)
    out += b"\x74\x00"
    out += far_jmp(old_leaf & 0xFFFF, EXT_CAVE_SEG)

    char_leaf = len(out)
    _patch_rel8(out, char_je, char_leaf)
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x00"
    out += LEAF_EXPECT
    out += b"\x51\x52\x56\x57"
    out += b"\x89\x46\xFC\x89\x5E\xFE"
    out += b"\x8B\xFA"
    out += b"\x8B\x36" + struct.pack("<H", WRAM_INDEX)
    out += b"\x9A" + struct.pack("<HH", BANK_SAVE_OFF, BANK_MAP_SEG)
    out += b"\x50"
    out += b"\x89\xF3"  # mov bx,si: local 0x0101..0x0FFF
    out += b"\xB0" + bytes((CHAR_SEG,))
    out += b"\x53"
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xBB\x00\x30\x8E\xC3"
    out += b"\x5E"
    out += b"\x81\xE6\xFF\x0F"
    out += b"\xD1\xE6"
    out += b"\x26\x8B\x84\x00\x00"
    out += b"\x9A" + struct.pack("<HH", FAD0_OFF, FAD0_SEG)
    out += b"\x89\x46\xF8\x89\x5E\xFA"

    stream_entry = len(out)
    out += b"\xC4\x5E\xF8\x26\x80\x3F\x00\x75\x00"
    stream_loop = len(out)
    out += bytes.fromhex(
        "ff46f8 c45ef8 4b 268a17 32f6 81fae000 7212 "
        "b108 d3e2 ff46f8 c45ef8 4b 268a1f 32ff 0bd3 "
        "8b46fc 8b5efe 8bca 8bd7"
    )
    out += b"\x9A" + struct.pack("<HH", LEAF_FAR_TRAMP & 0xFFFF, CODE_SEG_7A)
    out += bytes.fromhex("c45ef8 26803f00")
    stream_repeat = len(out)
    out += b"\x75\x00"
    _patch_rel8(out, stream_entry + 7, stream_loop)
    _patch_rel8(out, stream_repeat, stream_loop)

    out += b"\x58"
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\x5F\x5E\x5A\x59"
    out += far_jmp(LEAF_CLEANUP & 0xFFFF, CODE_SEG_7A)

    if len(out) > CHAR_CAVE_MAX:
        raise CharacterPortalRuntimeError(
            f"character dispatcher cave too large: {len(out)} > {CHAR_CAVE_MAX}"
        )
    return bytes(out), parts


def install_character_portal_runtime(
    rom: bytearray,
    parent: bytes,
    *,
    ext3_meta_path: Path,
) -> dict[str, Any]:
    meta = json.loads(ext3_meta_path.read_text(encoding="utf-8"))
    old_parts = meta.get("parts") or {}
    try:
        old_walker1 = int(str(old_parts["walker1"]), 16)
        old_walker2 = int(str(old_parts["walker2"]), 16)
        old_leaf = int(str(old_parts["leaf"]), 16)
    except Exception as exc:
        raise CharacterPortalRuntimeError("accepted ext3 handler metadata is incomplete") from exc

    site2, site2_return = find_site2(parent)
    blob, parts = build_dispatch_handlers(
        site2_return=site2_return,
        old_walker1=old_walker1,
        old_walker2=old_walker2,
        old_leaf=old_leaf,
    )
    cave_file = sab(parent, CHAR_CAVE)
    region_before = bytes(parent[cave_file : cave_file + CHAR_CAVE_MAX])
    if len(region_before) != CHAR_CAVE_MAX or any(byte != 0xFF for byte in region_before):
        raise CharacterPortalRuntimeError("dedicated character dispatcher cave is not free")

    # Bind the complete accepted old cave before touching hooks.  This is the
    # strongest preservation check for E5 18 and the later prefix helper that
    # shares the old code area.
    old_cave_start = sab(parent, int(str(meta.get("cave") or "7FFD10"), 16))
    old_cave_end = cave_file
    old_cave_before = bytes(parent[old_cave_start:old_cave_end])

    rom[cave_file : cave_file + len(blob)] = blob
    walker1 = (CHAR_CAVE + parts["walker1_dispatch"]) & 0xFFFF
    walker2 = (CHAR_CAVE + parts["walker2_dispatch"]) & 0xFFFF
    leaf = (CHAR_CAVE + parts["leaf_dispatch"]) & 0xFFFF
    rom[sab(parent, SITE1) : sab(parent, SITE1) + HOOK_LEN] = far_jmp(walker1, EXT_CAVE_SEG)
    rom[sab(parent, site2) : sab(parent, site2) + HOOK_LEN] = far_jmp(walker2, EXT_CAVE_SEG)
    rom[sab(parent, LEAF) : sab(parent, LEAF) + 6] = far_jmp(leaf, EXT_CAVE_SEG) + b"\x90"

    if bytes(rom[old_cave_start:old_cave_end]) != old_cave_before:
        raise CharacterPortalRuntimeError("accepted ext3/prefix cave changed")
    for logical, length, name in (
        (HANGUL_FAR_STUB, 4, "hangul_far_stub"),
        (LEAF_FAR_TRAMP, 9, "leaf_far_trampoline"),
    ):
        start = sab(parent, logical)
        if bytes(rom[start : start + length]) != parent[start : start + length]:
            raise CharacterPortalRuntimeError(f"{name} changed unexpectedly")

    new_region = bytes(rom[cave_file : cave_file + len(blob)])
    semantics = {
        "character_magic_dispatchers_two": new_region.count(bytes.fromhex("81FA2FE5")) == 2,
        "character_flag2_writes_two": new_region.count(
            b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x02"
        ) == 2,
        "character_flag2_leaf_check_one": new_region.count(
            b"\x80\x3E" + struct.pack("<H", WRAM_FLAG) + b"\x02"
        ) == 1,
        "fixed_bank21_map_one": new_region.count(b"\xB0\x21") == 1,
        "nonmatch_far_jumps_to_old_handlers": all(
            far_jmp(address & 0xFFFF, EXT_CAVE_SEG) in new_region
            for address in (old_walker1, old_walker2, old_leaf)
        ),
        "compact3_magic_not_added": bytes.fromhex("81FA19E5") not in new_region,
        "old_cave_byte_exact": bytes(rom[old_cave_start:old_cave_end]) == old_cave_before,
    }
    if not all(semantics.values()):
        raise CharacterPortalRuntimeError(f"dispatcher semantic fingerprint failed: {semantics}")

    return {
        "magic": f"{CHAR_MAGIC:04X}",
        "encoding": "E5 2F hh ll -> expansion bank 0x21 local slot 0xhhll",
        "token_bytes": 4,
        "character_segment": f"{CHAR_SEG:02X}",
        "cave": f"{CHAR_CAVE:06X}",
        "cave_end_exclusive": f"{CHAR_CAVE_END:06X}",
        "cave_len": len(blob),
        "cave_capacity": CHAR_CAVE_MAX,
        "parts": {name: f"{CHAR_CAVE + offset:06X}" for name, offset in parts.items()},
        "site1": f"{SITE1:06X}",
        "site2": f"{site2:06X}",
        "leaf": f"{LEAF:06X}",
        "old_handlers": {
            "walker1": f"{old_walker1:06X}",
            "walker2": f"{old_walker2:06X}",
            "leaf": f"{old_leaf:06X}",
            "preserved_range": [f"{int(str(meta.get('cave') or '7FFD10'), 16):06X}", f"{CHAR_CAVE:06X}"],
            "preserved_sha256": _sha256(old_cave_before),
        },
        "wram_index": f"{WRAM_INDEX:04X}",
        "wram_flag": f"{WRAM_FLAG:04X}",
        "flag_contract": {"existing_ext3": 1, "character_bank": 2},
        "semantic_checks": semantics,
        "fingerprints": {
            "site1": bytes(rom[sab(parent, SITE1) : sab(parent, SITE1) + HOOK_LEN]).hex().upper(),
            "site2": bytes(rom[sab(parent, site2) : sab(parent, site2) + HOOK_LEN]).hex().upper(),
            "leaf": bytes(rom[sab(parent, LEAF) : sab(parent, LEAF) + 6]).hex().upper(),
            "cave_head": new_region[:16].hex().upper(),
            "cave_sha256": _sha256(new_region),
        },
    }
