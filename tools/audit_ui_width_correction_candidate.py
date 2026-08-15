#!/usr/bin/env python3
"""Independent static audit for the 13-record UI width correction candidate."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

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
PARENT = ROOT / "out/patch/broad_stage2_placeholder_candidate.wsc"
CANDIDATE = ROOT / "out/patch/ui_width_correction_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ui_width_correction_candidate.sav"
SPEC = ROOT / "data/ui_width_corrections_ko.json"
BUILD_REPORT = ROOT / "out/patch/ui_width_correction_report.json"
PARENT_RESIDUAL = ROOT / "out/patch/broad_stage2_final_residual_audit.json"
CANDIDATE_RESIDUAL = ROOT / "out/patch/ui_width_correction_residual_audit.json"
STRUCTURE_PARENT = ROOT / "out/patch/ui_width_correction_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/ui_width_correction_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/ui_width_correction_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/ui_width_correction_false_segptr_candidate.json"
NONDIAG_PARENT = ROOT / "out/patch/ui_width_correction_nondialogue_parent.json"
NONDIAG_CANDIDATE = ROOT / "out/patch/ui_width_correction_nondialogue_candidate.json"
MIXED_PARENT = ROOT / "out/patch/ui_width_correction_mixed_parent.json"
MIXED_CANDIDATE = ROOT / "out/patch/ui_width_correction_mixed_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/ui_width_correction_smoke_parent.json"
SMOKE_CANDIDATE = ROOT / "out/patch/ui_width_correction_smoke_candidate.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/ui_width_correction_candidate_audit.json"

EXPECTED_MAIN_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
EXPECTED_PARENT_SHA = "c9f7049873d6040c63d99144db709c80a163ba1ff679f58f139e8eadea47635c"
EXPECTED_CANDIDATE_SHA = "f1d2352a4384250df3e55fdf9ee507f366a11f12ab477cb07f4ee9a909c46c45"
EXPECTED_TARGETS = 13
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ALLOC_SEG = 0x1C
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
    result: list[dict[str, Any]] = []
    records = document.get("records") or {}
    if isinstance(records, list):
        return [dict(row) for row in records]
    for bucket in records.values():
        result.extend(dict(row) for row in (bucket or []))
    return result


def issue_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row.get("abs"), row.get("kind"), row.get("orig_terminator"), row.get("target_terminator"), row.get("delta"))


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main() -> int:
    main_rom = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    parent = PARENT.read_bytes()
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
        "rom_sizes": len(main_rom) == len(parent) == len(candidate) == ROM_SIZE,
        "save_sizes": len(main_save) == len(candidate_save) == SAVE_SIZE,
        "main_sha": sha(main_rom) == EXPECTED_MAIN_SHA,
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
    sb = stock_base(parent)
    spec_by_abs = {str(row["abs"]).upper(): row for row in spec.get("records") or []}
    build_by_abs = {str(row["abs"]).upper(): row for row in build.get("records") or []}

    target_checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    target_extents: list[tuple[int, int]] = []
    target_addresses = set(spec_by_abs)
    for address, item in sorted(spec_by_abs.items()):
        logical = int(address, 16)
        applied = build_by_abs.get(address)
        before_read = read_encoded_z_safe(parent, sb + logical, max_len=256)
        after_read = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if applied is None or before_read is None or after_read is None:
            failures.append({"abs": address, "reason": "missing_report_or_record"})
            continue
        before_payload, before_term = bytes(before_read[0]), int(before_read[1])
        after_payload, after_term = bytes(after_read[0]), int(after_read[1])
        before_text = before_dictionary.expand(before_payload, tbl).rstrip("\u3000 \t")
        after_text = after_dictionary.expand(after_payload, tbl).rstrip("\u3000 \t")
        expected_before = normalize_ko_text(str(item["before"])).rstrip("\u3000 \t")
        expected_after = normalize_ko_text(str(item["after"])).rstrip("\u3000 \t")
        cells = len(after_text)
        limit = int(item["max_visual_cells"])
        strategy = str(applied.get("strategy") or "")
        if strategy == "private_ext3":
            index = int(str(applied.get("ext3_index") or "0"), 16)
            token_ok = after_payload[:4] == token_from_ext3_index(index, num_banks=num_banks)
        else:
            index = int(str(applied.get("stock_index") or "0"), 16)
            token_ok = after_payload[:2] == token_from_dict_index(index)
        ok = (
            before_text == expected_before
            and after_text == expected_after
            and cells <= limit
            and not any(is_japanese_character(character) for character in after_text)
            and len(before_payload) == len(after_payload)
            and before_term == after_term
            and candidate[after_term] == 0
            and token_ok
            and str(applied.get("before") or "").rstrip("\u3000 \t") == expected_before
            and str(applied.get("after") or "").rstrip("\u3000 \t") == expected_after
            and int(applied.get("visual_cells") or 0) == cells
            and int(applied.get("max_visual_cells") or 0) == limit
        )
        check = {"abs": address, "before": before_text, "after": after_text, "visual_cells": cells, "max_visual_cells": limit, "strategy": strategy, "token_ok": token_ok, "leading_ideographic_space": after_text.startswith("　"), "ok": ok}
        target_checks.append(check)
        if not ok:
            failures.append(check)
        target_extents.append((sb + logical, sb + logical + len(after_payload)))

    selected_retired = {int(str(value), 16) for value in ((build.get("allocation") or {}).get("selected_retired_slots") or [])}
    stock_checks: list[dict[str, Any]] = []
    if selected_retired:
        parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_retired)
        parent_nested = nested_occurrence_map(before_dictionary, wanted=selected_retired, ext3_aware=True)
        parent_raw = _raw_pair_hits(parent, sorted(selected_retired))
        candidate_external = external_occurrence_map(candidate, ext3_aware=True, wanted=selected_retired)
        candidate_nested = nested_occurrence_map(after_dictionary, wanted=selected_retired, ext3_aware=True)
        for index in sorted(selected_retired):
            expected_sites = {
                (str(row["abs"]), str(row["abs"]))
                for row in build.get("records") or []
                if int(str(row.get("stock_index") or "0"), 16) == index
            }
            actual_sites = {(str(row.get("record_abs") or ""), str(row.get("token_abs") or "")) for row in candidate_external.get(index, [])}
            actual_phrase = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
            ok = not parent_external.get(index) and not parent_nested.get(index) and not parent_raw.get(index) and not candidate_nested.get(index) and actual_sites == expected_sites and actual_phrase == "뒤로"
            stock_checks.append({"index": f"{index:04X}", "expected_sites": sorted([list(site) for site in expected_sites]), "actual_sites": sorted([list(site) for site in actual_sites]), "actual_phrase": actual_phrase, "ok": ok})

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    ext3_checks = []
    for row in build.get("records") or []:
        if not row.get("ext3_index"):
            continue
        index = int(str(row["ext3_index"]), 16)
        segment, _local = bank_local_for_index(index)
        actual = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        expected = str(row["after"]).rstrip("\u3000 \t")
        ext3_checks.append({"index": f"{index:05X}", "was_free": index in inventory.ext3_free, "segment": f"{segment:02X}", "expected": expected, "actual": actual, "ok": index in inventory.ext3_free and segment == ALLOC_SEG and actual == expected})

    non_target = verify_non_target_invariance(parent, candidate, before_dictionary=before_dictionary, after_dictionary=after_dictionary, tbl=tbl, excluded={int(address, 16) for address in target_addresses})

    allocation = build.get("allocation") or {}
    allowed = list(target_extents)
    for row in build.get("records") or []:
        if row.get("ext3_index"):
            index = int(str(row["ext3_index"]), 16)
            segment, local = bank_local_for_index(index)
            extent = (segment * BANK_SIZE + local * 2, segment * BANK_SIZE + local * 2 + 2)
            if extent not in allowed:
                allowed.append(extent)
    allowed.append((ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_before") or "0"), 16), ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_after") or "0"), 16)))
    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    for index in selected_retired:
        allowed.append((stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2))
    allowed.append((stock_bank_file + int(str(allocation.get("stock_cursor_before") or "0"), 16), stock_bank_file + int(str(allocation.get("stock_cursor_after") or "0"), 16)))
    allowed.append((len(parent) - 2, len(parent)))
    runs = diff_runs(parent, candidate)
    unaccounted = [{"start": f"{left:08X}", "end_exclusive": f"{right:08X}"} for left, right in runs if not covered((left, right), allowed)]
    diff_gate = {"ok": not unaccounted and sum(right-left for left,right in runs) == int((build.get("diff") or {}).get("changed_bytes_from_parent") or 0) and len(runs) == int((build.get("diff") or {}).get("runs") or 0), "changed_bytes": sum(right-left for left,right in runs), "runs": len(runs), "unaccounted": unaccounted}

    parent_rows = flatten(parent_residual)
    candidate_rows = flatten(candidate_residual)
    residual_gate = {"ok": parent_residual.get("ok") is True and candidate_residual.get("ok") is True and len(parent_rows) == len(candidate_rows) == 195 and {str(row.get("record_id") or "") for row in parent_rows} == {str(row.get("record_id") or "") for row in candidate_rows}, "parent": len(parent_rows), "candidate": len(candidate_rows)}
    parent_issues = {issue_signature(row) for row in structure_parent.get("first_issues") or []}
    candidate_issues = {issue_signature(row) for row in structure_candidate.get("first_issues") or []}
    structure_gate = {"ok": int(structure_parent.get("issues") or 0) == int(structure_candidate.get("issues") or 0) and parent_issues == candidate_issues, "parent": int(structure_parent.get("issues") or 0), "candidate": int(structure_candidate.get("issues") or 0)}
    false_gate = {"ok": int(false_parent.get("sites_found") or 0) == 0 and int(false_candidate.get("sites_found") or 0) == 0, "parent": int(false_parent.get("sites_found") or 0), "candidate": int(false_candidate.get("sites_found") or 0)}
    nondialogue_gate = {"ok": all((nond_candidate.get(key) or {}).get("ok") is True for key in ("check_ii_marker_records", "check_iii_length_terminator", "check_iv_nested_dictionary_detachment")) and non_target.get("ok") is True, "parent_check_i": nond_parent.get("check_i_dict_expansion"), "candidate_check_i": nond_candidate.get("check_i_dict_expansion")}
    mp = mixed_parent.get("counts") or {}
    mc = mixed_candidate.get("counts") or {}
    mixed_gate = {"ok": int(mc.get("scan_errors") or 0) == 0 and int(mc.get("broken_word_hits") or 0) <= int(mp.get("broken_word_hits") or 0) and int(mc.get("split_compound_hits") or 0) <= int(mp.get("split_compound_hits") or 0) and int(mc.get("particle_hits") or 0) <= int(mp.get("particle_hits") or 0), "parent": mp, "candidate": mc}

    unit_changes = []
    for bank in UNIT_BANKS:
        start = sb + bank * BANK_SIZE
        end = start + BANK_SIZE
        unit_changes.extend(offset for offset in range(start, end) if parent[offset] != candidate[offset])
    outside = [f"{offset-sb:06X}" for offset in unit_changes if not any(start <= offset < end for start, end in target_extents)]
    smoke_gate = {"ok": smoke_candidate.get("jagd_ok") is True and smoke_candidate.get("opening_required_ok") is True and smoke_candidate.get("hangul_ok") is True and not outside, "parent_overall": smoke_parent.get("overall_ok"), "candidate_overall": smoke_candidate.get("overall_ok"), "outside_targets": outside}

    checks = {
        "identities": all(identities.values()),
        "target_population": len(target_checks) == EXPECTED_TARGETS and set(build_by_abs) == set(spec_by_abs),
        "targets_exact_and_within_width": not failures,
        "leading_prompt_spaces_preserved": all(row["leading_ideographic_space"] for row in target_checks if row["abs"] in {"75B522", "75B52C"}),
        "new_stock_allocation": len(stock_checks) == len(selected_retired) and all(row["ok"] for row in stock_checks),
        "ext3_allocations": len(ext3_checks) == 9 and all(row["ok"] for row in ext3_checks),
        "non_target_invariance": non_target.get("ok") is True,
        "diffs_bounded": diff_gate["ok"],
        "residual_population": residual_gate["ok"],
        "structure_delta": structure_gate["ok"],
        "false_segmented_pointer_writes": false_gate["ok"],
        "nondialogue_delta": nondialogue_gate["ok"],
        "mixed_artifact_delta": mixed_gate["ok"],
        "smoke_delta": smoke_gate["ok"],
        "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ui_width_correction_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_visual_test" if ok else "failed",
        "inputs": {"main": identity(MAIN, main_rom), "main_save": identity(MAIN_SAVE, main_save), "parent": identity(PARENT, parent), "candidate": identity(CANDIDATE, candidate), "candidate_save": identity(CANDIDATE_SAVE, candidate_save), "spec": identity(SPEC), "build_report": identity(BUILD_REPORT), "candidate_residual": identity(CANDIDATE_RESIDUAL)},
        "checks": checks,
        "identity_checks": identities,
        "counts": {"targets": len(target_checks), "target_failures": len(failures), "ext3_slots": len(ext3_checks), "new_stock_slots": len(stock_checks), "non_target_records": int(non_target.get("records_checked") or 0), "remaining_residuals": len(candidate_rows)},
        "target_failures": failures,
        "target_checks": target_checks,
        "stock_checks": stock_checks,
        "ext3_checks": ext3_checks,
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
