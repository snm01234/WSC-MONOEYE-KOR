#!/usr/bin/env python3
"""Independent audit for the screen-proven battle/ID-command follow-up candidate."""
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
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from extract_script import split_prefix_body
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union
from monoeye_rom import BANK_SIZE, Tbl, read_encoded_z_safe, stock_base
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CANDIDATE = ROOT / "out/patch/battle_id_command_followup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_id_command_followup_candidate.sav"
SPEC = ROOT / "data/battle_id_command_followup_ko.json"
BUILD_REPORT = ROOT / "out/patch/battle_id_command_followup_report.json"
PARENT_RESIDUAL = ROOT / "out/patch/ui_width_correction_v2_postpromotion_residual_audit.json"
CANDIDATE_RESIDUAL = ROOT / "out/patch/battle_id_command_followup_residual_audit.json"
STRUCTURE_PARENT = ROOT / "out/patch/battle_id_command_followup_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/battle_id_command_followup_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/battle_id_command_followup_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/battle_id_command_followup_false_segptr_candidate.json"
NONDIAG_PARENT = ROOT / "out/patch/battle_id_command_followup_nondialogue_parent.json"
NONDIAG_CANDIDATE = ROOT / "out/patch/battle_id_command_followup_nondialogue_candidate.json"
MIXED_PARENT = ROOT / "out/patch/battle_id_command_followup_mixed_parent.json"
MIXED_CANDIDATE = ROOT / "out/patch/battle_id_command_followup_mixed_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/battle_id_command_followup_smoke_parent.json"
SMOKE_CANDIDATE = ROOT / "out/patch/battle_id_command_followup_smoke_candidate.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/battle_id_command_followup_candidate_audit.json"

EXPECTED_PARENT_SHA = "29f790238e02eb228db97619b9c55bc900495bfef2f76eb819d1abcfb5d430f0"
EXPECTED_CANDIDATE_SHA = "72d36edf19c06fd6c332e98e9824b93413f8c12819e8b2eb3755607ffb6dcc76"
EXPECTED_TARGETS = 19
EXPECTED_PHRASES = 3
EXPECTED_RESIDUALS = 195
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
UNIT_BANKS = tuple(range(0x50, 0x5E)) + tuple(range(0x6A, 0x70))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha(payload)}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON root: {path}")
    return value


def flatten(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = document.get("records") or {}
    if isinstance(records, list):
        return [dict(row) for row in records]
    result: list[dict[str, Any]] = []
    for bucket in records.values():
        result.extend(dict(row) for row in (bucket or []))
    return result


def issue_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row.get("abs"), row.get("kind"), row.get("orig_terminator"), row.get("target_terminator"), row.get("delta"))


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def count_occurrences(blob: bytes, needle: bytes) -> int:
    count = 0
    cursor = 0
    while True:
        cursor = blob.find(needle, cursor)
        if cursor < 0:
            return count
        count += 1
        cursor += 1


