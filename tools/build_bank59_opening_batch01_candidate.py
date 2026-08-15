#!/usr/bin/env python3
"""Build candidate for the omitted bank-59 opening/A Baoa Qu dialogue block.

The candidate starts from the current main TIP, installs the user-validated
single-bank E5 18 alias leaf, stores 27 freshly reviewed Korean phrases in
expansion bank 0x21, and replaces only the body of the 27 proven dialogue
records in 59:0000-59:0243.  Existing event prefixes, payload capacities,
terminators, stock dictionary, ext3 banks 0x11-0x20 and accepted walkers remain
byte-exact.

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

import build_ext3_bank21_probe_candidate as runtime
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from hangul_marker import marker_code
from mixed_residual_classification import japanese_character_count
from monoeye_rom import BANK_SIZE, Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CATALOG = ROOT / "data/bank59_opening_batch01_ko.json"
GAP_AUDIT = ROOT / "out/patch/bank59_opening_gap_audit.json"
PROBE_VALIDATION = ROOT / "out/patch/ext3_bank21_probe_user_validation.json"
OUT_ROM = ROOT / "out/patch/bank59_opening_batch01_candidate.wsc"
OUT_SAVE = ROOT / "sram/bank59_opening_batch01_candidate.sav"
OUT_REPORT = ROOT / "out/patch/bank59_opening_batch01_report.json"

EXPECTED_MAIN_SHA256 = runtime.EXPECTED_MAIN_SHA256
EXPECTED_RECORDS = 27
MAX_VISUAL_CELLS = 26
BANK21_SEG = 0x21
POINTER_COUNT = 0x1000
EMPTY_AT = POINTER_COUNT * 2
FIRST_LOCAL = 0x0001


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def alias_token(local: int) -> bytes:
    if not FIRST_LOCAL <= local < runtime.BANK21_LOCAL_COUNT:
        raise BuildError(f"bank21 local out of range: {local:04X}")
    raw = runtime.BANK21_ALIAS_RAW_START + local
    if (raw & 0xFF) == 0:
        raise BuildError(f"unsafe alias token with zero low byte: {raw:04X}")
    return bytes((0xE5, 0x18, (raw >> 8) & 0xFF, raw & 0xFF))


def visual_cells(text: str) -> int:
    return sum(1 for ch in normalize_ko_text(text) if ch not in "\r\n")


def encode_phrase(text: str, tbl: Tbl, dictionary: Any) -> bytes:
    normalized = normalize_ko_text(text)
    if japanese_character_count(normalized):
        raise BuildError(f"Japanese remains in Korean translation: {text}")
    if visual_cells(normalized) > MAX_VISUAL_CELLS:
        raise BuildError(f"translation exceeds {MAX_VISUAL_CELLS} cells: {text}")
    payload = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or not payload or b"\x00" in payload:
        raise BuildError(f"translation is not safely encodable: {text}")
    if dictionary.expand(payload, tbl) != normalized:
        raise BuildError(f"translation render mismatch: {text}")
    return payload


def format_bank21(rows: list[dict[str, Any]]) -> tuple[bytes, list[dict[str, Any]], int]:
    bank = bytearray([0xFF] * BANK_SIZE)
    for local in range(POINTER_COUNT):
        struct.pack_into("<H", bank, local * 2, EMPTY_AT)
    bank[EMPTY_AT] = 0
    cursor = EMPTY_AT + 1
    placements: list[dict[str, Any]] = []
    for row in rows:
        local = int(row["local"])
        payload = bytes(row["encoded"])
        end = cursor + len(payload)
        if end + 1 > BANK_SIZE:
            raise BuildError("bank21 phrase storage overflow")
        struct.pack_into("<H", bank, local * 2, cursor)
        bank[cursor:end] = payload
        bank[end] = 0
        placements.append(
            {
                "abs": row["abs"],
                "local": f"{local:04X}",
                "token": bytes(row["token"]).hex().upper(),
                "pointer": f"{cursor:04X}",
                "end_exclusive": f"{end:04X}",
                "encoded_bytes": len(payload),
                "encoded_sha256": sha256(payload),
                "ko": row["ko"],
            }
        )
        cursor = end + 1
    return bytes(bank), placements, BANK_SIZE - cursor


def in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


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


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != runtime.ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file():
        raise BuildError("main SaveRAM is missing")
    main_save = MAIN_SAVE.read_bytes()
    main_save_sha = sha256(main_save)

    catalog = load_object(CATALOG)
    gap = load_object(GAP_AUDIT)
    validation = load_object(PROBE_VALIDATION)
    if catalog.get("legacy_machine_translation_used") is not False:
        raise BuildError("catalog does not explicitly reject legacy machine translation")
    if catalog.get("translation_source") != "fresh_llm_reviewed":
        raise BuildError("catalog translation source is not approved")
    if validation.get("status") != "user_visual_probe_passed":
        raise BuildError("bank21 first-line user validation is missing")
    if not gap.get("ok"):
        raise BuildError("bank59 opening gap audit is not approved")

    entries = catalog.get("entries") or []
    gap_rows = gap.get("meaningful_records") or []
    if len(entries) != EXPECTED_RECORDS or len(gap_rows) != EXPECTED_RECORDS:
        raise BuildError("bank59 opening record count drifted")
    gap_by_abs = {str(row["abs"]).upper(): row for row in gap_rows}
    entry_by_abs = {str(row["abs"]).upper(): row for row in entries}
    if len(gap_by_abs) != EXPECTED_RECORDS or len(entry_by_abs) != EXPECTED_RECORDS:
        raise BuildError("duplicate addresses in catalog or audit")
    if set(gap_by_abs) != set(entry_by_abs):
        raise BuildError("catalog addresses differ from proven gap population")

    sb = stock_base(parent)
    old_leaf = parent[sb + runtime.OLD_LEAF_START:sb + runtime.OLD_LEAF_END]
    walker_span = parent[sb + runtime.WALKER1_START:sb + runtime.FREE_CAVE_START]
    if sha256(old_leaf) != runtime.EXPECTED_OLD_LEAF_SHA256:
        raise BuildError("accepted ext3 leaf body drifted")
    if sha256(parent[sb + runtime.WALKER1_START:sb + runtime.WALKER2_START]) != runtime.EXPECTED_WALKER1_SHA256:
        raise BuildError("accepted walker1 drifted")
    if sha256(parent[sb + runtime.WALKER2_START:sb + runtime.FREE_CAVE_START]) != runtime.EXPECTED_WALKER2_SHA256:
        raise BuildError("accepted walker2 drifted")
    if parent[sb + runtime.LEAF:sb + runtime.LEAF + 6] != runtime.EXPECTED_LEAF_HOOK:
        raise BuildError("accepted leaf hook drifted")
    if not all(b == 0xFF for b in parent[sb + runtime.FREE_CAVE_START:sb + runtime.FREE_CAVE_END]):
        raise BuildError("runtime cave is not empty")

    bank21_start = BANK21_SEG * BANK_SIZE
    if not all(b == 0xFF for b in parent[bank21_start:bank21_start + BANK_SIZE]):
        raise BuildError("expansion bank21 is not empty")
    parent_alias_hits = runtime.reserved_token_hits(parent)
    if parent_alias_hits:
        raise BuildError(f"reserved alias range already referenced: {parent_alias_hits[:8]}")

    ext_meta = load_ext_meta(runtime.EXT_META_PATH)
    ext3_meta = load_ext_meta(runtime.EXT3_META_PATH)
    if ext3_meta.get("compact3") is not False:
        raise BuildError("compact3 unexpectedly enabled")
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    tbl = Tbl.load(runtime.TBL_PATH)

    prepared: list[dict[str, Any]] = []
    target_ranges: list[tuple[int, int]] = []
    expected_alias_hits: list[int] = []
    for local, address in enumerate(sorted(entry_by_abs), start=FIRST_LOCAL):
        entry = entry_by_abs[address]
        evidence = gap_by_abs[address]
        logical = int(address, 16)
        got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        if got is None:
            raise BuildError(f"unreadable target record: {address}")
        payload = bytes(got[0])
        terminator = int(got[1]) - sb
        prefix, body, kind = split_prefix_body(payload)
        if kind != "dialogue":
            raise BuildError(f"target is not dialogue: {address}")
        if payload.hex().upper() != str(evidence["prefix_hex"]).upper() + str(evidence["body_hex"]).upper():
            raise BuildError(f"target payload differs from gap audit: {address}")
        if prefix.hex().upper() != str(evidence["prefix_hex"]).upper():
            raise BuildError(f"target prefix differs from gap audit: {address}")
        if len(body) != int(evidence["body_capacity"]):
            raise BuildError(f"target body capacity differs: {address}")
        if terminator != int(str(evidence["terminator"]), 16):
            raise BuildError(f"target terminator differs: {address}")
        rendered_source = dictionary.expand(body, tbl).rstrip("\u3000 \t")
        if rendered_source != str(entry["source"]):
            raise BuildError(f"catalog source differs from current ROM: {address}")
        if len(body) < 4:
            raise BuildError(f"target cannot hold an E5 18 token: {address}")

        encoded = encode_phrase(str(entry["ko"]), tbl, dictionary)
        token = alias_token(local)
        replacement = prefix + token + bytes([0x01]) * (len(body) - len(token))
        if len(replacement) != len(payload):
            raise BuildError(f"replacement length mismatch: {address}")
        file_start = sb + logical
        body_start = file_start + len(prefix)
        target_ranges.append((body_start, file_start + len(payload)))
        expected_alias_hits.append(body_start)
        prepared.append(
            {
                "abs": address,
                "logical": logical,
                "file_start": file_start,
                "prefix": prefix,
                "body_capacity": len(body),
                "terminator": terminator,
                "source": entry["source"],
                "ko": normalize_ko_text(str(entry["ko"])),
                "local": local,
                "token": token,
                "encoded": encoded,
                "replacement": replacement,
                "before_payload": payload,
            }
        )

    bank21, placements, phrase_room_after = format_bank21(prepared)
    candidate = bytearray(parent)
    leaf = runtime.build_bank21_leaf()
    candidate[sb + runtime.FREE_CAVE_START:sb + runtime.FREE_CAVE_START + len(leaf)] = leaf
    candidate[sb + runtime.LEAF:sb + runtime.LEAF + 6] = (
        runtime.far_jmp(runtime.FREE_CAVE_START & 0xFFFF, runtime.EXT_CAVE_SEG) + b"\x90"
    )
    candidate[bank21_start:bank21_start + BANK_SIZE] = bank21
    for row in prepared:
        start = int(row["file_start"])
        before = bytes(row["before_payload"])
        replacement = bytes(row["replacement"])
        candidate[start:start + len(before)] = replacement
        if candidate[sb + int(row["terminator"])] != 0:
            raise BuildError(f"target terminator changed: {row['abs']}")
        if candidate[start:start + len(row["prefix"])] != bytes(row["prefix"]):
            raise BuildError(f"target prefix changed: {row['abs']}")

    checksum = update_ws_checksum(candidate)
    allowed = [
        (bank21_start, bank21_start + BANK_SIZE),
        (sb + runtime.FREE_CAVE_START, sb + runtime.FREE_CAVE_START + len(leaf)),
        (sb + runtime.LEAF, sb + runtime.LEAF + 6),
        *target_ranges,
        (len(parent) - 2, len(parent)),
    ]
    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    unaccounted = [i for i in changed if not in_ranges(i, allowed)]
    if unaccounted:
        raise BuildError(f"unaccounted changed bytes: {unaccounted[:16]}")
    if candidate[sb + runtime.OLD_LEAF_START:sb + runtime.OLD_LEAF_END] != old_leaf:
        raise BuildError("accepted old leaf body changed")
    if candidate[sb + runtime.WALKER1_START:sb + runtime.FREE_CAVE_START] != walker_span:
        raise BuildError("accepted walkers changed")
    for seg in range(0x11, 0x21):
        start = seg * BANK_SIZE
        if candidate[start:start + BANK_SIZE] != parent[start:start + BANK_SIZE]:
            raise BuildError(f"accepted ext3 bank {seg:02X} changed")
    stock_start = sb + 0x5F0000
    if candidate[stock_start:stock_start + BANK_SIZE] != parent[stock_start:stock_start + BANK_SIZE]:
        raise BuildError("stock dictionary bank changed")
    candidate_alias_hits = runtime.reserved_token_hits(bytes(candidate))
    if candidate_alias_hits != expected_alias_hits:
        raise BuildError(
            f"candidate alias references differ: {candidate_alias_hits[:32]} != {expected_alias_hits[:32]}"
        )

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(candidate)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    applied = []
    placement_by_abs = {row["abs"]: row for row in placements}
    for row in prepared:
        applied.append(
            {
                "abs": row["abs"],
                "prefix_hex": bytes(row["prefix"]).hex().upper(),
                "body_capacity": row["body_capacity"],
                "terminator": f"{row['terminator']:06X}",
                "source": row["source"],
                "ko": row["ko"],
                "before_payload": bytes(row["before_payload"]).hex().upper(),
                "after_payload": bytes(row["replacement"]).hex().upper(),
                **placement_by_abs[row["abs"]],
            }
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_bank59_opening_batch01_candidate.py",
        "status": "candidate_static_verified",
        "ok": True,
        "parent": {
            "path": str(MAIN.relative_to(ROOT)),
            "sha256": sha256(parent),
            "size": len(parent),
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "sha256": sha256(candidate),
            "size": len(candidate),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "main_path": str(MAIN_SAVE.relative_to(ROOT)),
            "candidate_path": str(OUT_SAVE.relative_to(ROOT)),
            "main_sha256": main_save_sha,
            "candidate_sha256": sha256(OUT_SAVE.read_bytes()),
            "copied_byte_exact": OUT_SAVE.read_bytes() == main_save,
            "policy": "candidate SaveRAM is test-only and must never be promoted back to main",
        },
        "translation": {
            "catalog": str(CATALOG.relative_to(ROOT)),
            "source": catalog["translation_source"],
            "legacy_machine_translation_used": catalog["legacy_machine_translation_used"],
            "records": len(prepared),
            "max_visual_cells": max(visual_cells(row["ko"]) for row in prepared),
        },
        "runtime": {
            "existing_token": "E5 18 xx yy",
            "alias_tokens_used": "E5 18 06 01 .. E5 18 06 1B",
            "expansion_bank": "21",
            "new_token_added": False,
            "new_wram_state_added": False,
            "leaf_address": f"{runtime.FREE_CAVE_START:06X}",
            "leaf_length": len(leaf),
            "leaf_sha256": sha256(leaf),
            "accepted_walkers_byte_exact": True,
            "accepted_old_leaf_body_byte_exact": True,
        },
        "bank21": {
            "pointer_count": POINTER_COUNT,
            "phrases": len(placements),
            "phrase_room_after": phrase_room_after,
            "placements": placements,
        },
        "applied": applied,
        "invariance": {
            "target_prefixes_preserved": True,
            "target_capacities_preserved": True,
            "target_terminators_preserved": True,
            "ext3_banks_11_20_byte_exact": True,
            "stock_dictionary_bank_byte_exact": True,
            "accepted_walkers_byte_exact": True,
            "accepted_old_leaf_body_byte_exact": True,
            "unaccounted_changed_bytes": len(unaccounted),
            "main_rom_untouched": sha256(MAIN.read_bytes()) == EXPECTED_MAIN_SHA256,
            "main_save_untouched": sha256(MAIN_SAVE.read_bytes()) == main_save_sha,
        },
        "diff_runs": diff_runs(parent, bytes(candidate)),
        "test_requirements": [
            "Confirm the already validated first line still renders as Korean.",
            "Advance through the Gihren speech and the Eguile Delaz S-field message; confirm all 27 target lines are Korean and dialogue order is intact.",
            "Confirm there is no Event Error, glyph corruption, freeze, or skipped dialogue.",
            "Enter battle, save using the candidate SaveRAM, fully restart, and reload.",
            "Never copy the candidate .sav back over the main .sav.",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "records": len(applied),
        "phrase_room_after": phrase_room_after,
        "unaccounted_changed_bytes": len(unaccounted),
        "report": str(OUT_REPORT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
