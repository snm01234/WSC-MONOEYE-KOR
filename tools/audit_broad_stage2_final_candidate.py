#!/usr/bin/env python3
"""Comprehensive independent audit of the cumulative broad stage-2 candidate.

The final candidate combines:
* 288 proven dialogue/voice records;
* 127 complete work/stage/title/map/communication UI records;
* 149 bank-5C unused placeholders.

The remaining 195 Japanese-looking records are classified as structural/data
exclusions and are not treated as untranslated visible text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union
from monoeye_rom import BANK_SIZE, DICT_PTR_START, SEG_DICT, Tbl, read_encoded_z_safe, stock_base, token_from_dict_index
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DIALOGUE_CANDIDATE = ROOT / "out/patch/broad_stage2_dialogue_voice_candidate.wsc"
TITLE_CANDIDATE = ROOT / "out/patch/broad_stage2_title_ui_candidate.wsc"
FINAL = ROOT / "out/patch/broad_stage2_placeholder_candidate.wsc"
FINAL_SAVE = ROOT / "sram/broad_stage2_placeholder_candidate.sav"
BASELINE_AUDIT = ROOT / "out/patch/broad_stage2_ui_system_postpromotion_residual_audit.json"
FINAL_RESIDUAL = ROOT / "out/patch/broad_stage2_final_residual_audit.json"
DIALOGUE_CATALOG = ROOT / "data/broad_stage2_dialogue_voice_ko.json"
TITLE_CATALOG = ROOT / "data/broad_stage2_title_ui_ko.json"
PLACEHOLDER_CATALOG = ROOT / "data/broad_stage2_placeholder_ko.json"
DIALOGUE_REPORT = ROOT / "out/patch/broad_stage2_dialogue_voice_report.json"
TITLE_REPORT = ROOT / "out/patch/broad_stage2_title_ui_report.json"
PLACEHOLDER_REPORT = ROOT / "out/patch/broad_stage2_placeholder_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
STRUCTURE_PARENT = ROOT / "out/patch/broad_stage2_final_structure_parent.json"
STRUCTURE_FINAL = ROOT / "out/patch/broad_stage2_final_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/broad_stage2_final_false_segptr_parent.json"
FALSE_FINAL = ROOT / "out/patch/broad_stage2_final_false_segptr_candidate.json"
NONDIAG_PARENT = ROOT / "out/patch/broad_stage2_final_nondialogue_parent.json"
NONDIAG_FINAL = ROOT / "out/patch/broad_stage2_final_nondialogue_candidate.json"
MIXED_PARENT = ROOT / "out/patch/broad_stage2_final_mixed_parent.json"
MIXED_FINAL = ROOT / "out/patch/broad_stage2_final_mixed_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/broad_stage2_final_smoke_parent.json"
SMOKE_FINAL = ROOT / "out/patch/broad_stage2_final_smoke_candidate.json"
OUT = ROOT / "out/patch/broad_stage2_final_candidate_audit.json"
ANALYSIS_OUT = ROOT / "out/patch/broad_stage2_remaining_759_analysis.json"

EXPECTED_MAIN_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
EXPECTED_FINAL_SHA = "c9f7049873d6040c63d99144db709c80a163ba1ff679f58f139e8eadea47635c"
EXPECTED_BASELINE = 759
EXPECTED_PATCHED = 564
EXPECTED_REMAINING = 195
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ALLOC_SEG = 0x1C
UNIT_BANKS = tuple(range(0x50, 0x5E)) + tuple(range(0x6A, 0x70))


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha(payload)}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"invalid JSON root: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def flatten(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = document.get("records") or {}
    if isinstance(records, list):
        return [dict(row) for row in records]
    result: list[dict[str, Any]] = []
    if isinstance(records, Mapping):
        for bucket in records.values():
            result.extend(dict(row) for row in (bucket or []))
    return result


def issue_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row.get("abs"), row.get("kind"), row.get("orig_terminator"), row.get("target_terminator"), row.get("delta"))


def within(offset: int, extents: Iterable[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in extents)


def normalize_targets() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    targets: list[dict[str, Any]] = []
    build_rows: dict[str, dict[str, Any]] = {}
    reports = [load(DIALOGUE_REPORT), load(TITLE_REPORT), load(PLACEHOLDER_REPORT)]
    for report in reports:
        if report.get("ok") is not True or report.get("published") is not False:
            raise AuditError("one or more build reports are not successful/unpublished")
        for row in report.get("records") or []:
            address = str(row.get("abs") or "").upper()
            if address in build_rows:
                raise AuditError(f"duplicate build target: {address}")
            build_rows[address] = dict(row)

    dialogue = load(DIALOGUE_CATALOG)
    for row in dialogue.get("lines") or []:
        targets.append({"abs": str(row["abs"]).upper(), "record_id": row["record_id"], "group": "dialogue_voice", "jp": row["jp_body"], "ko": normalize_ko_text(row["ko"])})
    title = load(TITLE_CATALOG)
    for row in title.get("lines") or []:
        targets.append({"abs": str(row["abs"]).upper(), "record_id": row["record_id"], "group": "title_ui", "jp": row["jp"], "ko": normalize_ko_text(row["ko"])})
    placeholder = load(PLACEHOLDER_CATALOG)
    for row in placeholder.get("lines") or []:
        targets.append({"abs": str(row["abs"]).upper(), "record_id": row["record_id"], "group": "unused_placeholder", "jp": row["jp"], "ko": normalize_ko_text(row["ko"])})
    targets.sort(key=lambda row: int(row["abs"], 16))
    if len(targets) != EXPECTED_PATCHED or len({row["abs"] for row in targets}) != EXPECTED_PATCHED:
        raise AuditError("cumulative target population is not 564 unique records")
    if set(build_rows) != {row["abs"] for row in targets}:
        raise AuditError("build report target union differs from catalog union")
    return targets, build_rows


def classify_remaining(rows: list[dict[str, Any]]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts = {"script_structural_noise": 0, "name75_data_or_fragment": 0, "ui_walker_noise": 0, "ui_ambiguous_system": 0, "ui_kana_index": 0}
    classified: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item["logical_address"])):
        region = str(row.get("region") or "")
        logical = int(row["logical_address"])
        if region == "script":
            category = "script_structural_noise"
            action = "exclude until a real event/table consumer proves this is visible text"
        elif region == "name75":
            category = "name75_data_or_fragment"
            action = "exclude; one-byte fragment or known data tail"
        elif region == "name75_ui" and logical < 0x75B2E3:
            category = "ui_walker_noise"
            action = "exclude; walker begins inside non-zstring data and yields sentence fragments"
        elif region == "name75_ui" and logical < 0x75B889:
            category = "ui_ambiguous_system"
            action = "screen/table-specific proof required; includes one-byte and separator records"
        elif region == "name75_ui" and logical < 0x75B8DA:
            category = "ui_kana_index"
            action = "exclude from direct translation; kana sorting/index table requires renderer redesign"
        else:
            raise AuditError(f"unexpected remaining record outside exclusion policy: {row.get('abs')}")
        counts[category] += 1
        classified.append({"record_id": row.get("record_id"), "abs": row.get("abs"), "region": region, "text": row.get("current_text"), "body_capacity": row.get("body_capacity"), "category": category, "recommended_action": action})
    expected = {"script_structural_noise": 43, "name75_data_or_fragment": 56, "ui_walker_noise": 80, "ui_ambiguous_system": 7, "ui_kana_index": 9}
    if counts != expected:
        raise AuditError(f"remaining exclusion population drifted: {counts}")
    return counts, classified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--analysis-out", type=Path, default=ANALYSIS_OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc" or args.analysis_out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    main_rom = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    dialogue_candidate = DIALOGUE_CANDIDATE.read_bytes()
    title_candidate = TITLE_CANDIDATE.read_bytes()
    final = FINAL.read_bytes()
    final_save = FINAL_SAVE.read_bytes()
    baseline = load(BASELINE_AUDIT)
    residual = load(FINAL_RESIDUAL)
    dialogue_report = load(DIALOGUE_REPORT)
    title_report = load(TITLE_REPORT)
    placeholder_report = load(PLACEHOLDER_REPORT)
    structure_parent = load(STRUCTURE_PARENT)
    structure_final = load(STRUCTURE_FINAL)
    false_parent = load(FALSE_PARENT)
    false_final = load(FALSE_FINAL)
    nond_parent = load(NONDIAG_PARENT)
    nond_final = load(NONDIAG_FINAL)
    mixed_parent = load(MIXED_PARENT)
    mixed_final = load(MIXED_FINAL)
    smoke_parent = load(SMOKE_PARENT)
    smoke_final = load(SMOKE_FINAL)

    identity_checks = {
        "rom_sizes": len(main_rom) == len(final) == ROM_SIZE,
        "save_sizes": len(main_save) == len(final_save) == SAVE_SIZE,
        "main_sha": sha(main_rom) == EXPECTED_MAIN_SHA,
        "final_sha": sha(final) == EXPECTED_FINAL_SHA,
        "dialogue_parent_binding": str((dialogue_report.get("parent") or {}).get("sha256") or "") == EXPECTED_MAIN_SHA,
        "title_main_binding": str((title_report.get("main_tip") or {}).get("sha256") or "") == EXPECTED_MAIN_SHA,
        "title_parent_binding": str((title_report.get("parent_dialogue_candidate") or {}).get("sha256") or "") == str((dialogue_report.get("candidate") or {}).get("sha256") or ""),
        "placeholder_main_binding": str((placeholder_report.get("main_tip") or {}).get("sha256") or "") == EXPECTED_MAIN_SHA,
        "placeholder_parent_binding": str((placeholder_report.get("parent_title_ui_candidate") or {}).get("sha256") or "") == str((title_report.get("candidate") or {}).get("sha256") or ""),
        "final_report_binding": str((placeholder_report.get("candidate") or {}).get("sha256") or "") == EXPECTED_FINAL_SHA,
        "baseline_ok": baseline.get("ok") is True,
        "residual_ok": residual.get("ok") is True,
    }

    targets, build_rows = normalize_targets()
    baseline_rows = flatten(baseline)
    baseline_by_abs = {str(row.get("abs") or "").upper(): row for row in baseline_rows}
    target_ids = {str(row["record_id"]) for row in targets}
    target_addresses = {row["abs"] for row in targets}

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    before_dictionary = make_dictionary_ext3(main_rom, ext_meta, ext3_meta)
    after_dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)
    sb = stock_base(main_rom)

    target_checks: list[dict[str, Any]] = []
    target_failures: list[dict[str, Any]] = []
    target_extents: list[tuple[int, int]] = []
    new_stock_expected: dict[int, set[tuple[str, str]]] = {}
    ext3_expected: dict[int, str] = {}
    group_counts: dict[str, int] = {}
    new_stock_slots: set[int] = set()
    for report in (dialogue_report, title_report):
        new_stock_slots.update(int(str(value), 16) for value in ((report.get("allocation") or {}).get("selected_retired_slots") or []))
    if str((placeholder_report.get("allocation") or {}).get("strategy") or "") == "strong_retired_stock":
        new_stock_slots.add(int(str((placeholder_report.get("allocation") or {}).get("stock_index") or "0"), 16))

    for target in targets:
        address = target["abs"]
        source = baseline_by_abs.get(address)
        applied = build_rows[address]
        if source is None:
            target_failures.append({"abs": address, "reason": "baseline_source_missing"})
            continue
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        body = bytes.fromhex(str(source.get("body_hex") or ""))
        prefix_len = int(source.get("prefix_bytes") or 0)
        body_capacity = int(source.get("body_capacity") or 0)
        expected = str(target["ko"]).rstrip("\u3000 \t")
        before_read = read_encoded_z_safe(main_rom, sb + logical, max_len=256)
        after_read = read_encoded_z_safe(final, sb + logical, max_len=256)
        if before_read is None or after_read is None:
            target_failures.append({"abs": address, "reason": "record_unreadable"})
            continue
        before_payload, before_term = bytes(before_read[0]), int(before_read[1])
        after_payload, after_term = bytes(after_read[0]), int(after_read[1])
        source_bound = before_payload == prefix + body and str(source.get("record_id") or "") == str(target["record_id"])
        structure_ok = len(after_payload) == len(before_payload) and after_payload[:prefix_len] == before_payload[:prefix_len] and before_term == after_term and final[after_term] == 0
        rendered = after_dictionary.expand(after_payload[prefix_len:], tbl).rstrip("\u3000 \t")
        japanese = sum(is_japanese_character(character) for character in rendered)
        strategy = str(applied.get("strategy") or "")
        token_ok = False
        if strategy == "private_ext3":
            index = int(str(applied.get("ext3_index") or "0"), 16)
            token = token_from_ext3_index(index, num_banks=num_banks)
            token_ok = after_payload[prefix_len:prefix_len + 4] == token
            previous = ext3_expected.setdefault(index, expected)
            if previous != expected:
                target_failures.append({"abs": address, "reason": "ext3_index_phrase_conflict"})
        elif strategy in {"strong_retired_stock", "existing_exact_stock"}:
            index = int(str(applied.get("stock_index") or "0"), 16)
            token = token_from_dict_index(index)
            token_ok = after_payload[prefix_len:prefix_len + 2] == token
            if index in new_stock_slots:
                new_stock_expected.setdefault(index, set()).add((address, f"{logical + prefix_len:06X}"))
        report_bound = str(applied.get("record_id") or "") == str(target["record_id"]) and str(applied.get("after") or "").rstrip("\u3000 \t") == expected and int(applied.get("body_capacity") or 0) == body_capacity
        ok = source_bound and structure_ok and report_bound and token_ok and rendered == expected and japanese == 0
        check = {"abs": address, "record_id": target["record_id"], "group": target["group"], "expected": expected, "actual": rendered, "strategy": strategy, "source_bound": source_bound, "structure_ok": structure_ok, "report_bound": report_bound, "token_ok": token_ok, "japanese": japanese, "ok": ok}
        target_checks.append(check)
        if not ok:
            target_failures.append(check)
        target_extents.append((sb + logical + prefix_len, sb + logical + prefix_len + body_capacity))
        group_counts[target["group"]] = group_counts.get(target["group"], 0) + 1

    dialogue_slots = {int(str(value), 16) for value in ((dialogue_report.get("allocation") or {}).get("selected_retired_slots") or [])}
    title_slots = {int(str(value), 16) for value in ((title_report.get("allocation") or {}).get("selected_retired_slots") or [])}
    placeholder_slots = set()
    if str((placeholder_report.get("allocation") or {}).get("strategy") or "") == "strong_retired_stock":
        placeholder_slots.add(int(str((placeholder_report.get("allocation") or {}).get("stock_index") or "0"), 16))
    stage_parents = {
        "main": (main_rom, before_dictionary),
        "dialogue": (dialogue_candidate, make_dictionary_ext3(dialogue_candidate, ext_meta, ext3_meta)),
        "title": (title_candidate, make_dictionary_ext3(title_candidate, ext_meta, ext3_meta)),
    }
    slot_stage = {index: "main" for index in dialogue_slots}
    slot_stage.update({index: "dialogue" for index in title_slots})
    slot_stage.update({index: "title" for index in placeholder_slots})
    if set(slot_stage) != new_stock_slots:
        raise AuditError("new stock slot stage ownership mismatch")
    stage_reference_maps: dict[str, tuple[dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]] = {}
    for stage, (stage_rom, stage_dictionary) in stage_parents.items():
        wanted = {index for index, owner in slot_stage.items() if owner == stage}
        if not wanted:
            stage_reference_maps[stage] = ({}, {}, {})
            continue
        stage_reference_maps[stage] = (
            external_occurrence_map(stage_rom, ext3_aware=True, wanted=wanted),
            nested_occurrence_map(stage_dictionary, wanted=wanted, ext3_aware=True),
            _raw_pair_hits(stage_rom, sorted(wanted)),
        )
    final_external = external_occurrence_map(final, ext3_aware=True, wanted=new_stock_slots)
    final_nested = nested_occurrence_map(after_dictionary, wanted=new_stock_slots, ext3_aware=True)
    stock_checks: list[dict[str, Any]] = []
    for index in sorted(new_stock_slots):
        stage = slot_stage[index]
        parent_external, parent_nested, parent_raw = stage_reference_maps[stage]
        actual_sites = {(str(row.get("record_abs") or ""), str(row.get("token_abs") or "")) for row in final_external.get(index, [])}
        expected_sites = new_stock_expected.get(index, set())
        phrases = {str(row.get("after") or "").rstrip("\u3000 \t") for row in build_rows.values() if int(str(row.get("stock_index") or "0"), 16) == index}
        actual_phrase = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        ok = not parent_external.get(index) and not parent_nested.get(index) and not parent_raw.get(index) and not final_nested.get(index) and actual_sites == expected_sites and len(phrases) == 1 and actual_phrase in phrases
        stock_checks.append({"index": f"{index:04X}", "allocation_parent": stage, "expected_sites": sorted([list(site) for site in expected_sites]), "actual_sites": sorted([list(site) for site in actual_sites]), "expected_phrases": sorted(phrases), "actual_phrase": actual_phrase, "ok": ok})

    union = build_reference_union(original, main_rom, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(main_rom, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    ext3_checks: list[dict[str, Any]] = []
    for index, expected in sorted(ext3_expected.items()):
        segment, _local = bank_local_for_index(index)
        actual = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        ok = index in inventory.ext3_free and segment == ALLOC_SEG and actual == expected and not any(is_japanese_character(character) for character in actual)
        ext3_checks.append({"index": f"{index:05X}", "segment": f"{segment:02X}", "was_free_in_main": index in inventory.ext3_free, "expected": expected, "actual": actual, "ok": ok})

    non_target = verify_non_target_invariance(main_rom, final, before_dictionary=before_dictionary, after_dictionary=after_dictionary, tbl=tbl, excluded={int(address, 16) for address in target_addresses})

    allowed = list(target_extents)
    for report in (dialogue_report, title_report):
        allocation = report.get("allocation") or {}
        for row in report.get("records") or []:
            if row.get("ext3_index"):
                index = int(str(row["ext3_index"]), 16)
                segment, local = bank_local_for_index(index)
                extent = (segment * BANK_SIZE + local * 2, segment * BANK_SIZE + local * 2 + 2)
                if extent not in allowed:
                    allowed.append(extent)
        allowed.append((ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_before") or "0"), 16), ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_after") or "0"), 16)))
        stock_bank_file = stock_base(main_rom) + SEG_DICT * BANK_SIZE
        for value in allocation.get("selected_retired_slots") or []:
            index = int(str(value), 16)
            allowed.append((stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2))
        allowed.append((stock_bank_file + int(str(allocation.get("stock_cursor_before") or "0"), 16), stock_bank_file + int(str(allocation.get("stock_cursor_after") or "0"), 16)))
    placeholder_allocation = placeholder_report.get("allocation") or {}
    stock_bank_file = stock_base(main_rom) + SEG_DICT * BANK_SIZE
    if str(placeholder_allocation.get("strategy") or "") == "strong_retired_stock":
        index = int(str(placeholder_allocation.get("stock_index") or "0"), 16)
        allowed.append((stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2))
        allowed.append((stock_bank_file + int(str(placeholder_allocation.get("stock_cursor_before") or "0"), 16), stock_bank_file + int(str(placeholder_allocation.get("stock_cursor_after") or "0"), 16)))
    allowed.append((len(main_rom) - 2, len(main_rom)))
    runs = diff_runs(main_rom, final)
    unaccounted = [{"start": f"{left:08X}", "end_exclusive": f"{right:08X}"} for left, right in runs if not covered((left, right), allowed)]
    diff_gate = {"ok": not unaccounted, "changed_bytes": sum(right-left for left,right in runs), "runs": len(runs), "unaccounted": unaccounted}

    residual_rows = flatten(residual)
    baseline_ids = {str(row.get("record_id") or "") for row in baseline_rows}
    residual_ids = {str(row.get("record_id") or "") for row in residual_rows}
    expected_residual_ids = baseline_ids - target_ids
    remaining_counts, remaining_records = classify_remaining(residual_rows)
    residual_gate = {"ok": len(baseline_rows) == EXPECTED_BASELINE and len(target_ids) == EXPECTED_PATCHED and len(residual_rows) == EXPECTED_REMAINING and int((residual.get("counts") or {}).get("japanese_residual_records") or 0) == EXPECTED_REMAINING and residual_ids == expected_residual_ids and not (target_ids & residual_ids), "baseline": len(baseline_rows), "patched": len(target_ids), "remaining": len(residual_rows), "missing_expected": sorted(expected_residual_ids-residual_ids), "unexpected_remaining": sorted(residual_ids-expected_residual_ids), "remaining_classification": remaining_counts}

    parent_issues = {issue_signature(row) for row in structure_parent.get("first_issues") or []}
    final_issues = {issue_signature(row) for row in structure_final.get("first_issues") or []}
    structure_gate = {"ok": int(structure_parent.get("issues") or 0) == int(structure_final.get("issues") or 0) and parent_issues == final_issues, "parent": int(structure_parent.get("issues") or 0), "final": int(structure_final.get("issues") or 0), "new": [list(x) for x in sorted(final_issues-parent_issues)], "missing": [list(x) for x in sorted(parent_issues-final_issues)]}
    false_gate = {"ok": int(false_parent.get("sites_found") or 0) == 0 and int(false_final.get("sites_found") or 0) == 0, "parent": int(false_parent.get("sites_found") or 0), "final": int(false_final.get("sites_found") or 0)}
    nondialogue_gate = {"ok": all((nond_final.get(key) or {}).get("ok") is True for key in ("check_ii_marker_records", "check_iii_length_terminator", "check_iv_nested_dictionary_detachment")) and non_target.get("ok") is True, "parent_check_i": nond_parent.get("check_i_dict_expansion"), "final_check_i": nond_final.get("check_i_dict_expansion"), "final_marker": nond_final.get("check_ii_marker_records"), "final_length": nond_final.get("check_iii_length_terminator"), "final_nested": nond_final.get("check_iv_nested_dictionary_detachment"), "non_target": non_target}
    mixed_parent_counts = mixed_parent.get("counts") or {}
    mixed_final_counts = mixed_final.get("counts") or {}
    mixed_gate = {"ok": int(mixed_final_counts.get("scan_errors") or 0) == 0 and int(mixed_final_counts.get("broken_word_hits") or 0) <= int(mixed_parent_counts.get("broken_word_hits") or 0) and int(mixed_final_counts.get("split_compound_hits") or 0) <= int(mixed_parent_counts.get("split_compound_hits") or 0) and int(mixed_final_counts.get("particle_hits") or 0) <= int(mixed_parent_counts.get("particle_hits") or 0), "parent": mixed_parent_counts, "final": mixed_final_counts}

    unit_changes: list[int] = []
    for bank in UNIT_BANKS:
        start = sb + bank * BANK_SIZE
        end = start + BANK_SIZE
        unit_changes.extend(offset for offset in range(start, end) if main_rom[offset] != final[offset])
    outside = [f"{offset-sb:06X}" for offset in unit_changes if not within(offset, target_extents)]
    smoke_gate = {"ok": smoke_final.get("jagd_ok") is True and smoke_final.get("opening_required_ok") is True and smoke_final.get("hangul_ok") is True and not outside, "parent_overall": smoke_parent.get("overall_ok"), "final_overall": smoke_final.get("overall_ok"), "jagd": smoke_final.get("jagd_ok"), "opening": smoke_final.get("opening_required_ok"), "hangul": smoke_final.get("hangul_ok"), "unit_changed_bytes": len(unit_changes), "outside_targets": outside}
    runtime_start = stock_base(main_rom) + 0x7A0600
    runtime_end = stock_base(main_rom) + 0x7A1000
    runtime_unchanged = main_rom[runtime_start:runtime_end] == final[runtime_start:runtime_end]

    checks = {
        "identities": all(identity_checks.values()),
        "target_population": len(target_checks) == EXPECTED_PATCHED and group_counts == {"dialogue_voice": 288, "title_ui": 127, "unused_placeholder": 149},
        "targets_exact": not target_failures,
        "new_stock_allocations": len(stock_checks) == len(new_stock_slots) and all(row["ok"] for row in stock_checks),
        "ext3_allocations": len(ext3_checks) == len(ext3_expected) and all(row["ok"] for row in ext3_checks),
        "non_target_invariance": non_target.get("ok") is True,
        "diffs_bounded": diff_gate["ok"],
        "residual_population_and_exclusions": residual_gate["ok"],
        "structure_delta": structure_gate["ok"],
        "false_segmented_pointer_writes": false_gate["ok"],
        "nondialogue_delta": nondialogue_gate["ok"],
        "mixed_artifact_delta": mixed_gate["ok"],
        "smoke_delta": smoke_gate["ok"],
        "runtime_hook_unchanged": runtime_unchanged,
        "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
    }
    ok = all(checks.values())

    analysis = {
        "schema_version": 1,
        "generated_by": "tools/audit_broad_stage2_final_candidate.py",
        "read_only_rom": True,
        "ok": True,
        "baseline": {"records": EXPECTED_BASELINE, "audit": identity(BASELINE_AUDIT)},
        "patched": {"records": EXPECTED_PATCHED, "by_group": group_counts, "candidate": identity(FINAL, final)},
        "remaining": {"records": EXPECTED_REMAINING, "by_exclusion_class": remaining_counts, "policy": "No remaining record is approved for direct text replacement. Each requires structural/screen/renderer proof or remains data.", "records_detail": remaining_records},
        "capacity_after_candidate": residual.get("capacity"),
    }
    atomic_json(args.analysis_out, analysis)

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_broad_stage2_final_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_visual_test" if ok else "failed",
        "inputs": {"main": identity(MAIN, main_rom), "main_save": identity(MAIN_SAVE, main_save), "dialogue_candidate": identity(DIALOGUE_CANDIDATE, dialogue_candidate), "title_candidate": identity(TITLE_CANDIDATE, title_candidate), "final": identity(FINAL, final), "final_save": identity(FINAL_SAVE, final_save), "baseline_audit": identity(BASELINE_AUDIT), "final_residual_audit": identity(FINAL_RESIDUAL), "dialogue_report": identity(DIALOGUE_REPORT), "title_report": identity(TITLE_REPORT), "placeholder_report": identity(PLACEHOLDER_REPORT), "dialogue_catalog": identity(DIALOGUE_CATALOG), "title_catalog": identity(TITLE_CATALOG), "placeholder_catalog": identity(PLACEHOLDER_CATALOG), "analysis": identity(args.analysis_out)},
        "checks": checks,
        "identity_checks": identity_checks,
        "counts": {"baseline_residuals": EXPECTED_BASELINE, "patched_targets": len(target_checks), "target_failures": len(target_failures), "dialogue_voice": group_counts.get("dialogue_voice", 0), "title_ui": group_counts.get("title_ui", 0), "unused_placeholder": group_counts.get("unused_placeholder", 0), "new_stock_slots": len(stock_checks), "ext3_slots": len(ext3_checks), "non_target_records": int(non_target.get("records_checked") or 0), "remaining_structural_or_data": len(residual_rows)},
        "target_failures": target_failures,
        "target_checks": target_checks,
        "new_stock_checks": stock_checks,
        "ext3_checks": ext3_checks,
        "diff_gate": diff_gate,
        "residual_gate": residual_gate,
        "structure_gate": structure_gate,
        "false_segptr_gate": false_gate,
        "nondialogue_gate": nondialogue_gate,
        "mixed_gate": mixed_gate,
        "smoke_gate": smoke_gate,
        "saveram_policy": {"candidate_save_size_valid": len(final_save) == SAVE_SIZE, "candidate_save_hash_not_a_gate": True, "live_main_save_untouched_by_audit": True},
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(args.out, report)
    print(json.dumps({"ok": ok, "status": report["status"], "checks": checks, "counts": report["counts"], "remaining": remaining_counts, "out": str(args.out.resolve()), "analysis": str(args.analysis_out.resolve())}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
