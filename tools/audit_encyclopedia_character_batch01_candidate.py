#!/usr/bin/env python3
"""Independent static audit for the character-encyclopedia batch01 candidate."""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from character_portal_runtime import (
    CHAR_CAVE,
    CHAR_CAVE_END,
    CHAR_CAVE_MAX,
    CHAR_MAGIC,
    CHAR_SEG,
    build_dispatch_handlers,
)
from expand_dictionary import _walk_zstring_range
from mixed_residual_classification import is_japanese_character
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_index_from_token,
    le16,
    read_encoded_z_safe,
    stock_base,
)
from patch_3byte_dict_token import (
    EXT_CAVE_SEG,
    HOOK_LEN,
    LEAF,
    SITE1,
    far_jmp,
    find_site2,
    sab,
)

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CANDIDATE = ROOT / "out/patch/encyclopedia_character_batch01_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/encyclopedia_character_batch01_candidate.sav"
WORKLIST = ROOT / "out/patch/encyclopedia_character_batch01_worklist.json"
CATALOG = ROOT / "data/encyclopedia_character_batch01_ko.json"
BUILD_REPORT = ROOT / "out/patch/encyclopedia_character_batch01_report.json"
PORTAL_META = ROOT / "out/patch/encyclopedia_character_batch01_portal_meta.json"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/encyclopedia_character_batch01_candidate_audit.json"
RESIDUAL_OUT = ROOT / "out/patch/encyclopedia_character_batch01_candidate_residual_audit.json"

EXPECTED_PARENT_SHA = "c8d3b308299da3b2354aac70ff65a3b439da3d0ed97660946b39fd97341aa821"
EXPECTED_CANDIDATE_SHA = "d178a7c1888eec9b48b0362a954a8b67235edf1cba2fa32fae0b6c60d98ae5e8"
EXPECTED_TARGETS = 693
EXPECTED_PORTAL_RECORDS = 686
EXPECTED_SHORT = 7
EXPECTED_UNIQUE_PORTAL = 667
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
SCOPE_START = 0x5C0000
SCOPE_END = 0x5C2E62
CHAR_SLOTS = 0x1000
CHAR_EMPTY_AT = CHAR_SLOTS * 2
CHAR_TOKEN = bytes((0xE5, 0x2F))


class AuditError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    result = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if result is None:
        raise AuditError(f"unreadable record {logical:06X}")
    return bytes(result[0]), int(result[1])


def character_raw(rom: bytes, local: int) -> tuple[bytes, int]:
    if not (0 <= local < CHAR_SLOTS):
        raise AuditError(f"character local out of range: {local:04X}")
    bank_start = CHAR_SEG * BANK_SIZE
    pointer = le16(rom, bank_start + local * 2)
    if not (CHAR_EMPTY_AT <= pointer < BANK_SIZE):
        raise AuditError(f"character pointer out of range: local={local:04X} ptr={pointer:04X}")
    result = read_encoded_z_safe(rom, bank_start + pointer, max_len=256)
    if result is None:
        raise AuditError(f"character phrase unreadable: local={local:04X}")
    return bytes(result[0]), pointer


