#!/usr/bin/env python3
"""Independent audit of the runtime-safe character encyclopedia batch01."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_remaining_dialogue_candidate import covered, diff_runs, phrase_cursor, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, SEG_DICT, Tbl, read_encoded_z_safe, slice_expansion_bank, stock_base
from patch_3byte_dict_token import bank_local_for_index

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CANDIDATE = ROOT / "out/patch/encyclopedia_character_safe_batch01_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/encyclopedia_character_safe_batch01_candidate.sav"
WORKLIST = ROOT / "out/patch/encyclopedia_character_safe_batch01_worklist.json"
CATALOG = ROOT / "data/encyclopedia_character_safe_batch01_ko.json"
BUILD_REPORT = ROOT / "out/patch/encyclopedia_character_safe_batch01_report.json"
RESIDUAL_REPORT = ROOT / "out/patch/encyclopedia_character_safe_batch01_candidate_residual_audit.json"
REJECTION_REPORT = ROOT / "out/patch/encyclopedia_character_batch01_rejection_report.json"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/encyclopedia_character_safe_batch01_candidate_audit.json"

EXPECTED_PARENT_SHA = "c8d3b308299da3b2354aac70ff65a3b439da3d0ed97660946b39fd97341aa821"
EXPECTED_CANDIDATE_SHA = "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825"
EXPECTED_TARGETS = 90
SELECTED_SEGMENTS = (0x19, 0x1C)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
FORBIDDEN_MAGIC = b"\xE5\x2F"


class AuditError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": digest(payload)}


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise AuditError("parent identity drifted")
    if len(candidate) != ROM_SIZE or digest(candidate) != EXPECTED_CANDIDATE_SHA:
        raise AuditError("candidate identity drifted")
    if len(parent_save) != SAVE_SIZE or len(candidate_save) != SAVE_SIZE:
        raise AuditError("SaveRAM size invalid")

    worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    residual = json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    rejected = json.loads(REJECTION_REPORT.read_text(encoding="utf-8"))
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    tbl = Tbl.load(TBL_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    source_rows = [dict(row) for row in worklist.get("records") or []]
    source_by_abs = {str(row["abs"]).upper(): row for row in source_rows}
    catalog_rows = [dict(row) for row in catalog.get("lines") or []]
    catalog_by_abs = {str(row["abs"]).upper(): row for row in catalog_rows}
    population_ok = (
        len(source_rows) == EXPECTED_TARGETS
        and len(source_by_abs) == EXPECTED_TARGETS
        and len(catalog_rows) == EXPECTED_TARGETS
        and len(catalog_by_abs) == EXPECTED_TARGETS
        and set(source_by_abs) == set(catalog_by_abs)
    )

    target_failures: list[dict[str, Any]] = []
    target_logicals: set[int] = set()
    target_indices: set[int] = set()
    target_extents: list[tuple[int, int]] = []
    for address in sorted(catalog_by_abs, key=lambda value: int(value, 16)):
        source = source_by_abs[address]
        line = catalog_by_abs[address]
        logical = int(address, 16)
        expected = str(line.get("ko") or "").rstrip("\u3000 \t")
        before_payload, before_terminator = payload_at(parent, logical)
        after_payload, after_terminator = payload_at(candidate, logical)
        reasons: list[str] = []
        if before_payload != bytes.fromhex(str(source.get("current_payload_hex") or "")):
            reasons.append("parent_payload_not_bound")
        capacity = int(source.get("payload_len") or 0)
        if capacity < 4 or len(before_payload) != capacity or len(after_payload) != capacity:
            reasons.append("payload_length_or_policy_failure")
        if before_terminator != stock_base(parent) + logical + capacity or parent[before_terminator] != 0:
            reasons.append("parent_terminator_drifted")
        if after_terminator != stock_base(candidate) + logical + capacity or candidate[after_terminator] != 0:
            reasons.append("candidate_terminator_drifted")
        if after_payload[:2] != b"\xE5\x18":
            reasons.append("not_existing_e518_token")
        if FORBIDDEN_MAGIC in after_payload:
            reasons.append("forbidden_e52f_present")
        if after_payload[4:] != b"\x01" * (capacity - 4):
            reasons.append("padding_mismatch")
        if len(after_payload) >= 4 and after_payload[:2] == b"\xE5\x18":
            index = 0x1000 + ((after_payload[2] << 8) | after_payload[3])
            target_indices.add(index)
            segment, _local = bank_local_for_index(index)
            if segment not in SELECTED_SEGMENTS:
                reasons.append("target_outside_selected_segments")
        rendered = candidate_dictionary.expand(after_payload, tbl).rstrip("\u3000 \t")
        if rendered != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(character) for character in rendered):
            reasons.append("japanese_residual")
        if len(expected) > 13:
            reasons.append("visual_width_over_13")
        target_logicals.add(logical)
        target_extents.append((stock_base(parent) + logical, stock_base(parent) + logical + capacity))
        if reasons:
            target_failures.append({"abs": address, "expected": expected, "actual": rendered, "reasons": reasons})

    target_slot_failures: list[dict[str, Any]] = []
    phrase_to_index: dict[str, int] = {}
    for address, line in catalog_by_abs.items():
        logical = int(address, 16)
        payload, _terminator = payload_at(candidate, logical)
        index = 0x1000 + ((payload[2] << 8) | payload[3])
        expected = str(line.get("ko") or "").rstrip("\u3000 \t")
        rendered = candidate_dictionary.expand(bytes(candidate_dictionary.raw_entry(index)), tbl).rstrip("\u3000 \t")
        if rendered != expected:
            target_slot_failures.append({"index": f"{index:05X}", "expected": expected, "actual": rendered})
        previous = phrase_to_index.get(expected)
        if previous is not None and previous != index:
            target_slot_failures.append({"phrase": expected, "reason": "duplicate_phrase_multiple_slots"})
        phrase_to_index[expected] = index

    non_target_ext3_failures: list[str] = []
    for index in range(0x1000, 0x11000):
        if index in target_indices:
            continue
        if bytes(parent_dictionary.raw_entry(index)) != bytes(candidate_dictionary.raw_entry(index)):
            non_target_ext3_failures.append(f"{index:05X}")
            if len(non_target_ext3_failures) >= 20:
                break

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )

    runtime_start = stock_base(parent) + 0x7A0000
    runtime_end = runtime_start + BANK_SIZE
    runtime_exact = parent[runtime_start:runtime_end] == candidate[runtime_start:runtime_end]
    stock_start = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_exact = parent[stock_start : stock_start + BANK_SIZE] == candidate[stock_start : stock_start + BANK_SIZE]
    other_ext3_exact = all(
        bytes(slice_expansion_bank(parent, segment)) == bytes(slice_expansion_bank(candidate, segment))
        for segment in range(0x11, 0x21)
        if segment not in SELECTED_SEGMENTS
    )

    cursor_before = {segment: phrase_cursor(bytes(slice_expansion_bank(parent, segment))) for segment in SELECTED_SEGMENTS}
    cursor_after = {segment: phrase_cursor(bytes(slice_expansion_bank(candidate, segment))) for segment in SELECTED_SEGMENTS}
    pointer_extents = []
    for index in target_indices:
        segment, local = bank_local_for_index(index)
        pointer_extents.append((segment * BANK_SIZE + local * 2, segment * BANK_SIZE + local * 2 + 2))
    phrase_extents = [
        (segment * BANK_SIZE + cursor_before[segment], segment * BANK_SIZE + cursor_after[segment])
        for segment in SELECTED_SEGMENTS
    ]
    runs = diff_runs(parent, candidate)
    allowed = target_extents + pointer_extents + phrase_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    selected_bank_diff_bounded = True
    for segment in SELECTED_SEGMENTS:
        local_pointer_extents = []
        for index in target_indices:
            target_segment, local = bank_local_for_index(index)
            if target_segment == segment:
                local_pointer_extents.append((local * 2, local * 2 + 2))
        local_phrase_extents = [(cursor_before[segment], cursor_after[segment])]
        if not all(
            covered((left, right), local_pointer_extents + local_phrase_extents)
            for left, right in diff_runs(
                bytes(slice_expansion_bank(parent, segment)),
                bytes(slice_expansion_bank(candidate, segment)),
            )
        ):
            selected_bank_diff_bounded = False
            break

    residual_counts = residual.get("counts") or {}
    provenance = catalog.get("provenance") or {}
    checks = {
        "identities": True,
        "population_90": population_ok,
        "approved_nonlegacy_provenance": (
            provenance.get("translation_source") in {"llm", "human", "user_verified", "curated_project_data"}
            and provenance.get("review_status") in {"approved", "user_verified"}
            and provenance.get("legacy_machine_translation_used") is False
        ),
        "targets_exact": not target_failures,
        "target_slot_count_90": len(target_indices) == EXPECTED_TARGETS,
        "target_slots_exact": not target_slot_failures,
        "all_target_tokens_e518": all(
            payload_at(candidate, int(address, 16))[0][:2] == b"\xE5\x18"
            for address in catalog_by_abs
        ),
        "forbidden_e52f_absent_from_targets": all(
            FORBIDDEN_MAGIC not in payload_at(candidate, int(address, 16))[0]
            for address in catalog_by_abs
        ),
        "runtime_code_bank_7a_exact": runtime_exact,
        "stock_dictionary_bank_exact": stock_exact,
        "compact3_disabled": ext3_meta.get("compact3") is False,
        "other_ext3_banks_exact": other_ext3_exact,
        "all_non_target_ext3_raw_exact": not non_target_ext3_failures,
        "selected_ext3_changes_bounded": selected_bank_diff_bounded,
        "non_target_invariance": invariance.get("ok") is True,
        "diffs_bounded": not unaccounted,
        "residual_range_zero": (
            int(residual_counts.get("scanned_records", 0)) == 124
            and int(residual_counts.get("actionable_records", -1)) == 0
            and int(residual_counts.get("unreadable_records", -1)) == 0
        ),
        "failed_design_explicitly_rejected": (
            rejected.get("ok") is False
            and rejected.get("status") == "rejected_runtime_failure_do_not_use_do_not_promote"
            and rejected.get("promotion") == "permanently prohibited"
        ),
        "build_report_bound": (
            build.get("ok") is True
            and build.get("published") is False
            and ((build.get("candidate") or {}).get("sha256") == EXPECTED_CANDIDATE_SHA)
            and int((build.get("counts") or {}).get("runtime_changes", -1)) == 0
            and int((build.get("counts") or {}).get("stock_dictionary_changes", -1)) == 0
        ),
        "main_tip_unchanged": digest(PARENT.read_bytes()) == EXPECTED_PARENT_SHA,
        "main_saveram_untouched": PARENT_SAVE.read_bytes() == parent_save,
        "candidate_saveram_matches_live_at_audit": candidate_save == parent_save,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_character_safe_batch01_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_user_visual_test" if ok else "failed",
        "inputs": {
            "parent": identity(PARENT, parent),
            "candidate": identity(CANDIDATE, candidate),
            "parent_save": identity(PARENT_SAVE, parent_save),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "worklist": identity(WORKLIST),
            "catalog": identity(CATALOG),
            "build_report": identity(BUILD_REPORT),
            "residual_report": identity(RESIDUAL_REPORT),
            "rejection_report": identity(REJECTION_REPORT),
        },
        "checks": checks,
        "counts": {
            "targets": len(catalog_rows),
            "target_failures": len(target_failures),
            "target_slots": len(target_indices),
            "target_slot_failures": len(target_slot_failures),
            "non_target_ext3_failures": len(non_target_ext3_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
            "residual_scanned": int(residual_counts.get("scanned_records") or 0),
            "residual_actionable": int(residual_counts.get("actionable_records") or 0),
        },
        "selected_banks": {
            f"{segment:02X}": {
                "cursor_before": f"{cursor_before[segment]:04X}",
                "cursor_after": f"{cursor_after[segment]:04X}",
                "phrase_bytes_added": cursor_after[segment] - cursor_before[segment],
                "room_after": BANK_SIZE - cursor_after[segment],
                "target_slots": sum(bank_local_for_index(index)[0] == segment for index in target_indices),
            }
            for segment in SELECTED_SEGMENTS
        },
        "invariance": invariance,
        "target_failures": target_failures,
        "target_slot_failures": target_slot_failures,
        "non_target_ext3_failures": non_target_ext3_failures,
        "unaccounted_diff_runs": unaccounted,
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(OUT, report)
    print(json.dumps({
        "ok": ok,
        "status": report["status"],
        "checks": checks,
        "counts": report["counts"],
        "selected_banks": report["selected_banks"],
        "out": str(OUT.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
