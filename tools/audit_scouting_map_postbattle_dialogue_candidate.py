#!/usr/bin/env python3
"""Independent audit for the corrected scouting-map post-battle candidate."""
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
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
)
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/battle_ui_action_labels_candidate.wsc"
CANDIDATE = ROOT / "out/patch/scouting_map_postbattle_dialogue_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/scouting_map_postbattle_dialogue_candidate.sav"
SPEC = ROOT / "data/scouting_map_postbattle_dialogue_ko.json"
BUILD_REPORT = ROOT / "out/patch/scouting_map_postbattle_dialogue_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
RESIDUAL_PARENT = ROOT / "out/patch/scouting_map_postbattle_dialogue_residual_parent.json"
RESIDUAL_CANDIDATE = ROOT / "out/patch/scouting_map_postbattle_dialogue_residual_audit.json"
STRUCTURE_PARENT = ROOT / "out/patch/scouting_map_postbattle_dialogue_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/scouting_map_postbattle_dialogue_structure_candidate.json"
FALSE_PARENT = ROOT / "out/patch/scouting_map_postbattle_dialogue_false_segptr_parent.json"
FALSE_CANDIDATE = ROOT / "out/patch/scouting_map_postbattle_dialogue_false_segptr_candidate.json"
NONDIAG_PARENT = ROOT / "out/patch/scouting_map_postbattle_dialogue_nondialogue_parent.json"
NONDIAG_CANDIDATE = ROOT / "out/patch/scouting_map_postbattle_dialogue_nondialogue_candidate.json"
MIXED_PARENT = ROOT / "out/patch/scouting_map_postbattle_dialogue_mixed_parent.json"
MIXED_CANDIDATE = ROOT / "out/patch/scouting_map_postbattle_dialogue_mixed_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/scouting_map_postbattle_dialogue_smoke_parent.json"
SMOKE_CANDIDATE = ROOT / "out/patch/scouting_map_postbattle_dialogue_smoke_candidate.json"
OUT = ROOT / "out/patch/scouting_map_postbattle_dialogue_candidate_audit.json"

EXPECTED_MAIN_SHA = "29f790238e02eb228db97619b9c55bc900495bfef2f76eb819d1abcfb5d430f0"
EXPECTED_PARENT_SHA = "6e63e4830e0391f00e0ccdf7d07c6b3b3309e5e3fb797cd934d20900b050e33f"
EXPECTED_CANDIDATE_SHA = "1232e86a4e4a0ed4e7f1fa5ba40bf7b3f0df38bd458b466072d22e13aed951f0"
EXPECTED_TARGETS = 4
EXPECTED_RESIDUALS = 195
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXT3_MARKER = b"\xE5\x18"


class AuditError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": digest(payload)}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"invalid JSON root: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
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
        str(row.get("abs") or ""),
        str(row.get("kind") or ""),
        str(row.get("orig_terminator") or ""),
        str(row.get("target_terminator") or ""),
        int(row.get("delta") or 0),
    )


