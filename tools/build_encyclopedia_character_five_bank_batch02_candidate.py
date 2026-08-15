#!/usr/bin/env python3
"""Build character-encyclopedia five-bank batch02 candidate.

The candidate uses only the promoted and user-validated E5 18 page aliases in
physical expansion banks 0x21..0x25.  Seventy current residual records are
replaced size-preservingly through the complete Gihren Zabi entry, while phrases
are distributed round-robin across all five banks.  No runtime, stock dictionary, old ext3 bank, main TIP, or main
SaveRAM write is allowed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    verify_non_target_invariance,
)
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, SEG_DICT, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
WORKLIST = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_worklist.json"
CATALOG_VALIDATION = ROOT / "out/patch/encyclopedia_character_current_catalog_validation.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_candidate.wsc"
OUT_SAVE = ROOT / "sram/encyclopedia_character_five_bank_batch02_candidate.sav"
REPORT = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_report.json"

EXPECTED_PARENT_SHA256 = "05a2124e5402c8e8f46b1ca7af3c131748a034939af7c6381cdbb4242d43f65e"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
FIRST_BANK = 0x21
PAGES = 5
POINTER_COUNT = 0x1000
EMPTY_AT = 0x2000
ALIAS_LOCAL_LIMIT = 0x0A00
EXPECTED_ROWS = 70


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def read_phrase(bank: bytes | bytearray, pointer: int) -> bytes:
    if not 0 <= pointer < BANK_SIZE:
        raise BuildError(f"phrase pointer outside bank: {pointer:04X}")
    end = pointer
    while end < BANK_SIZE and bank[end] != 0:
        end += 1
    if end >= BANK_SIZE:
        raise BuildError(f"unterminated phrase at {pointer:04X}")
    return bytes(bank[pointer:end])


def safe_local(local: int) -> bool:
    return 1 <= local < ALIAS_LOCAL_LIMIT and (local & 0xFF) != 0


def alias_token(page: int, local: int) -> bytes:
    if not 0 <= page < PAGES or not safe_local(local):
        raise BuildError(f"unsafe alias allocation page={page} local={local:04X}")
    # Runtime alias decoding subtracts 0x0600 from the token-local field.
    # Addition is required here. Bitwise OR happens to match only below local
    # 0x0200 and silently collides once a large allocation crosses that bit.
    raw_local = 0x0600 + local
    if raw_local >= 0x1000 or (raw_local & 0xFF) == 0:
        raise BuildError(f"unsafe encoded alias local page={page} local={local:04X}")
    raw = (page << 12) | raw_local
    return bytes((0xE5, 0x18, raw >> 8, raw & 0xFF))


def inspect_bank(rom: bytes, page: int) -> dict[str, Any]:
    segment = FIRST_BANK + page
    start = segment * BANK_SIZE
    bank = rom[start:start + BANK_SIZE]
    if len(bank) != BANK_SIZE or bank[EMPTY_AT] != 0:
        raise BuildError(f"bank{segment:02X} layout drifted")
    used: set[int] = set()
    cursor = EMPTY_AT + 1
    for local in range(POINTER_COUNT):
        pointer = int.from_bytes(bank[local * 2:local * 2 + 2], "little")
        if pointer == EMPTY_AT:
            continue
        if not safe_local(local):
            raise BuildError(f"bank{segment:02X} has unsafe used local {local:04X}")
        phrase = read_phrase(bank, pointer)
        used.add(local)
        cursor = max(cursor, pointer + len(phrase) + 1)
    free = [local for local in range(1, ALIAS_LOCAL_LIMIT) if safe_local(local) and local not in used]
    hits = five.scan_range_hits(rom, page)
    referenced: set[int] = set()
    for pos in hits:
        raw = (rom[pos + 2] << 8) | rom[pos + 3]
        referenced.add((raw & 0x0FFF) - 0x0600)
    if not referenced <= used:
        raise BuildError(f"bank{segment:02X} has references to unpopulated locals")
    return {
        "page": page,
        "segment": segment,
        "start": start,
        "bank": bytearray(bank),
        "used_before": used,
        "free": free,
        "referenced_before": referenced,
        "cursor_before": cursor,
        "cursor": cursor,
    }


def load_rows(parent: bytes) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
    validation = json.loads(CATALOG_VALIDATION.read_text(encoding="utf-8"))
    if worklist.get("ok") is not True or validation.get("ok") is not True:
        raise BuildError("catalog validation or worklist did not pass")
    if str((worklist.get("tip") or {}).get("sha256", "")).lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("worklist is not bound to current TIP")
    policy = worklist.get("policy") or {}
    if (
        int(policy.get("records") or 0) != EXPECTED_ROWS
        or policy.get("token") != "existing E5 18 xx yy five-bank alias only"
        or policy.get("new_token") is not False
        or policy.get("runtime_change") is not False
        or policy.get("stock_dictionary_change") is not False
        or policy.get("short_records_included") is not False
    ):
        raise BuildError("worklist safety policy drifted")
    rows = [dict(row) for row in worklist.get("records") or []]
    if len(rows) != EXPECTED_ROWS or len({str(row.get("abs")) for row in rows}) != EXPECTED_ROWS:
        raise BuildError("worklist population drifted")
    prepared: list[dict[str, Any]] = []
    for row in rows:
        address = str(row.get("abs") or "").upper()
        logical = int(address, 16)
        ko = normalize_ko_text(str(row.get("ko") or ""))
        if not ko or any(is_japanese_character(char) for char in ko) or len(ko) > 13:
            raise BuildError(f"invalid approved Korean at {address}: {ko!r}")
        payload, terminator = payload_at(parent, logical)
        expected = bytes.fromhex(str(row.get("current_payload_hex") or ""))
        if payload != expected or len(payload) != int(row.get("payload_len") or 0):
            raise BuildError(f"current payload drifted at {address}")
        if len(payload) < 4:
            raise BuildError(f"short record leaked into E5 18 batch at {address}")
        if terminator != stock_base(parent) + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {address}")
        prepared.append({**row, "logical": logical, "ko": ko, "payload": payload})
    prepared.sort(key=lambda row: int(row["logical"]))
    return worklist, validation, prepared


def allocate(
    parent: bytes,
    phrases: list[tuple[str, bytes]],
) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    states = {page: inspect_bank(parent, page) for page in range(PAGES)}
    assignments: dict[str, dict[str, Any]] = {}
    for sequence, (phrase, encoded) in enumerate(phrases):
        page = sequence % PAGES
        state = states[page]
        if not state["free"]:
            raise BuildError(f"bank{state['segment']:02X} has no free alias locals")
        local = state["free"].pop(0)
        if local in state["referenced_before"]:
            raise BuildError(f"selected local is already referenced: page{page} {local:04X}")
        pointer = int(state["cursor"])
        end = pointer + len(encoded)
        if end + 1 > BANK_SIZE:
            raise BuildError(f"bank{state['segment']:02X} phrase storage exhausted")
        bank = state["bank"]
        struct.pack_into("<H", bank, local * 2, pointer)
        bank[pointer:end] = encoded
        bank[end] = 0
        state["cursor"] = end + 1
        token = alias_token(page, local)
        assignments[phrase] = {
            "page": page,
            "segment": int(state["segment"]),
            "local": local,
            "pointer": pointer,
            "token": token,
            "encoded": encoded,
        }
    return assignments, states


def main() -> int:
    parent = bytes(load_rom(MAIN))
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("main SaveRAM is missing or wrong size")

    worklist, validation, rows = load_rows(parent)
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)

    unique_texts = sorted({str(row["ko"]) for row in rows})
    encoded_by_text = {text: encode_phrase(text, tbl) for text in unique_texts}
    # Deterministic address-first order while still sharing exact duplicates.
    phrase_order: list[str] = []
    seen: set[str] = set()
    for row in rows:
        text = str(row["ko"])
        if text not in seen:
            seen.add(text)
            phrase_order.append(text)
    assignments, states = allocate(
        parent,
        [(text, encoded_by_text[text]) for text in phrase_order],
    )

    candidate = bytearray(parent)
    bank_pointer_extents: list[tuple[int, int]] = []
    bank_phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        segment = int(state["segment"])
        start = int(state["start"])
        candidate[start:start + BANK_SIZE] = state["bank"]
        new_locals = [
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page
        ]
        bank_pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2) for local in new_locals
        )
        if int(state["cursor"]) > int(state["cursor_before"]):
            bank_phrase_extents.append(
                (start + int(state["cursor_before"]), start + int(state["cursor"]))
            )

    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    sb = stock_base(parent)
    for row in rows:
        logical = int(row["logical"])
        capacity = len(row["payload"])
        info = assignments[str(row["ko"])]
        token = bytes(info["token"])
        replacement = token + b"\x01" * (capacity - 4)
        start = sb + logical
        candidate[start:start + capacity] = replacement
        target_extents.append((start, start + capacity))
        applied.append(
            {
                "abs": f"{logical:06X}",
                "jp": row.get("jp"),
                "ko": row["ko"],
                "payload_len": capacity,
                "before_payload_hex": bytes(row["payload"]).hex().upper(),
                "after_payload_hex": replacement.hex().upper(),
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
                "token_hex": token.hex().upper(),
                "phrase_bytes": len(info["encoded"]),
                "phrase_sha256": sha256(info["encoded"]),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        payload, terminator = payload_at(candidate_bytes, logical)
        rendered = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload.hex().upper() != row["after_payload_hex"]:
            reasons.append("payload_mismatch")
        if rendered != row["ko"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(char) for char in rendered):
            reasons.append("japanese_residual")
        if terminator != sb + logical + int(row["payload_len"]) or candidate_bytes[terminator] != 0:
            reasons.append("terminator_mismatch")
        if reasons:
            target_failures.append(
                {"abs": row["abs"], "expected": row["ko"], "actual": rendered, "reasons": reasons}
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )

    page_hits = {page: five.scan_range_hits(candidate_bytes, page) for page in range(PAGES)}
    expected_page_counts = {
        page: len(five.scan_range_hits(parent, page))
        + sum(int(row["page"]) == page for row in applied)
        for page in range(PAGES)
    }
    page_counts_exact = all(len(page_hits[page]) == expected_page_counts[page] for page in range(PAGES))

    stock_start = sb + SEG_DICT * BANK_SIZE
    runtime_7a = (sb + 0x7A0000, sb + 0x7B0000)
    runtime_7f = (sb + 0x7F0000, sb + 0x800000)
    stock_dictionary_exact = (
        parent[stock_start:stock_start + BANK_SIZE]
        == candidate_bytes[stock_start:stock_start + BANK_SIZE]
    )
    runtime_exact = (
        parent[runtime_7a[0]:runtime_7a[1]] == candidate_bytes[runtime_7a[0]:runtime_7a[1]]
        and parent[runtime_7f[0]:runtime_7f[1] - 2]
        == candidate_bytes[runtime_7f[0]:runtime_7f[1] - 2]
    )
    old_ext3_exact = all(
        parent[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        == candidate_bytes[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )

    runs = diff_runs(parent, candidate_bytes)
    allowed = (
        target_extents
        + bank_pointer_extents
        + bank_phrase_extents
        + [(len(parent) - 2, len(parent))]
    )
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    bank_reports: list[dict[str, Any]] = []
    for page, state in states.items():
        segment = int(state["segment"])
        assigned = [info for info in assignments.values() if int(info["page"]) == page]
        bank_reports.append(
            {
                "page": page,
                "physical_bank": f"{segment:02X}",
                "used_slots_before": len(state["used_before"]),
                "new_slots": len(assigned),
                "used_slots_after": len(state["used_before"]) + len(assigned),
                "reference_count_before": len(state["referenced_before"]),
                "reference_count_after": len(page_hits[page]),
                "cursor_before": f"{int(state['cursor_before']):04X}",
                "cursor_after": f"{int(state['cursor']):04X}",
                "phrase_bytes_added": int(state["cursor"]) - int(state["cursor_before"]),
                "phrase_room_after": BANK_SIZE - int(state["cursor"]),
            }
        )

    checks = {
        "parent_tip_exact": sha256(parent) == EXPECTED_PARENT_SHA256,
        "catalog_validation_ok": validation.get("ok") is True,
        "worklist_70": len(rows) == EXPECTED_ROWS,
        "gihren_description_included": {
            "5C0B37", "5C0B3E", "5C0B47", "5C0B59",
            "5C0B68", "5C0B76", "5C0B85",
        } <= {f"{int(row['logical']):06X}" for row in rows},
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "page_reference_counts_exact": page_counts_exact,
        "all_five_banks_receive_new_phrases": all(row["new_slots"] > 0 for row in bank_reports),
        "stock_dictionary_exact": stock_dictionary_exact,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == main_save,
    }
    ok = all(checks.values())
    if not ok:
        print(json.dumps({
            "checks": checks,
            "target_failures": target_failures[:20],
            "invariance": invariance,
            "expected_page_counts": expected_page_counts,
            "actual_page_counts": {str(page): len(page_hits[page]) for page in range(PAGES)},
            "unaccounted": unaccounted[:20],
        }, ensure_ascii=True, indent=2))
        raise BuildError("character five-bank batch02 candidate verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_encyclopedia_character_five_bank_batch02_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_user_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE),
            "policy": "mutable test-only SaveRAM; never promote to main",
        },
        "source_worklist": identity(WORKLIST),
        "catalog_validation": identity(CATALOG_VALIDATION),
        "runtime": {
            "token": "existing E5 18 xx yy",
            "physical_banks": ["21", "22", "23", "24", "25"],
            "new_token": False,
            "runtime_change": False,
            "new_wram_state": False,
        },
        "counts": {
            "targets": len(rows),
            "unique_phrases": len(assignments),
            "target_failures": len(target_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "banks": bank_reports,
        "verification": {
            "checks": checks,
            "target_failures": target_failures,
            "non_target_invariance": invariance,
            "unaccounted_diff_runs": unaccounted,
        },
        "diff": {
            "changed_bytes": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "applied": applied,
        "test_scope": {
            "encyclopedia_range": [applied[0]["abs"], applied[-1]["abs"]],
            "records": len(applied),
            "instruction": (
                "Open the character encyclopedia and inspect entries covering 5C064B..5C0B85, "
                "including every Gihren Zabi description line; "
                "confirm Korean lines, page navigation, return to menu, battle transition, save, "
                "full restart and reload."
            ),
        },
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "counts": report["counts"],
        "banks": report["banks"],
        "diff": report["diff"],
        "test_scope": report["test_scope"],
        "report": str(REPORT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