def main() -> int:
    parent = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    spec = load(SPEC)
    build = load(BUILD_REPORT)
    parent_residual = load(PARENT_RESIDUAL)
    candidate_residual = load(CANDIDATE_RESIDUAL)
    structure_parent = load(STRUCTURE_PARENT)
    structure_candidate = load(STRUCTURE_CANDIDATE)
    false_parent = load(FALSE_PARENT)
    false_candidate = load(FALSE_CANDIDATE)
    nond_parent = load(NONDIAG_PARENT)
    nond_candidate = load(NONDIAG_CANDIDATE)
    mixed_parent = load(MIXED_PARENT)
    mixed_candidate = load(MIXED_CANDIDATE)
    smoke_parent = load(SMOKE_PARENT)
    smoke_candidate = load(SMOKE_CANDIDATE)

    identities = {
        "rom_sizes": len(parent) == len(candidate) == ROM_SIZE,
        "save_sizes": len(main_save) == len(candidate_save) == SAVE_SIZE,
        "parent_sha": sha(parent) == EXPECTED_PARENT_SHA,
        "candidate_sha": sha(candidate) == EXPECTED_CANDIDATE_SHA,
        "report_parent_bound": str((build.get("parent") or {}).get("sha256") or "") == EXPECTED_PARENT_SHA,
        "report_candidate_bound": str((build.get("candidate") or {}).get("sha256") or "") == EXPECTED_CANDIDATE_SHA,
        "report_ok_unpublished": build.get("ok") is True and build.get("published") is False,
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    after_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    base = stock_base(parent)

    spec_rows = spec.get("records") or []
    report_rows = build.get("records") or []
    report_by_start = {str(row.get("record_start") or "").upper(): row for row in report_rows}
    target_starts = {int(str(row["record_start"]), 16) for row in spec_rows}
    target_extents: list[tuple[int, int]] = []
    target_checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    pattern_parent_counts: dict[str, int] = {}
    pattern_candidate_counts: dict[str, int] = {}

    for item in spec_rows:
        record_hex = str(item["record_start"]).upper()
        record_start = int(record_hex, 16)
        body_start = int(str(item["body_start"]), 16)
        prefix = bytes.fromhex(str(item.get("prefix_hex") or ""))
        body = bytes.fromhex(str(item.get("body_hex") or ""))
        expected = normalize_ko_text(str(item["ko"])).rstrip("\u3000 \t")
        applied = report_by_start.get(record_hex)
        before_read = read_encoded_z_safe(parent, base + record_start, max_len=128)
        after_read = read_encoded_z_safe(candidate, base + record_start, max_len=128)
        if applied is None or before_read is None or after_read is None:
            failures.append({"record_start": record_hex, "reason": "missing_report_or_record"})
            continue
        before_payload, before_term = bytes(before_read[0]), int(before_read[1])
        after_payload, after_term = bytes(after_read[0]), int(after_read[1])
        index = int(str(applied.get("ext3_index") or "0"), 16)
        token = token_from_ext3_index(index, num_banks=num_banks)
        rendered = after_dictionary.expand(after_payload[len(prefix):], tbl).rstrip("\u3000 \t")
        source_bound = before_payload == prefix + body and body_start == record_start + len(prefix)
        structure_ok = (
            len(after_payload) == len(before_payload)
            and after_payload[: len(prefix)] == prefix
            and before_term == after_term
            and candidate[after_term] == 0
        )
        token_ok = after_payload[len(prefix):len(prefix)+4] == token
        report_bound = (
            str(applied.get("body_start") or "").upper() == str(item["body_start"]).upper()
            and str(applied.get("after") or "").rstrip("\u3000 \t") == expected
            and int(applied.get("body_capacity") or 0) == len(body)
        )
        ok = source_bound and structure_ok and token_ok and report_bound and rendered == expected and not any(is_japanese_character(c) for c in rendered)
        check = {
            "record_start": record_hex,
            "body_start": str(item["body_start"]).upper(),
            "category": item.get("category"),
            "expected": expected,
            "actual": rendered,
            "source_bound": source_bound,
            "structure_ok": structure_ok,
            "token_ok": token_ok,
            "report_bound": report_bound,
            "ok": ok,
        }
        target_checks.append(check)
        if not ok:
            failures.append(check)
        target_extents.append((base + body_start, base + body_start + len(body)))
        key = body.hex().upper()
        pattern_parent_counts[key] = count_occurrences(parent, body)
        pattern_candidate_counts[key] = count_occurrences(candidate, body)

    patterns_gate = {
        "ok": all(pattern_candidate_counts.get(key, -1) == 0 for key in pattern_parent_counts),
        "parent_occurrences": pattern_parent_counts,
        "candidate_occurrences": pattern_candidate_counts,
    }

    assignment = (build.get("allocation") or {}).get("assignments") or {}
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    ext3_checks: list[dict[str, Any]] = []
    for phrase, value in sorted(assignment.items()):
        index = int(str(value), 16)
        segment, _local = bank_local_for_index(index)
        actual = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        expected = str(phrase).rstrip("\u3000 \t")
        ok = index in inventory.ext3_free and segment == ALLOC_SEG and actual == expected and not any(is_japanese_character(c) for c in actual)
        ext3_checks.append({"index": f"{index:05X}", "segment": f"{segment:02X}", "was_free": index in inventory.ext3_free, "expected": expected, "actual": actual, "ok": ok})

    existing_expected = {
        0x75E277: "……내게는　보인다",
        0x75D5A9: "미노프스키　입자　산포！",
        0x63EB33: "으악！",
        0x5D4D88: "큭、맞았다！！",
        0x5D5082: "큭、맞았다！！",
    }
    existing_checks: list[dict[str, Any]] = []
    for address, expected in existing_expected.items():
        got = read_encoded_z_safe(candidate, base + address, max_len=128)
        if got is None:
            existing_checks.append({"abs": f"{address:06X}", "expected": expected, "actual": None, "ok": False})
            continue
        payload = bytes(got[0])
        if address == 0x63EB33:
            _prefix, body, _kind = split_prefix_body(payload)
        else:
            body = payload
        actual = after_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        existing_checks.append({"abs": f"{address:06X}", "expected": expected, "actual": actual, "ok": actual == expected})

    non_target = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=before_dictionary,
        after_dictionary=after_dictionary,
        tbl=tbl,
        excluded=target_starts,
    )

    allocation = build.get("allocation") or {}
    allowed = list(target_extents)
    for row in report_rows:
        index = int(str(row.get("ext3_index") or "0"), 16)
        segment, local = bank_local_for_index(index)
        extent = (segment * BANK_SIZE + local * 2, segment * BANK_SIZE + local * 2 + 2)
        if extent not in allowed:
            allowed.append(extent)
    allowed.append((ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_before") or "0"), 16), ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_after") or "0"), 16)))
    allowed.append((len(parent)-2, len(parent)))
    runs = diff_runs(parent, candidate)
    unaccounted = [{"start": f"{left:08X}", "end_exclusive": f"{right:08X}"} for left, right in runs if not covered((left, right), allowed)]
    diff_gate = {
        "ok": not unaccounted and sum(right-left for left,right in runs) == int((build.get("diff") or {}).get("changed_bytes_from_parent") or 0) and len(runs) == int((build.get("diff") or {}).get("runs") or 0),
        "changed_bytes": sum(right-left for left,right in runs),
        "runs": len(runs),
        "unaccounted": unaccounted,
    }

    parent_rows = flatten(parent_residual)
    candidate_rows = flatten(candidate_residual)
    residual_gate = {
        "ok": parent_residual.get("ok") is True and candidate_residual.get("ok") is True and len(parent_rows) == len(candidate_rows) == EXPECTED_RESIDUALS and {str(r.get("record_id") or "") for r in parent_rows} == {str(r.get("record_id") or "") for r in candidate_rows},
        "parent": len(parent_rows),
        "candidate": len(candidate_rows),
    }
    parent_issues = {issue_signature(row) for row in structure_parent.get("first_issues") or []}
    candidate_issues = {issue_signature(row) for row in structure_candidate.get("first_issues") or []}
    structure_gate = {
        "ok": int(structure_parent.get("issues") or 0) == int(structure_candidate.get("issues") or 0) and parent_issues == candidate_issues,
        "parent": int(structure_parent.get("issues") or 0),
        "candidate": int(structure_candidate.get("issues") or 0),
    }
    false_gate = {
        "ok": int(false_parent.get("sites_found") or 0) == 0 and int(false_candidate.get("sites_found") or 0) == 0,
        "parent": int(false_parent.get("sites_found") or 0),
        "candidate": int(false_candidate.get("sites_found") or 0),
    }
    nondialogue_gate = {
        "ok": all((nond_candidate.get(key) or {}).get("ok") is True for key in ("check_ii_marker_records", "check_iii_length_terminator", "check_iv_nested_dictionary_detachment")) and non_target.get("ok") is True,
        "parent_check_i": nond_parent.get("check_i_dict_expansion"),
        "candidate_check_i": nond_candidate.get("check_i_dict_expansion"),
    }
    mp = mixed_parent.get("counts") or {}
    mc = mixed_candidate.get("counts") or {}
    mixed_gate = {
        "ok": int(mc.get("scan_errors") or 0) == 0 and int(mc.get("broken_word_hits") or 0) <= int(mp.get("broken_word_hits") or 0) and int(mc.get("split_compound_hits") or 0) <= int(mp.get("split_compound_hits") or 0) and int(mc.get("particle_hits") or 0) <= int(mp.get("particle_hits") or 0),
        "parent": mp,
        "candidate": mc,
    }
    unit_changes: list[int] = []
    for bank in UNIT_BANKS:
        start = base + bank * BANK_SIZE
        end = start + BANK_SIZE
        unit_changes.extend(offset for offset in range(start, end) if parent[offset] != candidate[offset])
    outside = [f"{offset-base:06X}" for offset in unit_changes if not any(start <= offset < end for start, end in target_extents)]
    smoke_gate = {
        "ok": smoke_candidate.get("jagd_ok") is True and smoke_candidate.get("opening_required_ok") is True and smoke_candidate.get("hangul_ok") is True and not outside,
        "parent_overall": smoke_parent.get("overall_ok"),
        "candidate_overall": smoke_candidate.get("overall_ok"),
        "unit_changed_bytes": len(unit_changes),
        "outside_targets": outside,
    }

    checks = {
        "identities": all(identities.values()),
        "target_population": len(target_checks) == EXPECTED_TARGETS and len(report_rows) == EXPECTED_TARGETS,
        "targets_exact": not failures,
        "original_duplicate_patterns_removed": patterns_gate["ok"],
        "ext3_allocations": len(ext3_checks) == EXPECTED_PHRASES and all(row["ok"] for row in ext3_checks),
        "existing_screenshot_translations_preserved": len(existing_checks) == 5 and all(row["ok"] for row in existing_checks),
        "non_target_invariance": non_target.get("ok") is True,
        "diffs_bounded": diff_gate["ok"],
        "residual_population": residual_gate["ok"],
        "structure_delta": structure_gate["ok"],
        "false_segmented_pointer_writes": false_gate["ok"],
        "nondialogue_delta": nondialogue_gate["ok"],
        "mixed_artifact_delta": mixed_gate["ok"],
        "smoke_delta": smoke_gate["ok"],
        "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_PARENT_SHA,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_id_command_followup_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_visual_test" if ok else "failed",
        "inputs": {
            "main": identity(MAIN, parent),
            "main_save": identity(MAIN_SAVE, main_save),
            "candidate": identity(CANDIDATE, candidate),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "spec": identity(SPEC),
            "build_report": identity(BUILD_REPORT),
            "candidate_residual": identity(CANDIDATE_RESIDUAL),
        },
        "checks": checks,
        "identity_checks": identities,
        "counts": {
            "targets": len(target_checks),
            "target_failures": len(failures),
            "battle_dialogue_duplicates": sum(str(row.get("category")) == "battle_dialogue_duplicate" for row in spec_rows),
            "id_command_activation_duplicates": sum(str(row.get("category")) == "id_command_activation_duplicate" for row in spec_rows),
            "ext3_slots": len(ext3_checks),
            "existing_screenshot_translations": len(existing_checks),
            "non_target_records": int(non_target.get("records_checked") or 0),
            "remaining_residuals": len(candidate_rows),
        },
        "target_failures": failures,
        "target_checks": target_checks,
        "pattern_gate": patterns_gate,
        "ext3_checks": ext3_checks,
        "existing_translation_checks": existing_checks,
        "diff_gate": diff_gate,
        "residual_gate": residual_gate,
        "structure_gate": structure_gate,
        "false_segptr_gate": false_gate,
        "nondialogue_gate": nondialogue_gate,
        "mixed_gate": mixed_gate,
        "smoke_gate": smoke_gate,
        "saveram_policy": {"candidate_save_size_valid": len(candidate_save) == SAVE_SIZE, "candidate_save_hash_not_a_gate": True},
        "promotion": "blocked_pending_user_visual_verification",
    }
    write_json(OUT, report)
    print(json.dumps({"ok": ok, "status": report["status"], "checks": checks, "counts": report["counts"], "out": str(OUT.resolve())}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
