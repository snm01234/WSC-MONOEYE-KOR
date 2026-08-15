#!/usr/bin/env python3
"""
Allocate Hangul codepoints beyond the 192-slot E740–E7FF window.

Pools (in order):
  1) E740–E7FF          — reserved Hangul window (safe; matches seed patch)
  2) unused/unassigned E000–E73D — JP reclaim (experimental; can crash New Game)
  3) E800–EFFF          — extended glyph pages (experimental; can crash New Game)
  4) optional rare JP codes (usage <= N) when --recycle-rare is set

Default gameplay builds should use primary_only=True until E8/reclaim are
validated in-emulator.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from monoeye_rom import (
    Dictionary,
    Tbl,
    compact_font_file_offset,
    is_dict_token,
    read_encoded_z,
    slice_bank,
)

HANGUL_PRIMARY_START = 0xE740
HANGUL_PRIMARY_END = 0xE7FF
EXTENDED_START = 0xE800
EXTENDED_END = 0xEFFF  # inclusive; F0xx is dictionary space


@dataclass
class AllocResult:
    char_to_code: Dict[str, int]
    overflow_chars: List[str]
    pool_counts: Dict[str, int]
    reused_jp_codes: List[int]


def scan_extended_code_usage(rom: bytes | bytearray, dictionary: Dictionary) -> Counter:
    """Count E0xx–EFxx code usage in dictionary + script banks."""
    usage: Counter = Counter()

    def scan(payload: bytes) -> None:
        i = 0
        while i < len(payload):
            b = payload[i]
            if is_dict_token(b) and i + 1 < len(payload):
                i += 2
            elif 0xE0 <= b <= 0xEF and i + 1 < len(payload):
                usage[(b << 8) | payload[i + 1]] += 1
                i += 2
            else:
                i += 1

    for index in range(dictionary.count):
        scan(dictionary.raw_entry(index))
    for seg in range(0x60, 0x70):
        bank = slice_bank(rom, seg)
        cur = 0
        while cur < len(bank):
            if bank[cur] == 0:
                cur += 1
                continue
            payload, term = read_encoded_z(bank, cur, len(bank) - cur)
            scan(payload)
            cur = term + 1
    return usage


def build_code_pools(
    base_tbl: Tbl,
    usage: Counter,
    rom: bytes | bytearray | None = None,
    *,
    recycle_rare_max_usage: int = 0,
    primary_only: bool = False,
    allow_reclaim: bool = True,
    allow_extended: bool = True,
    text_safe_primary: bool = False,
    tail_pad_safe: bool = False,
    e7_blank_safe: bool = False,
) -> List[Tuple[str, List[int]]]:
    """
    text_safe_primary: E740–E7FF with usage==0 (often nonempty UI glyphs — UNSAFE).
    tail_pad_safe: bank40 FF padding + usage==0 (~16) — ROM-safe but NOT displayed
                   (font pages only reliably render E0–E7).
    e7_blank_safe: blank (00/FF) glyph slots in E000–E7FF only (~8). Displayable + low crash risk.
    """
    if e7_blank_safe:
        if rom is None:
            raise ValueError("e7_blank_safe requires rom bytes")
        blank: List[Tuple[int, int]] = []
        for code in range(0xE000, 0xE800):
            off = compact_font_file_offset(code)
            g = bytes(rom[off : off + 16])
            if not (all(b == 0 for b in g) or all(b == 0xFF for b in g)):
                continue
            blank.append((usage[code], code))
        blank.sort()
        return [("e7_blank", [code for _u, code in blank])]

    if tail_pad_safe:
        if rom is None:
            raise ValueError("tail_pad_safe requires rom bytes to inspect glyph padding")
        safe: List[int] = []
        for code in range(0xE000, 0xF000):
            if usage[code] != 0:
                continue
            off = compact_font_file_offset(code)
            if not (0x40F9F8 <= off <= 0x40FFF0):
                continue
            if all(b == 0xFF for b in rom[off : off + 16]):
                safe.append(code)
        return [("tail_pad_safe", safe)]

    if text_safe_primary:
        safe = [code for code in range(HANGUL_PRIMARY_START, HANGUL_PRIMARY_END + 1) if usage[code] == 0]
        return [("text_safe_E740", safe)]

    primary = list(range(HANGUL_PRIMARY_START, HANGUL_PRIMARY_END + 1))
    pools: List[Tuple[str, List[int]]] = [("primary_E740", primary)]
    if primary_only:
        return pools

    if allow_reclaim:
        unassigned = [
            code
            for code in range(0xE000, HANGUL_PRIMARY_START)
            if code not in base_tbl.code_to_char
        ]
        unused_assigned = [
            code
            for code in range(0xE000, HANGUL_PRIMARY_START)
            if code in base_tbl.code_to_char and usage[code] == 0
        ]
        pools.append(("safe_reclaim_E000", sorted(set(unassigned + unused_assigned))))

    if allow_extended:
        pools.append(("extended_E800", list(range(EXTENDED_START, EXTENDED_END + 1))))

    if recycle_rare_max_usage > 0:
        rare = sorted(
            code
            for code in range(0xE000, HANGUL_PRIMARY_START)
            if code in base_tbl.code_to_char
            and 0 < usage[code] <= recycle_rare_max_usage
        )
        pools.append(("rare_jp_recycle", rare))
    return pools


def hangul_by_frequency(texts: Sequence[str]) -> List[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        for ch in text.replace(" ", "　"):
            if "가" <= ch <= "힣":
                counts[ch] += 1
    return sorted(counts.keys(), key=lambda ch: (-counts[ch], ch))


def allocate_hangul_codes(
    hangul_chars: Sequence[str],
    base_tbl: Tbl,
    usage: Counter,
    *,
    rom: bytes | bytearray | None = None,
    recycle_rare_max_usage: int = 0,
    primary_only: bool = False,
    allow_reclaim: bool = True,
    allow_extended: bool = True,
    text_safe_primary: bool = False,
    tail_pad_safe: bool = False,
    e7_blank_safe: bool = False,
) -> AllocResult:
    pools = build_code_pools(
        base_tbl,
        usage,
        rom,
        recycle_rare_max_usage=recycle_rare_max_usage,
        primary_only=primary_only,
        allow_reclaim=allow_reclaim,
        allow_extended=allow_extended,
        text_safe_primary=text_safe_primary,
        tail_pad_safe=tail_pad_safe,
        e7_blank_safe=e7_blank_safe,
    )
    flat: List[Tuple[str, int]] = []
    for name, codes in pools:
        for code in codes:
            flat.append((name, code))

    char_to_code: Dict[str, int] = {}
    pool_counts: Counter = Counter()
    reused_jp: List[int] = []
    overflow: List[str] = []

    cursor = 0
    for ch in hangul_chars:
        if cursor >= len(flat):
            overflow.append(ch)
            continue
        pool_name, code = flat[cursor]
        cursor += 1
        char_to_code[ch] = code
        pool_counts[pool_name] += 1
        if pool_name in {"safe_reclaim_E000", "rare_jp_recycle"}:
            reused_jp.append(code)

    return AllocResult(
        char_to_code=char_to_code,
        overflow_chars=overflow,
        pool_counts=dict(pool_counts),
        reused_jp_codes=sorted(reused_jp),
    )
