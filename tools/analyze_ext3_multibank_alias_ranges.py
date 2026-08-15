#!/usr/bin/env python3
"""Read-only proof for a five-bank E5 18 alias expansion (banks 21..25).

For raw E5 18 payload pages 0..4, locals 0600..0FFF are reserved only when the
parent has zero references.  They can be rebased to locals 0000..09FF in empty
physical expansion banks 21..25.  All other E5 18 indices retain the accepted
mapping exactly.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import BANK_SIZE, load_rom, stock_base
from patch_3byte_dict_token import (
    BANK_MAP_OFF,
    BANK_MAP_SEG,
    BANK_SAVE_OFF,
    CODE_SEG_7A,
    EXP3_PTR_OFF,
    FAD0_OFF,
    FAD0_SEG,
    INDEX_BASE,
    LEAF_CONTINUE,
    LEAF_EXPECT,
    far_jmp,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SCENARIO_REPORT = ROOT / "out/patch/bank64plus_scenario_capacity.json"
OUT = ROOT / "out/patch/ext3_multibank_alias_ranges.json"
EXPECTED_MAIN_SHA256 = "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825"

FREE_CAVE_START = 0x7FFE9D
FREE_CAVE_END = 0x7FFFF0
FIRST_PAGE = 0
PAGE_COUNT = 5
LOCAL_START = 0x0600
LOCAL_END_EXCLUSIVE = 0x1000
FIRST_PHYSICAL_BANK = 0x21
FULL_POINTER_EMPTY_AT = 0x2000
PHRASE_ROOM_PER_BANK = BANK_SIZE - (FULL_POINTER_EMPTY_AT + 1)
USABLE_TOKENS_PER_BANK = (LOCAL_END_EXCLUSIVE - LOCAL_START) - 10


class AnalysisError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def patch_rel8(buf: bytearray, at: int, target: int) -> None:
    displacement = target - (at + 2)
    if not -128 <= displacement <= 127:
        raise AnalysisError(f"rel8 out of range: {displacement}")
    buf[at + 1] = displacement & 0xFF


def build_five_bank_leaf() -> bytes:
    """Assemble the proposed generalized leaf for size/branch proof only."""
    out = bytearray()
    out += b"\x80\x3E\xFA\x19\x01"
    not_flag_at = len(out)
    out += b"\x75\x00"
    out += b"\xC6\x06\xFA\x19\x00"
    out += LEAF_EXPECT
    out += b"\x51\x52\x56\x57"
    out += b"\x89\x46\xFC\x89\x5E\xFE"
    out += b"\x8B\xFA"
    out += b"\x8B\x36\xF8\x19"
    out += b"\x9A" + struct.pack("<HH", BANK_SAVE_OFF, BANK_MAP_SEG)
    out += b"\x50"

    # BX=raw; AL=page; BX=local.
    out += b"\x89\xF0"
    out += b"\x2D" + struct.pack("<H", INDEX_BASE)
    out += b"\x89\xC3"
    out += b"\x89\xD8"
    out += b"\xB1\x0C\xD3\xE8"
    out += b"\x81\xE3\xFF\x0F"
    out += b"\x81\xFB" + struct.pack("<H", LOCAL_START)
    below_at = len(out)
    out += b"\x72\x00"
    out += b"\x3C" + bytes([PAGE_COUNT])
    above_at = len(out)
    out += b"\x73\x00"

    # Alias page 0..4 -> bank21..25 and local 0000..09FF.
    out += b"\x81\xEB" + struct.pack("<H", LOCAL_START)
    out += b"\x04" + bytes([FIRST_PHYSICAL_BANK])
    alias_jump_at = len(out)
    out += b"\xEB\x00"

    normal_at = len(out)
    patch_rel8(out, below_at, normal_at)
    patch_rel8(out, above_at, normal_at)
    out += b"\x04\x11"

    map_at = len(out)
    patch_rel8(out, alias_jump_at, map_at)
    out += b"\x53"
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xBB\x00\x30\x8E\xC3"
    out += b"\x5E"
    out += b"\xD1\xE6"
    out += b"\x26\x8B\x84" + struct.pack("<H", EXP3_PTR_OFF)
    out += b"\x9A" + struct.pack("<HH", FAD0_OFF, FAD0_SEG)
    out += b"\x89\x46\xF8\x89\x5E\xFA"
    out += far_jmp(0x0743, CODE_SEG_7A)

    fallback_at = len(out)
    patch_rel8(out, not_flag_at, fallback_at)
    out += LEAF_EXPECT
    out += far_jmp(LEAF_CONTINUE & 0xFFFF, CODE_SEG_7A)
    return bytes(out)


def scan_range_hits(data: bytes, page: int) -> list[int]:
    hits: list[int] = []
    raw_start = (page << 12) | LOCAL_START
    raw_end = ((page + 1) << 12)
    for pos in range(len(data) - 3):
        if data[pos] != 0xE5 or data[pos + 1] != 0x18:
            continue
        raw = (data[pos + 2] << 8) | data[pos + 3]
        if raw_start <= raw < raw_end and data[pos + 3] != 0:
            hits.append(pos)
    return hits


def main() -> int:
    rom = bytes(load_rom(MAIN))
    if sha256(rom) != EXPECTED_MAIN_SHA256:
        raise AnalysisError("main TIP identity drifted")
    sb = stock_base(rom)
    leaf = build_five_bank_leaf()
    cave = rom[sb + FREE_CAVE_START:sb + FREE_CAVE_END]

    ranges: list[dict[str, Any]] = []
    all_safe = True
    for page in range(FIRST_PAGE, FIRST_PAGE + PAGE_COUNT):
        physical = FIRST_PHYSICAL_BANK + page
        hits = scan_range_hits(rom, page)
        bank = rom[physical * BANK_SIZE:(physical + 1) * BANK_SIZE]
        bank_empty = len(bank) == BANK_SIZE and all(byte == 0xFF for byte in bank)
        safe = not hits and bank_empty
        all_safe = all_safe and safe
        ranges.append(
            {
                "page": page,
                "raw_start": f"{(page << 12) | LOCAL_START:04X}",
                "raw_end_exclusive": f"{((page + 1) << 12):04X}",
                "token_start": f"E5 18 {((page << 4) | 0x06):02X} 01",
                "token_end": f"E5 18 {((page << 4) | 0x0F):02X} FF",
                "physical_bank": f"{physical:02X}",
                "rebased_local_start": "0001",
                "rebased_local_end": "09FF",
                "usable_tokens": USABLE_TOKENS_PER_BANK,
                "parent_reference_count": len(hits),
                "parent_reference_sample": [f"{pos:07X}" for pos in hits[:16]],
                "physical_bank_all_ff": bank_empty,
                "safe": safe,
            }
        )

    scenario = json.loads(SCENARIO_REPORT.read_text(encoding="utf-8"))
    needed_unique = int(
        scenario.get("counts", {}).get("needed_proxy_unique_token_phrases", 0)
    )
    needed_bytes = int(
        scenario.get("capacity", {}).get("needed_proxy_phrase_bytes_including_nul", 0)
    )
    total_tokens = USABLE_TOKENS_PER_BANK * PAGE_COUNT
    total_phrase_room = PHRASE_ROOM_PER_BANK * PAGE_COUNT

    checks = {
        "all_five_alias_ranges_have_zero_parent_references": all(
            row["parent_reference_count"] == 0 for row in ranges
        ),
        "physical_banks_21_25_all_ff": all(
            row["physical_bank_all_ff"] for row in ranges
        ),
        "current_cave_is_all_ff": all(byte == 0xFF for byte in cave),
        "five_bank_leaf_fits_current_cave": len(leaf) <= len(cave),
        "legacy_proxy_token_count_fits": needed_unique <= total_tokens,
        "legacy_proxy_phrase_bytes_fit": needed_bytes <= total_phrase_room,
    }
    ok = all(checks.values()) and all_safe

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_ext3_multibank_alias_ranges.py",
        "read_only": True,
        "ok": ok,
        "main": {
            "path": str(MAIN.relative_to(ROOT)),
            "sha256": sha256(rom),
        },
        "design": {
            "existing_token": "E5 18 xx yy",
            "new_token": False,
            "new_wram_state": False,
            "page_rule": (
                "if page < 5 and local >= 0600: bank=21+page, local-=0600; "
                "otherwise preserve bank=11+page and original local"
            ),
            "ranges": ranges,
            "five_bank_leaf_length": len(leaf),
            "five_bank_leaf_sha256": sha256(leaf),
            "verified_cave": [f"{FREE_CAVE_START:06X}", f"{FREE_CAVE_END:06X}"],
            "verified_cave_bytes": len(cave),
            "code_room_after": len(cave) - len(leaf),
        },
        "capacity": {
            "banks": PAGE_COUNT,
            "total_usable_tokens": total_tokens,
            "total_phrase_room_full_4096_pointer_layout": total_phrase_room,
            "scenario_legacy_proxy_unique_phrases": needed_unique,
            "scenario_legacy_proxy_phrase_bytes": needed_bytes,
            "token_margin": total_tokens - needed_unique,
            "phrase_byte_margin": total_phrase_room - needed_bytes,
            "warning": (
                "legacy ko is sizing-only; fresh reviewed Korean must be remeasured and "
                "must fit before a production build"
            ),
        },
        "checks": checks,
        "next_gate": (
            "Do not build or promote the five-bank production runtime until the one-bank "
            "bank21 emulator probe passes A Baoa Qu dialogue, battle transition, save, restart, and reload."
        ),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
