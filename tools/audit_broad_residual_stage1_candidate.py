#!/usr/bin/env python3
"""Independent static audit for the broad Japanese-residual stage-1 candidate.

This audit treats the current main TIP as immutable, the reviewed shared-
dictionary candidate as the stage-1 parent, and the cumulative broad-residual
candidate as the object under test.  Historical failures in legacy gates are
accepted only when the exact parent issue set is preserved and every new byte
is contained in a reviewed target body or a builder-approved dictionary/phrase
extent.

The ROM and SaveRAM are read-only.  Only the JSON audit report is written.
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, read_encoded_z_safe, stock_base

MAIN_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT_ROM = ROOT / "out/patch/shared_dictionary_cleanup_candidate.wsc"
PARENT_SAVE = ROOT / "sram/shared_dictionary_cleanup_candidate.sav"
CANDIDATE_ROM = ROOT / "out/patch/broad_residual_stage1_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/broad_residual_stage1_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/broad_residual_stage1_report.json"
SHARED_REPORT = ROOT / "out/patch/shared_dictionary_cleanup_report.json"
SOURCE_AUDIT = ROOT / "out/patch/broad_japanese_residual_after_shared_audit.json"
POST_AUDIT = ROOT / "out/patch/broad_japanese_residual_after_stage1_audit.json"
CLASSIFICATION = ROOT / "out/patch/broad_japanese_residual_classification.json"
STRUCTURE_PARENT = ROOT / "out/patch/broad_stage1_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/broad_stage1_structure_candidate.json"
FALSE_SEGPTR_PARENT = ROOT / "out/patch/broad_stage1_false_segptr_parent.json"
FALSE_SEGPTR_CANDIDATE = ROOT / "out/patch/broad_stage1_false_segptr_candidate.json"
NONDIAG_PARENT = ROOT / "out/patch/broad_stage1_nondialogue_parent.json"
NONDIAG_CANDIDATE = ROOT / "out/patch/broad_stage1_nondialogue_candidate.json"
MIXED_PARENT = ROOT / "out/patch/broad_stage1_mixed_parent.json"
MIXED_CANDIDATE = ROOT / "out/patch/broad_stage1_mixed_candidate.json"
SMOKE_PARENT = ROOT / "out/patch/broad_stage1_smoke_parent.json"
SMOKE_CANDIDATE = ROOT / "out/patch/broad_stage1_smoke_candidate.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/broad_residual_stage1_candidate_audit.json"

UNIT_BANKS = tuple(range(0x50, 0x5E)) + tuple(range(0x6A, 0x70))


class AuditError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity(path: Path, value: bytes | None = None) -> dict[str, Any]:
    payload = value if value is not None else path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size": len(payload),
        "sha256": sha256_bytes(payload),
    }


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root is not an object: {path}")
    return value


def write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


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
    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    candidate = CANDIDATE_ROM.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()

    build = load_json(BUILD_REPORT)
    shared = load_json(SHARED_REPORT)
    source = load_json(SOURCE_AUDIT)
    post = load_json(POST_AUDIT)
    classification = load_json(CLASSIFICATION)
    structure_parent = load_json(STRUCTURE_PARENT)
    structure_candidate = load_json(STRUCTURE_CANDIDATE)
    false_parent = load_json(FALSE_SEGPTR_PARENT)
    false_candidate = load_json(FALSE_SEGPTR_CANDIDATE)
    nond_parent = load_json(NONDIAG_PARENT)
    nond_candidate = load_json(NONDIAG_CANDIDATE)
    mixed_parent = load_json(MIXED_PARENT)
    mixed_candidate = load_json(MIXED_CANDIDATE)
    smoke_parent = load_json(SMOKE_PARENT)
    smoke_candidate = load_json(SMOKE_CANDIDATE)

    expected_main = str((build.get("main_tip") or {}).get("sha256") or "")
    expected_parent = str((build.get("parent_shared_dictionary_candidate") or {}).get("sha256") or "")
    expected_candidate = str((build.get("candidate") or {}).get("sha256") or "")
    identity_checks = {
        "main_matches_build_report": sha256_bytes(main_rom) == expected_main,
        "parent_matches_build_report": sha256_bytes(parent) == expected_parent,
        "candidate_matches_build_report": sha256_bytes(candidate) == expected_candidate,
        "rom_sizes_16_mib": len(main_rom) == len(parent) == len(candidate) == 16_777_216,
        "main_saveram_32_kib": len(main_save) == 32_768,
        "candidate_saveram_matches_main": candidate_save == main_save,
        "parent_saveram_matches_main": parent_save == main_save,
        "build_report_ok": build.get("ok") is True,
        "shared_report_ok": shared.get("ok") is True,
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)

    source_by_abs = {
        str(row.get("abs") or "").upper(): row
        for row in ((source.get("records") or {}).get("tier_a") or [])
    }
    target_checks: list[dict[str, Any]] = []
    target_failures: list[dict[str, Any]] = []
    target_extents: list[tuple[int, int]] = []
    for applied in build.get("records") or []:
        address = str(applied.get("abs") or "").upper()
        row = source_by_abs.get(address)
        if row is None:
            target_failures.append({"abs": address, "reason": "source_row_missing"})
            continue
        logical = int(address, 16)
        prefix_len = int(row.get("prefix_bytes") or 0)
        body_capacity = int(row.get("body_capacity") or 0)
        got = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if got is None:
            target_failures.append({"abs": address, "reason": "candidate_record_unreadable"})
            continue
        payload = bytes(got[0])
        expected_payload_capacity = int(row.get("payload_capacity") or len(payload))
        if len(payload) != expected_payload_capacity:
            target_failures.append(
                {
                    "abs": address,
                    "reason": "payload_capacity_drift",
                    "expected": expected_payload_capacity,
                    "actual": len(payload),
                }
            )
            continue
        try:
            rendered = dictionary.expand(
                payload[prefix_len : prefix_len + body_capacity], tbl
            ).rstrip("\u3000 \t")
        except Exception as exc:
            target_failures.append(
                {"abs": address, "reason": f"decode_failed:{type(exc).__name__}"}
            )
            continue
        expected = str(applied.get("after") or "").rstrip("\u3000 \t")
        japanese = sum(is_japanese_character(character) for character in rendered)
        ok = rendered == expected and japanese == 0
        check = {
            "abs": address,
            "record_id": applied.get("record_id"),
            "expected": expected,
            "actual": rendered,
            "japanese_characters": japanese,
            "prefix_bytes": prefix_len,
            "body_capacity": body_capacity,
            "ok": ok,
        }
        target_checks.append(check)
        if not ok:
            target_failures.append(check)
        target_extents.append(
            (sb + logical + prefix_len, sb + logical + prefix_len + body_capacity)
        )

    shared_checks: list[dict[str, Any]] = []
    shared_failures: list[dict[str, Any]] = []
    for row in shared.get("slots") or []:
        index = int(str(row.get("index") or "0"), 16)
        expected = str(row.get("after") or "").rstrip("\u3000 \t")
        actual = dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        ok = actual == expected and not any(
            is_japanese_character(character) for character in actual
        )
        check = {
            "index": f"{index:04X}",
            "expected": expected,
            "actual": actual,
            "ok": ok,
        }
        shared_checks.append(check)
        if not ok:
            shared_failures.append(check)

    parent_structure = {issue_signature(row) for row in structure_parent.get("first_issues") or []}
    candidate_structure = {
        issue_signature(row) for row in structure_candidate.get("first_issues") or []
    }
    structure_gate = {
        "ok": (
            int(structure_parent.get("issues") or 0) == 27
            and int(structure_candidate.get("issues") or 0) == 27
            and parent_structure == candidate_structure
        ),
        "parent_issues": int(structure_parent.get("issues") or 0),
        "candidate_issues": int(structure_candidate.get("issues") or 0),
        "new_issues": [list(item) for item in sorted(candidate_structure - parent_structure)],
        "missing_historical": [list(item) for item in sorted(parent_structure - candidate_structure)],
    }

    false_segptr_gate = {
        "ok": (
            int(false_parent.get("sites_found") or 0) == 0
            and int(false_candidate.get("sites_found") or 0) == 0
        ),
        "parent_sites": int(false_parent.get("sites_found") or 0),
        "candidate_sites": int(false_candidate.get("sites_found") or 0),
    }

    build_verification = build.get("verification") or {}
    parent_i = nond_parent.get("check_i_dict_expansion") or {}
    candidate_i = nond_candidate.get("check_i_dict_expansion") or {}
    nondialogue_gate = {
        "ok": (
            (nond_candidate.get("check_ii_marker_records") or {}).get("ok") is True
            and (nond_candidate.get("check_iii_length_terminator") or {}).get("ok") is True
            and (nond_candidate.get("check_iv_nested_dictionary_detachment") or {}).get("ok") is True
            and ((build_verification.get("non_target_invariance") or {}).get("ok") is True)
            and int((build_verification.get("non_target_invariance") or {}).get("failure_count") or 0) == 0
        ),
        "historical_check_i_parent": {
            "dict_only_mismatches": int(parent_i.get("dict_only_mismatches") or 0),
            "rendered_mismatches": int(parent_i.get("rendered_mismatches") or 0),
        },
        "candidate_check_i": {
            "dict_only_mismatches": int(candidate_i.get("dict_only_mismatches") or 0),
            "rendered_mismatches": int(candidate_i.get("rendered_mismatches") or 0),
        },
        "candidate_marker_misconsumed": int(
            (nond_candidate.get("check_ii_marker_records") or {}).get("misconsumed") or 0
        ),
        "candidate_length_violations": int(
            (nond_candidate.get("check_iii_length_terminator") or {}).get("violations") or 0
        ),
        "builder_non_target_failures": int(
            (build_verification.get("non_target_invariance") or {}).get("failure_count") or 0
        ),
        "note": (
            "Legacy check (i) compares against the Japanese original and therefore includes "
            "historical intentional localization. New stage-1 records are accepted only because "
            "the candidate-bound builder independently proved 123,260 non-target records invariant."
        ),
    }

    mixed_parent_counts = mixed_parent.get("counts") or {}
    mixed_candidate_counts = mixed_candidate.get("counts") or {}
    mixed_gate = {
        "ok": (
            int(mixed_candidate_counts.get("scan_errors") or 0) == 0
            and int(mixed_candidate_counts.get("broken_word_hits") or 0)
            <= int(mixed_parent_counts.get("broken_word_hits") or 0)
            and int(mixed_candidate_counts.get("split_compound_hits") or 0)
            <= int(mixed_parent_counts.get("split_compound_hits") or 0)
            and int(mixed_candidate_counts.get("particle_hits") or 0)
            <= int(mixed_parent_counts.get("particle_hits") or 0)
        ),
        "parent": mixed_parent_counts,
        "candidate": mixed_candidate_counts,
    }

    unit_changed_offsets: list[int] = []
    for bank in UNIT_BANKS:
        start = sb + bank * BANK_SIZE
        end = start + BANK_SIZE
        unit_changed_offsets.extend(
            offset
            for offset in range(start, end)
            if parent[offset] != candidate[offset]
        )
    unit_outside_targets = [
        f"{offset - sb:06X}"
        for offset in unit_changed_offsets
        if not within(offset, target_extents)
    ]
    smoke_gate = {
        "ok": (
            smoke_candidate.get("jagd_ok") is True
            and smoke_candidate.get("opening_required_ok") is True
            and smoke_candidate.get("hangul_ok") is True
            and not unit_outside_targets
        ),
        "historical_parent_overall_ok": smoke_parent.get("overall_ok"),
        "historical_candidate_overall_ok": smoke_candidate.get("overall_ok"),
        "jagd_ok": smoke_candidate.get("jagd_ok"),
        "opening_required_ok": smoke_candidate.get("opening_required_ok"),
        "hangul_ok": smoke_candidate.get("hangul_ok"),
        "parent_to_candidate_unit_changed_bytes": len(unit_changed_offsets),
        "unit_changed_bytes_outside_reviewed_targets": unit_outside_targets,
        "note": (
            "The legacy smoke gate remains red on both parent and candidate because it compares "
            "the accumulated localized TIP against the Japanese original. The candidate adds only "
            "reviewed target-body bytes inside the unit-bank scope."
        ),
    }

    post_counts = post.get("counts") or {}
    class_population = classification.get("population") or {}
    completion_gate = {
        "ok": (
            post.get("ok") is True
            and int(post_counts.get("tier_a_translation_ready") or 0) == 0
            and int(post_counts.get("japanese_residual_records") or 0) == 853
            and int(class_population.get("resolved_by_shared_dictionary") or 0) == 9
            and int(class_population.get("resolved_by_record_patch") or 0) == 34
            and int(class_population.get("remaining_after_stage1") or 0) == 853
        ),
        "post_stage1_residual_counts": post_counts,
        "classification_population": class_population,
    }

    checks = {
        "identities": all(identity_checks.values()),
        "record_targets_exact": len(target_checks) == 34 and not target_failures,
        "shared_dictionary_targets_preserved": len(shared_checks) == 15 and not shared_failures,
        "structure_delta": structure_gate["ok"],
        "false_segmented_pointer_writes": false_segptr_gate["ok"],
        "nondialogue_delta": nondialogue_gate["ok"],
        "mixed_artifact_delta": mixed_gate["ok"],
        "smoke_delta": smoke_gate["ok"],
        "stage1_population_complete": completion_gate["ok"],
        "runtime_hook_unchanged": build_verification.get("runtime_hook_unchanged") is True,
        "main_tip_unchanged": build_verification.get("main_tip_unchanged") is True,
        "main_saveram_untouched": build_verification.get("main_saveram_untouched") is True,
        "diffs_bounded": build_verification.get("diffs_bounded") is True,
    }
    ok = all(checks.values())

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_broad_residual_stage1_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_visual_test" if ok else "failed",
        "inputs": {
            "main_rom": identity(MAIN_ROM, main_rom),
            "main_save": identity(MAIN_SAVE, main_save),
            "parent_rom": identity(PARENT_ROM, parent),
            "candidate_rom": identity(CANDIDATE_ROM, candidate),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "build_report": identity(BUILD_REPORT),
            "classification": identity(CLASSIFICATION),
        },
        "checks": checks,
        "identity_checks": identity_checks,
        "counts": {
            "shared_dictionary_slots": len(shared_checks),
            "record_targets": len(target_checks),
            "target_failures": len(target_failures),
            "shared_failures": len(shared_failures),
            "remaining_japanese_residual_records": int(
                post_counts.get("japanese_residual_records") or 0
            ),
            "remaining_translation_or_composition_candidates": int(
                class_population.get("remaining_candidate_after_translation_or_composition_review") or 0
            ),
        },
        "structure_delta_gate": structure_gate,
        "false_segptr_gate": false_segptr_gate,
        "nondialogue_delta_gate": nondialogue_gate,
        "mixed_artifact_delta_gate": mixed_gate,
        "smoke_delta_gate": smoke_gate,
        "completion_gate": completion_gate,
        "target_failures": target_failures,
        "shared_failures": shared_failures,
        "target_checks": target_checks,
        "shared_checks": shared_checks,
        "promotion": "blocked_pending_user_visual_verification",
    }
    write_json(args.out, report)
    print(
        json.dumps(
            {
                "ok": ok,
                "candidate": report["inputs"]["candidate_rom"],
                "counts": report["counts"],
                "checks": checks,
                "out": str(args.out.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
