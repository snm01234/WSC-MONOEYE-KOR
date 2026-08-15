#!/usr/bin/env python3
"""Shadow-aware decoding helpers for the bank61 native 2-byte dictionary runtime.

The runtime candidate installed by ``build_bank61_shadow_dictionary_candidate``
changes native F0-FE token lookup only while ROM1 is reading stock bank61.  The
same token index keeps its original stock meaning in every other source bank.

Generic ``Dictionary.expand`` therefore remains intentionally unchanged.  Use
``expand_source_body`` whenever a record's logical source bank is known and the
ROM may contain the bank61 shadow runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_compact3_token,
    dict_index_from_ext3_token,
    dict_index_from_token,
    is_compact3_magic,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    le16,
    slice_expansion_bank,
    stock_base,
)

SOURCE_BANK = 0x61
SHADOW_SEG0 = 0x26
SHADOW_GROUPS = 4
SHADOW_LOCAL_MASK = 0x03FF
SHADOW_PTR_BYTES = 0x0800
SHADOW_SENTINEL = 0xFFFF
HOOK_LOGICAL = 0x7FFF0D
HOOK_BYTES = bytes.fromhex("EA18FF00F0909090909090")


def runtime_installed(rom: bytes | bytearray) -> bool:
    sb = stock_base(rom)
    return bytes(rom[sb + HOOK_LOGICAL : sb + HOOK_LOGICAL + len(HOOK_BYTES)]) == HOOK_BYTES


def shadow_raw_entry(rom: bytes | bytearray, index: int) -> bytes | None:
    """Return a bank61 shadow phrase for a native 12-bit index, else ``None``."""
    if not 0 <= index <= 0x0FFF:
        return None
    group = index >> 10
    if group >= SHADOW_GROUPS:
        return None
    seg = SHADOW_SEG0 + group
    bank = bytes(slice_expansion_bank(rom, seg))
    local = index & SHADOW_LOCAL_MASK
    ptr = le16(bank, local * 2)
    if ptr == SHADOW_SENTINEL or not SHADOW_PTR_BYTES <= ptr < 0x10000:
        return None
    end = ptr
    while end < 0x10000 and bank[end] != 0:
        end += 1
    if end >= 0x10000:
        return None
    return bank[ptr:end]


def expand_source_body(
    dictionary: Dictionary,
    rom: bytes | bytearray,
    data: bytes,
    tbl: Optional[Tbl] = None,
    *,
    source_logical_bank: int,
    depth: int = 0,
    max_depth: int = 12,
    as_codes: bool = False,
) -> str:
    """Expand one encoded body with bank61 shadow semantics when applicable.

    Shadow phrase payloads themselves are expanded with the ordinary dictionary
    rules.  That matches runtime behavior: once bank26/27 is mapped, a nested
    native token sees source ROM1 bank 26/27 rather than E1 and therefore falls
    back to the stock dictionary.
    """
    if depth > max_depth:
        return "…"
    use_shadow = source_logical_bank == SOURCE_BANK and runtime_installed(rom)
    i = 0
    parts: list[str] = []
    while i < len(data):
        b = data[i]
        if b == 0:
            break
        if is_dict_token(b):
            if i + 1 >= len(data):
                parts.append(f"<TRUNC:{b:02X}>")
                break
            idx = dict_index_from_token(b, data[i + 1])
            raw = shadow_raw_entry(rom, idx) if use_shadow else None
            if raw is not None:
                parts.append(
                    dictionary.expand(
                        raw,
                        tbl,
                        depth=depth + 1,
                        max_depth=max_depth,
                        as_codes=as_codes,
                    )
                )
            else:
                parts.append(dictionary.expand_index(idx, tbl))
            i += 2
            continue
        if is_kanji_lead(b):
            if i + 1 >= len(data):
                parts.append(f"<TRUNC:{b:02X}>")
                break
            if is_compact3_magic(b, data[i + 1]):
                if i + 2 >= len(data):
                    parts.append(f"<TRUNC:{b:02X}>")
                    break
                idx = dict_index_from_compact3_token(b, data[i + 1], data[i + 2])
                parts.append(dictionary.expand_index(idx, tbl))
                i += 3
                continue
            if is_ext3_magic(b, data[i + 1]):
                if i + 3 >= len(data):
                    parts.append(f"<TRUNC:{b:02X}>")
                    break
                idx = dict_index_from_ext3_token(b, data[i + 1], data[i + 2], data[i + 3])
                parts.append(dictionary.expand_index(idx, tbl))
                i += 4
                continue
            code = (b << 8) | data[i + 1]
            if as_codes:
                parts.append(f"[{code:04X}]")
            elif tbl:
                parts.append(tbl.decode_char(code))
            else:
                parts.append(f"[{code:04X}]")
            i += 2
            continue
        if as_codes:
            parts.append(f"[{b:02X}]")
        elif tbl:
            parts.append(tbl.decode_char(b))
        else:
            parts.append(f"[{b:02X}]")
        i += 1
    return "".join(parts)


def expand_logical_body(
    dictionary: Dictionary,
    rom: bytes | bytearray,
    data: bytes,
    tbl: Optional[Tbl],
    logical_address: int,
) -> str:
    return expand_source_body(
        dictionary,
        rom,
        data,
        tbl,
        source_logical_bank=(logical_address >> 16) & 0xFF,
    )
