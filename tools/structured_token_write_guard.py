#!/usr/bin/env python3
"""Fail-closed guard against treating structured 16-bit tables as text tokens.

Dictionary tokens use two bytes and can occur by chance inside non-text data.
The P2 nested-duplicate regression at 5C:B5C2 was caused by interpreting the
little-endian table value 0x85F5 (bytes F5 85) as dictionary token F585 and
rewriting it to F573.  This module protects known sorted tables and also detects
similar monotonic little-endian 16-bit runs before a token-tail writer mutates
an external occurrence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Mapping

from monoeye_rom import BANK_SIZE, stock_base


@dataclass(frozen=True)
class ProtectedTable:
    logical_start: int
    logical_end_exclusive: int
    name: str
    minimum_words: int
    expected_sha256: str


# These adjacent bank-5C blocks are zero-delimited, strictly ascending u16
# tables.  They are not zstrings even when one value happens to encode Fxxx.
PROTECTED_TABLES: tuple[ProtectedTable, ...] = (
    ProtectedTable(
        0x5CB5BC,
        0x5CB5FE,
        "bank5c_id_command_sorted_u16_table_0",
        33,
        "eb38ef74ee59dbb12941ce10b7ba157c65f1032662e3868af70009c41d19e40e",
    ),
    ProtectedTable(
        0x5CB604,
        0x5CB61C,
        "bank5c_id_command_sorted_u16_table_1",
        12,
        "3c5760be8d046305d432355c9204f65cdff440fbb6f488cae0158293ef2602af",
    ),
    ProtectedTable(
        0x5CB628,
        0x5CB64C,
        "bank5c_id_command_sorted_u16_table_2",
        18,
        "3ca73c5e6daeaef0d934d91fa3d09910660055c44b4ab21ad609d69517168323",
    ),
)


class StructuredTokenWriteError(RuntimeError):
    """Raised when a proposed text-token write overlaps structured data."""


def _file_offset(rom: bytes | bytearray, logical: int) -> int:
    if not 0 <= logical < 0x800000:
        raise ValueError(f"logical address outside stock ROM: {logical:06X}")
    return stock_base(rom) + logical


def logical_slice(rom: bytes | bytearray, logical: int, length: int) -> bytes:
    start = _file_offset(rom, logical)
    end = start + length
    if end > len(rom):
        raise ValueError(f"logical slice exceeds ROM: {logical:06X}+{length}")
    return bytes(rom[start:end])


def protected_table_for_site(logical: int, length: int = 2) -> ProtectedTable | None:
    end = logical + length
    for table in PROTECTED_TABLES:
        if logical < table.logical_end_exclusive and table.logical_start < end:
            return table
    return None


def table_values(rom: bytes | bytearray, table: ProtectedTable) -> tuple[int, ...]:
    payload = logical_slice(
        rom,
        table.logical_start,
        table.logical_end_exclusive - table.logical_start,
    )
    if len(payload) % 2:
        raise ValueError(f"protected table has odd byte length: {table.name}")
    return tuple(
        int.from_bytes(payload[offset : offset + 2], "little")
        for offset in range(0, len(payload), 2)
    )


def validate_protected_table(
    rom: bytes | bytearray,
    table: ProtectedTable,
) -> dict[str, Any]:
    payload = logical_slice(
        rom,
        table.logical_start,
        table.logical_end_exclusive - table.logical_start,
    )
    values = table_values(rom, table)
    positive = all(value not in (0x0000, 0xFFFF) for value in values)
    ascending = all(left < right for left, right in zip(values, values[1:]))
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    expected_exact = actual_sha256 == table.expected_sha256
    return {
        "name": table.name,
        "logical_start": f"{table.logical_start:06X}",
        "logical_end_exclusive": f"{table.logical_end_exclusive:06X}",
        "words": len(values),
        "minimum_words": table.minimum_words,
        "positive_non_sentinel": positive,
        "strictly_ascending": ascending,
        "expected_sha256": table.expected_sha256,
        "actual_sha256": actual_sha256,
        "expected_exact": expected_exact,
        "first": f"{values[0]:04X}" if values else None,
        "last": f"{values[-1]:04X}" if values else None,
        "ok": (
            len(values) >= table.minimum_words
            and positive
            and ascending
            and expected_exact
        ),
    }


def monotonic_u16_run_at(
    rom: bytes | bytearray,
    logical: int,
    *,
    radius_words: int = 16,
    minimum_words: int = 6,
    maximum_step: int = 0x0200,
) -> dict[str, Any] | None:
    """Return a monotonic u16 run containing ``logical``, if one is evident.

    The alignment is anchored at the proposed two-byte write itself, so odd-
    aligned tables are detected as well.  Runs may not cross a 64 KiB bank.
    """
    if logical < 0 or logical + 2 > 0x800000:
        return None
    bank_start = (logical // BANK_SIZE) * BANK_SIZE
    bank_end = bank_start + BANK_SIZE

    def value_at(address: int) -> int | None:
        if address < bank_start or address + 2 > bank_end:
            return None
        try:
            return int.from_bytes(logical_slice(rom, address, 2), "little")
        except ValueError:
            return None

    center = value_at(logical)
    if center in (None, 0x0000, 0xFFFF):
        return None

    start = logical
    current = center
    for _ in range(radius_words):
        previous_address = start - 2
        previous = value_at(previous_address)
        if previous in (None, 0x0000, 0xFFFF):
            break
        step = current - previous
        if step <= 0 or step > maximum_step:
            break
        start = previous_address
        current = previous

    end = logical + 2
    current = center
    for _ in range(radius_words):
        following = value_at(end)
        if following in (None, 0x0000, 0xFFFF):
            break
        step = following - current
        if step <= 0 or step > maximum_step:
            break
        end += 2
        current = following

    words = (end - start) // 2
    if words < minimum_words:
        return None
    values = tuple(
        int.from_bytes(logical_slice(rom, address, 2), "little")
        for address in range(start, end, 2)
    )
    return {
        "logical_start": f"{start:06X}",
        "logical_end_exclusive": f"{end:06X}",
        "words": words,
        "first": f"{values[0]:04X}",
        "last": f"{values[-1]:04X}",
        "maximum_step": maximum_step,
        "values": [f"{value:04X}" for value in values],
    }


def classify_structured_token_site(
    rom: bytes | bytearray,
    logical: int,
    *,
    length: int = 2,
) -> dict[str, Any] | None:
    protected = protected_table_for_site(logical, length)
    monotonic = monotonic_u16_run_at(rom, logical)
    if protected is None and monotonic is None:
        return None
    result: dict[str, Any] = {
        "logical_start": f"{logical:06X}",
        "logical_end_exclusive": f"{logical + length:06X}",
    }
    if protected is not None:
        result["protected_table"] = validate_protected_table(rom, protected)
    if monotonic is not None:
        result["monotonic_u16_run"] = monotonic
    return result


def guard_external_token_write(
    rom: bytes | bytearray,
    *,
    token_abs: int,
    before: bytes,
    after: bytes,
    region: str,
    kind: str,
) -> None:
    """Reject a proposed two-byte external text-token rewrite in structure."""
    if len(before) != 2 or len(after) != 2:
        raise StructuredTokenWriteError("external dictionary-token writes must be two bytes")
    actual = logical_slice(rom, token_abs, 2)
    if actual != before:
        raise StructuredTokenWriteError(
            f"guard input drift at {token_abs:06X}: expected {before.hex().upper()}, "
            f"found {actual.hex().upper()}"
        )
    classification = classify_structured_token_site(rom, token_abs)
    if classification is None:
        return
    raise StructuredTokenWriteError(
        "refusing external token rewrite in structured data at "
        f"{token_abs:06X} ({region}/{kind}) "
        f"{before.hex().upper()}->{after.hex().upper()}: {classification}"
    )


def audit_external_token_writes(
    rom: bytes | bytearray,
    writes: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in writes:
        token_abs = int(str(row["token_abs"]), 16)
        classification = classify_structured_token_site(rom, token_abs)
        if classification is None:
            continue
        issues.append(
            {
                "token_abs": f"{token_abs:06X}",
                "record_abs": str(row.get("record_abs") or ""),
                "region": str(row.get("region") or ""),
                "kind": str(row.get("kind") or ""),
                "before_hex": str(row.get("before_hex") or "").upper(),
                "after_hex": str(row.get("after_hex") or "").upper(),
                "classification": classification,
            }
        )
    return issues
