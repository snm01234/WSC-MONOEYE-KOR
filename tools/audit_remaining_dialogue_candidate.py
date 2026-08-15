#!/usr/bin/env python3
"""Independently audit the two-stage remaining-dialogue candidate.

The audit does not build or modify a ROM.  It reconstructs the 108-target set
from the read-only source audit, verifies the 88-record ext3 intermediate, then
verifies the cumulative 20-record stock-token stage and all non-target runtime
zstrings.  Main TIP and SaveRAM identities are also checked unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_current_untranslated_dialogue import classify_text, identity, load_json, sha256
from mixed_residual_reference_union import _reference_scopes, _walk_zstring_range
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    slice_bank,
    slice_expansion_bank,
    stock_base,
)
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
AUDIT_SOURCE = ROOT / "out/patch/current_untranslated_dialogue_audit.json"
STAGE_A_ROM = ROOT / "out/patch/remaining_dialogue_ext3_candidate.wsc"
STAGE_A_SAVE = ROOT / "sram/remaining_dialogue_ext3_candidate.sav"
STAGE_A_REPORT = ROOT / "out/patch/remaining_dialogue_ext3_report.json"
FINAL_ROM = ROOT / "out/patch/remaining_dialogue_complete_candidate.wsc"
FINAL_SAVE = ROOT / "sram/remaining_dialogue_complete_candidate.sav"
FINAL_REPORT = ROOT / "out/patch/remaining_dialogue_complete_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/remaining_dialogue_candidate_audit.json"

EXPECTED_PARENT_SHA256 = "31acde8c486b5ba13bc00b74ae019444608051478c5e0b874516e74f4cab8eb6"
EXPECTED_SOURCE_AUDIT_SHA256 = "fb281cf7835647ac400e9e287930c7cebd60ca11e507a1bdba24b1e6cbea9680"
ALLOC_SEG = 0x1C


class CandidateAuditError(RuntimeError):
    pass


def write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise CandidateAuditError("diff input sizes differ")
    result: list[tuple[int, int]] = []
    start: int | None = None
    for offset, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = offset
        elif left == right and start is not None:
            result.append((start, offset))
            start = None
    if start is not None:
        result.append((start, len(before)))
    return result


def covered(run: tuple[int, int], extents: Iterable[tuple[int, int]]) -> bool:
    lo, hi = run
    cursor = lo
    for left, right in sorted(extents):
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= hi:
            return True
    return cursor >= hi


def payload_at(rom: bytes, logical: int, *, max_len: int = 256) -> tuple[bytes, int]:
    result = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if result is None:
        raise CandidateAuditError(f"unreadable record at {logical:06X}")
    return bytes(result[0]), int(result[1])


def source_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if sha256(AUDIT_SOURCE.read_bytes()) != EXPECTED_SOURCE_AUDIT_SHA256:
        raise CandidateAuditError("source audit identity drifted")
    source = load_json(AUDIT_SOURCE)
    rows: list[dict[str, Any]] = []
    for category, items in (source.get("records") or {}).items():
        for item in items or []:
            row = dict(item)
            row["category"] = category
            row["logical"] = int(str(row["abs"]), 16)
            row["prefix_bytes"] = len(bytes.fromhex(str(row.get("prefix_hex") or "")))
            rows.append(row)
    rows.sort(key=lambda row: int(row["logical"]))
    direct = [row for row in rows if int(row["body_capacity"]) >= 4]
    short = [row for row in rows if int(row["body_capacity"]) < 4]
    if (len(rows), len(direct), len(short)) != (108, 88, 20):
        raise CandidateAuditError("source target counts drifted")
    return rows, direct, short


def verify_rows(
    rom: bytes,
    rows: Sequence[Mapping[str, Any]],
    *,
    dictionary: Any,
    tbl: Tbl,
    expected_localized: bool,
    compare_parent_payload: bytes | None = None,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(rom, logical)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        original_payload = bytes.fromhex(str(row["payload_hex"]))
        if len(payload) != len(original_payload):
            failures.append({"record_id": row["record_id"], "reason": "length_changed"})
            continue
        if payload[: len(prefix)] != prefix:
            failures.append({"record_id": row["record_id"], "reason": "prefix_changed"})
            continue
        if rom[terminator] != 0:
            failures.append({"record_id": row["record_id"], "reason": "terminator_changed"})
            continue
        if compare_parent_payload is not None:
            parent_record, _ = payload_at(compare_parent_payload, logical)
            if payload != parent_record:
                failures.append(
                    {"record_id": row["record_id"], "reason": "unexpected_stage_change"}
                )
            continue
        rendered = dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        classified = classify_text(rendered)
        if expected_localized:
            expected = str(row["ko"]).rstrip("\u3000 \t")
            if rendered != expected:
                failures.append(
                    {
                        "record_id": row["record_id"],
                        "reason": "render_mismatch",
                        "expected": expected,
                        "actual": rendered,
                    }
                )
            elif int(classified["japanese"]):
                failures.append(
                    {
                        "record_id": row["record_id"],
                        "reason": "japanese_residual",
                        "actual": rendered,
                    }
                )
    return failures


def non_target_invariance(
    before: bytes,
    after: bytes,
    *,
    before_dictionary: Any,
    after_dictionary: Any,
    tbl: Tbl,
    excluded: set[int],
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    checked = 0
    after_base = stock_base(after)
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(
            before, lo, hi, region=region, max_len=max_len
        ):
            if logical in excluded:
                continue
            checked += 1
            result = read_encoded_z_safe(after, after_base + logical, max_len=max_len)
            if result is None:
                failures.append(
                    {"abs": f"{logical:06X}", "region": region, "reason": "unreadable"}
                )
                continue
            candidate_payload = bytes(result[0])
            if candidate_payload != payload:
                failures.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "kind": kind,
                        "reason": "payload_changed",
                    }
                )
                continue
            try:
                left = before_dictionary.expand(payload, tbl)
                right = after_dictionary.expand(candidate_payload, tbl)
            except Exception as exc:
                failures.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "reason": f"decode_failed:{exc}",
                    }
                )
                continue
            if left != right:
                failures.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "reason": "render_changed",
                        "before": left,
                        "after": right,
                    }
                )
    return {
        "ok": not failures,
        "records_checked": checked,
        "failure_count": len(failures),
        "failures": failures[:100],
    }


def ext3_bank_allowed_extents(
    before: bytes,
    after: bytes,
    selected_indices: set[int],
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    before_bank = slice_expansion_bank(before, ALLOC_SEG)
    after_bank = slice_expansion_bank(after, ALLOC_SEG)
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    changed_locals: set[int] = set()
    bank_file = ALLOC_SEG * BANK_SIZE
    for index in selected_indices:
        segment, local = bank_local_for_index(index)
        if segment != ALLOC_SEG:
            raise CandidateAuditError("stage A index escaped allocation bank")
        before_pointer = before_bank[local * 2] | (before_bank[local * 2 + 1] << 8)
        after_pointer = after_bank[local * 2] | (after_bank[local * 2 + 1] << 8)
        if before_pointer != after_pointer:
            changed_locals.add(local)
        pointer_extents.append((bank_file + local * 2, bank_file + local * 2 + 2))
        end = after_pointer
        while end < BANK_SIZE and after_bank[end] != 0:
            end += 1
        if end >= BANK_SIZE:
            raise CandidateAuditError(f"unterminated selected ext3 phrase: {index:05X}")
        phrase_extents.append((bank_file + after_pointer, bank_file + end + 1))

    unexpected_pointer_changes: list[str] = []
    for local in range(0x1000):
        before_pointer = before_bank[local * 2 : local * 2 + 2]
        after_pointer = after_bank[local * 2 : local * 2 + 2]
        if before_pointer != after_pointer and local not in changed_locals:
            unexpected_pointer_changes.append(f"{local:03X}")
    return pointer_extents + phrase_extents, {
        "selected": len(selected_indices),
        "changed_selected_pointers": len(changed_locals),
        "unexpected_pointer_changes": unexpected_pointer_changes,
        "ok": len(changed_locals) == len(selected_indices) and not unexpected_pointer_changes,
    }


def stock_bank_allowed_extents(
    before: bytes,
    after: bytes,
    selected_slots: set[int],
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    before_dictionary = Dictionary(before)
    after_dictionary = Dictionary(after)
    before_ptrs = list(before_dictionary.ptrs)
    after_ptrs = list(after_dictionary.ptrs)
    changed = {
        index
        for index, (left, right) in enumerate(zip(before_ptrs, after_ptrs))
        if left != right
    }
    bank_file = stock_base(before) + SEG_DICT * BANK_SIZE
    extents: list[tuple[int, int]] = []
    for index in selected_slots:
        extents.append(
            (
                bank_file + DICT_PTR_START + index * 2,
                bank_file + DICT_PTR_START + index * 2 + 2,
            )
        )
        pointer = after_ptrs[index]
        raw = bytes(after_dictionary.raw_entry(index))
        extents.append((bank_file + pointer, bank_file + pointer + len(raw) + 1))
    return extents, {
        "selected": len(selected_slots),
        "changed_pointer_indices": [f"{index:04X}" for index in sorted(changed)],
        "ok": changed == selected_slots,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--stage-a-rom", type=Path, default=STAGE_A_ROM)
    parser.add_argument("--final-rom", type=Path, default=FINAL_ROM)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise CandidateAuditError("refusing to write a ROM")

    parent = bytes(load_rom(args.parent))
    stage_a = bytes(load_rom(args.stage_a_rom))
    final = bytes(load_rom(args.final_rom))
    if sha256(parent) != EXPECTED_PARENT_SHA256:
        raise CandidateAuditError("main TIP identity drifted")
    if len(parent) != len(stage_a) or len(stage_a) != len(final):
        raise CandidateAuditError("candidate sizes differ")

    stage_a_report = load_json(STAGE_A_REPORT)
    final_report = load_json(FINAL_REPORT)
    if stage_a_report.get("ok") is not True or final_report.get("ok") is not True:
        raise CandidateAuditError("builder report is not successful")
    if (stage_a_report.get("candidate") or {}).get("sha256") != sha256(stage_a):
        raise CandidateAuditError("stage A report candidate identity mismatch")
    if (final_report.get("candidate") or {}).get("sha256") != sha256(final):
        raise CandidateAuditError("final report candidate identity mismatch")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stage_a_dictionary = make_dictionary_ext3(stage_a, ext_meta, ext3_meta)
    final_dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)
    rows, direct_rows, short_rows = source_rows()

    parent_binding_failures: list[dict[str, Any]] = []
    for row in rows:
        payload, terminator = payload_at(parent, int(row["logical"]))
        if payload != bytes.fromhex(str(row["payload_hex"])):
            parent_binding_failures.append(
                {"record_id": row["record_id"], "reason": "parent_payload_drift"}
            )
        elif parent[terminator] != 0:
            parent_binding_failures.append(
                {"record_id": row["record_id"], "reason": "parent_terminator_drift"}
            )

    stage_a_target_failures = verify_rows(
        stage_a,
        direct_rows,
        dictionary=stage_a_dictionary,
        tbl=tbl,
        expected_localized=True,
    )
    stage_a_short_unchanged = verify_rows(
        stage_a,
        short_rows,
        dictionary=stage_a_dictionary,
        tbl=tbl,
        expected_localized=False,
        compare_parent_payload=parent,
    )
    final_target_failures = verify_rows(
        final,
        rows,
        dictionary=final_dictionary,
        tbl=tbl,
        expected_localized=True,
    )

    stage_a_invariance = non_target_invariance(
        parent,
        stage_a,
        before_dictionary=parent_dictionary,
        after_dictionary=stage_a_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in direct_rows},
    )
    final_invariance = non_target_invariance(
        stage_a,
        final,
        before_dictionary=stage_a_dictionary,
        after_dictionary=final_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in short_rows},
    )

    stage_a_indices = {
        int(str(row["ext3_index"]), 16) for row in stage_a_report.get("records") or []
    }
    if len(stage_a_indices) != 88:
        raise CandidateAuditError("stage A selected ext3 count drifted")
    stage_a_bank_extents, stage_a_pointer_check = ext3_bank_allowed_extents(
        parent, stage_a, stage_a_indices
    )
    parent_base = stock_base(parent)
    stage_a_record_extents = [
        (
            parent_base + int(row["logical"]) + int(row["prefix_bytes"]),
            parent_base
            + int(row["logical"])
            + int(row["prefix_bytes"])
            + int(row["body_capacity"]),
        )
        for row in direct_rows
    ]
    stage_a_allowed = stage_a_record_extents + stage_a_bank_extents + [
        (len(parent) - 2, len(parent))
    ]
    stage_a_runs = diff_runs(parent, stage_a)
    stage_a_unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in stage_a_runs
        if not covered((lo, hi), stage_a_allowed)
    ]

    selected_slots = {
        int(value, 16)
        for value in (final_report.get("stock_allocation") or {}).get(
            "selected_retired_slots", []
        )
    }
    if len(selected_slots) != 11:
        raise CandidateAuditError("stage B selected retired slot count drifted")
    stock_extents, stock_pointer_check = stock_bank_allowed_extents(
        stage_a, final, selected_slots
    )
    stage_b_record_extents = [
        (
            parent_base + int(row["logical"]) + int(row["prefix_bytes"]),
            parent_base
            + int(row["logical"])
            + int(row["prefix_bytes"])
            + int(row["body_capacity"]),
        )
        for row in short_rows
    ]
    final_allowed = stage_b_record_extents + stock_extents + [
        (len(stage_a) - 2, len(stage_a))
    ]
    final_runs = diff_runs(stage_a, final)
    final_unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in final_runs
        if not covered((lo, hi), final_allowed)
    ]

    num_banks = int(ext3_meta.get("num_banks") or 0)
    stage_a_other_ext3_unchanged = all(
        slice_expansion_bank(parent, segment)
        == slice_expansion_bank(stage_a, segment)
        for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if segment != ALLOC_SEG
    )
    final_all_ext3_unchanged = all(
        slice_expansion_bank(stage_a, segment)
        == slice_expansion_bank(final, segment)
        for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
    )
    stage_a_stock_unchanged = slice_bank(parent, SEG_DICT) == slice_bank(stage_a, SEG_DICT)

    runtime_lo = stock_base(parent) + 0x7A0600
    runtime_hi = stock_base(parent) + 0x7A1000
    runtime_unchanged = (
        parent[runtime_lo:runtime_hi]
        == stage_a[runtime_lo:runtime_hi]
        == final[runtime_lo:runtime_hi]
    )

    save_checks = {
        "parent_size": PARENT_SAVE.stat().st_size if PARENT_SAVE.is_file() else None,
        "stage_a_matches_parent": (
            STAGE_A_SAVE.is_file()
            and PARENT_SAVE.is_file()
            and STAGE_A_SAVE.read_bytes() == PARENT_SAVE.read_bytes()
        ),
        "final_matches_parent": (
            FINAL_SAVE.is_file()
            and PARENT_SAVE.is_file()
            and FINAL_SAVE.read_bytes() == PARENT_SAVE.read_bytes()
        ),
    }
    main_tip_unchanged = sha256(PARENT.read_bytes()) == EXPECTED_PARENT_SHA256

    failures = (
        len(parent_binding_failures)
        + len(stage_a_target_failures)
        + len(stage_a_short_unchanged)
        + len(final_target_failures)
        + len(stage_a_unaccounted)
        + len(final_unaccounted)
        + int(not stage_a_invariance["ok"])
        + int(not final_invariance["ok"])
        + int(not stage_a_pointer_check["ok"])
        + int(not stock_pointer_check["ok"])
        + int(not stage_a_other_ext3_unchanged)
        + int(not final_all_ext3_unchanged)
        + int(not stage_a_stock_unchanged)
        + int(not runtime_unchanged)
        + int(not save_checks["stage_a_matches_parent"])
        + int(not save_checks["final_matches_parent"])
        + int(not main_tip_unchanged)
    )
    ok = failures == 0
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_remaining_dialogue_candidate.py",
        "read_only": True,
        "ok": ok,
        "failures": failures,
        "inputs": {
            "parent": identity(args.parent, parent),
            "stage_a": identity(args.stage_a_rom, stage_a),
            "final": identity(args.final_rom, final),
            "source_audit": identity(AUDIT_SOURCE),
            "stage_a_report": identity(STAGE_A_REPORT),
            "final_report": identity(FINAL_REPORT),
        },
        "counts": {
            "targets": len(rows),
            "stage_a_targets": len(direct_rows),
            "stage_b_targets": len(short_rows),
            "final_exact": len(rows) - len(final_target_failures),
            "final_japanese_residuals": sum(
                failure.get("reason") == "japanese_residual"
                for failure in final_target_failures
            ),
            "parent_binding_failures": len(parent_binding_failures),
            "stage_a_target_failures": len(stage_a_target_failures),
            "stage_a_short_changed_early": len(stage_a_short_unchanged),
            "final_target_failures": len(final_target_failures),
            "stage_a_unaccounted_diff_runs": len(stage_a_unaccounted),
            "final_unaccounted_diff_runs": len(final_unaccounted),
        },
        "stage_a": {
            "pointer_check": stage_a_pointer_check,
            "non_target_invariance": stage_a_invariance,
            "stock_dictionary_unchanged": stage_a_stock_unchanged,
            "other_ext3_banks_unchanged": stage_a_other_ext3_unchanged,
            "unaccounted_diff_runs": stage_a_unaccounted,
            "target_failures": stage_a_target_failures,
            "short_changed_early": stage_a_short_unchanged,
            "diff": {
                "changed_bytes": sum(hi - lo for lo, hi in stage_a_runs),
                "runs": len(stage_a_runs),
            },
        },
        "final": {
            "stock_pointer_check": stock_pointer_check,
            "non_target_invariance": final_invariance,
            "ext3_banks_unchanged_from_stage_a": final_all_ext3_unchanged,
            "unaccounted_diff_runs": final_unaccounted,
            "target_failures": final_target_failures,
            "diff": {
                "changed_bytes": sum(hi - lo for lo, hi in final_runs),
                "runs": len(final_runs),
            },
        },
        "preservation": {
            "runtime_hook_unchanged": runtime_unchanged,
            "save_checks": save_checks,
            "main_tip_unchanged": main_tip_unchanged,
        },
        "promotion": "blocked_pending_visual_verification",
    }
    write_json(args.out, report)
    print(
        json.dumps(
            {
                "ok": ok,
                "failures": failures,
                "counts": report["counts"],
                "stage_a_pointer_check": stage_a_pointer_check,
                "stock_pointer_check": stock_pointer_check,
                "main_tip_unchanged": main_tip_unchanged,
                "out": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
