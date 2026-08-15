#!/usr/bin/env python3
"""Rebase the 2026-08-12 LLM-reviewed scenario translation onto current main TIP.

Conservative rules:
- every applied display row is <=20 cells (therefore any true two-row screen <=40),
- current runtime-contract record extent/prefix/terminator boundaries are preserved,
- quarantine record bytes are never changed,
- quarantine rows may change only through an already-present, consumer-safe ext3 slot,
- active ext3-capable rows may be detached to a new true-free ext3 slot,
- short active native rows are changed only when the new direct payload fits,
- shared/nested dictionary slots are never overwritten when consumers disagree.

This produces a test candidate only. It never promotes the main TIP.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_garrod_native_stock_guard import scan_families  # noqa: E402
from build_terminology_retranslation_candidate import ext3_storage_proof, inplace_phrase  # noqa: E402
from dialogue_runtime_contracts import audit_manifest, build_manifest  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from mixed_residual_reference_union import build_reference_union  # noqa: E402
from monoeye_rom import BANK_SIZE, Tbl, le16, load_rom, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from patch_3byte_dict_token import INDEX_BASE, index_end, token_from_ext3_index  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
CONTRACT_PATH = ROOT / "out/script/dialogue_runtime_contracts.json"
OVERRIDES = ROOT / "data/main_translation_rebase_overrides_ko.json"
TERMINOLOGY_OVERRIDES = ROOT / "data/main_translation_terminology_overrides_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/main_translation_rebase_candidate.wsc"
OUT_SAVE = ROOT / "sram/main_translation_rebase_candidate.sav"
OUT_REPORT = ROOT / "out/patch/main_translation_rebase_candidate_report.json"
OUT_CONTRACT = ROOT / "out/script/main_translation_rebase_candidate_contracts.json"
EXPECTED_MAIN_SHA = "9468c74624483a7b4a3b55c896d5277b73afc1b80fd5270e8e4b9d69fbfa197a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LINE_LIMIT = 20
# Japanese letters/ideographs only. U+30FB middle dot is intentionally allowed.
JP_TEXT_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u4e00-\u9fff]")


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ext3_index(body: bytes) -> int | None:
    if len(body) >= 4 and body[:2] == b"\xE5\x18":
        return INDEX_BASE + (body[2] << 8) + body[3]
    return None


def load_reviewed_rows() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    source_files = 0
    rejected_status = 0
    blank = 0
    for path in sorted(RESULT_DIR.glob("MR*_reviewed.csv")):
        source_files += 1
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                address = str(raw.get("abs") or "").upper()
                proposed = str(raw.get("proposed_ko") or "").strip()
                status = str(raw.get("review_status") or raw.get("new_review_status") or "")
                if not proposed:
                    blank += 1
                    continue
                if status != "llm_retranslated_structural_hold":
                    rejected_status += 1
                    continue
                if address in rows:
                    raise BuildError(f"duplicate reviewed address {address}")
                row = dict(raw)
                row["proposed_ko"] = normalize_ko_text(proposed)
                row["result_file"] = str(path.relative_to(ROOT)).replace("\\", "/")
                rows[address] = row

    override_doc = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    applied_overrides = 0
    for address, text in (override_doc.get("targets") or {}).items():
        address = str(address).upper()
        if address not in rows:
            raise BuildError(f"override target not present in reviewed rows: {address}")
        rows[address]["proposed_ko"] = normalize_ko_text(str(text))
        rows[address]["constraint_override"] = True
        applied_overrides += 1
    terminology_override_doc = json.loads(TERMINOLOGY_OVERRIDES.read_text(encoding="utf-8"))
    if terminology_override_doc.get("review_status") != "approved_for_candidate_build":
        raise BuildError("terminology override catalog is not approved")
    terminology_overrides = 0
    for address, text in (terminology_override_doc.get("targets") or {}).items():
        address = str(address).upper()
        if address not in rows:
            raise BuildError(f"terminology override target not present in reviewed rows: {address}")
        rows[address]["proposed_ko"] = normalize_ko_text(str(text))
        rows[address]["terminology_override"] = True
        terminology_overrides += 1
    return rows, {
        "result_files": source_files,
        "blank_rows_ignored": blank,
        "nonfinal_status_rows_ignored": rejected_status,
        "constraint_overrides": applied_overrides,
        "terminology_overrides": terminology_overrides,
        "deferred_overrides": override_doc.get("deferred") or {},
    }


def logical_for_physical(seg: int, local: int, *, alias_pages: int) -> int | None:
    if 0x11 <= seg < 0x11 + alias_pages:
        page = seg - 0x11
        if local >= 0x600:
            return None
        return INDEX_BASE + page * 0x1000 + local
    if 0x11 + alias_pages <= seg <= 0x20:
        page = seg - 0x11
        return INDEX_BASE + page * 0x1000 + local
    if 0x21 <= seg < 0x21 + alias_pages:
        page = seg - 0x21
        if local >= 0xA00:
            return None
        return INDEX_BASE + page * 0x1000 + local + 0x600
    return None


def bank_cursor(rom: bytes, seg: int, *, union, alias_pages: int) -> int:
    base = seg * BANK_SIZE
    cursor = 0x2001
    for local in range(0x1000):
        ptr = le16(rom, base + local * 2)
        if ptr < 0x2000 or ptr >= BANK_SIZE:
            continue
        end = rom.find(b"\x00", base + ptr, base + BANK_SIZE)
        if end < 0:
            logical = logical_for_physical(seg, local, alias_pages=alias_pages)
            if logical is None or union.is_true_free(logical):
                continue
            raise BuildError(f"live unterminated ext3 slot {seg:02X}:{local:03X}")
        cursor = max(cursor, end - base + 1)
    return cursor


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError("current main TIP identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("current SaveRAM missing or wrong size")

    reviewed, review_meta = load_reviewed_rows()
    contracts_doc = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contracts = {str(row["address"]).upper(): row for row in contracts_doc["contracts"]}
    if set(reviewed) - set(contracts):
        raise BuildError("review rows exist outside runtime contract")
    runtime_native_first_protected = {
        address
        for address, contract in contracts.items()
        if contract.get("route") == "scenario_first"
        and not bool((contract.get("decoder") or {}).get("ext3"))
        and str(contract.get("confidence") or "") == "runtime-proven"
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16:
        raise BuildError(f"unexpected ext3 bank count {num_banks}")
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    alias_pages = int(d_parent.ext3_alias_page_count)
    if alias_pages != 5:
        raise BuildError(f"expected active five-page ext3 alias runtime, got {alias_pages}")
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    # Page-boundary grammar protection.  In this family the first record is
    # followed by double-NUL, then an 18-head continuation and a bare third
    # continuation.  Runtime testing has proven that collapsing an original
    # [native dict, native dict] first-line body into one E5 18 portal can make
    # the following 18 byte leak as visible Japanese こ.  Preserve every such
    # first record that is still native in the current promoted main.
    parent_family_rows, family_scan_errors = scan_families(parent, original)
    if family_scan_errors:
        raise BuildError(f"page-boundary family scan failed: {family_scan_errors[:5]}")
    page_boundary_native_protected = {
        f"{int(row['logical']):06X}"
        for row in parent_family_rows
        if row.get("source_exact_native_two_token")
        and row.get("current_native_two_token_with_padding")
    }
    parent_family_non_native = {
        f"{int(row['logical']):06X}"
        for row in parent_family_rows
        if row.get("source_exact_native_two_token")
        and not row.get("current_native_two_token_with_padding")
    }

    encoded: dict[str, bytes] = {}
    preflight_skips: dict[str, str] = {}
    width_rows = []
    deferred_addresses = {str(value).upper() for value in review_meta["deferred_overrides"]}
    for address, row in sorted(reviewed.items()):
        text = str(row["proposed_ko"])
        cells = len(text.replace("<E62F>", ""))
        width_rows.append({"abs": address, "cells": cells, "text": text})
        if cells > LINE_LIMIT:
            raise BuildError(f"20-cell rule violation {address}: {cells} {text!r}")
        if address in deferred_addresses:
            preflight_skips[address] = "deferred_constraint_or_runtime_protection"
            continue
        if address in runtime_native_first_protected:
            preflight_skips[address] = "runtime_proven_native_first_preserved"
            continue
        if address in page_boundary_native_protected:
            preflight_skips[address] = "page_boundary_native_two_token_preserved"
            continue
        if JP_TEXT_RE.search(text):
            preflight_skips[address] = "japanese_text_residual"
            continue
        payload = try_encode_ko_text(
            text, tbl,
            hangul_marker_code=marker_code(), hangul_marker_mode="run",
        )
        if payload is None or b"\x00" in payload:
            preflight_skips[address] = "unencodable_with_current_font_tbl"
            continue
        encoded[address] = bytes(payload)

    candidate = bytearray(parent)
    sb = stock_base(parent)
    applied: dict[str, dict[str, Any]] = {}
    skipped: dict[str, dict[str, Any]] = {
        address: {"reason": reason, "text": reviewed[address]["proposed_ko"]}
        for address, reason in preflight_skips.items()
    }
    storage_changes: list[dict[str, Any]] = []
    body_ranges: list[tuple[int, int]] = []

    # Tail cursors are computed from every existing pointer entry. One known bad,
    # true-free bank14 pointer is ignored by bank_cursor rather than compacting.
    physical_segs = list(range(0x11, 0x21)) + list(range(0x21, 0x26))
    cursors = {
        seg: bank_cursor(parent, seg, union=union, alias_pages=alias_pages)
        for seg in physical_segs
    }
    cursor_start = dict(cursors)

    def append_slot(index: int, payload: bytes) -> bool:
        seg, local = d_parent._ext3_bank_local(index)
        cursor = cursors[int(seg)]
        need = len(payload) + 1
        if cursor + need > BANK_SIZE:
            return False
        base = int(seg) * BANK_SIZE
        candidate[base + cursor:base + cursor + len(payload)] = payload
        candidate[base + cursor + len(payload)] = 0
        candidate[base + local * 2:base + local * 2 + 2] = cursor.to_bytes(2, "little")
        cursors[int(seg)] = cursor + need
        storage_changes.append({
            "kind": "slot_repoint_append",
            "index": f"{index:05X}",
            "physical_segment": f"{int(seg):02X}",
            "physical_local": f"{int(local):03X}",
            "new_ptr": f"{cursor:04X}",
            "encoded_len": len(payload),
        })
        return True

    # True-free, non-alias logical slots are the private allocation pool.
    free_by_seg: dict[int, deque[int]] = defaultdict(deque)
    for index in range(INDEX_BASE, index_end(num_banks) + 1):
        raw = index - INDEX_BASE
        if ((raw >> 8) & 0xFF) == 0 or (raw & 0xFF) == 0:
            continue
        if d_parent._ext3_is_alias(index) or not union.is_true_free(index):
            continue
        seg, _local = d_parent._ext3_bank_local(index)
        free_by_seg[int(seg)].append(index)

    allocated_private: set[int] = set()

    def allocate_private(payload: bytes) -> int | None:
        need = len(payload) + 1
        candidates = [
            seg for seg, slots in free_by_seg.items()
            if slots and cursors.get(seg, BANK_SIZE) + need <= BANK_SIZE
        ]
        if not candidates:
            return None
        seg = max(candidates, key=lambda value: BANK_SIZE - cursors[value])
        index = free_by_seg[seg].popleft()
        if index in allocated_private:
            raise BuildError(f"private slot allocated twice {index:05X}")
        allocated_private.add(index)
        if not append_slot(index, payload):
            raise BuildError("private allocator cursor race")
        return index

    def apply_body(address: str, payload: bytes, mode: str, *, slot: int | None = None) -> None:
        contract = contracts[address]
        capacity = int(contract["body_capacity"])
        if len(payload) > capacity:
            raise BuildError(f"payload does not fit {address}: {len(payload)}>{capacity}")
        logical = int(contract["body_start"], 16)
        start = sb + logical
        before = bytes.fromhex(str(contract.get("baseline_body_hex") or ""))
        if bytes(parent[start:start + capacity]) != before:
            raise BuildError(f"contract body drift at {address}")
        if any(a < start + capacity and start < b for a, b in body_ranges):
            raise BuildError(f"overlapping body edit {address}")
        after = bytes(payload) + b"\x01" * (capacity - len(payload))
        candidate[start:start + capacity] = after
        body_ranges.append((start, start + capacity))
        applied[address] = {
            "mode": mode,
            "text": reviewed[address]["proposed_ko"],
            "body_capacity": capacity,
            "slot": f"{slot:05X}" if slot is not None else None,
        }

    by_index: dict[int, list[str]] = defaultdict(list)
    native_rows: list[str] = []
    for address in reviewed:
        body = bytes.fromhex(str(contracts[address].get("baseline_body_hex") or ""))
        index = ext3_index(body)
        if index is None:
            native_rows.append(address)
        else:
            by_index[index].append(address)

    safe_indices: set[int] = set()
    unsafe_index_reasons: dict[int, list[str]] = {}
    all_reviewed_addresses = set(reviewed)
    for index, addresses in by_index.items():
        working_consumers = {
            f"{consumer.abs:06X}"
            for consumer in union.consumers_for(index)
            if "working" in consumer.seen_in
        }
        reasons: list[str] = []
        if union.parents_of(index):
            reasons.append("nested_dictionary_parent")
        if not working_consumers:
            reasons.append("no_working_consumer")
        if not working_consumers.issubset(all_reviewed_addresses):
            reasons.append("nonreviewed_consumer")
        if working_consumers != set(addresses):
            reasons.append("consumer_group_mismatch")
        desired = {reviewed[address]["proposed_ko"] for address in working_consumers if address in reviewed}
        if len(desired) != 1:
            reasons.append("conflicting_proposals")
        if reasons:
            unsafe_index_reasons[index] = reasons
        else:
            safe_indices.add(index)

    # 1) Consumer-safe existing ext3 slots. Prefer byte-local in-place writes,
    # then same-slot pointer append (also safe for quarantine record bytes).
    for index in sorted(safe_indices):
        addresses = by_index[index]
        usable = [address for address in addresses if address in encoded]
        if len(usable) != len(addresses):
            for address in addresses:
                if address not in encoded and address not in skipped:
                    skipped[address] = {"reason": "group_contains_unencodable_row", "text": reviewed[address]["proposed_ko"]}
            continue
        text = reviewed[addresses[0]]["proposed_ko"]
        if any(reviewed[address]["proposed_ko"] != text for address in addresses):
            raise BuildError(f"safe index proposal drift {index:05X}")
        payload = encoded[addresses[0]]
        proof = None
        try:
            proof = ext3_storage_proof(parent, d_parent, index)
        except Exception:
            proof = None
        if proof and proof.get("ok") and len(payload) <= int(proof["old_len"]):
            start, end = inplace_phrase(candidate, proof, payload)
            storage_changes.append({
                "kind": "slot_inplace",
                "index": f"{index:05X}",
                "range": [start, end],
                "old_len": int(proof["old_len"]),
                "new_len": len(payload),
            })
            for address in addresses:
                applied[address] = {"mode": "existing_ext3_inplace", "text": text, "slot": f"{index:05X}"}
            continue
        if append_slot(index, payload):
            for address in addresses:
                applied[address] = {"mode": "existing_ext3_repoint_append", "text": text, "slot": f"{index:05X}"}
            continue

        # No room in this physical bank. Active/ext3-proven consumers may detach;
        # quarantine consumers remain byte-exact and are left on the old phrase.
        for address in addresses:
            contract = contracts[address]
            if contract.get("status") == "active" and bool((contract.get("decoder") or {}).get("ext3")) and int(contract["body_capacity"]) >= 4:
                new_index = allocate_private(encoded[address])
                if new_index is None:
                    skipped[address] = {"reason": "private_ext3_capacity_exhausted", "text": reviewed[address]["proposed_ko"]}
                    continue
                apply_body(address, token_from_ext3_index(new_index, num_banks=num_banks), "active_detach_after_same_slot_no_room", slot=new_index)
            else:
                skipped[address] = {"reason": "same_slot_growth_no_room_quarantine_preserved", "text": reviewed[address]["proposed_ko"]}

    # 2) Shared/nested ext3 slots: never rewrite the shared slot. Detach only
    # active ext3-proven rows into newly allocated true-free slots.
    for index, addresses in sorted(by_index.items()):
        if index in safe_indices:
            continue
        reasons = unsafe_index_reasons.get(index) or ["unsafe_existing_slot"]
        for address in addresses:
            if address not in encoded:
                continue
            contract = contracts[address]
            if contract.get("status") == "active" and bool((contract.get("decoder") or {}).get("ext3")) and int(contract["body_capacity"]) >= 4:
                new_index = allocate_private(encoded[address])
                if new_index is None:
                    skipped[address] = {"reason": "private_ext3_capacity_exhausted", "text": reviewed[address]["proposed_ko"]}
                    continue
                apply_body(address, token_from_ext3_index(new_index, num_banks=num_banks), "active_detach_from_shared_ext3", slot=new_index)
            else:
                skipped[address] = {
                    "reason": "shared_or_nested_ext3_quarantine_preserved",
                    "details": reasons,
                    "text": reviewed[address]["proposed_ko"],
                }

    # 3) Native rows. Quarantine bodies remain exact. Active scenario-first rows
    # either fit directly or use a private ext3 portal when the decoder allows it.
    for address in sorted(native_rows):
        if address not in encoded:
            continue
        contract = contracts[address]
        payload = encoded[address]
        capacity = int(contract["body_capacity"])
        if contract.get("status") != "active":
            skipped[address] = {"reason": "native_quarantine_record_preserved", "text": reviewed[address]["proposed_ko"]}
            continue
        if len(payload) <= capacity:
            apply_body(address, payload, "active_native_direct_fit")
            continue
        if bool((contract.get("decoder") or {}).get("ext3")) and capacity >= 4:
            new_index = allocate_private(payload)
            if new_index is None:
                skipped[address] = {"reason": "private_ext3_capacity_exhausted", "text": reviewed[address]["proposed_ko"]}
                continue
            apply_body(address, token_from_ext3_index(new_index, num_banks=num_banks), "active_native_to_private_ext3", slot=new_index)
            continue
        skipped[address] = {"reason": "active_native_body_too_short_for_reviewed_text_or_ext3", "text": reviewed[address]["proposed_ko"]}

    # Ensure every final reviewed row is accounted for exactly once.
    overlap = set(applied) & set(skipped)
    if overlap:
        raise BuildError(f"applied/skipped overlap: {sorted(overlap)[:10]}")
    unaccounted = set(reviewed) - set(applied) - set(skipped)
    if unaccounted:
        raise BuildError(f"unaccounted review rows: {sorted(unaccounted)[:20]}")

    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    # Regression gate for the proven page-boundary failure family.  The new
    # candidate may retain already-promoted exceptions, but it must never add
    # another original native-two-token first record that became non-native.
    candidate_family_rows, candidate_family_scan_errors = scan_families(candidate_bytes, original)
    if candidate_family_scan_errors:
        raise BuildError(
            f"candidate page-boundary family scan failed: {candidate_family_scan_errors[:5]}"
        )
    candidate_family_non_native = {
        f"{int(row['logical']):06X}"
        for row in candidate_family_rows
        if row.get("source_exact_native_two_token")
        and not row.get("current_native_two_token_with_padding")
    }
    new_page_boundary_grammar_regressions = sorted(
        candidate_family_non_native - parent_family_non_native
    )
    if new_page_boundary_grammar_regressions:
        raise BuildError(
            "page-boundary native grammar regression: "
            f"{new_page_boundary_grammar_regressions[:20]}"
        )

    OUT_ROM.write_bytes(candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)

    # Rebuild a contract manifest from the candidate; quarantine record bodies
    # must remain byte-exact even when an existing ext3 phrase storage changed.
    manifest = build_manifest(original, candidate_bytes, target_path=OUT_ROM)
    safety = audit_manifest(candidate_bytes, manifest, target_path=OUT_ROM)
    OUT_CONTRACT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Verify every applied target through its final direct/ext3 body route.
    d_candidate = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    verify_failures: list[dict[str, Any]] = []
    for address, info in sorted(applied.items()):
        target = reviewed[address]["proposed_ko"]
        contract = contracts[address]
        start = sb + int(contract["body_start"], 16)
        body = bytes(candidate_bytes[start:start + int(contract["body_capacity"])])
        index = ext3_index(body)
        if index is not None:
            actual = normalize_ko_text(d_candidate.expand_index(index, tbl))
        else:
            raw = encoded[address]
            actual = normalize_ko_text(d_candidate.expand(raw, tbl))
        if actual != target:
            verify_failures.append({"abs": address, "expected": target, "actual": actual, "mode": info["mode"]})

    applied_counts = Counter(str(info["mode"]) for info in applied.values())
    skip_counts = Counter(str(info["reason"]) for info in skipped.values())
    quarantine_body_changed = []
    for address, contract in contracts.items():
        if contract.get("status") != "quarantine":
            continue
        start = sb + int(contract["body_start"], 16)
        capacity = int(contract["body_capacity"])
        if candidate_bytes[start:start + capacity] != parent[start:start + capacity]:
            quarantine_body_changed.append(address)

    if safety["counts"]["hard_failures"] or verify_failures or quarantine_body_changed:
        raise BuildError(
            f"candidate verification failed hard={safety['counts']['hard_failures']} "
            f"text={len(verify_failures)} quarantine_body={len(quarantine_body_changed)}"
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_main_translation_rebase_candidate.py",
        "status": "candidate_requires_user_runtime_validation",
        "promotion_allowed": False,
        "rules": {
            "line_limit": LINE_LIMIT,
            "two_line_limit": 40,
            "all_reviewed_rows_le_20": all(row["cells"] <= LINE_LIMIT for row in width_rows),
            "quarantine_record_bytes_must_remain_exact": True,
            "shared_or_nested_slots_never_overwritten_on_conflict": True,
            "page_boundary_native_two_token_first_records_preserved": True,
            "runtime_proven_native_first_records_preserved": True,
        },
        "main": {"path": str(MAIN.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(parent)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(candidate_bytes)},
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(OUT_SAVE.read_bytes()), "copied_from_live": True},
        "review": {
            **review_meta,
            "final_review_rows": len(reviewed),
            "encodable_rows": len(encoded),
            "preflight_skips": len(preflight_skips),
            "max_cells": max((row["cells"] for row in width_rows), default=0),
        },
        "counts": {
            "applied_rows": len(applied),
            "skipped_rows": len(skipped),
            "applied_modes": dict(sorted(applied_counts.items())),
            "skip_reasons": dict(sorted(skip_counts.items())),
            "private_ext3_slots_allocated": len(allocated_private),
            "storage_changes": len(storage_changes),
            "page_boundary_native_two_token_protected": len(page_boundary_native_protected),
            "runtime_proven_native_first_protected": len(runtime_native_first_protected),
            "page_boundary_new_grammar_regressions": len(new_page_boundary_grammar_regressions),
            "candidate_hard_failures": safety["counts"]["hard_failures"],
            "text_verify_failures": len(verify_failures),
            "quarantine_record_body_changes": len(quarantine_body_changed),
        },
        "physical_bank_room": {
            f"{seg:02X}": {
                "cursor_before": cursor_start[seg],
                "cursor_after": cursors[seg],
                "room_after": BANK_SIZE - cursors[seg],
            }
            for seg in physical_segs
        },
        "storage_changes": storage_changes,
        "applied": applied,
        "skipped": skipped,
        "verification": {
            "contract_safety": safety,
            "text_failures": verify_failures,
            "quarantine_body_changed": quarantine_body_changed,
            "page_boundary_parent_non_native": sorted(parent_family_non_native),
            "page_boundary_candidate_non_native": sorted(candidate_family_non_native),
            "page_boundary_new_grammar_regressions": new_page_boundary_grammar_regressions,
        },
        "note": "Test candidate only. Promote only after representative BizHawk runtime validation, especially continuation/quarantine screens.",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate": str(OUT_ROM),
        "candidate_sha256": sha(candidate_bytes),
        "applied_rows": len(applied),
        "skipped_rows": len(skipped),
        "private_ext3_slots_allocated": len(allocated_private),
        "candidate_hard_failures": safety["counts"]["hard_failures"],
        "max_cells": max((row["cells"] for row in width_rows), default=0),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