def render_record(rom: bytes, logical: int, *, dictionary: Any, tbl: Tbl) -> tuple[str, bytes, int, int | None]:
    payload, terminator = payload_at(rom, logical)
    local: int | None = None
    if len(payload) >= 4 and payload[:2] == CHAR_TOKEN:
        local = (payload[2] << 8) | payload[3]
        raw, _pointer = character_raw(rom, local)
        text = dictionary.expand(raw, tbl).rstrip("\u3000 \t")
    else:
        text = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
    return text, payload, terminator, local


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA:
        raise AuditError("parent identity drifted")
    if len(candidate) != ROM_SIZE or sha256(candidate) != EXPECTED_CANDIDATE_SHA:
        raise AuditError("candidate identity drifted")
    if len(parent_save) != SAVE_SIZE or len(candidate_save) != SAVE_SIZE:
        raise AuditError("main or convenience candidate SaveRAM has the wrong size")

    worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    portal_meta = json.loads(PORTAL_META.read_text(encoding="utf-8"))
    source_rows = [dict(row) for row in (worklist.get("records") or []) if row.get("status") == "japanese_residual"]
    source_by_abs = {str(row["abs"]).upper(): row for row in source_rows}
    catalog_rows = [dict(row) for row in (catalog.get("lines") or [])]
    catalog_by_abs = {str(row["abs"]).upper(): row for row in catalog_rows}
    population_ok = (
        len(source_rows) == EXPECTED_TARGETS
        and len(source_by_abs) == EXPECTED_TARGETS
        and len(catalog_rows) == EXPECTED_TARGETS
        and len(catalog_by_abs) == EXPECTED_TARGETS
        and set(source_by_abs) == set(catalog_by_abs)
    )
    provenance = catalog.get("provenance") or {}
    provenance_ok = (
        provenance.get("translation_source") in {"llm", "human", "user_verified", "curated_project_data"}
        and provenance.get("review_status") in {"approved", "user_verified"}
        and provenance.get("legacy_machine_translation_used") is False
    )

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    portal_records = 0
    short_records = 0
    used_locals: dict[int, str] = {}
    short_indices: set[int] = set()
    target_logicals: set[int] = set()
    target_extents: list[tuple[int, int]] = []
    for address in sorted(catalog_by_abs, key=lambda value: int(value, 16)):
        source = source_by_abs[address]
        line = catalog_by_abs[address]
        logical = int(address, 16)
        target_logicals.add(logical)
        expected = str(line.get("ko") or "").rstrip("\u3000 \t")
        capacity = int(source.get("payload_len") or 0)
        before_payload, before_terminator = payload_at(parent, logical)
        try:
            actual, payload, terminator, local = render_record(candidate, logical, dictionary=candidate_dictionary, tbl=tbl)
        except Exception as exc:
            target_failures.append({"abs": address, "reason": "render_exception", "detail": str(exc)})
            continue
        reasons: list[str] = []
        if bytes.fromhex(str(source.get("current_payload_hex") or "")) != before_payload:
            reasons.append("parent_payload_not_bound")
        if len(before_payload) != capacity or len(payload) != capacity:
            reasons.append("payload_length_changed")
        if before_terminator != stock_base(parent) + logical + capacity or parent[before_terminator] != 0:
            reasons.append("parent_terminator_drifted")
        if terminator != stock_base(candidate) + logical + capacity or candidate[terminator] != 0:
            reasons.append("candidate_terminator_drifted")
        if actual != expected:
            reasons.append("render_mismatch")
        if len(expected) > 13:
            reasons.append("visual_width_over_13")
        if any(is_japanese_character(character) for character in actual):
            reasons.append("japanese_residual")
        if capacity >= 4:
            portal_records += 1
            if payload[:2] != CHAR_TOKEN or local is None:
                reasons.append("missing_character_portal")
            else:
                if payload[2] == 0 or payload[3] == 0:
                    reasons.append("unsafe_character_token_nul")
                raw, pointer = character_raw(candidate, local)
                phrase_text = candidate_dictionary.expand(raw, tbl).rstrip("\u3000 \t")
                if phrase_text != expected:
                    reasons.append("portal_phrase_mismatch")
                prior = used_locals.get(local)
                if prior is not None and prior != expected:
                    reasons.append("portal_local_aliases_different_phrases")
                used_locals[local] = expected
                if pointer == CHAR_EMPTY_AT:
                    reasons.append("portal_points_to_empty")
                if payload[4:] != b"\x01" * (capacity - 4):
                    reasons.append("portal_padding_mismatch")
        else:
            short_records += 1
            if len(payload) < 2 or not (0xF0 <= payload[0] <= 0xFF):
                reasons.append("short_record_not_stock_token")
            else:
                index = dict_index_from_token(payload[0], payload[1])
                short_indices.add(index)
                if payload[2:] != b"\x01" * (capacity - 2):
                    reasons.append("short_padding_mismatch")
        target_extents.append((stock_base(parent) + logical, stock_base(parent) + logical + capacity))
        if reasons:
            target_failures.append({"abs": address, "expected": expected, "actual": actual, "reasons": reasons})

    # Bank 0x21 must be a self-contained 4096-pointer dictionary.  Used locals
    # point to exact catalog phrases; every unused pointer remains on the shared
    # empty NUL at 0x2000.
    bank_start = CHAR_SEG * BANK_SIZE
    unused_pointer_failures: list[str] = []
    used_pointer_failures: list[str] = []
    for local in range(CHAR_SLOTS):
        pointer = le16(candidate, bank_start + local * 2)
        if local in used_locals:
            if pointer == CHAR_EMPTY_AT or not (CHAR_EMPTY_AT < pointer < BANK_SIZE):
                used_pointer_failures.append(f"{local:04X}:{pointer:04X}")
        elif pointer != CHAR_EMPTY_AT:
            unused_pointer_failures.append(f"{local:04X}:{pointer:04X}")
        if len(unused_pointer_failures) >= 20 or len(used_pointer_failures) >= 20:
            break
    bank_empty_nul_ok = candidate[bank_start + CHAR_EMPTY_AT] == 0
    bank_before_empty = all(byte == 0xFF for byte in parent[bank_start : bank_start + BANK_SIZE])

    # Existing ext3 data remains byte-exact and independently decodes to the
    # same raw payload at all 65536 indices.
    ext3_banks_exact = all(
        parent[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        == candidate[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    ext3_raw_failures: list[str] = []
    for index in range(0x1000, 0x11000):
        if bytes(parent_dictionary.raw_entry(index)) != bytes(candidate_dictionary.raw_entry(index)):
            ext3_raw_failures.append(f"{index:05X}")
            if len(ext3_raw_failures) >= 20:
                break

    # Rebuild the dispatcher from the preserved ext3 metadata and compare every
    # byte, each hook, and the untouched old runtime range.
    old_parts = ext3_meta.get("parts") or {}
    site2, site2_return = find_site2(parent)
    expected_blob, expected_parts = build_dispatch_handlers(
        site2_return=site2_return,
        old_walker1=int(str(old_parts["walker1"]), 16),
        old_walker2=int(str(old_parts["walker2"]), 16),
        old_leaf=int(str(old_parts["leaf"]), 16),
    )
    char_cave_file = sab(parent, CHAR_CAVE)
    runtime_checks = {
        "dispatcher_blob_exact": candidate[char_cave_file : char_cave_file + len(expected_blob)] == expected_blob,
        "dispatcher_tail_stays_ff": all(byte == 0xFF for byte in candidate[char_cave_file + len(expected_blob) : sab(parent, CHAR_CAVE_END)]),
        "parent_dispatcher_cave_was_ff": all(byte == 0xFF for byte in parent[char_cave_file : sab(parent, CHAR_CAVE_END)]),
        "dispatcher_fits": len(expected_blob) <= CHAR_CAVE_MAX,
        "site1_hook_exact": candidate[sab(parent, SITE1) : sab(parent, SITE1) + HOOK_LEN] == far_jmp((CHAR_CAVE + expected_parts["walker1_dispatch"]) & 0xFFFF, EXT_CAVE_SEG),
        "site2_hook_exact": candidate[sab(parent, site2) : sab(parent, site2) + HOOK_LEN] == far_jmp((CHAR_CAVE + expected_parts["walker2_dispatch"]) & 0xFFFF, EXT_CAVE_SEG),
        "leaf_hook_exact": candidate[sab(parent, LEAF) : sab(parent, LEAF) + 6] == far_jmp((CHAR_CAVE + expected_parts["leaf_dispatch"]) & 0xFFFF, EXT_CAVE_SEG) + b"\x90",
        "old_ext3_and_prefix_runtime_exact": parent[sab(parent, int(str(ext3_meta["cave"]), 16)) : char_cave_file] == candidate[sab(parent, int(str(ext3_meta["cave"]), 16)) : char_cave_file],
        "e52f_dispatch_count_two": expected_blob.count(bytes.fromhex("81FA2FE5")) == 2,
        "flag2_write_count_two": expected_blob.count(b"\xC6\x06" + struct.pack("<H", int(str(ext3_meta["wram_flag"]), 16)) + b"\x02") == 2,
        "flag2_leaf_check_count_one": expected_blob.count(b"\x80\x3E" + struct.pack("<H", int(str(ext3_meta["wram_flag"]), 16)) + b"\x02") == 1,
        "fixed_bank21_map_count_one": expected_blob.count(b"\xB0\x21") == 1,
        "compact3_not_installed": ext3_meta.get("compact3") is False and bytes.fromhex("81FA19E5") not in expected_blob,
    }

    parent_text = parent[stock_base(parent) + 0x5C0000 : stock_base(parent) + 0x760000]
    original_text = original[0x5C0000:0x760000]
    collision_checks = {
        "original_text_e52f_hits_zero": original_text.count(CHAR_TOKEN) == 0,
        "parent_text_e52f_hits_zero": parent_text.count(CHAR_TOKEN) == 0,
    }

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )

    # Independently derive changed stock slots and prove each was unreachable in
    # the parent.  Exact reused short slots do not appear in this changed set.
    parent_stock = Dictionary(parent)
    candidate_stock = Dictionary(candidate)
    changed_stock_indices = {
        index
        for index, (before, after) in enumerate(zip(parent_stock.ptrs, candidate_stock.ptrs))
        if before != after
    }
    changed_raw_indices = {
        index
        for index in range(min(parent_stock.count, candidate_stock.count))
        if bytes(parent_stock.raw_entry(index)) != bytes(candidate_stock.raw_entry(index))
    }
    changed_stock_consistent = changed_stock_indices == changed_raw_indices and changed_stock_indices <= short_indices
    external = external_occurrence_map(parent, ext3_aware=True, wanted=changed_stock_indices)
    nested = nested_occurrence_map(parent_dictionary, wanted=changed_stock_indices, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, sorted(changed_stock_indices))
    retired_parent_zero = all(not external.get(index) and not nested.get(index) and not raw_hits.get(index) for index in changed_stock_indices)

    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2)
        for index in changed_stock_indices
    ]
    stock_phrase_extents = []
    for index in changed_stock_indices:
        pointer = candidate_stock.ptrs[index]
        raw = bytes(candidate_stock.raw_entry(index))
        stock_phrase_extents.append((stock_bank_file + pointer, stock_bank_file + pointer + len(raw) + 1))

    runtime_extents = [
        (char_cave_file, char_cave_file + len(expected_blob)),
        (sab(parent, SITE1), sab(parent, SITE1) + HOOK_LEN),
        (sab(parent, site2), sab(parent, site2) + HOOK_LEN),
        (sab(parent, LEAF), sab(parent, LEAF) + 6),
    ]
    allowed = target_extents + stock_pointer_extents + stock_phrase_extents + runtime_extents + [
        (bank_start, bank_start + BANK_SIZE),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    # Portal-aware full scope residual audit over Original-derived boundaries.
    residuals: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    scanned = 0
    for logical, _original_payload, kind in _walk_zstring_range(original, SCOPE_START, SCOPE_END, region="bank5c_character_encyclopedia", max_len=256):
        scanned += 1
        try:
            text, _payload, _terminator, _local = render_record(candidate, logical, dictionary=candidate_dictionary, tbl=tbl)
        except Exception as exc:
            unreadable.append({"abs": f"{logical:06X}", "kind": kind, "error": str(exc)})
            continue
        count = sum(is_japanese_character(character) for character in text)
        if count:
            residuals.append({"abs": f"{logical:06X}", "kind": kind, "text": text, "japanese_count": count})
    residual_report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_character_batch01_candidate.py",
        "read_only": True,
        "candidate": identity(CANDIDATE, candidate),
        "scope": {"start": f"{SCOPE_START:06X}", "end_exclusive": f"{SCOPE_END:06X}"},
        "counts": {
            "scanned_records": scanned,
            "japanese_residual_records": len(residuals),
            "unreadable_records": len(unreadable),
            "actionable_records": len(residuals) + len(unreadable),
        },
        "records": residuals + unreadable,
    }
    atomic_json(RESIDUAL_OUT, residual_report)

    checks = {
        "identities": True,
        "worklist_catalog_population": population_ok,
        "approved_nonlegacy_provenance": provenance_ok,
        "targets_exact": not target_failures,
        "targets_within_13_cells": all(len(str(row.get("ko") or "")) <= 13 for row in catalog_rows),
        "portal_record_count": portal_records == EXPECTED_PORTAL_RECORDS,
        "short_record_count": short_records == EXPECTED_SHORT,
        "portal_unique_slot_count": len(used_locals) == EXPECTED_UNIQUE_PORTAL,
        "portal_bank_parent_empty": bank_before_empty,
        "portal_bank_shared_empty_nul": bank_empty_nul_ok,
        "portal_used_pointers_valid": not used_pointer_failures,
        "portal_unused_pointers_empty": not unused_pointer_failures,
        "runtime_exact": all(runtime_checks.values()),
        "magic_collision_zero": all(collision_checks.values()),
        "existing_ext3_banks_exact": ext3_banks_exact,
        "existing_ext3_raw_exact": not ext3_raw_failures,
        "changed_stock_slots_consistent": changed_stock_consistent,
        "changed_stock_slot_count_five": len(changed_stock_indices) == 5,
        "changed_stock_slots_parent_unreachable": retired_parent_zero,
        "non_target_invariance": invariance.get("ok") is True,
        "diffs_bounded": not unaccounted,
        "post_residual_zero": not residuals and not unreadable and scanned == 920,
        "main_tip_unchanged": sha256(PARENT.read_bytes()) == EXPECTED_PARENT_SHA,
        "main_saveram_untouched": PARENT_SAVE.read_bytes() == parent_save,
        "candidate_saveram_convenience_copy_size_valid": len(candidate_save) == SAVE_SIZE,
        "build_report_bindings": (
            build.get("ok") is True
            and build.get("published") is False
            and ((build.get("candidate") or {}).get("sha256") == EXPECTED_CANDIDATE_SHA)
            and ((portal_meta.get("candidate_sha256") or "") == EXPECTED_CANDIDATE_SHA)
        ),
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_character_batch01_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_user_visual_test" if ok else "failed",
        "inputs": {
            "parent": identity(PARENT, parent),
            "candidate": identity(CANDIDATE, candidate),
            "original": identity(ORIGINAL, original),
            "parent_save": identity(PARENT_SAVE, parent_save),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "worklist": identity(WORKLIST),
            "catalog": identity(CATALOG),
            "build_report": identity(BUILD_REPORT),
            "portal_meta": identity(PORTAL_META),
        },
        "checks": checks,
        "counts": {
            "targets": len(catalog_rows),
            "target_failures": len(target_failures),
            "portal_records": portal_records,
            "short_records": short_records,
            "portal_unique_slots": len(used_locals),
            "changed_stock_slots": len(changed_stock_indices),
            "non_target_records": int(invariance.get("records_checked") or invariance.get("checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
            "post_scanned_records": scanned,
            "post_japanese_residuals": len(residuals),
            "post_unreadable": len(unreadable),
        },
        "runtime": {
            "checks": runtime_checks,
            "expected_blob_len": len(expected_blob),
            "expected_blob_sha256": sha256(expected_blob),
            "parts": {name: f"{CHAR_CAVE + offset:06X}" for name, offset in expected_parts.items()},
        },
        "portal_bank": {
            "used_locals": len(used_locals),
            "first_local": f"{min(used_locals):04X}" if used_locals else None,
            "last_local": f"{max(used_locals):04X}" if used_locals else None,
            "used_pointer_failures": used_pointer_failures,
            "unused_pointer_failures": unused_pointer_failures,
        },
        "stock": {
            "short_indices": [f"{index:04X}" for index in sorted(short_indices)],
            "changed_indices": [f"{index:04X}" for index in sorted(changed_stock_indices)],
            "changed_raw_indices": [f"{index:04X}" for index in sorted(changed_raw_indices)],
            "parent_unreachable": retired_parent_zero,
        },
        "collision": collision_checks,
        "invariance": invariance,
        "target_failures": target_failures,
        "ext3_raw_failures": ext3_raw_failures,
        "unaccounted_diff_runs": unaccounted,
        "residual_audit": identity(RESIDUAL_OUT),
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(OUT, report)
    print(json.dumps({
        "ok": ok,
        "status": report["status"],
        "checks": checks,
        "counts": report["counts"],
        "runtime": report["runtime"],
        "stock": report["stock"],
        "residual_audit": report["residual_audit"],
        "out": str(OUT.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
