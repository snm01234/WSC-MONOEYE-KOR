#!/usr/bin/env python3
"""Build a bounded experimental ext3 candidate for script 60:0000-60:40A4.

This range contains coherent Japanese dialogue but was deliberately excluded from
the normal 60:40A5-63:FFFF writer after older global-pointer and blank-record
experiments caused regressions.  This builder does not use either mechanism:

* Original-ROM zstring boundaries and prefixes are rebound for every record;
* only quality-reviewed rows from translations_apply_all.json are considered;
* event-like/no-dictionary-token/short records are refused fail-closed;
* each accepted body is replaced in place with one ordinary four-byte ext3 token
  plus 0x01 padding, preserving prefix, payload length, terminator and next start;
* no script pointer, stock dictionary pointer, runtime hook or FF-page entry moves;
* new phrases are allocated only in expansion bank 1C, whose free capacity is
  measured from the current accepted TIP.

The result is an experimental ROM/SaveRAM pair.  Static acceptance does not prove
that every scene in this historically out-of-band range reaches the ext3 renderer,
so promotion remains blocked pending emulator/real-hardware coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    _reference_scopes,
    _walk_zstring_range,
    build_free_slot_inventory,
    build_reference_union,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from patch_3byte_dict_token import (  # noqa: E402
    EXP3_SEG0,
    INDEX_BASE,
    bank_local_for_index,
    token_from_ext3_index,
    write_ext3_dictionary_slots,
)

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SHEET = ROOT / "out/script/translations_apply_all.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXP_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

OUT_ROM = ROOT / "out/patch/preopening_ext3_candidate.wsc"
OUT_SAVE = ROOT / "sram/preopening_ext3_candidate.sav"
CAPACITY = ROOT / "out/patch/preopening_ext3_capacity_report.json"
APPROVAL = ROOT / "out/patch/preopening_ext3_approval.json"
REPORT = ROOT / "out/patch/preopening_ext3_report.json"
GATE_DIR = ROOT / "out/patch/preopening_ext3_gates"

LO = 0x600000
HI_EXCLUSIVE = 0x6040A5
ALLOC_SEG = 0x1C
EXPECTED_PARENT_SHA256 = "0c6fd5c71d7ebb1f27204ebd2cff9bf889406fc483b4bd4c5b2e9156e51b8a6b"


class CandidateError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    raw = data if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size": len(raw),
        "sha256": sha256_bytes(raw),
    }


def write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise CandidateError("diff inputs differ in size")
    out: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            out.append((start, index))
            start = None
    if start is not None:
        out.append((start, len(before)))
    return out


def in_ranges(lo: int, hi: int, ranges: Iterable[tuple[int, int]]) -> bool:
    cursor = lo
    for left, right in sorted(ranges):
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= hi:
            return True
    return cursor >= hi


def phrase_cursor(bank: bytes) -> int:
    empty_at = 0x1000 * 2
    cursor = empty_at + 1
    for local in range(0x1000):
        poff = bank[local * 2] | (bank[local * 2 + 1] << 8)
        if poff <= empty_at or poff >= BANK_SIZE:
            continue
        end = poff
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        cursor = max(cursor, min(BANK_SIZE, end + 1))
    return cursor


def verify_parent_nondialogue_invariance(
    parent: bytes,
    candidate: bytes,
    *,
    tbl: Tbl,
    parent_dictionary: Any,
    candidate_dictionary: Any,
) -> dict[str, Any]:
    """Prove this candidate changes no current parent aux/name75 rendering.

    The legacy verifier compares against the pristine Original and therefore also
    reports already-approved historical TIP localization.  This comparison is
    deliberately parent-to-child and catches only newly introduced ext3 aliasing.
    """
    failures: list[dict[str, Any]] = []
    checked = 0
    parent_base = stock_base(parent)
    candidate_base = stock_base(candidate)
    for region, lo, hi, max_len in _reference_scopes():
        if region == "script":
            continue
        for logical, parent_payload, kind in _walk_zstring_range(
            parent, lo, hi, region=region, max_len=max_len
        ):
            checked += 1
            parent_file = parent_base + logical
            candidate_file = candidate_base + logical
            candidate_payload = bytes(
                candidate[candidate_file : candidate_file + len(parent_payload)]
            )
            if candidate_payload != parent_payload:
                failures.append(
                    {
                        "site": f"{logical:06X}",
                        "region": region,
                        "kind": kind,
                        "reason": "payload_changed",
                    }
                )
                continue
            if candidate[candidate_file + len(parent_payload)] != 0:
                failures.append(
                    {
                        "site": f"{logical:06X}",
                        "region": region,
                        "kind": kind,
                        "reason": "terminator_changed",
                    }
                )
                continue
            try:
                before_text = parent_dictionary.expand(parent_payload, tbl)
                after_text = candidate_dictionary.expand(candidate_payload, tbl)
            except Exception as exc:
                failures.append(
                    {
                        "site": f"{logical:06X}",
                        "region": region,
                        "kind": kind,
                        "reason": f"decode_failed:{exc}",
                    }
                )
                continue
            if before_text != after_text:
                failures.append(
                    {
                        "site": f"{logical:06X}",
                        "region": region,
                        "kind": kind,
                        "reason": "render_changed",
                        "before": before_text,
                        "after": after_text,
                    }
                )
    return {
        "ok": not failures,
        "records_checked": checked,
        "failures": failures,
        "failure_count": len(failures),
        "policy": "current_parent_vs_candidate_exact_payload_and_render",
    }


def run_gate(name: str, command: Sequence[str], output: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, *command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    document: dict[str, Any] | None = None
    if output.is_file():
        try:
            document = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = None
    ok = completed.returncode == 0
    if isinstance(document, dict) and "ok" in document:
        ok = ok and document.get("ok") is True
    return {
        "name": name,
        "ok": ok,
        "returncode": completed.returncode,
        "command": [sys.executable, *command],
        "output": identity(output) if output.is_file() else None,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
        "document_status": (
            document.get("status") if isinstance(document, dict) else None
        ),
    }


def collect_rows(
    original: bytes,
    parent: bytes,
    *,
    tbl: Tbl,
    dictionary: Any,
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    original_base = stock_base(original)
    parent_base = stock_base(parent)
    sheet = json.loads(SHEET.read_text(encoding="utf-8"))["lines"]
    counters: Counter[str] = Counter()
    accepted: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []

    for source in sheet:
        try:
            logical = int(str(source["abs"]), 16)
        except (KeyError, TypeError, ValueError):
            continue
        if not LO <= logical < HI_EXCLUSIVE:
            continue
        counters["quality_population"] += 1
        ko = normalize_ko_text(source.get("ko") or "")
        if not ko:
            counters["empty_ko"] += 1
            continue
        if is_low_quality_ko(ko):
            counters["low_quality"] += 1
            continue

        original_record = read_encoded_z_safe(
            original, original_base + logical, max_len=256
        )
        parent_record = read_encoded_z_safe(parent, parent_base + logical, max_len=256)
        if original_record is None or parent_record is None:
            counters["no_record"] += 1
            refused.append({"abs": f"{logical:06X}", "reason": "no_record"})
            continue
        original_payload, original_term_file = original_record
        parent_payload, parent_term_file = parent_record
        if len(original_payload) != len(parent_payload):
            counters["boundary_drift"] += 1
            refused.append({"abs": f"{logical:06X}", "reason": "boundary_drift"})
            continue
        original_prefix, original_body, original_kind = split_prefix_body(original_payload)
        parent_prefix, parent_body, parent_kind = split_prefix_body(parent_payload)
        if original_kind != "dialogue" or parent_kind != "dialogue":
            counters["not_dialogue"] += 1
            refused.append({"abs": f"{logical:06X}", "reason": "not_dialogue"})
            continue
        if original_prefix != parent_prefix:
            counters["prefix_drift"] += 1
            refused.append({"abs": f"{logical:06X}", "reason": "prefix_drift"})
            continue
        try:
            current_text = dictionary.expand(parent_body, tbl).rstrip("\u3000")
        except Exception as exc:
            counters["current_decode_failed"] += 1
            refused.append(
                {"abs": f"{logical:06X}", "reason": f"current_decode_failed:{exc}"}
            )
            continue
        if current_text == ko:
            counters["already_exact"] += 1
            continue
        if len(parent_body) < 4:
            counters["too_short"] += 1
            refused.append(
                {
                    "abs": f"{logical:06X}",
                    "reason": "too_short",
                    "body_capacity": len(parent_body),
                }
            )
            continue
        if looks_like_event_body(original_body):
            counters["event_body"] += 1
            refused.append({"abs": f"{logical:06X}", "reason": "event_body"})
            continue
        if not any(is_dict_token(value) for value in original_body):
            counters["no_dict_token"] += 1
            refused.append(
                {"abs": f"{logical:06X}", "reason": "no_dict_token"}
            )
            continue
        if len(parent_payload) >= 256:
            counters["record_too_long"] += 1
            refused.append(
                {"abs": f"{logical:06X}", "reason": "record_too_long"}
            )
            continue
        pad = len(parent_body) - 4
        if pad > 32:
            counters["pad_too_large"] += 1
            refused.append({"abs": f"{logical:06X}", "reason": "pad_too_large"})
            continue
        encoded = try_encode_ko_text(
            ko,
            tbl,
            hangul_marker_code=marker_code(),
            hangul_marker_mode="run",
        )
        if encoded is None or b"\x00" in encoded:
            counters["encode_fail"] += 1
            refused.append({"abs": f"{logical:06X}", "reason": "encode_fail"})
            continue
        accepted.append(
            {
                "record_id": f"script:{logical:06X}",
                "abs": f"{logical:06X}",
                "logical_address": logical,
                "jp": str(source.get("jp") or ""),
                "ko": ko,
                "prefix_bytes": len(parent_prefix),
                "prefix_hex": parent_prefix.hex().upper(),
                "payload_capacity": len(parent_payload),
                "body_capacity": len(parent_body),
                "terminator": f"{logical + len(parent_payload):06X}",
                "original_payload_sha256": sha256_bytes(bytes(original_payload)),
                "parent_payload_sha256": sha256_bytes(bytes(parent_payload)),
                "encoded_payload_hex": bytes(encoded).hex().upper(),
                "encoded_bytes": bytes(encoded),
                "pad_bytes": pad,
                "original_term_file": original_term_file,
                "parent_term_file": parent_term_file,
            }
        )
        counters["eligible"] += 1

    accepted.sort(key=lambda row: row["logical_address"])
    return accepted, dict(sorted(counters.items())), refused


def build(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(ORIGINAL))
    parent = bytes(load_rom(PARENT))
    if sha256_bytes(parent) != EXPECTED_PARENT_SHA256:
        raise CandidateError("main TIP identity drifted")
    if len(parent) != 16_777_216:
        raise CandidateError("main TIP is not 16 MiB")
    if not PARENT_SAVE.is_file() or PARENT_SAVE.stat().st_size != 32_768:
        raise CandidateError("main 32 KiB SaveRAM is missing")

    tbl = Tbl.load(TBL)
    exp_meta = load_ext_meta(EXP_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16:
        raise CandidateError("expected installed 16-bank ext3 runtime")
    parent_dictionary = make_dictionary_ext3(parent, exp_meta, ext3_meta)
    rows, counters, refused = collect_rows(
        original, parent, tbl=tbl, dictionary=parent_dictionary
    )
    if len(rows) != 808:
        raise CandidateError(f"eligibility drifted: expected 808, got {len(rows)}")

    union = build_reference_union(
        original, parent, ext_meta=exp_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent,
        union=union,
        ext_meta=exp_meta,
        ext3_meta=ext3_meta,
    )
    allocation_bank = ALLOC_SEG - EXP3_SEG0
    bank_room = int(inventory.ext3_bank_room.get(allocation_bank, 0))

    existing_payload: dict[bytes, int] = {}
    for index in range(INDEX_BASE, parent_dictionary.count):
        try:
            token_from_ext3_index(index, num_banks=num_banks)
            payload = bytes(parent_dictionary.raw_entry(index))
        except Exception:
            continue
        if payload:
            existing_payload.setdefault(payload, index)

    grouped: dict[bytes, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[bytes.fromhex(str(row["encoded_payload_hex"]))].append(row)
    phrases = sorted(
        grouped.items(),
        key=lambda item: (-len(item[1]), item[1][0]["logical_address"]),
    )

    free_indices = [
        index
        for index in inventory.ext3_free
        if bank_local_for_index(index)[0] == ALLOC_SEG
    ]
    free_indices.sort()
    assignments: dict[bytes, tuple[int, bool]] = {}
    slot_payload: dict[int, bytes] = {}
    free_cursor = 0
    bytes_needed = 0
    for payload, _phrase_rows in phrases:
        existing = existing_payload.get(payload)
        if existing is not None:
            assignments[payload] = (existing, False)
            continue
        if free_cursor >= len(free_indices):
            raise CandidateError("bank 1C has insufficient free ext3 slots")
        need = len(payload) + 1
        if bytes_needed + need > bank_room:
            raise CandidateError("bank 1C has insufficient phrase bytes")
        index = free_indices[free_cursor]
        free_cursor += 1
        bytes_needed += need
        assignments[payload] = (index, True)
        slot_payload[index] = payload

    allocation_rows: list[dict[str, Any]] = []
    for payload, phrase_rows in phrases:
        index, write_required = assignments[payload]
        allocation_rows.append(
            {
                "ext3_index": f"{index:05X}",
                "write_required": write_required,
                "target_ko": phrase_rows[0]["ko"],
                "encoded_payload_hex": payload.hex().upper(),
                "record_count": len(phrase_rows),
                "records": [row["record_id"] for row in phrase_rows],
            }
        )
        token = token_from_ext3_index(index, num_banks=num_banks)
        for row in phrase_rows:
            row["ext3_index"] = f"{index:05X}"
            row["token_hex"] = token.hex().upper()
            row["write_required"] = write_required

    capacity_report = {
        "schema_version": 1,
        "generated_by": "tools/build_preopening_ext3_candidate.py",
        "read_only_analysis": True,
        "inputs": {
            "original_rom": identity(ORIGINAL, original),
            "parent_tip": identity(PARENT, parent),
            "parent_save": identity(PARENT_SAVE),
            "sheet": identity(SHEET),
            "tbl": identity(TBL),
            "exp_meta": identity(EXP_META),
            "ext3_meta": identity(EXT3_META),
        },
        "scope": {
            "logical_start": f"{LO:06X}",
            "logical_end_exclusive": f"{HI_EXCLUSIVE:06X}",
            "historical_policy": "outside_normal_dialogue_band",
            "record_strategy": "original_boundary_in_place_ext3_only",
        },
        "population": {
            "counters": counters,
            "eligible_records": len(rows),
            "eligible_unique_phrases": len(phrases),
            "new_slots": len(slot_payload),
            "reused_existing_ext3_phrases": len(phrases) - len(slot_payload),
            "refused_records": len(refused),
            "refused": refused,
        },
        "capacity": {
            "allocation_expansion_bank": f"{ALLOC_SEG:02X}",
            "free_slots_before": len(free_indices),
            "phrase_room_before": bank_room,
            "new_phrase_bytes_required": bytes_needed,
            "phrase_room_after_projected": bank_room - bytes_needed,
            "all_eligible_fit": True,
        },
        "allocations": allocation_rows,
        "records": [
            {key: value for key, value in row.items() if key != "encoded_bytes"}
            for row in rows
        ],
        "decision": {
            "static_capacity": "GO",
            "candidate_generation": "GO",
            "promotion": "HOLD_runtime_ext3_route_not_proven_for_all_scenes",
        },
    }
    write_json(CAPACITY, capacity_report)
    if args.analyze_only:
        return {
            "status": "analysis_only",
            "capacity_report": identity(CAPACITY),
            "eligible_records": len(rows),
            "unique_phrases": len(phrases),
            "new_phrase_bytes": bytes_needed,
        }

    candidate = bytearray(parent)
    bank_before = slice_expansion_bank(candidate, ALLOC_SEG)
    cursor_before = phrase_cursor(bank_before)
    dictionary_write = write_ext3_dictionary_slots(
        candidate, slot_payload, num_banks=num_banks
    )
    if dictionary_write.get("written") != len(slot_payload):
        raise CandidateError("not every selected ext3 slot was written")
    if dictionary_write.get("skipped_overflow"):
        raise CandidateError("ext3 writer reported overflow")

    parent_base = stock_base(parent)
    target_body_ranges: list[tuple[int, int]] = []
    for row in rows:
        logical = int(row["logical_address"])
        payload_len = int(row["payload_capacity"])
        prefix_len = int(row["prefix_bytes"])
        file_start = parent_base + logical
        parent_payload = bytes(parent[file_start : file_start + payload_len])
        token = bytes.fromhex(str(row["token_hex"]))
        new_payload = (
            parent_payload[:prefix_len]
            + token
            + b"\x01" * (payload_len - prefix_len - len(token))
        )
        if len(new_payload) != payload_len:
            raise CandidateError(f"size drift at {logical:06X}")
        candidate[file_start : file_start + payload_len] = new_payload
        target_body_ranges.append(
            (file_start + prefix_len, file_start + payload_len)
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(candidate_bytes)
    shutil.copy2(PARENT_SAVE, OUT_SAVE)

    candidate_dictionary = make_dictionary_ext3(candidate_bytes, exp_meta, ext3_meta)
    target_failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical_address"])
        payload_len = int(row["payload_capacity"])
        prefix_len = int(row["prefix_bytes"])
        file_start = parent_base + logical
        payload = candidate_bytes[file_start : file_start + payload_len]
        parent_payload = parent[file_start : file_start + payload_len]
        if payload[:prefix_len] != parent_payload[:prefix_len]:
            target_failures.append({"abs": f"{logical:06X}", "reason": "prefix_changed"})
            continue
        if candidate_bytes[file_start + payload_len] != 0:
            target_failures.append({"abs": f"{logical:06X}", "reason": "terminator_changed"})
            continue
        try:
            rendered = candidate_dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000")
        except Exception as exc:
            target_failures.append(
                {"abs": f"{logical:06X}", "reason": f"decode_failed:{exc}"}
            )
            continue
        if rendered != row["ko"]:
            target_failures.append(
                {
                    "abs": f"{logical:06X}",
                    "reason": "render_mismatch",
                    "expected": row["ko"],
                    "actual": rendered,
                }
            )

    bank_after = slice_expansion_bank(candidate_bytes, ALLOC_SEG)
    cursor_after = phrase_cursor(bank_after)
    pointer_ranges: list[tuple[int, int]] = []
    bank_file_start = ALLOC_SEG * BANK_SIZE
    for index in slot_payload:
        _seg, local = bank_local_for_index(index)
        pointer_ranges.append(
            (bank_file_start + local * 2, bank_file_start + local * 2 + 2)
        )
    payload_range = (bank_file_start + cursor_before, bank_file_start + cursor_after)
    allowed_ranges = (
        target_body_ranges
        + pointer_ranges
        + [payload_range, (len(parent) - 2, len(parent))]
    )
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not in_ranges(lo, hi, allowed_ranges)
    ]

    stock_5f_start = parent_base + 0x5F0000
    stock_5f_end = stock_5f_start + BANK_SIZE
    stock_5f_unchanged = (
        parent[stock_5f_start:stock_5f_end]
        == candidate_bytes[stock_5f_start:stock_5f_end]
    )
    hook_ranges = ((parent_base + 0x7A0600, parent_base + 0x7A1000),)
    runtime_unchanged = all(
        parent[lo:hi] == candidate_bytes[lo:hi] for lo, hi in hook_ranges
    )
    other_expansion_banks_unchanged = all(
        slice_expansion_bank(parent, seg) == slice_expansion_bank(candidate_bytes, seg)
        for seg in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if seg != ALLOC_SEG
    )

    nondialogue_invariance = verify_parent_nondialogue_invariance(
        parent,
        candidate_bytes,
        tbl=tbl,
        parent_dictionary=parent_dictionary,
        candidate_dictionary=candidate_dictionary,
    )
    nondialogue_invariance_path = GATE_DIR / "nondialogue_parent_invariance.json"
    write_json(nondialogue_invariance_path, nondialogue_invariance)

    approval_document = {
        "schema_version": 1,
        "generated_by": "tools/build_preopening_ext3_candidate.py",
        "mode": "experimental_preopening_ext3_approval",
        "ok": (
            not target_failures
            and not unaccounted
            and stock_5f_unchanged
            and runtime_unchanged
            and other_expansion_banks_unchanged
            and nondialogue_invariance["ok"]
        ),
        "parent_rom": identity(PARENT, parent),
        "candidate_rom": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "capacity_report": identity(CAPACITY),
        "scope": [f"{LO:06X}", f"{HI_EXCLUSIVE - 1:06X}"],
        "approved_records": len(rows),
        "approved_ext3_slots": [f"{index:05X}" for index in sorted(slot_payload)],
        "approved_change_ranges": [
            {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}"}
            for lo, hi in sorted(allowed_ranges)
        ],
        "proof": {
            "parent_sha_locked": sha256_bytes(parent) == EXPECTED_PARENT_SHA256,
            "original_boundaries_rebound": True,
            "prefixes_preserved": not any(
                failure.get("reason") == "prefix_changed" for failure in target_failures
            ),
            "terminators_preserved": not any(
                failure.get("reason") == "terminator_changed" for failure in target_failures
            ),
            "event_like_records_refused": True,
            "no_dict_token_records_refused": True,
            "record_lengths_preserved": True,
            "target_render_exact": not target_failures,
            "diffs_within_exact_extents": not unaccounted,
            "stock_5f_unchanged": stock_5f_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "other_ext3_banks_unchanged": other_expansion_banks_unchanged,
            "parent_nondialogue_render_unchanged": nondialogue_invariance["ok"],
            "no_pointer_relocation": True,
            "no_ff_page_write": True,
            "same_stem_save_present": OUT_SAVE.is_file() and OUT_SAVE.stat().st_size == 32_768,
        },
        "target_failures": target_failures,
        "unaccounted_diff_runs": unaccounted,
        "diff": {
            "bytes": sum(hi - lo for lo, hi in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "ext3_write": {
            **dictionary_write,
            "allocation_bank": f"{ALLOC_SEG:02X}",
            "cursor_before": f"{cursor_before:04X}",
            "cursor_after": f"{cursor_after:04X}",
            "phrase_bytes": cursor_after - cursor_before,
        },
        "parent_nondialogue_invariance": identity(nondialogue_invariance_path),
        "runtime_status": "not_tested_promotion_blocked",
    }
    write_json(APPROVAL, approval_document)
    if approval_document["ok"] is not True:
        raise CandidateError("pre-gate approval failed")

    GATE_DIR.mkdir(parents=True, exist_ok=True)
    structure_path = GATE_DIR / "structure.json"
    nondialogue_path = GATE_DIR / "nondialogue.json"
    false_segptr_path = GATE_DIR / "false_segptr.json"
    smoke_path = GATE_DIR / "smoke.json"
    gates = [
        run_gate(
            "structure",
            [
                "tools/scan_script_record_structure.py",
                "--jp",
                str(ORIGINAL),
                "--target",
                str(OUT_ROM),
                "--lo",
                hex(LO),
                "--hi",
                hex(HI_EXCLUSIVE),
                "--out",
                str(structure_path),
            ],
            structure_path,
        ),
        run_gate(
            "nondialogue",
            [
                "tools/verify_nondialogue_text.py",
                "--jp",
                str(ORIGINAL),
                "--target",
                str(OUT_ROM),
                "--baseline",
                str(PARENT),
                "--ui-report-dir",
                str(ROOT / "out/patch"),
                "--out",
                str(nondialogue_path),
                "--quiet",
            ],
            nondialogue_path,
        ),
        run_gate(
            "false_segptr",
            [
                "tools/scan_false_segptr_writes.py",
                "--jp",
                str(ORIGINAL),
                "--target",
                str(OUT_ROM),
                "--lo-bank",
                "0x60",
                "--hi-bank",
                "0x60",
                "--out",
                str(false_segptr_path),
            ],
            false_segptr_path,
        ),
        run_gate(
            "legacy_smoke",
            [
                "tools/verify_all_stages_smoke.py",
                "--rom",
                str(OUT_ROM),
                "--report",
                str(smoke_path),
                "--baseline-meta",
                str(ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_baseline_meta.json"),
            ],
            smoke_path,
        ),
    ]

    smoke_document = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke_parent_invariant = (
        smoke_document.get("unit_vs_tip_nonzero") == {}
        and smoke_document.get("opening_required_ok") is True
        and smoke_document.get("hangul_ok") is True
        and smoke_document.get("jagd_ok") is True
    )

    # The legacy nondialogue verifier compares against the pristine Original and
    # reports already-approved historical TIP localization.  Keep it as diagnostic
    # evidence, but candidate acceptance uses the independent parent-to-child
    # invariance proof above.  Structure and false-segptr remain blocking.
    blocking_gates_ok = all(
        gate["ok"]
        for gate in gates
        if gate["name"] not in {"nondialogue", "legacy_smoke"}
    )
    static_ok = approval_document["ok"] and blocking_gates_ok and smoke_parent_invariant
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_preopening_ext3_candidate.py",
        "status": (
            "experimental_static_accepted_runtime_pending"
            if static_ok
            else "rejected"
        ),
        "accepted_static": static_ok,
        "published": False,
        "main_tip_modified": False,
        "parent_tip": identity(PARENT, parent),
        "candidate_rom": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "capacity_report": identity(CAPACITY),
        "approval_report": identity(APPROVAL),
        "targets": {
            "eligible": len(rows),
            "decoded_exact": len(rows) - len(target_failures),
            "unique_phrases": len(phrases),
            "new_slots": len(slot_payload),
            "reused_slots": len(phrases) - len(slot_payload),
            "remaining_too_short": counters.get("too_short", 0),
            "remaining_no_dict_token": counters.get("no_dict_token", 0),
        },
        "diff": approval_document["diff"],
        "ext3_write": approval_document["ext3_write"],
        "gates": gates,
        "legacy_nondialogue_gate": {
            "blocking": False,
            "reason": "compares pristine Original against already-localized parent TIP",
        },
        "legacy_smoke_gate": {
            "blocking": False,
            "reason": "unit-bank Original comparison lacks cumulative P2 approvals",
            "parent_tip_unit_banks_unchanged": smoke_document.get("unit_vs_tip_nonzero") == {},
            "opening_required_ok": smoke_document.get("opening_required_ok"),
            "hangul_ok": smoke_document.get("hangul_ok"),
            "jagd_ok": smoke_document.get("jagd_ok"),
            "candidate_specific_checks_ok": smoke_parent_invariant,
        },
        "parent_nondialogue_invariance": nondialogue_invariance,
        "static_proof": approval_document["proof"],
        "runtime_gate": {
            "status": "pending",
            "blocking_for_promotion": True,
            "reason": (
                "the normal accepted writer deliberately excludes 600000-6040A4; "
                "static in-place safety is proven, but scene reachability and ext3 "
                "renderer traversal must be observed in emulator or hardware"
            ),
            "minimum_checks": [
                "load a scene that reads a record in 600005-603B39",
                "confirm Korean rendering without BADDICT/padding artifacts",
                "continue through the next event transition without event error",
                "verify one late record near 603F91",
            ],
        },
        "rollback": (
            "delete preopening_ext3_candidate.wsc/.sav, capacity/approval/report JSON "
            "and preopening_ext3_gates; main TIP is unchanged"
        ),
    }
    write_json(REPORT, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build(args)
    except CandidateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.analyze_only:
        return 0
    return 0 if result.get("accepted_static") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
