#!/usr/bin/env python3
"""Independent static audit for the broad stage-2B dialogue/voice candidate."""
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
CANDIDATE = ROOT / "out/patch/broad_stage2_dialogue_voice_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/broad_stage2_dialogue_voice_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/broad_stage2_dialogue_voice_report.json"
CATALOG = ROOT / "data/broad_stage2_dialogue_voice_ko.json"
SOURCE_AUDIT = ROOT / "out/patch/broad_stage2_ui_system_postpromotion_residual_audit.json"
POST_AUDIT = ROOT / "out/patch/broad_stage2_dialogue_voice_residual_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
STRUCTURE_PARENT = ROOT / "out/patch/broad_stage2_dialogue_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/broad_stage2_dialogue_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/broad_stage2_dialogue_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/broad_stage2_dialogue_false_segptr_candidate.json"
NONDIAG_PARENT = ROOT / "out/patch/broad_stage2_dialogue_nondialogue_parent.json"
NONDIAG_CANDIDATE = ROOT / "out/patch/broad_stage2_dialogue_nondialogue_candidate.json"
MIXED_PARENT = ROOT / "out/patch/broad_stage2_dialogue_mixed_parent.json"
MIXED_CANDIDATE = ROOT / "out/patch/broad_stage2_dialogue_mixed_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/broad_stage2_dialogue_smoke_parent.json"
SMOKE_CANDIDATE = ROOT / "out/patch/broad_stage2_dialogue_smoke_candidate.json"
OUT = ROOT / "out/patch/broad_stage2_dialogue_voice_candidate_audit.json"

EXPECTED_PARENT_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
EXPECTED_CANDIDATE_SHA = "1a950ebc0090adffbf04dd5e5667482ed3b08be38f98cf387398e043e8a786b9"
EXPECTED_TARGETS = 288
EXPECTED_POST = 471
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
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


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


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