def within(offset: int, extents: Iterable[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in extents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    main_rom = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    spec = load(SPEC)
    build = load(BUILD_REPORT)
    residual_parent = load(RESIDUAL_PARENT)
    residual_candidate = load(RESIDUAL_CANDIDATE)
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
        "rom_sizes": len(main_rom) == len(parent) == len(candidate) == ROM_SIZE,
        "save_sizes": len(main_save) == len(candidate_save) == SAVE_SIZE,
        "main_sha": digest(main_rom) == EXPECTED_MAIN_SHA,
        "parent_sha": digest(parent) == EXPECTED_PARENT_SHA,
        "candidate_sha": digest(candidate) == EXPECTED_CANDIDATE_SHA,
        "build_parent_binding": str((build.get("parent_battle_ui_candidate") or {}).get("sha256") or "") == EXPECTED_PARENT_SHA,
        "build_candidate_binding": str((build.get("candidate") or {}).get("sha256") or "") == EXPECTED_CANDIDATE_SHA,
        "build_ok_unpublished": build.get("ok") is True and build.get("published") is False,
        "spec_parent_binding": str((spec.get("parent_rom") or {}).get("sha256") or "").lower() == EXPECTED_PARENT_SHA,
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    candidate_legacy_dictionary = Dictionary(candidate)
    original_dictionary = Dictionary(original)
    base = stock_base(parent)

    build_by_abs = {
        str(row.get("abs") or "").upper(): dict(row)
        for row in build.get("records") or []
    }
    selected_slots = {
        int(str(value), 16)
        for value in ((build.get("allocation") or {}).get("selected_retired_slots") or [])
    }
    target_checks: list[dict[str, Any]] = []
    target_failures: list[dict[str, Any]] = []
    target_extents: list[tuple[int, int]] = []
    expected_token_offsets: dict[int, int] = {}
    target_addresses: set[int] = set()

    for item in spec.get("records") or []:
        address = str(item.get("abs") or "").upper()
        logical = int(address, 16)
        target_addresses.add(logical)
        applied = build_by_abs.get(address)
        prefix = bytes.fromhex(str(item.get("prefix_hex") or ""))
        parent_expected = bytes.fromhex(str(item.get("parent_payload_hex") or ""))
        original_expected = bytes.fromhex(str(item.get("original_payload_hex") or ""))
        capacity = int(item.get("target_body_capacity") or 0)
        parent_term = int(str(item.get("parent_terminator") or "0"), 16)
        target_term = int(str(item.get("target_terminator") or "0"), 16)
        expected_text = normalize_ko_text(str(item.get("ko") or "")).rstrip("\u3000 \t")

        if applied is None:
            target_failures.append({"abs": address, "reason": "build_record_missing"})
            continue
        slot = int(str(applied.get("stock_index") or "0"), 16)
        token = token_from_dict_index(slot)
        expected_payload = prefix + token + b"\x01" * (capacity - 2)

        parent_read = read_encoded_z_safe(parent, base + logical, max_len=96)
        candidate_read = read_encoded_z_safe(candidate, base + logical, max_len=96)
        original_read = read_encoded_z_safe(original, stock_base(original) + logical, max_len=96)
        if parent_read is None or candidate_read is None or original_read is None:
            target_failures.append({"abs": address, "reason": "record_unreadable"})
            continue
        parent_payload, parent_term_file = bytes(parent_read[0]), int(parent_read[1])
        candidate_payload, candidate_term_file = bytes(candidate_read[0]), int(candidate_read[1])
        original_payload, original_term_file = bytes(original_read[0]), int(original_read[1])
        candidate_body = candidate_payload[len(prefix) :]

        source_bound = (
            parent_payload == parent_expected
            and original_payload == original_expected
            and parent_term_file - base == parent_term
            and original_term_file - stock_base(original) == target_term
            and parent_payload.startswith(prefix)
            and original_payload.startswith(prefix)
            and parent_payload[len(prefix) :].startswith(EXT3_MARKER)
        )
        boundary_ok = (
            candidate_payload == expected_payload
            and candidate_term_file - base == target_term
            and candidate[candidate_term_file] == 0
            and (
                parent_term == target_term
                or candidate[base + parent_term] == 0
            )
        )
        ext3_render = candidate_dictionary.expand(candidate_body, tbl).rstrip("\u3000 \t")
        legacy_render = candidate_legacy_dictionary.expand(candidate_body, tbl).rstrip("\u3000 \t")
        original_render = original_dictionary.expand(original_payload[len(prefix) :], tbl)
        report_bound = (
            str(applied.get("record_id") or "") == str(item.get("record_id") or "")
            and str(applied.get("after") or "").rstrip("\u3000 \t") == expected_text
            and int(applied.get("target_body_capacity") or 0) == capacity
            and str(applied.get("target_terminator") or "").upper() == f"{target_term:06X}"
            and str(applied.get("strategy") or "") == "dedicated_strong_retired_legacy_stock"
        )
        runtime_compatible = (
            EXT3_MARKER not in candidate_body
            and ext3_render == expected_text
            and legacy_render == expected_text
            and not any(is_japanese_character(character) for character in legacy_render)
        )
        slot_bound = slot in selected_slots
        ok = source_bound and boundary_ok and report_bound and runtime_compatible and slot_bound
        check = {
            "abs": address,
            "record_id": item.get("record_id"),
            "original_render": original_render,
            "expected": expected_text,
            "ext3_decoder_actual": ext3_render,
            "legacy_decoder_actual": legacy_render,
            "stock_index": f"{slot:04X}",
            "source_bound": source_bound,
            "boundary_ok": boundary_ok,
            "report_bound": report_bound,
            "runtime_compatible": runtime_compatible,
            "slot_bound": slot_bound,
            "ok": ok,
        }
        target_checks.append(check)
        if not ok:
            target_failures.append(check)
        expected_token_offsets[slot] = logical + len(prefix)
        target_extents.append(
            (
                base + logical + len(prefix),
                base + max(parent_term, target_term) + 1,
            )
        )

    population_ok = (
        len(target_checks) == EXPECTED_TARGETS
        and len(target_addresses) == EXPECTED_TARGETS
        and len(build_by_abs) == EXPECTED_TARGETS
        and len(selected_slots) == EXPECTED_TARGETS
    )

    parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_slots)
    parent_nested = nested_occurrence_map(parent_dictionary, wanted=selected_slots, ext3_aware=True)
    parent_raw = _raw_pair_hits(parent, sorted(selected_slots))
    candidate_external = external_occurrence_map(candidate, ext3_aware=True, wanted=selected_slots)
    candidate_nested = nested_occurrence_map(candidate_dictionary, wanted=selected_slots, ext3_aware=True)
    stock_checks: list[dict[str, Any]] = []
    for slot in sorted(selected_slots):
        occurrences = candidate_external.get(slot, [])
        token_offsets = {
            int(str(row.get("token_abs") or "0"), 16)
            for row in occurrences
        }
        phrase = candidate_legacy_dictionary.expand_index(slot, tbl).rstrip("\u3000 \t")
        expected_phrase = next(
            str(row.get("after") or "").rstrip("\u3000 \t")
            for row in build_by_abs.values()
            if int(str(row.get("stock_index") or "0"), 16) == slot
        )
        ok = (
            not parent_external.get(slot)
            and not parent_nested.get(slot)
            and not parent_raw.get(slot)
            and len(occurrences) == 1
            and token_offsets == {expected_token_offsets[slot]}
            and not candidate_nested.get(slot)
            and phrase == expected_phrase
        )
        stock_checks.append(
            {
                "index": f"{slot:04X}",
                "expected_token_offset": f"{expected_token_offsets[slot]:06X}",
                "actual_token_offsets": [f"{value:06X}" for value in sorted(token_offsets)],
                "occurrence_count": len(occurrences),
                "expected_phrase": expected_phrase,
                "actual_phrase": phrase,
                "ok": ok,
            }
        )

    non_target = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_addresses,
    )

    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    allocation = build.get("allocation") or {}
    allowed = list(target_extents)
    for slot in selected_slots:
        allowed.append(
            (
                stock_bank_file + DICT_PTR_START + slot * 2,
                stock_bank_file + DICT_PTR_START + slot * 2 + 2,
            )
        )
    allowed.extend(
        [
            (
                stock_bank_file + int(str(allocation.get("stock_cursor_before") or "0"), 16),
                stock_bank_file + int(str(allocation.get("stock_cursor_after") or "0"), 16),
            ),
            (len(parent) - 2, len(parent)),
        ]
    )
    runs = diff_runs(parent, candidate)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    diff_gate = {
        "ok": (
            not unaccounted
            and sum(right - left for left, right in runs)
            == int((build.get("diff") or {}).get("changed_bytes_from_parent") or 0)
            and len(runs) == int((build.get("diff") or {}).get("runs") or 0)
        ),
        "changed_bytes": sum(right - left for left, right in runs),
        "runs": len(runs),
        "unaccounted": unaccounted,
    }

    parent_issues = {
        issue_signature(row)
        for row in structure_parent.get("first_issues") or []
    }
    candidate_issues = {
        issue_signature(row)
        for row in structure_candidate.get("first_issues") or []
    }
    removed_issues = parent_issues - candidate_issues
    added_issues = candidate_issues - parent_issues
    structure_gate = {
        "ok": (
            int(structure_parent.get("issues") or 0) == 27
            and int(structure_candidate.get("issues") or 0) == 27
            and not removed_issues
            and not added_issues
        ),
        "parent_issues": int(structure_parent.get("issues") or 0),
        "candidate_issues": int(structure_candidate.get("issues") or 0),
        "removed": [list(row) for row in sorted(removed_issues)],
        "added": [list(row) for row in sorted(added_issues)],
    }

    false_gate = {
        "ok": int(false_parent.get("sites_found") or 0) == 0 and int(false_candidate.get("sites_found") or 0) == 0,
        "parent": int(false_parent.get("sites_found") or 0),
        "candidate": int(false_candidate.get("sites_found") or 0),
    }

    parent_residual_rows = flatten_records(residual_parent)
    candidate_residual_rows = flatten_records(residual_candidate)
    parent_residual_ids = {str(row.get("record_id") or "") for row in parent_residual_rows}
    candidate_residual_ids = {str(row.get("record_id") or "") for row in candidate_residual_rows}
    residual_gate = {
        "ok": (
            residual_parent.get("ok") is True
            and residual_candidate.get("ok") is True
            and len(parent_residual_rows) == EXPECTED_RESIDUALS
            and len(candidate_residual_rows) == EXPECTED_RESIDUALS
            and parent_residual_ids == candidate_residual_ids
        ),
        "parent": len(parent_residual_rows),
        "candidate": len(candidate_residual_rows),
        "missing": sorted(parent_residual_ids - candidate_residual_ids),
        "new": sorted(candidate_residual_ids - parent_residual_ids),
    }

    nondialogue_gate = {
        "ok": all(
            (nond_candidate.get(key) or {}).get("ok") is True
            for key in (
                "check_ii_marker_records",
                "check_iii_length_terminator",
                "check_iv_nested_dictionary_detachment",
            )
        )
        and all(
            (nond_parent.get(key) or {}).get("ok")
            == (nond_candidate.get(key) or {}).get("ok")
            for key in (
                "check_i_dict_expansion",
                "check_ii_marker_records",
                "check_iii_length_terminator",
                "check_iv_nested_dictionary_detachment",
            )
        ),
        "parent": {
            key: (nond_parent.get(key) or {}).get("ok")
            for key in (
                "check_i_dict_expansion",
                "check_ii_marker_records",
                "check_iii_length_terminator",
                "check_iv_nested_dictionary_detachment",
            )
        },
        "candidate": {
            key: (nond_candidate.get(key) or {}).get("ok")
            for key in (
                "check_i_dict_expansion",
                "check_ii_marker_records",
                "check_iii_length_terminator",
                "check_iv_nested_dictionary_detachment",
            )
        },
    }

    parent_mixed = mixed_parent.get("counts") or {}
    candidate_mixed = mixed_candidate.get("counts") or {}
    mixed_gate = {
        "ok": (
            int(candidate_mixed.get("scan_errors") or 0) == 0
            and int(candidate_mixed.get("broken_word_hits") or 0) <= int(parent_mixed.get("broken_word_hits") or 0)
            and int(candidate_mixed.get("split_compound_hits") or 0) <= int(parent_mixed.get("split_compound_hits") or 0)
            and int(candidate_mixed.get("particle_hits") or 0) <= int(parent_mixed.get("particle_hits") or 0)
        ),
        "parent": parent_mixed,
        "candidate": candidate_mixed,
    }

    smoke_gate = {
        "ok": (
            smoke_candidate.get("jagd_ok") is True
            and smoke_candidate.get("opening_required_ok") is True
            and smoke_candidate.get("hangul_ok") is True
            and smoke_parent.get("jagd_ok") == smoke_candidate.get("jagd_ok")
            and smoke_parent.get("opening_required_ok") == smoke_candidate.get("opening_required_ok")
            and smoke_parent.get("hangul_ok") == smoke_candidate.get("hangul_ok")
        ),
        "parent_overall": smoke_parent.get("overall_ok"),
        "candidate_overall": smoke_candidate.get("overall_ok"),
        "jagd": smoke_candidate.get("jagd_ok"),
        "opening": smoke_candidate.get("opening_required_ok"),
        "hangul": smoke_candidate.get("hangul_ok"),
    }

    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate[runtime_start:runtime_end]

    checks = {
        "identities": all(identity_checks.values()),
        "target_population": population_ok,
        "targets_exact_with_legacy_decoder": not target_failures,
        "dedicated_stock_allocations": len(stock_checks) == EXPECTED_TARGETS and all(row["ok"] for row in stock_checks),
        "non_target_invariance": non_target.get("ok") is True,
        "diffs_bounded": diff_gate["ok"],
        "structure_issue_set_unchanged": structure_gate["ok"],
        "false_segmented_pointer_writes": false_gate["ok"],
        "residual_population_unchanged": residual_gate["ok"],
        "nondialogue_delta": nondialogue_gate["ok"],
        "mixed_artifact_delta": mixed_gate["ok"],
        "smoke_delta": smoke_gate["ok"],
        "runtime_hook_unchanged": runtime_unchanged,
        "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_scouting_map_postbattle_dialogue_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "corrected_v2_candidate_static_verified_pending_visual_test" if ok else "failed",
        "inputs": {
            "main": identity(MAIN, main_rom),
            "main_save": identity(MAIN_SAVE, main_save),
            "parent": identity(PARENT, parent),
            "candidate": identity(CANDIDATE, candidate),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "spec": identity(SPEC),
            "build_report": identity(BUILD_REPORT),
            "residual_parent": identity(RESIDUAL_PARENT),
            "residual_candidate": identity(RESIDUAL_CANDIDATE),
        },
        "checks": checks,
        "identity_checks": identity_checks,
        "counts": {
            "targets": len(target_checks),
            "target_failures": len(target_failures),
            "stock_slots": len(stock_checks),
            "non_target_records": int(non_target.get("records_checked") or 0),
            "structure_parent": int(structure_parent.get("issues") or 0),
            "structure_candidate": int(structure_candidate.get("issues") or 0),
            "remaining_residuals": len(candidate_residual_rows),
        },
        "target_failures": target_failures,
        "target_checks": target_checks,
        "stock_checks": stock_checks,
        "diff_gate": diff_gate,
        "structure_gate": structure_gate,
        "false_segptr_gate": false_gate,
        "residual_gate": residual_gate,
        "nondialogue_gate": nondialogue_gate,
        "mixed_gate": mixed_gate,
        "smoke_gate": smoke_gate,
        "saveram_policy": {
            "candidate_save_size_valid": len(candidate_save) == SAVE_SIZE,
            "candidate_save_hash_not_a_gate": True,
            "live_main_save_untouched_by_audit": True,
        },
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
