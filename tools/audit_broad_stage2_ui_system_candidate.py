#!/usr/bin/env python3
"""Independent static audit for the broad stage-2A UI/system candidate.

The audit rebinds every reviewed catalog row to the stage-1 parent, decodes the
candidate without trusting the builder's per-record verdicts, proves that the
94 target records are the only visible-record changes, verifies the selected
stock/ext3 allocations, compares the post-candidate Japanese-residual
population, and consumes independent structure/pointer/non-dialogue/mixed/smoke
reports. ROM and SaveRAM inputs are read-only; only this JSON report is written.
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
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
)
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import bank_local_for_index, token_from_ext3_index

MAIN_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT_ROM = ROOT / "out/patch/broad_residual_stage1_candidate.wsc"
PARENT_SAVE = ROOT / "sram/broad_residual_stage1_candidate.sav"
CANDIDATE_ROM = ROOT / "out/patch/broad_stage2_ui_system_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/broad_stage2_ui_system_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/broad_stage2_ui_system_report.json"
CATALOG = ROOT / "data/broad_stage2_ui_system_ko.json"
CLASSIFICATION = ROOT / "out/patch/broad_japanese_residual_classification.json"
POST_AUDIT = ROOT / "out/patch/broad_japanese_residual_after_stage2_ui_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
STRUCTURE_PARENT = ROOT / "out/patch/broad_stage2_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/broad_stage2_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/broad_stage2_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/broad_stage2_false_segptr_candidate.json"
NONDIAG_PARENT = ROOT / "out/patch/broad_stage2_nondialogue_parent.json"
NONDIAG_CANDIDATE = ROOT / "out/patch/broad_stage2_nondialogue_candidate.json"
MIXED_PARENT = ROOT / "out/patch/broad_stage2_mixed_parent.json"
MIXED_CANDIDATE = ROOT / "out/patch/broad_stage2_mixed_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/broad_stage2_smoke_parent.json"
SMOKE_CANDIDATE = ROOT / "out/patch/broad_stage2_smoke_candidate.json"
OUT = ROOT / "out/patch/broad_stage2_ui_system_candidate_audit.json"

EXPECTED_TARGETS = 94
EXPECTED_AFTER_RESIDUALS = 759
ALLOC_SEGMENT = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
UNIT_BANKS = tuple(range(0x50, 0x5E)) + tuple(range(0x6A, 0x70))


class AuditError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity(path: Path, value: bytes | None = None) -> dict[str, Any]:
    payload = value if value is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256_bytes(payload)}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root is not an object: {path}")
    return value


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def flatten_records(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = document.get("records") or {}
    if isinstance(records, list):
        return [dict(row) for row in records]
    result: list[dict[str, Any]] = []
    if isinstance(records, Mapping):
        for bucket in records.values():
            result.extend(dict(row) for row in (bucket or []))
    return result


def issue_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("abs"),
        row.get("kind"),
        row.get("orig_terminator"),
        row.get("target_terminator"),
        row.get("delta"),
    )


def within(offset: int, extents: Iterable[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in extents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    main_rom = MAIN_ROM.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    candidate = CANDIDATE_ROM.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()

    build = load_json(BUILD_REPORT)
    catalog = load_json(CATALOG)
    classification = load_json(CLASSIFICATION)
    post = load_json(POST_AUDIT)
    structure_parent = load_json(STRUCTURE_PARENT)
    structure_candidate = load_json(STRUCTURE_CANDIDATE)
    false_parent = load_json(FALSE_PARENT)
    false_candidate = load_json(FALSE_CANDIDATE)
    nond_parent = load_json(NONDIAG_PARENT)
    nond_candidate = load_json(NONDIAG_CANDIDATE)
    mixed_parent = load_json(MIXED_PARENT)
    mixed_candidate = load_json(MIXED_CANDIDATE)
    smoke_parent = load_json(SMOKE_PARENT)
    smoke_candidate = load_json(SMOKE_CANDIDATE)

    identity_checks = {
        "rom_sizes": len(main_rom) == len(parent) == len(candidate) == ROM_SIZE,
        "save_sizes": len(main_save) == len(parent_save) == len(candidate_save) == SAVE_SIZE,
        "main_matches_build": sha256_bytes(main_rom) == str((build.get("main_tip") or {}).get("sha256") or ""),
        "parent_matches_build": sha256_bytes(parent) == str((build.get("parent") or {}).get("sha256") or ""),
        "candidate_matches_build": sha256_bytes(candidate) == str((build.get("candidate") or {}).get("sha256") or ""),
        "candidate_save_matches_live": candidate_save == main_save,
        "build_report_ok": build.get("ok") is True,
        "build_unpublished": build.get("published") is False,
        "main_is_not_candidate": main_rom != candidate,
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    class_by_abs = {
        str(row.get("abs") or "").upper(): dict(row)
        for row in classification.get("records") or []
    }
    report_by_abs = {
        str(row.get("abs") or "").upper(): dict(row)
        for row in build.get("records") or []
    }

    target_checks: list[dict[str, Any]] = []
    target_failures: list[dict[str, Any]] = []
    target_ids: set[str] = set()
    target_addresses: set[str] = set()
    target_extents: list[tuple[int, int]] = []
    expected_stock_occurrences: dict[int, set[tuple[str, str]]] = {}
    ext3_phrase_by_index: dict[int, str] = {}

    for translation in catalog.get("lines") or []:
        address = str(translation.get("abs") or "").upper()
        target_addresses.add(address)
        source = class_by_abs.get(address)
        applied = report_by_abs.get(address)
        if source is None or applied is None:
            target_failures.append({"abs": address, "reason": "classification_or_report_row_missing"})
            continue
        record_id = str(source.get("record_id") or "")
        target_ids.add(record_id)
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        body = bytes.fromhex(str(source.get("body_hex") or ""))
        body_capacity = int(source.get("body_capacity") or 0)
        prefix_len = int(source.get("prefix_bytes") or len(prefix))
        expected = normalize_ko_text(str(translation.get("ko") or "")).rstrip("\u3000 \t")

        parent_read = read_encoded_z_safe(parent, sb + logical, max_len=256)
        candidate_read = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if parent_read is None or candidate_read is None:
            target_failures.append({"abs": address, "reason": "record_unreadable"})
            continue
        parent_payload, parent_term = bytes(parent_read[0]), int(parent_read[1])
        candidate_payload, candidate_term = bytes(candidate_read[0]), int(candidate_read[1])
        source_bound = parent_payload == prefix + body and len(body) == body_capacity
        structure_preserved = (
            len(candidate_payload) == len(parent_payload)
            and candidate_payload[:prefix_len] == parent_payload[:prefix_len]
            and candidate_term == parent_term
            and candidate[parent_term] == 0
        )
        try:
            rendered = candidate_dictionary.expand(candidate_payload[prefix_len:], tbl).rstrip("\u3000 \t")
        except Exception as exc:
            rendered = f"<decode_failed:{type(exc).__name__}>"
        japanese = sum(is_japanese_character(character) for character in rendered)
        report_bound = (
            str(applied.get("record_id") or "") == record_id
            and str(applied.get("jp") or "") == str(translation.get("jp") or "")
            and str(applied.get("after") or "") == expected
            and int(applied.get("body_capacity") or 0) == body_capacity
        )
        strategy = str(applied.get("strategy") or "")
        token_ok = False
        if strategy == "private_ext3":
            index = int(str(applied.get("ext3_index") or "0"), 16)
            token = token_from_ext3_index(index, num_banks=num_banks)
            token_ok = candidate_payload[prefix_len : prefix_len + len(token)] == token
            previous = ext3_phrase_by_index.setdefault(index, expected)
            if previous != expected:
                target_failures.append({"abs": address, "reason": "ext3_index_phrase_conflict"})
        elif strategy in {"strong_retired_stock", "existing_exact_stock"}:
            index = int(str(applied.get("stock_index") or "0"), 16)
            token = token_from_dict_index(index)
            token_ok = candidate_payload[prefix_len : prefix_len + 2] == token
            expected_stock_occurrences.setdefault(index, set()).add((address, f"{logical + prefix_len:06X}"))
        else:
            token_ok = False

        ok = (
            str(source.get("original_text") or "") == str(translation.get("jp") or "")
            and source_bound
            and structure_preserved
            and report_bound
            and rendered == expected
            and japanese == 0
            and token_ok
        )
        check = {
            "abs": address,
            "record_id": record_id,
            "expected": expected,
            "actual": rendered,
            "japanese_characters": japanese,
            "strategy": strategy,
            "source_bound": source_bound,
            "structure_preserved": structure_preserved,
            "report_bound": report_bound,
            "token_ok": token_ok,
            "ok": ok,
        }
        target_checks.append(check)
        if not ok:
            target_failures.append(check)
        target_extents.append((sb + logical + prefix_len, sb + logical + prefix_len + body_capacity))

    catalog_population_ok = (
        len(catalog.get("lines") or []) == EXPECTED_TARGETS
        and len(target_addresses) == EXPECTED_TARGETS
        and set(report_by_abs) == target_addresses
        and len(target_checks) == EXPECTED_TARGETS
    )

    selected_stock = {
        int(str(value), 16)
        for value in ((build.get("allocation") or {}).get("selected_retired_slots") or [])
    }
    parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_stock)
    parent_nested = nested_occurrence_map(parent_dictionary, wanted=selected_stock, ext3_aware=True)
    parent_raw = _raw_pair_hits(parent, sorted(selected_stock))
    candidate_external = external_occurrence_map(candidate, ext3_aware=True, wanted=selected_stock)
    candidate_nested = nested_occurrence_map(candidate_dictionary, wanted=selected_stock, ext3_aware=True)

    stock_allocation_checks: list[dict[str, Any]] = []
    for index in sorted(selected_stock):
        actual_sites = {
            (str(row.get("record_abs") or ""), str(row.get("token_abs") or ""))
            for row in candidate_external.get(index, [])
        }
        expected_sites = expected_stock_occurrences.get(index, set())
        expected_phrase_values = {
            str(row.get("after") or "").rstrip("\u3000 \t")
            for row in build.get("records") or []
            if int(str(row.get("stock_index") or "0"), 16) == index
        }
        actual_phrase = candidate_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        ok = (
            not parent_external.get(index)
            and not parent_nested.get(index)
            and not parent_raw.get(index)
            and not candidate_nested.get(index)
            and actual_sites == expected_sites
            and len(expected_phrase_values) == 1
            and actual_phrase in expected_phrase_values
        )
        stock_allocation_checks.append(
            {
                "index": f"{index:04X}",
                "expected_sites": sorted([list(site) for site in expected_sites]),
                "actual_sites": sorted([list(site) for site in actual_sites]),
                "expected_phrases": sorted(expected_phrase_values),
                "actual_phrase": actual_phrase,
                "ok": ok,
            }
        )

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    ext3_allocation_checks: list[dict[str, Any]] = []
    for index, expected_phrase in sorted(ext3_phrase_by_index.items()):
        segment, _local = bank_local_for_index(index)
        actual_phrase = candidate_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        ok = (
            index in inventory.ext3_free
            and segment == ALLOC_SEGMENT
            and actual_phrase == expected_phrase
            and not any(is_japanese_character(character) for character in actual_phrase)
        )
        ext3_allocation_checks.append(
            {
                "index": f"{index:05X}",
                "segment": f"{segment:02X}",
                "was_free_in_parent": index in inventory.ext3_free,
                "expected": expected_phrase,
                "actual": actual_phrase,
                "ok": ok,
            }
        )

    non_target = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(address, 16) for address in target_addresses},
    )

    allocation = build.get("allocation") or {}
    ext3_pointer_extents: list[tuple[int, int]] = []
    for index in ext3_phrase_by_index:
        segment, local = bank_local_for_index(index)
        ext3_pointer_extents.append((segment * BANK_SIZE + local * 2, segment * BANK_SIZE + local * 2 + 2))
    ext3_phrase_extent = (
        ALLOC_SEGMENT * BANK_SIZE + int(str(allocation.get("ext3_cursor_before") or "0"), 16),
        ALLOC_SEGMENT * BANK_SIZE + int(str(allocation.get("ext3_cursor_after") or "0"), 16),
    )
    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2)
        for index in selected_stock
    ]
    stock_phrase_extent = (
        stock_bank_file + int(str(allocation.get("stock_cursor_before") or "0"), 16),
        stock_bank_file + int(str(allocation.get("stock_cursor_after") or "0"), 16),
    )
    allowed = target_extents + ext3_pointer_extents + stock_pointer_extents + [
        ext3_phrase_extent,
        stock_phrase_extent,
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    diff_gate = {
        "ok": (
            not unaccounted
            and sum(right - left for left, right in runs) == int((build.get("diff") or {}).get("changed_bytes_from_parent") or 0)
            and len(runs) == int((build.get("diff") or {}).get("runs") or 0)
        ),
        "changed_bytes": sum(right - left for left, right in runs),
        "runs": len(runs),
        "unaccounted": unaccounted,
    }

    post_rows = flatten_records(post)
    post_ids = {str(row.get("record_id") or "") for row in post_rows}
    remaining_stage1_ids = {
        str(row.get("record_id") or "")
        for row in classification.get("records") or []
        if not str(row.get("classification") or "").startswith("resolved_stage1_")
    }
    expected_post_ids = remaining_stage1_ids - target_ids
    population_gate = {
        "ok": (
            post.get("ok") is True
            and len(post_rows) == EXPECTED_AFTER_RESIDUALS
            and int((post.get("counts") or {}).get("japanese_residual_records") or 0) == EXPECTED_AFTER_RESIDUALS
            and post_ids == expected_post_ids
            and not (target_ids & post_ids)
        ),
        "stage1_remaining": len(remaining_stage1_ids),
        "targets_removed": len(target_ids),
        "post_residuals": len(post_rows),
        "missing_expected": sorted(expected_post_ids - post_ids),
        "unexpected_remaining": sorted(post_ids - expected_post_ids),
    }

    parent_structure_set = {issue_signature(row) for row in structure_parent.get("first_issues") or []}
    candidate_structure_set = {issue_signature(row) for row in structure_candidate.get("first_issues") or []}
    structure_gate = {
        "ok": (
            int(structure_parent.get("issues") or 0) == int(structure_candidate.get("issues") or 0)
            and parent_structure_set == candidate_structure_set
        ),
        "parent_issues": int(structure_parent.get("issues") or 0),
        "candidate_issues": int(structure_candidate.get("issues") or 0),
        "new_issues": [list(item) for item in sorted(candidate_structure_set - parent_structure_set)],
        "missing_historical": [list(item) for item in sorted(parent_structure_set - candidate_structure_set)],
    }
    false_segptr_gate = {
        "ok": int(false_parent.get("sites_found") or 0) == 0 and int(false_candidate.get("sites_found") or 0) == 0,
        "parent_sites": int(false_parent.get("sites_found") or 0),
        "candidate_sites": int(false_candidate.get("sites_found") or 0),
    }
    nondialogue_gate = {
        "ok": all(
            (nond_candidate.get(key) or {}).get("ok") is True
            for key in (
                "check_ii_marker_records",
                "check_iii_length_terminator",
                "check_iv_nested_dictionary_detachment",
            )
        ) and non_target.get("ok") is True,
        "parent_check_i": nond_parent.get("check_i_dict_expansion"),
        "candidate_check_i": nond_candidate.get("check_i_dict_expansion"),
        "candidate_marker": nond_candidate.get("check_ii_marker_records"),
        "candidate_length": nond_candidate.get("check_iii_length_terminator"),
        "candidate_nested": nond_candidate.get("check_iv_nested_dictionary_detachment"),
        "independent_non_target_invariance": non_target,
    }
    mixed_parent_counts = mixed_parent.get("counts") or {}
    mixed_candidate_counts = mixed_candidate.get("counts") or {}
    mixed_gate = {
        "ok": (
            int(mixed_candidate_counts.get("scan_errors") or 0) == 0
            and int(mixed_candidate_counts.get("broken_word_hits") or 0) <= int(mixed_parent_counts.get("broken_word_hits") or 0)
            and int(mixed_candidate_counts.get("split_compound_hits") or 0) <= int(mixed_parent_counts.get("split_compound_hits") or 0)
            and int(mixed_candidate_counts.get("particle_hits") or 0) <= int(mixed_parent_counts.get("particle_hits") or 0)
        ),
        "parent": mixed_parent_counts,
        "candidate": mixed_candidate_counts,
    }

    unit_changed_offsets: list[int] = []
    for bank in UNIT_BANKS:
        start = sb + bank * BANK_SIZE
        end = start + BANK_SIZE
        unit_changed_offsets.extend(offset for offset in range(start, end) if parent[offset] != candidate[offset])
    unit_outside_targets = [
        f"{offset - sb:06X}" for offset in unit_changed_offsets if not within(offset, target_extents)
    ]
    smoke_gate = {
        "ok": (
            smoke_candidate.get("jagd_ok") is True
            and smoke_candidate.get("opening_required_ok") is True
            and smoke_candidate.get("hangul_ok") is True
            and not unit_outside_targets
        ),
        "parent_overall": smoke_parent.get("overall_ok"),
        "candidate_overall": smoke_candidate.get("overall_ok"),
        "jagd_ok": smoke_candidate.get("jagd_ok"),
        "opening_required_ok": smoke_candidate.get("opening_required_ok"),
        "hangul_ok": smoke_candidate.get("hangul_ok"),
        "unit_changed_bytes": len(unit_changed_offsets),
        "unit_changed_bytes_outside_targets": unit_outside_targets,
    }

    checks = {
        "identities": all(identity_checks.values()),
        "catalog_population": catalog_population_ok,
        "targets_exact": not target_failures,
        "stock_allocations": len(stock_allocation_checks) == len(selected_stock) and all(row["ok"] for row in stock_allocation_checks),
        "ext3_allocations": len(ext3_allocation_checks) == len(ext3_phrase_by_index) and all(row["ok"] for row in ext3_allocation_checks),
        "non_target_invariance": non_target.get("ok") is True,
        "diffs_bounded": diff_gate["ok"],
        "post_population": population_gate["ok"],
        "structure_delta": structure_gate["ok"],
        "false_segmented_pointer_writes": false_segptr_gate["ok"],
        "nondialogue_delta": nondialogue_gate["ok"],
        "mixed_artifact_delta": mixed_gate["ok"],
        "smoke_delta": smoke_gate["ok"],
    }
    ok = all(checks.values())

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_broad_stage2_ui_system_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_visual_test" if ok else "failed",
        "inputs": {
            "main_rom": identity(MAIN_ROM, main_rom),
            "main_save": identity(MAIN_SAVE, main_save),
            "parent_rom": identity(PARENT_ROM, parent),
            "parent_save": identity(PARENT_SAVE, parent_save),
            "candidate_rom": identity(CANDIDATE_ROM, candidate),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "build_report": identity(BUILD_REPORT),
            "catalog": identity(CATALOG),
            "classification": identity(CLASSIFICATION),
            "post_audit": identity(POST_AUDIT),
        },
        "checks": checks,
        "identity_checks": identity_checks,
        "counts": {
            "targets": len(target_checks),
            "target_failures": len(target_failures),
            "stock_slots": len(stock_allocation_checks),
            "ext3_slots": len(ext3_allocation_checks),
            "non_target_records": int(non_target.get("records_checked") or 0),
            "post_residual_records": len(post_rows),
        },
        "target_failures": target_failures,
        "target_checks": target_checks,
        "stock_allocation_checks": stock_allocation_checks,
        "ext3_allocation_checks": ext3_allocation_checks,
        "diff_gate": diff_gate,
        "population_gate": population_gate,
        "structure_gate": structure_gate,
        "false_segptr_gate": false_segptr_gate,
        "nondialogue_gate": nondialogue_gate,
        "mixed_gate": mixed_gate,
        "smoke_gate": smoke_gate,
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(args.out, report)
    print(
        json.dumps(
            {
                "ok": ok,
                "status": report["status"],
                "checks": checks,
                "counts": report["counts"],
                "out": str(args.out.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
