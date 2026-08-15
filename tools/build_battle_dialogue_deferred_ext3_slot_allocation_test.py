#!/usr/bin/env python3
"""Build a test ROM that allocates fresh ext3 slots for deferred battle lines.

This is a capacity/decoder test, not a promotion candidate.  The 27 deferred
records contain 20 unique phrases for which no exact native stock token exists.
The test reuses the existing ext3 runtime and appends byte-exact copies of
those 20 raw dictionary payloads to a previously unused area of expansion bank
13, assigning previously unreferenced, empty ext3 slots.  The records are
rewritten to the new ``E5 18 xx yy`` tokens with the same payload lengths and
terminators.

The battle body-only consumer remains the important limitation: moving a
phrase to a fresh ext3 slot does not prove that a body-only battle route is
ext3-aware.  Therefore this ROM is useful for testing allocation, dictionary
rendering, and future capacity, but it is not promoted automatically.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from build_p2_stock_spill_candidate import _stock_phrase_cursor  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)


PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
PARENT_ROM = PATCH / "monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
DEFERRED_CSV = SCRIPT / "battle_dialogue_samecase_native_deferred.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
OUT_ROM = PATCH / "battle_dialogue_deferred_ext3_slot_allocation_test.wsc"
OUT_SAVE = ROOT / "sram/battle_dialogue_deferred_ext3_slot_allocation_test.sav"
OUT_ROWS = SCRIPT / "battle_dialogue_deferred_ext3_slot_allocation_test.csv"
OUT_REPORT = PATCH / "battle_dialogue_deferred_ext3_slot_allocation_test_report.json"

EXPECTED_PARENT_SHA = (
    "79083106361d471138392e78ccfd9698781d0f03b8f4b61ad1e61ba2d373d8be"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXT3_SEG0 = 0x11
EXT3_BANKS = 16
EXT3_SLOTS = 0x1000
EXT3_INDEX_BASE = 0x1000
EXT3_DATA_START = 0x2000
EXT3_MAGIC = b"\xE5\x18"
ALLOC_SEGMENT = 0x13
FUTURE_RESERVE_COUNT = 32
MAX_RECORD_LEN = 256
EXT3_META = {"num_banks": EXT3_BANKS, "exp_seg0": "11"}

# This is the previously audited strong retired native-stock population on
# the promoted TIP.  It is recorded here to make the native alternative
# review reproducible without silently reallocating stock dictionary data.
STRONG_RETIRED_NATIVE_SLOTS = (
    0x008B,
    0x0093,
    0x00B1,
    0x00C6,
    0x00D0,
    0x00D7,
    0x0105,
    0x0115,
    0x012E,
    0x0154,
    0x0158,
    0x019A,
    0x01B3,
    0x01BC,
    0x01BD,
    0x01BE,
    0x01C0,
    0x022B,
    0x023D,
    0x0248,
    0x02DB,
    0x0381,
    0x05E6,
    0x06E6,
    0x0B2F,
)


class BuildError(RuntimeError):
    pass


def sha(payload: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(payload)).hexdigest()


def relpath(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_text(path: Path, text: str) -> None:
    atomic_bytes(path, text.encode("utf-8"))


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def read_le16(data: bytes | bytearray, offset: int) -> int:
    return int(data[offset]) | (int(data[offset + 1]) << 8)


def write_le16(data: bytearray, offset: int, value: int) -> None:
    data[offset] = value & 0xFF
    data[offset + 1] = (value >> 8) & 0xFF


def ext3_index(segment: int, local: int) -> int:
    return EXT3_INDEX_BASE + ((segment - EXT3_SEG0) << 12) + local


def ext3_segment_local(index: int) -> tuple[int, int]:
    offset = index - EXT3_INDEX_BASE
    return EXT3_SEG0 + (offset >> 12), offset & 0xFFF


def payload_from_bank(bank: bytes, pointer: int) -> bytes:
    if not EXT3_DATA_START <= pointer < BANK_SIZE:
        raise BuildError(f"ext3 pointer outside data area: {pointer:04X}")
    end = bank.find(b"\x00", pointer)
    if end < 0:
        raise BuildError(f"unterminated ext3 phrase at {pointer:04X}")
    return bytes(bank[pointer:end])


def bank_cursor(bank: bytes) -> int:
    cursor = EXT3_DATA_START + 1
    for local in range(EXT3_SLOTS):
        pointer = read_le16(bank, local * 2)
        if not EXT3_DATA_START <= pointer < BANK_SIZE:
            continue
        end = bank.find(b"\x00", pointer)
        if end < 0:
            raise BuildError(f"unterminated ext3 phrase at slot {local:03X}")
        cursor = max(cursor, end + 1)
    return cursor


def ext3_indices(payload: bytes) -> Iterable[int]:
    """Conservatively find E5 18 references in a raw encoded payload."""
    cursor = 0
    while cursor + 3 < len(payload):
        if payload[cursor : cursor + 2] == EXT3_MAGIC and payload[cursor + 3] != 0:
            yield EXT3_INDEX_BASE + ((payload[cursor + 2] << 8) | payload[cursor + 3])
            cursor += 4
        else:
            cursor += 1


def external_ext3_refs(rom: bytes) -> set[int]:
    """Collect ext3 refs in runtime zstrings plus nested ext3 dictionary data."""
    refs: set[int] = set()
    for logical, payload, _kind in _walk_zstring_range(
        rom, 0x600000, 0x700000, region="script", max_len=256
    ):
        refs.update(ext3_indices(payload))
    for segment in list(range(0x50, 0x5F)) + [0x76]:
        for logical, payload, _kind in _walk_zstring_range(
            rom,
            segment * BANK_SIZE,
            (segment + 1) * BANK_SIZE,
            region="aux",
            max_len=128,
        ):
            refs.update(ext3_indices(payload))
    for segment in range(EXT3_SEG0, EXT3_SEG0 + EXT3_BANKS):
        bank = rom[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for local in range(EXT3_SLOTS):
            pointer = read_le16(bank, local * 2)
            if not EXT3_DATA_START <= pointer < BANK_SIZE:
                continue
            try:
                payload = payload_from_bank(bank, pointer)
            except BuildError:
                # One legacy expansion-bank pointer is intentionally outside
                # the active phrase stream. It is not in the allocation bank;
                # keep the test fail-closed by excluding that slot from the
                # reference set rather than trying to repair it here.
                continue
            refs.update(ext3_indices(payload))
    return refs


def free_slots_in_bank(rom: bytes, segment: int, refs: set[int]) -> list[int]:
    bank = rom[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
    result: list[int] = []
    for local in range(1, EXT3_SLOTS):
        if (local & 0xFF) == 0:
            continue  # E5 18 xx 00 is a zstring-unsafe token.
        pointer = read_le16(bank, local * 2)
        if not EXT3_DATA_START <= pointer < BANK_SIZE:
            continue
        try:
            payload = payload_from_bank(bank, pointer)
        except BuildError:
            continue
        if payload:
            continue
        if ext3_index(segment, local) in refs:
            continue
        result.append(local)
    return result


def diff_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    start: int | None = None
    for offset, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = offset
        if left == right and start is not None:
            runs.append({"start": f"{start:07X}", "end_exclusive": f"{offset:07X}", "length": offset - start})
            start = None
    if start is not None:
        runs.append({"start": f"{start:07X}", "end_exclusive": f"{len(before):07X}", "length": len(before) - start})
    return runs


def in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def read_deferred_rows(rom: bytes, dictionary: Dictionary, tbl: Tbl) -> tuple[list[dict[str, str]], list[str], dict[str, bytes]]:
    stock = stock_base(rom)
    with DEFERRED_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 27:
        raise BuildError(f"deferred population drifted: {len(rows)}")
    phrase_order: list[str] = []
    raw_by_phrase: dict[str, bytes] = {}
    for row in rows:
        logical = int(row["abs"], 16)
        got = read_encoded_z_safe(rom, stock + logical, max_len=MAX_RECORD_LEN)
        if got is None:
            raise BuildError(f"deferred record has no bounded terminator: {logical:06X}")
        payload, term = got
        if payload.hex().upper() != row["current_payload_hex"]:
            raise BuildError(f"deferred payload drifted at {logical:06X}")
        if payload[:2] != EXT3_MAGIC or len(payload) < 4 or any(byte != 1 for byte in payload[4:]):
            raise BuildError(f"deferred record is not E5 18 plus padding at {logical:06X}")
        old_index = dict_index_from_ext3_token(*payload[:4])
        raw = bytes(dictionary.raw_entry(old_index))
        rendered = clean(dictionary.expand(raw, tbl))
        if rendered != row["current_render"]:
            raise BuildError(f"deferred render drifted at {logical:06X}")
        if rendered not in raw_by_phrase:
            phrase_order.append(rendered)
            raw_by_phrase[rendered] = raw
        row["old_index"] = f"{old_index:04X}"
        row["terminator_file_offset"] = f"{term:07X}"
    if len(phrase_order) != 20:
        raise BuildError(f"deferred unique phrase count drifted: {len(phrase_order)}")
    return rows, phrase_order, raw_by_phrase


def native_capacity_review(rom: bytes, required_bytes: int, phrase_count: int) -> dict[str, Any]:
    dictionary = Dictionary(rom)
    cursor = _stock_phrase_cursor(rom)
    retired_bytes = sum(len(dictionary.raw_entry(index)) + 1 for index in STRONG_RETIRED_NATIVE_SLOTS)
    return {
        "status": "blocked_without_stock_dictionary_repack",
        "stock_count": dictionary.stock_count,
        "stock_spill_cursor": f"{cursor:04X}",
        "stock_spill_room_bytes": BANK_SIZE - cursor,
        "strong_retired_native_slots": len(STRONG_RETIRED_NATIVE_SLOTS),
        "strong_retired_slot_indices": [f"{index:04X}" for index in STRONG_RETIRED_NATIVE_SLOTS],
        "retired_slot_payload_room_including_nul": retired_bytes,
        "required_unique_phrases": phrase_count,
        "required_phrase_bytes_including_nul": required_bytes,
        "reason": (
            "The available native tail has only a few bytes and the audited "
            "retired stock payload ranges are far smaller than the 20 long "
            "phrases. Repacking the stock dictionary would move unrelated "
            "pointers and is outside this provisional test."
        ),
    }


def csv_text(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, buffer.getvalue())


def main() -> int:
    parent = bytes(load_rom(PARENT_ROM))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drifted: {sha(parent)}")
    main_save = PARENT_SAVE.read_bytes()
    if len(main_save) != SAVE_SIZE:
        raise BuildError("main SaveRAM size drifted")

    tbl = Tbl.load(TBL_PATH)
    parent_dictionary = make_dictionary_ext3(parent, {}, EXT3_META)
    rows, phrase_order, raw_by_phrase = read_deferred_rows(parent, parent_dictionary, tbl)
    required_bytes = sum(len(raw_by_phrase[text]) + 1 for text in phrase_order)

    refs = external_ext3_refs(parent)
    free_slots = free_slots_in_bank(parent, ALLOC_SEGMENT, refs)
    if len(free_slots) < len(phrase_order) + FUTURE_RESERVE_COUNT:
        raise BuildError(
            f"bank {ALLOC_SEGMENT:02X} lacks free safe ext3 slots: "
            f"{len(free_slots)} < {len(phrase_order) + FUTURE_RESERVE_COUNT}"
        )
    selected_locals = free_slots[: len(phrase_order)]
    reserve_locals = free_slots[len(phrase_order) : len(phrase_order) + FUTURE_RESERVE_COUNT]

    parent_bank = bytes(parent[ALLOC_SEGMENT * BANK_SIZE : (ALLOC_SEGMENT + 1) * BANK_SIZE])
    cursor = bank_cursor(parent_bank)
    if cursor + required_bytes > BANK_SIZE:
        raise BuildError(f"ext3 bank {ALLOC_SEGMENT:02X} lacks phrase room")

    candidate = bytearray(parent)
    bank = bytearray(parent_bank)
    allocations: dict[str, dict[str, Any]] = {}
    phrase_to_index: dict[str, int] = {}
    write_ranges: list[tuple[int, int]] = []
    allocation_rows: list[dict[str, Any]] = []
    for phrase, local in zip(phrase_order, selected_locals):
        index = ext3_index(ALLOC_SEGMENT, local)
        token = token_from_dict_index(index)
        raw = raw_by_phrase[phrase]
        old_pointer = read_le16(bank, local * 2)
        if payload_from_bank(bank, old_pointer):
            raise BuildError(f"selected ext3 slot is no longer empty: {index:04X}")
        pointer = cursor
        write_le16(bank, local * 2, pointer)
        bank[pointer : pointer + len(raw)] = raw
        bank[pointer + len(raw)] = 0
        cursor += len(raw) + 1
        phrase_to_index[phrase] = index
        allocations[phrase] = {
            "index": f"{index:04X}",
            "token_hex": token.hex().upper(),
            "segment": f"{ALLOC_SEGMENT:02X}",
            "local_slot": f"{local:03X}",
            "old_pointer": f"{old_pointer:04X}",
            "new_pointer": f"{pointer:04X}",
            "raw_length": len(raw),
            "raw_sha256": sha(raw),
            "rendered_text": phrase,
        }
        write_ranges.append((ALLOC_SEGMENT * BANK_SIZE + local * 2, ALLOC_SEGMENT * BANK_SIZE + local * 2 + 2))
        write_ranges.append((ALLOC_SEGMENT * BANK_SIZE + pointer, ALLOC_SEGMENT * BANK_SIZE + pointer + len(raw) + 1))

    candidate[ALLOC_SEGMENT * BANK_SIZE : (ALLOC_SEGMENT + 1) * BANK_SIZE] = bank

    stock = stock_base(parent)
    for row in rows:
        logical = int(row["abs"], 16)
        at = stock + logical
        payload, term = read_encoded_z_safe(parent, at, max_len=MAX_RECORD_LEN) or (b"", -1)
        phrase = row["current_render"]
        token = token_from_dict_index(phrase_to_index[phrase])
        replacement = token + (b"\x01" * (len(payload) - len(token)))
        if len(replacement) != len(payload):
            raise BuildError(f"record extent drift at {logical:06X}")
        boundary_before = parent[term : term + 8]
        candidate[at : at + len(payload)] = replacement
        if candidate[term : term + 8] != boundary_before or candidate[term] != 0:
            raise BuildError(f"terminator/next-control drift at {logical:06X}")
        write_ranges.append((at, at + len(payload)))
        allocation_rows.append(
            {
                "family": row["family"],
                "abs": f"{logical:06X}",
                "phrase": phrase,
                "old_index": row["old_index"],
                "new_index": f"{phrase_to_index[phrase]:04X}",
                "old_token_hex": payload[:4].hex().upper(),
                "new_token_hex": token.hex().upper(),
                "record_length": len(payload),
                "terminator_file_offset": f"{term:07X}",
                "next_boundary_hex": boundary_before.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    write_ranges.append((len(result) - 2, len(result)))
    unexpected = [
        offset
        for offset, (before, after) in enumerate(zip(parent, result))
        if before != after and not in_ranges(offset, write_ranges)
    ]
    if unexpected:
        raise BuildError(f"unaccounted changed byte at {unexpected[0]:07X}")

    final_dictionary = make_dictionary_ext3(result, {}, EXT3_META)
    for phrase, index in phrase_to_index.items():
        raw = bytes(final_dictionary.raw_entry(index))
        if raw != raw_by_phrase[phrase] or clean(final_dictionary.expand(raw, tbl)) != phrase:
            raise BuildError(f"new ext3 slot render mismatch: {index:04X}")
    for row in allocation_rows:
        logical = int(row["abs"], 16)
        payload, term = read_encoded_z_safe(result, stock + logical, max_len=MAX_RECORD_LEN) or (b"", -1)
        if payload.hex().upper() != row["new_token_hex"] + ("01" * (row["record_length"] - 4)):
            raise BuildError(f"final record token mismatch at {logical:06X}")
        if term != stock + logical + len(payload) or result[term] != 0:
            raise BuildError(f"final record terminator mismatch at {logical:06X}")

    # The reserved slots remain untouched and unreferenced in the test ROM.
    final_refs = external_ext3_refs(result)
    reserved_indices = [ext3_index(ALLOC_SEGMENT, local) for local in reserve_locals]
    if any(index in final_refs for index in reserved_indices):
        raise BuildError("future reserve slot unexpectedly referenced")

    native_review = native_capacity_review(parent, required_bytes, len(phrase_order))
    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, main_save)
    csv_text(
        OUT_ROWS,
        allocation_rows,
        [
            "family",
            "abs",
            "phrase",
            "old_index",
            "new_index",
            "old_token_hex",
            "new_token_hex",
            "record_length",
            "terminator_file_offset",
            "next_boundary_hex",
        ],
    )
    report = {
        "schema_version": 1,
        "generated_by": relpath(Path(__file__)),
        "status": "candidate_static_verified",
        "ok": True,
        "promotion": {"allowed": False, "reason": "allocation/decoder test only; body-only consumer proof is still required"},
        "parent": {"path": relpath(PARENT_ROM), "size": len(parent), "sha256": sha(parent)},
        "candidate": {"path": relpath(OUT_ROM), "size": len(result), "sha256": sha(result), "ws_checksum": f"{checksum:04X}"},
        "save": {"parent_path": relpath(PARENT_SAVE), "candidate_path": relpath(OUT_SAVE), "sha256": sha(main_save), "byte_identical": True},
        "deferred_scope": {
            "records": len(rows),
            "unique_phrases": len(phrase_order),
            "raw_phrase_bytes_including_nul": required_bytes,
            "allocation_bank": f"{ALLOC_SEGMENT:02X}",
            "allocation_start_cursor_before": f"{bank_cursor(parent_bank):04X}",
            "allocation_cursor_after": f"{cursor:04X}",
            "records_rewritten_to_new_ext3_tokens": len(allocation_rows),
        },
        "ext3_allocation": {
            "existing_token_format": "E5 18 xx yy",
            "new_runtime_hook": False,
            "existing_banks_byte_exact_except_allocation_bank": True,
            "allocated": allocations,
            "future_reserve_count": len(reserved_indices),
            "future_reserve_indices": [f"{index:04X}" for index in reserved_indices],
            "future_free_safe_slots_remaining_in_bank": len(free_slots) - len(phrase_order) - len(reserved_indices),
            "future_phrase_room_bytes_after_allocation": BANK_SIZE - cursor,
            "external_reference_scan_before": len(refs),
            "external_reference_scan_after": len(final_refs),
        },
        "native_allocation_review": native_review,
        "invariance": {
            "record_extents_preserved": True,
            "terminators_preserved": True,
            "next_control_boundaries_preserved": True,
            "new_tokens_zstring_safe": True,
            "new_slots_render_exact": True,
            "future_reserve_unreferenced": True,
            "unexpected_changed_bytes": len(unexpected),
            "parent_rom_untouched": sha(PARENT_ROM.read_bytes()) == EXPECTED_PARENT_SHA,
            "parent_save_untouched": sha(PARENT_SAVE.read_bytes()) == sha(main_save),
        },
        "diff_runs": diff_runs(parent, result),
        "allocation_rows": allocation_rows,
        "test_requirements": [
            "Run a battle scene containing the 27 deferred records and check whether text still exposes a sprite/control lead.",
            "Check that each new E5 18 token renders the same Korean phrase as its former token.",
            "Advance across a terminator and separator after a rewritten line.",
            "Do not promote this test ROM based on static rendering alone.",
        ],
    }
    atomic_text(OUT_REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"candidate": report["candidate"], "deferred_scope": report["deferred_scope"], "native_allocation_review": native_review, "promotion": report["promotion"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
