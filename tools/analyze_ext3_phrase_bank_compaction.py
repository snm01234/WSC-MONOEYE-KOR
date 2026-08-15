#!/usr/bin/env python3
"""Read-only feasibility analysis for compacting ext3 phrase banks.

Every non-empty slot is preserved, regardless of whether current static
reference analysis sees a consumer.  Slot indices therefore remain stable.
Identical payloads may share one packed copy.  The report measures how many
bytes can be recovered from append-only stale payload history without changing
runtime hooks, tokens, or dictionary semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta  # noqa: E402
from monoeye_rom import BANK_SIZE, le16, load_rom, slice_expansion_bank  # noqa: E402
from patch_3byte_dict_token import EXP3_SEG0, EXP3_SLOTS  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/ext3_phrase_bank_compaction_feasibility.json"
DATA_START = EXP3_SLOTS * 2
EMPTY_AT = DATA_START
ROM_SIZE = 16_777_216


class AnalyzeError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": digest(payload),
    }


def read_payload(bank: bytes, pointer: int) -> bytes:
    if pointer < DATA_START or pointer >= BANK_SIZE:
        raise AnalyzeError(f"pointer out of phrase range: {pointer:04X}")
    end = bank.find(b"\x00", pointer)
    if end < 0:
        raise AnalyzeError(f"unterminated phrase at {pointer:04X}")
    return bytes(bank[pointer:end])


def physical_cursor(bank: bytes) -> int:
    cursor = DATA_START + 1
    for local in range(EXP3_SLOTS):
        pointer = le16(bank, local * 2)
        if pointer < DATA_START or pointer >= BANK_SIZE:
            continue
        end = bank.find(b"\x00", pointer)
        if end < 0:
            raise AnalyzeError(f"unterminated phrase at slot {local:03X}")
        cursor = max(cursor, end + 1)
    return cursor


def analyze_bank(bank: bytes, segment: int) -> dict[str, Any]:
    if len(bank) != BANK_SIZE:
        raise AnalyzeError(f"bank {segment:02X} wrong size")
    if all(value == 0xFF for value in bank[:64]):
        return {
            "segment": f"{segment:02X}",
            "formatted": False,
            "nonempty_slots": 0,
            "unique_payloads": 0,
            "duplicate_slot_payloads": 0,
            "current_cursor": "0000",
            "current_room": BANK_SIZE,
            "packed_cursor": f"{DATA_START + 1:04X}",
            "packed_room": BANK_SIZE - (DATA_START + 1),
            "recoverable_bytes": 0,
            "payload_bytes_current_unique": 0,
            "payload_bytes_packed": 0,
            "pointer_alias_groups": 0,
            "payload_duplicate_groups": 0,
            "ok": True,
        }

    if bank[EMPTY_AT] != 0:
        raise AnalyzeError(f"bank {segment:02X} empty sentinel is not NUL")

    payload_by_slot: dict[int, bytes] = {}
    pointer_by_slot: dict[int, int] = {}
    for local in range(EXP3_SLOTS):
        pointer = le16(bank, local * 2)
        pointer_by_slot[local] = pointer
        payload = read_payload(bank, pointer)
        if payload:
            payload_by_slot[local] = payload

    unique_payloads = sorted(set(payload_by_slot.values()), key=lambda item: (len(item), item))
    packed_bytes = sum(len(payload) + 1 for payload in unique_payloads)
    packed_cursor = DATA_START + 1 + packed_bytes
    if packed_cursor > BANK_SIZE:
        raise AnalyzeError(f"bank {segment:02X} cannot fit its own live payloads after packing")

    current_cursor = physical_cursor(bank)
    current_room = BANK_SIZE - current_cursor
    packed_room = BANK_SIZE - packed_cursor
    payload_counts = Counter(payload_by_slot.values())
    pointer_counts = Counter(
        pointer_by_slot[local]
        for local in payload_by_slot
    )

    return {
        "segment": f"{segment:02X}",
        "formatted": True,
        "nonempty_slots": len(payload_by_slot),
        "unique_payloads": len(unique_payloads),
        "duplicate_slot_payloads": len(payload_by_slot) - len(unique_payloads),
        "current_cursor": f"{current_cursor:04X}",
        "current_room": current_room,
        "packed_cursor": f"{packed_cursor:04X}",
        "packed_room": packed_room,
        "recoverable_bytes": packed_room - current_room,
        "payload_bytes_current_unique": sum(
            len(read_payload(bank, pointer)) + 1
            for pointer in sorted(set(pointer_by_slot.values()))
            if DATA_START <= pointer < BANK_SIZE and read_payload(bank, pointer)
        ),
        "payload_bytes_packed": packed_bytes,
        "pointer_alias_groups": sum(count > 1 for count in pointer_counts.values()),
        "payload_duplicate_groups": sum(count > 1 for count in payload_counts.values()),
        "largest_payload_bytes": max((len(payload) for payload in unique_payloads), default=0),
        "ok": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--meta", type=Path, default=META)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    rom = bytes(load_rom(args.tip))
    if len(rom) != ROM_SIZE:
        raise AnalyzeError("16 MiB TIP required")
    meta = load_ext_meta(args.meta)
    num_banks = int(meta.get("num_banks") or 0)
    if not 1 <= num_banks <= 16:
        raise AnalyzeError(f"invalid ext3 bank count: {num_banks}")

    banks = [
        analyze_bank(bytes(slice_expansion_bank(rom, EXP3_SEG0 + offset)), EXP3_SEG0 + offset)
        for offset in range(num_banks)
    ]
    totals = {
        "banks": len(banks),
        "formatted_banks": sum(bool(row["formatted"]) for row in banks),
        "nonempty_slots": sum(int(row["nonempty_slots"]) for row in banks),
        "unique_payloads_within_banks": sum(int(row["unique_payloads"]) for row in banks),
        "duplicate_slot_payloads": sum(int(row["duplicate_slot_payloads"]) for row in banks),
        "current_room": sum(int(row["current_room"]) for row in banks),
        "packed_room": sum(int(row["packed_room"]) for row in banks),
        "recoverable_bytes": sum(int(row["recoverable_bytes"]) for row in banks),
        "all_banks_self_consistent": all(row["ok"] is True for row in banks),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_ext3_phrase_bank_compaction.py",
        "read_only": True,
        "ok": totals["all_banks_self_consistent"],
        "inputs": {
            "tip": identity(args.tip, rom),
            "meta": identity(args.meta),
        },
        "policy": {
            "preserve_every_nonempty_slot": True,
            "preserve_slot_indices": True,
            "allow_identical_payload_pointer_aliasing": True,
            "runtime_hook_change": False,
            "cross_bank_migration": False,
            "raw_token_rewrite": False,
        },
        "totals": totals,
        "banks": banks,
        "next_gate": (
            "Build a compaction-only candidate and prove all nonempty ext3 slot payloads "
            "render byte-identically before allocating new translations."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