def within(offset: int, extents: Iterable[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in extents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    parent = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    build = load(BUILD_REPORT)
    catalog = load(CATALOG)
    source = load(SOURCE_AUDIT)
    post = load(POST_AUDIT)
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

    identity_checks = {
        "rom_sizes": len(parent) == len(candidate) == ROM_SIZE,
        "save_sizes": len(main_save) == len(candidate_save) == SAVE_SIZE,
        "parent_sha": sha(parent) == EXPECTED_PARENT_SHA,
        "candidate_sha": sha(candidate) == EXPECTED_CANDIDATE_SHA,
        "parent_matches_build": sha(parent) == str((build.get("parent") or {}).get("sha256") or ""),
        "candidate_matches_build": sha(candidate) == str((build.get("candidate") or {}).get("sha256") or ""),
        "candidate_save_matches_live": candidate_save == main_save,
        "build_ok_unpublished": build.get("ok") is True and build.get("published") is False,
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    after_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    source_by_abs = {str(row.get("abs") or "").upper(): row for row in flatten(source)}
    build_by_abs = {str(row.get("abs") or "").upper(): row for row in build.get("records") or []}
    target_checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    target_ids: set[str] = set()
    target_addresses: set[str] = set()
    target_extents: list[tuple[int, int]] = []
    stock_expected: dict[int, set[tuple[str, str]]] = {}
    ext3_expected: dict[int, str] = {}

    for translation in catalog.get("lines") or []:
        address = str(translation.get("abs") or "").upper()
        source_row = source_by_abs.get(address)
        applied = build_by_abs.get(address)
        target_addresses.add(address)
        if source_row is None or applied is None:
            failures.append({"abs": address, "reason": "source_or_build_row_missing"})
            continue
        target_ids.add(str(source_row.get("record_id") or ""))
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source_row.get("prefix_hex") or ""))
        body = bytes.fromhex(str(source_row.get("body_hex") or ""))
        prefix_len = int(source_row.get("prefix_bytes") or 0)
        body_capacity = int(source_row.get("body_capacity") or 0)
        expected = normalize_ko_text(str(translation.get("ko") or "")).rstrip("\u3000 \t")
        before_read = read_encoded_z_safe(parent, sb + logical, max_len=256)
        after_read = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if before_read is None or after_read is None:
            failures.append({"abs": address, "reason": "unreadable"})
            continue
        before_payload, before_term = bytes(before_read[0]), int(before_read[1])
        after_payload, after_term = bytes(after_read[0]), int(after_read[1])
        source_bound = (
            before_payload == prefix + body
            and str(source_row.get("current_text") or "") == str(translation.get("jp_body") or "")
            and str(source_row.get("original_text") or "") == str(translation.get("jp_full") or "")
        )
        structure_ok = (
            len(after_payload) == len(before_payload)
            and after_payload[:prefix_len] == before_payload[:prefix_len]
            and before_term == after_term
            and candidate[after_term] == 0
        )
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
                failures.append({"abs": address, "reason": "ext3_phrase_conflict"})
        elif strategy in {"strong_retired_stock", "existing_exact_stock"}:
            index = int(str(applied.get("stock_index") or "0"), 16)
            token = token_from_dict_index(index)
            token_ok = after_payload[prefix_len:prefix_len + 2] == token
            stock_expected.setdefault(index, set()).add((address, f"{logical + prefix_len:06X}"))
        report_bound = (
            str(applied.get("record_id") or "") == str(source_row.get("record_id") or "")
            and str(applied.get("jp") or "") == str(translation.get("jp_body") or "")
            and str(applied.get("after") or "") == expected
            and int(applied.get("body_capacity") or 0) == body_capacity
        )
        ok = source_bound and structure_ok and report_bound and token_ok and rendered == expected and japanese == 0
        check = {"abs": address, "expected": expected, "actual": rendered, "strategy": strategy, "source_bound": source_bound, "structure_ok": structure_ok, "report_bound": report_bound, "token_ok": token_ok, "japanese": japanese, "ok": ok}
        target_checks.append(check)
        if not ok:
            failures.append(check)
        target_extents.append((sb + logical + prefix_len, sb + logical + prefix_len + body_capacity))

    population_ok = (
        len(catalog.get("lines") or []) == EXPECTED_TARGETS
        and len(target_addresses) == EXPECTED_TARGETS
        and set(build_by_abs) == target_addresses
        and len(target_checks) == EXPECTED_TARGETS
    )

    selected_stock = {int(str(value), 16) for value in ((build.get("allocation") or {}).get("selected_retired_slots") or [])}
    parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_stock)
    parent_nested = nested_occurrence_map(before_dictionary, wanted=selected_stock, ext3_aware=True)
    parent_raw = _raw_pair_hits(parent, sorted(selected_stock))
    candidate_external = external_occurrence_map(candidate, ext3_aware=True, wanted=selected_stock)
    candidate_nested = nested_occurrence_map(after_dictionary, wanted=selected_stock, ext3_aware=True)
    stock_checks: list[dict[str, Any]] = []
    for index in sorted(selected_stock):
        actual_sites = {(str(row.get("record_abs") or ""), str(row.get("token_abs") or "")) for row in candidate_external.get(index, [])}
        expected_sites = stock_expected.get(index, set())
        phrases = {str(row.get("after") or "").rstrip("\u3000 \t") for row in build.get("records") or [] if int(str(row.get("stock_index") or "0"), 16) == index}
        actual_phrase = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        ok = not parent_external.get(index) and not parent_nested.get(index) and not parent_raw.get(index) and not candidate_nested.get(index) and actual_sites == expected_sites and len(phrases) == 1 and actual_phrase in phrases
        stock_checks.append({"index": f"{index:04X}", "expected_sites": sorted([list(x) for x in expected_sites]), "actual_sites": sorted([list(x) for x in actual_sites]), "expected_phrases": sorted(phrases), "actual_phrase": actual_phrase, "ok": ok})

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    ext3_checks: list[dict[str, Any]] = []
    for index, expected in sorted(ext3_expected.items()):
        segment, _local = bank_local_for_index(index)
        actual = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        ok = index in inventory.ext3_free and segment == ALLOC_SEG and actual == expected and not any(is_japanese_character(c) for c in actual)
        ext3_checks.append({"index": f"{index:05X}", "segment": f"{segment:02X}", "was_free": index in inventory.ext3_free, "expected": expected, "actual": actual, "ok": ok})

    non_target = verify_non_target_invariance(parent, candidate, before_dictionary=before_dictionary, after_dictionary=after_dictionary, tbl=tbl, excluded={int(address, 16) for address in target_addresses})

    allocation = build.get("allocation") or {}
    ext3_pointer_extents = []
    for index in ext3_expected:
        segment, local = bank_local_for_index(index)
        ext3_pointer_extents.append((segment * BANK_SIZE + local * 2, segment * BANK_SIZE + local * 2 + 2))
    ext3_phrase_extent = (ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_before") or "0"), 16), ALLOC_SEG * BANK_SIZE + int(str(allocation.get("ext3_cursor_after") or "0"), 16))
    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [(stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2) for index in selected_stock]
    stock_phrase_extent = (stock_bank_file + int(str(allocation.get("stock_cursor_before") or "0"), 16), stock_bank_file + int(str(allocation.get("stock_cursor_after") or "0"), 16))
    allowed = target_extents + ext3_pointer_extents + stock_pointer_extents + [ext3_phrase_extent, stock_phrase_extent, (len(parent) - 2, len(parent))]
    runs = diff_runs(parent, candidate)
    unaccounted = [{"start": f"{left:08X}", "end_exclusive": f"{right:08X}"} for left, right in runs if not covered((left, right), allowed)]
    diff_gate = {"ok": not unaccounted and sum(right-left for left,right in runs) == int((build.get("diff") or {}).get("changed_bytes_from_parent") or 0) and len(runs) == int((build.get("diff") or {}).get("runs") or 0), "changed_bytes": sum(right-left for left,right in runs), "runs": len(runs), "unaccounted": unaccounted}

    source_rows = flatten(source)
    post_rows = flatten(post)
    source_ids = {str(row.get("record_id") or "") for row in source_rows}
    post_ids = {str(row.get("record_id") or "") for row in post_rows}
    expected_post_ids = source_ids - target_ids
    residual_gate = {"ok": post.get("ok") is True and len(source_rows) == 759 and len(post_rows) == EXPECTED_POST and int((post.get("counts") or {}).get("japanese_residual_records") or 0) == EXPECTED_POST and post_ids == expected_post_ids and not (target_ids & post_ids), "source": len(source_rows), "targets": len(target_ids), "post": len(post_rows), "missing": sorted(expected_post_ids-post_ids), "unexpected": sorted(post_ids-expected_post_ids)}

    parent_issues = {issue_signature(row) for row in structure_parent.get("first_issues") or []}
    candidate_issues = {issue_signature(row) for row in structure_candidate.get("first_issues") or []}
    structure_gate = {"ok": int(structure_parent.get("issues") or 0) == int(structure_candidate.get("issues") or 0) and parent_issues == candidate_issues, "parent": int(structure_parent.get("issues") or 0), "candidate": int(structure_candidate.get("issues") or 0), "new": [list(x) for x in sorted(candidate_issues-parent_issues)], "missing": [list(x) for x in sorted(parent_issues-candidate_issues)]}
    false_gate = {"ok": int(false_parent.get("sites_found") or 0) == 0 and int(false_candidate.get("sites_found") or 0) == 0, "parent": int(false_parent.get("sites_found") or 0), "candidate": int(false_candidate.get("sites_found") or 0)}
    nondialogue_gate = {"ok": all((nond_candidate.get(key) or {}).get("ok") is True for key in ("check_ii_marker_records", "check_iii_length_terminator", "check_iv_nested_dictionary_detachment")) and non_target.get("ok") is True, "parent_check_i": nond_parent.get("check_i_dict_expansion"), "candidate_check_i": nond_candidate.get("check_i_dict_expansion"), "candidate_marker": nond_candidate.get("check_ii_marker_records"), "candidate_length": nond_candidate.get("check_iii_length_terminator"), "candidate_nested": nond_candidate.get("check_iv_nested_dictionary_detachment"), "non_target": non_target}
    mixed_parent_counts = mixed_parent.get("counts") or {}
    mixed_candidate_counts = mixed_candidate.get("counts") or {}
    mixed_gate = {"ok": int(mixed_candidate_counts.get("scan_errors") or 0) == 0 and int(mixed_candidate_counts.get("broken_word_hits") or 0) <= int(mixed_parent_counts.get("broken_word_hits") or 0) and int(mixed_candidate_counts.get("split_compound_hits") or 0) <= int(mixed_parent_counts.get("split_compound_hits") or 0) and int(mixed_candidate_counts.get("particle_hits") or 0) <= int(mixed_parent_counts.get("particle_hits") or 0), "parent": mixed_parent_counts, "candidate": mixed_candidate_counts}

    unit_changes: list[int] = []
    for bank in UNIT_BANKS:
        start = sb + bank * BANK_SIZE
        end = start + BANK_SIZE
        unit_changes.extend(offset for offset in range(start, end) if parent[offset] != candidate[offset])
    outside = [f"{offset-sb:06X}" for offset in unit_changes if not within(offset, target_extents)]
    smoke_gate = {"ok": smoke_candidate.get("jagd_ok") is True and smoke_candidate.get("opening_required_ok") is True and smoke_candidate.get("hangul_ok") is True and not outside, "parent_overall": smoke_parent.get("overall_ok"), "candidate_overall": smoke_candidate.get("overall_ok"), "jagd": smoke_candidate.get("jagd_ok"), "opening": smoke_candidate.get("opening_required_ok"), "hangul": smoke_candidate.get("hangul_ok"), "unit_changed_bytes": len(unit_changes), "outside_targets": outside}

    checks = {
        "identities": all(identity_checks.values()),
        "catalog_population": population_ok,
        "targets_exact": not failures,
        "stock_allocations": len(stock_checks) == len(selected_stock) and all(row["ok"] for row in stock_checks),
        "ext3_allocations": len(ext3_checks) == len(ext3_expected) and all(row["ok"] for row in ext3_checks),
        "non_target_invariance": non_target.get("ok") is True,
        "diffs_bounded": diff_gate["ok"],
        "residual_population": residual_gate["ok"],
        "structure_delta": structure_gate["ok"],
        "false_segmented_pointer_writes": false_gate["ok"],
        "nondialogue_delta": nondialogue_gate["ok"],
        "mixed_artifact_delta": mixed_gate["ok"],
        "smoke_delta": smoke_gate["ok"],
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_broad_stage2_dialogue_voice_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_visual_test" if ok else "failed",
        "inputs": {"parent": identity(MAIN, parent), "main_save": identity(MAIN_SAVE, main_save), "candidate": identity(CANDIDATE, candidate), "candidate_save": identity(CANDIDATE_SAVE, candidate_save), "build_report": identity(BUILD_REPORT), "catalog": identity(CATALOG), "source_audit": identity(SOURCE_AUDIT), "post_audit": identity(POST_AUDIT)},
        "checks": checks,
        "identity_checks": identity_checks,
        "counts": {"targets": len(target_checks), "target_failures": len(failures), "stock_slots": len(stock_checks), "ext3_slots": len(ext3_checks), "non_target_records": int(non_target.get("records_checked") or 0), "remaining_residuals": len(post_rows)},
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
        "promotion": "blocked_pending_user_visual_verification",
    }
    write_json(args.out, report)
    print(json.dumps({"ok": ok, "status": report["status"], "checks": checks, "counts": report["counts"], "out": str(args.out.resolve())}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
