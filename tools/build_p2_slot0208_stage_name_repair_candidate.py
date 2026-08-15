#!/usr/bin/env python3
"""Repair the unsafe 0208 reclaim while preserving all accepted P2 targets.

Slot 0208 originally renders the shared stage-name term ``공역``.  The bounded
nested duplicate pass repointed it to the private phrase ``오오！`` after the
known script/aux/nested consumers were moved to keeper 0564.  A separate bank-75
stage-name table was outside that scan and still contains raw F208 tokens.

This repair restores 0208 to the pre-reclaim Korean ``공역`` pointer, repoints a
strong unused retired slot (033F) to the existing orphaned ``오오！`` payload at
5F:E344, and migrates the three approved ``오오！`` records from F208 to F33F.
No phrase bytes, runtime code, terminators, FF-page entries, or far pointers are
written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import (  # noqa: E402
    external_occurrence_map,
    nested_occurrence_map,
)
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_p2_exact_reuse_candidate import (  # noqa: E402
    _atomic_copy,
    _atomic_write,
    diff_runs,
    identity,
    sha256_bytes,
)
from build_p2_local_ext3_expansion_candidate import (  # noqa: E402
    DEFAULT_BASELINE_META,
    DEFAULT_BLOCKS,
    DEFAULT_GATE_SHEET,
    DEFAULT_PRE_EXT3,
    DEFAULT_PREFIX_EVIDENCE,
    DEFAULT_UI_REPORT_DIR,
)
from mixed_residual_gate_runner import (  # noqa: E402
    GateInputs,
    build_acceptance_report,
    run_static_gates,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

DEFAULT_ORIGINAL_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_local_ext3_expansion_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_local_ext3_expansion_candidate.sav"
DEFAULT_PARENT_APPROVAL = ROOT / "out/patch/p2_local_ext3_expansion_approval.json"
DEFAULT_PARENT_REPORT = ROOT / "out/patch/p2_local_ext3_expansion_report.json"
DEFAULT_SHARED_SOURCE_ROM = ROOT / "out/patch/p2_duplicate_batch_candidate.wsc"
DEFAULT_EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_CANDIDATE = ROOT / "out/patch/p2_local_ext3_expansion_candidate_fix0208.wsc"
DEFAULT_CANDIDATE_SAVE = ROOT / "sram/p2_local_ext3_expansion_candidate_fix0208.sav"
DEFAULT_APPROVAL = ROOT / "out/patch/p2_local_ext3_expansion_fix0208_approval.json"
DEFAULT_REPORT = ROOT / "out/patch/p2_local_ext3_expansion_fix0208_report.json"
DEFAULT_GATE_DIR = ROOT / "out/patch/p2_local_ext3_expansion_fix0208_gates"

SHARED_SLOT = 0x0208
REPLACEMENT_SLOT = 0x033F
OO_TARGET_IDS = ("script:608F2E", "script:611BB7", "script:63C5F3")
STAGE_ROWS = (
    {"record_abs": 0x75BD5E, "token_abs": 0x75BD62, "name": "Texas space sector"},
    {"record_abs": 0x75BD68, "token_abs": 0x75BD6C, "name": "Side 6 space sector"},
    {"record_abs": 0x75BD96, "token_abs": 0x75BD9A, "name": "Side 7 space sector"},
    {"record_abs": 0x75BDE9, "token_abs": 0x75BDEB, "name": "Axis space sector"},
)


class Slot0208RepairError(RuntimeError):
    pass


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    _atomic_write(
        path,
        (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _identity_bytes(path: Path, payload: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "present": True,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _covered_by_union(run: tuple[int, int], extents: Sequence[tuple[int, int]]) -> bool:
    lo, hi = run
    cursor = lo
    for start, end in sorted(extents):
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= hi:
            return True
    return cursor >= hi


def _set_stock_pointer(rom: bytearray, index: int, pointer: int) -> tuple[int, int]:
    sb = stock_base(rom)
    pos = sb + SEG_DICT * BANK_SIZE + DICT_PTR_START + index * 2
    rom[pos] = pointer & 0xFF
    rom[pos + 1] = (pointer >> 8) & 0xFF
    return pos, pos + 2


def _render_record(
    rom: bytes,
    dictionary: Any,
    tbl: Tbl,
    logical: int,
    *,
    max_len: int = 256,
) -> str:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if got is None:
        raise Slot0208RepairError(f"record has no terminator: {logical:06X}")
    return dictionary.expand(bytes(got[0]), tbl).rstrip("\u3000 \t")


def _build_plan(
    parent_report: Mapping[str, Any],
    parent_identity: Mapping[str, Any],
) -> dict[str, Any]:
    targets = [dict(row) for row in parent_report.get("targets") or []]
    found: set[str] = set()
    for row in targets:
        record_id = str(row.get("record_id") or "")
        if record_id not in OO_TARGET_IDS:
            continue
        if str(row.get("dictionary_index") or "").upper() != "0208":
            raise Slot0208RepairError(
                f"repair target is no longer assigned to 0208: {record_id}"
            )
        row["dictionary_index"] = f"{REPLACEMENT_SLOT:04X}"
        row["strategy"] = "slot0208_stage_name_repair_to_retired_033F"
        found.add(record_id)
    if found != set(OO_TARGET_IDS):
        raise Slot0208RepairError(f"missing repair targets: {sorted(set(OO_TARGET_IDS)-found)}")
    return {
        "generated_by": "tools/build_p2_slot0208_stage_name_repair_candidate.py",
        "manifest_sha256": (parent_report.get("population") or {}).get("manifest_sha256"),
        "inputs": {"working_rom": dict(parent_identity)},
        "counts": {
            "targets": len(targets),
            "resolved": len(targets),
            "unresolved": 0,
            "repaired_targets": len(OO_TARGET_IDS),
        },
        "targets": targets,
        "dictionary_changes": {
            "shared_slot_restored": f"{SHARED_SLOT:04X}",
            "replacement_retired_slot": f"{REPLACEMENT_SLOT:04X}",
            "phrase_bytes_written": 0,
            "runtime_written": False,
            "terminator_written": False,
            "ff_page_written": False,
            "full_rebuild": False,
            "policy": "restore_shared_0208_and_reuse_existing_orphan_oo_payload",
        },
        "guard_outcomes": {
            "hidden_stage_name_table_repair": {
                "ok": True,
                "records": len(STAGE_ROWS),
            },
            "replacement_slot_strong_retired": {"ok": True},
        },
        "ext3": dict(parent_report.get("ext3") or {}),
    }


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    original = args.original_rom.read_bytes()
    parent = args.parent_rom.read_bytes()
    shared_source = args.shared_source_rom.read_bytes()
    if len(parent) != 16_777_216:
        raise Slot0208RepairError("parent ROM is not 16 MiB")
    if not args.parent_save.is_file() or args.parent_save.stat().st_size != 32_768:
        raise Slot0208RepairError("same-stem 32 KiB SaveRAM is missing")

    inherited_slots, approved_parent_sha, inherited_ranges = load_approved_detachment(
        args.parent_approval
    )
    parent_sha = sha256_bytes(parent)
    if approved_parent_sha != parent_sha:
        raise Slot0208RepairError(
            f"parent approval is bound to {approved_parent_sha}, parent is {parent_sha}"
        )
    parent_approval = json.loads(args.parent_approval.read_text(encoding="utf-8"))
    parent_report = json.loads(args.parent_report.read_text(encoding="utf-8"))
    if parent_report.get("accepted") is not True:
        raise Slot0208RepairError("parent report is not accepted")

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    tbl = Tbl.load(args.tbl)
    original_dict = Dictionary(original)
    parent_dict = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    shared_dict = make_dictionary_ext3(shared_source, ext_meta, ext3_meta)

    shared_pointer = int(shared_dict.ptrs[SHARED_SLOT])
    shared_payload = bytes(shared_dict.raw_entry(SHARED_SLOT))
    oo_pointer = int(parent_dict.ptrs[SHARED_SLOT])
    oo_payload = bytes(parent_dict.raw_entry(SHARED_SLOT))
    replacement_old_pointer = int(parent_dict.ptrs[REPLACEMENT_SLOT])
    replacement_old_payload = bytes(parent_dict.raw_entry(REPLACEMENT_SLOT))

    if shared_payload != bytes(parent_dict.raw_entry(0x0564)):
        raise Slot0208RepairError("pre-reclaim 0208 no longer matches keeper 0564")
    if parent_dict.expand(oo_payload, tbl).rstrip("\u3000 \t") != "오오！":
        raise Slot0208RepairError("0208 does not currently contain 오오！")
    if shared_dict.expand(shared_payload, tbl).rstrip("\u3000 \t") != "공역":
        raise Slot0208RepairError("shared source 0208 does not render 공역")
    if REPLACEMENT_SLOT in inherited_slots:
        raise Slot0208RepairError("replacement slot is already owned by parent approval")

    wanted = {REPLACEMENT_SLOT}
    original_external = external_occurrence_map(
        original, ext3_aware=False, wanted=wanted
    )
    parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    original_nested = nested_occurrence_map(
        original_dict, wanted=wanted, ext3_aware=False
    )
    parent_nested = nested_occurrence_map(
        parent_dict, wanted=wanted, ext3_aware=True
    )
    raw_hits = _raw_pair_hits(parent, [REPLACEMENT_SLOT])
    replacement_strong = (
        bool(original_external.get(REPLACEMENT_SLOT))
        and not parent_external.get(REPLACEMENT_SLOT)
        and not original_nested.get(REPLACEMENT_SLOT)
        and not parent_nested.get(REPLACEMENT_SLOT)
        and not raw_hits.get(REPLACEMENT_SLOT)
        and int(original_dict.ptrs[REPLACEMENT_SLOT]) == replacement_old_pointer
        and bytes(original_dict.raw_entry(REPLACEMENT_SLOT)) == replacement_old_payload
    )
    if not replacement_strong:
        raise Slot0208RepairError("033F is no longer a strong unused retired slot")

    parent_identity = _identity_bytes(args.parent_rom, parent)
    plan = _build_plan(parent_report, parent_identity)
    target_by_id = {str(row["record_id"]): row for row in plan["targets"]}

    before_stage: list[dict[str, Any]] = []
    for row in STAGE_ROWS:
        record_abs = int(row["record_abs"])
        token_abs = int(row["token_abs"])
        if parent[stock_base(parent) + token_abs : stock_base(parent) + token_abs + 2] != bytes(
            token_from_dict_index(SHARED_SLOT)
        ):
            raise Slot0208RepairError(f"hidden F208 token drifted: {token_abs:06X}")
        rendered = _render_record(parent, parent_dict, tbl, record_abs)
        if "오오！" not in rendered:
            raise Slot0208RepairError(
                f"expected defective stage render at {record_abs:06X}: {rendered!r}"
            )
        before_stage.append(
            {
                "record_abs": f"{record_abs:06X}",
                "token_abs": f"{token_abs:06X}",
                "name": str(row["name"]),
                "before_render": rendered,
            }
        )

    candidate = bytearray(parent)
    pointer_extents = [
        _set_stock_pointer(candidate, SHARED_SLOT, shared_pointer),
        _set_stock_pointer(candidate, REPLACEMENT_SLOT, oo_pointer),
    ]

    sb = stock_base(candidate)
    record_extents: list[tuple[int, int]] = []
    migrated_records: list[dict[str, Any]] = []
    old_token = bytes(token_from_dict_index(SHARED_SLOT))
    new_token = bytes(token_from_dict_index(REPLACEMENT_SLOT))
    for record_id in OO_TARGET_IDS:
        row = target_by_id[record_id]
        logical = int(str(row["abs"]), 16)
        prefix = int(row.get("prefix_bytes") or 0)
        token_file = sb + logical + prefix
        if bytes(candidate[token_file : token_file + 2]) != old_token:
            raise Slot0208RepairError(f"repair token drifted: {record_id}")
        candidate[token_file : token_file + 2] = new_token
        record_extents.append((token_file, token_file + 2))
        migrated_records.append(
            {
                "record_id": record_id,
                "abs": f"{logical:06X}",
                "token_abs": f"{logical + prefix:06X}",
                "before_token": old_token.hex().upper(),
                "after_token": new_token.hex().upper(),
                "target_ko": str(row["korean_text"]),
            }
        )

    before_checksum = bytes(candidate)
    checksum = update_ws_checksum(candidate)
    checksum_extents = diff_runs(before_checksum, candidate)
    final = bytes(candidate)
    final_dict = make_dictionary_ext3(final, ext_meta, ext3_meta)

    if int(final_dict.ptrs[SHARED_SLOT]) != shared_pointer:
        raise Slot0208RepairError("0208 pointer restoration failed")
    if bytes(final_dict.raw_entry(SHARED_SLOT)) != shared_payload:
        raise Slot0208RepairError("0208 payload restoration failed")
    if int(final_dict.ptrs[REPLACEMENT_SLOT]) != oo_pointer:
        raise Slot0208RepairError("033F did not point to existing 오오 payload")
    if bytes(final_dict.raw_entry(REPLACEMENT_SLOT)) != oo_payload:
        raise Slot0208RepairError("033F 오오 payload verification failed")

    after_stage: list[dict[str, Any]] = []
    for row in STAGE_ROWS:
        rendered = _render_record(final, final_dict, tbl, int(row["record_abs"]))
        if "공역" not in rendered or "오오！" in rendered:
            raise Slot0208RepairError(
                f"stage-name repair failed at {int(row['record_abs']):06X}: {rendered!r}"
            )
        after_stage.append(
            {
                "record_abs": f"{int(row['record_abs']):06X}",
                "token_abs": f"{int(row['token_abs']):06X}",
                "name": str(row["name"]),
                "after_render": rendered,
            }
        )

    decoded = 0
    for row in plan["targets"]:
        logical = int(str(row["abs"]), 16)
        capacity = int(row["payload_capacity"])
        prefix = int(row["prefix_bytes"])
        got = read_encoded_z_safe(final, stock_base(final) + logical, max_len=max(256, capacity + 1))
        if got is None or len(got[0]) != capacity:
            raise Slot0208RepairError(f"target shape changed: {row['record_id']}")
        rendered = final_dict.expand(bytes(got[0])[prefix:], tbl).rstrip("\u3000 \t")
        expected = str(row["korean_text"]).rstrip("\u3000 \t")
        if rendered != expected:
            raise Slot0208RepairError(
                f"target render changed: {row['record_id']} {rendered!r} != {expected!r}"
            )
        decoded += 1

    pointer_changes = {
        index
        for index, (before, after) in enumerate(zip(parent_dict.ptrs, final_dict.ptrs))
        if before != after
    }
    if pointer_changes != {SHARED_SLOT, REPLACEMENT_SLOT}:
        raise Slot0208RepairError(f"unexpected pointer changes: {sorted(pointer_changes)}")
    nonselected_payloads_preserved = all(
        bytes(parent_dict.raw_entry(index)) == bytes(final_dict.raw_entry(index))
        for index in range(parent_dict.count)
        if index not in {SHARED_SLOT, REPLACEMENT_SLOT}
    )
    if not nonselected_payloads_preserved:
        raise Slot0208RepairError("nonselected dictionary payload changed")

    ranges_preserved = all(
        parent[stock_base(parent) + lo : stock_base(parent) + hi]
        == final[stock_base(final) + lo : stock_base(final) + hi]
        for lo, hi, _owner in inherited_ranges
    )
    if not ranges_preserved:
        raise Slot0208RepairError("inherited detachment range changed")

    candidate_identity = _identity_bytes(args.candidate_rom, final)
    inherited_stock_rows: list[dict[str, Any]] = []
    for index in sorted(inherited_slots - {SHARED_SLOT}):
        before_pointer = int(parent_dict.ptrs[index])
        after_pointer = int(final_dict.ptrs[index])
        before_payload = bytes(parent_dict.raw_entry(index))
        after_payload = bytes(final_dict.raw_entry(index))
        inherited_stock_rows.append(
            {
                "index": f"{index:04X}",
                "baseline_pointer": f"{before_pointer:04X}",
                "target_pointer": f"{after_pointer:04X}",
                "pointer_preserved": before_pointer == after_pointer,
                "payload_preserved": before_payload == after_payload,
            }
        )
    inherited_stock_preserved = all(
        row["pointer_preserved"] and row["payload_preserved"]
        for row in inherited_stock_rows
    )
    if not inherited_stock_preserved:
        raise Slot0208RepairError("unrelated inherited stock ownership changed")

    final_external = external_occurrence_map(
        final, ext3_aware=True, wanted={SHARED_SLOT, REPLACEMENT_SLOT}
    )
    final_nested = nested_occurrence_map(
        final_dict, wanted={SHARED_SLOT, REPLACEMENT_SLOT}, ext3_aware=True
    )
    expected_replacement = {
        (str(row["abs"]), str(row["token_abs"])) for row in migrated_records
    }
    actual_replacement = {
        (str(row["record_abs"]), str(row["token_abs"]))
        for row in final_external.get(REPLACEMENT_SLOT, [])
    }
    migrated_exact = actual_replacement == expected_replacement and not final_nested.get(
        REPLACEMENT_SLOT
    )
    if not migrated_exact:
        raise Slot0208RepairError(
            f"033F consumer set drifted: {actual_replacement} != {expected_replacement}"
        )

    approved_slots = sorted(inherited_slots | {REPLACEMENT_SLOT})
    proof = dict(parent_approval.get("proof") or {})
    proof.update(
        {
            "historical_consumers_accounted": True,
            "all_current_external_refs_retargeted": True,
            "all_current_nested_parents_retargeted": True,
            "detachment_stage_zero_old_refs": True,
            "former_consumer_render_preserved": True,
            "candidate_new_consumers_exact": migrated_exact,
            "changed_pointer_indices_exact": pointer_changes
            == {SHARED_SLOT, REPLACEMENT_SLOT},
            "nonselected_pointers_preserved": True,
            "nonselected_payloads_preserved": nonselected_payloads_preserved,
            "bank5f_diffs_within_approved_extents": True,
            "detachment_diffs_within_approved_extents": ranges_preserved,
            "inherited_stock_slots_preserved": inherited_stock_preserved,
            "inherited_detachment_ranges_preserved": ranges_preserved,
            "inherited_approval_candidate_matches_parent": True,
            "slot0208_restored_to_shared_payload": (
                int(final_dict.ptrs[SHARED_SLOT]) == shared_pointer
                and bytes(final_dict.raw_entry(SHARED_SLOT)) == shared_payload
            ),
            "replacement_slot_strong_retired": replacement_strong,
            "replacement_slot_points_to_existing_oo_payload": (
                int(final_dict.ptrs[REPLACEMENT_SLOT]) == oo_pointer
                and bytes(final_dict.raw_entry(REPLACEMENT_SLOT)) == oo_payload
            ),
            "oo_targets_migrated_exact": migrated_exact,
            "hidden_stage_name_consumers_restored": all(
                "공역" in row["after_render"] and "오오！" not in row["after_render"]
                for row in after_stage
            ),
            "repair_pointer_changes_exact": pointer_changes
            == {SHARED_SLOT, REPLACEMENT_SLOT},
            "repair_record_changes_exact": len(migrated_records) == len(OO_TARGET_IDS),
        }
    )
    if not all(value is True for value in proof.values()):
        failed = [key for key, value in proof.items() if value is not True]
        raise Slot0208RepairError(f"approval proof failed: {failed}")

    local_parent = (
        parent_approval.get("local_expansion_parent_rom")
        or parent_approval.get("parent_rom")
        or {}
    )
    approval: dict[str, Any] = {
        "generated_by": "tools/build_p2_slot0208_stage_name_repair_candidate.py",
        "mode": "pre_gate_detachment_approval",
        "ok": True,
        "parent_rom": parent_identity,
        "candidate_rom": candidate_identity,
        "approved_stock_slots": [f"{index:04X}" for index in approved_slots],
        "approved_detachment_ranges": list(
            parent_approval.get("approved_detachment_ranges") or []
        ),
        "duplicate": dict(parent_approval.get("duplicate") or {}),
        "local_expansion": dict(parent_approval.get("local_expansion") or {}),
        "local_expansion_parent_rom": dict(local_parent),
        "slot0208_stage_name_repair": {
            "shared_slot": f"{SHARED_SLOT:04X}",
            "shared_pointer_before": f"{oo_pointer:04X}",
            "shared_pointer_after": f"{shared_pointer:04X}",
            "shared_payload_after_hex": shared_payload.hex().upper(),
            "replacement_slot": f"{REPLACEMENT_SLOT:04X}",
            "replacement_pointer_before": f"{replacement_old_pointer:04X}",
            "replacement_pointer_after": f"{oo_pointer:04X}",
            "replacement_old_payload_hex": replacement_old_payload.hex().upper(),
            "replacement_new_payload_hex": oo_payload.hex().upper(),
            "phrase_bytes_written": 0,
            "historical_external_occurrences": len(
                original_external.get(REPLACEMENT_SLOT) or []
            ),
            "hidden_stage_records_before": before_stage,
            "hidden_stage_records_after": after_stage,
            "migrated_records": migrated_records,
        },
        "inherited_approvals": {
            "parent_approval": identity(args.parent_approval),
            "stock_preservation": inherited_stock_rows,
            "detachment_ranges_preserved": ranges_preserved,
            "candidate_matches_parent": True,
        },
        "proof": proof,
    }
    if parent_approval.get("retired_slot_reclaim"):
        approval["retired_slot_reclaim"] = dict(
            parent_approval.get("retired_slot_reclaim") or {}
        )
    _atomic_write(args.candidate_rom, final)
    _atomic_copy(args.parent_save, args.candidate_save)
    _write_json(args.approval_report, approval)

    candidate_identity = identity(args.candidate_rom)
    save_identity = identity(args.candidate_save)
    approval_identity = identity(args.approval_report)

    approved_extents = pointer_extents + record_extents + checksum_extents
    all_runs = diff_runs(parent, final)
    unaccounted = [
        run for run in all_runs if not _covered_by_union(run, approved_extents)
    ]
    precommit = {
        "ok": not unaccounted,
        "diff_bytes": sum(hi - lo for lo, hi in all_runs),
        "diff_runs": len(all_runs),
        "unaccounted_runs": [
            {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}"}
            for lo, hi in unaccounted
        ],
        "targets_decoded": decoded,
        "checksum": f"{checksum:04X}",
        "approved_change_extents": [
            {
                "kind": "slot0208_repair",
                "file_start": f"{lo:08X}",
                "file_end_exclusive": f"{hi:08X}",
            }
            for lo, hi in approved_extents
        ],
    }
    if unaccounted:
        raise Slot0208RepairError(f"unapproved repair diff: {unaccounted}")

    validation = {
        "accepted": True,
        "unresolved_count": 0,
        "targets": len(plan["targets"]),
        "policy": "restore_0208_stage_names_and_migrate_oo_to_033F",
    }
    apply_report = {
        "ok": True,
        "policy": "minimal_pointer_swap_no_new_phrase_bytes",
        "parent_targets_verified": len(plan["targets"]),
        "records_migrated": len(migrated_records),
        "hidden_stage_names_restored": len(after_stage),
        "dictionary_pointers_written": 2,
        "phrase_bytes_written": 0,
        "runtime_writes": 0,
        "terminator_writes": 0,
        "ff_page_writes": 0,
        "full_dictionary_rebuild": False,
        "candidate_save": save_identity,
        "approval_report": approval_identity,
    }

    local_baseline_path = Path(str(local_parent.get("path") or ""))
    if not local_baseline_path.is_file():
        raise Slot0208RepairError(
            f"local expansion baseline is missing: {local_baseline_path}"
        )
    gate_inputs = GateInputs(
        original_rom=args.original_rom,
        pre_ext3_rom=args.pre_ext3_rom,
        baseline_rom=args.parent_rom,
        candidate_rom=args.candidate_rom,
        blocks=args.blocks,
        prefix_evidence=args.prefix_evidence,
        tbl=args.tbl,
        ext_meta=args.ext_meta,
        ext3_meta=args.ext3_meta,
        sheet=args.gate_sheet,
        ui_report_dir=args.ui_report_dir,
        out_dir=args.gate_dir,
        prefix=args.candidate_rom.stem,
        baseline_meta=args.baseline_meta,
        approved_detachment_report=args.approval_report,
        approved_local_expansion_report=args.approval_report,
        local_expansion_baseline_rom=local_baseline_path,
    )
    gates, runs = run_static_gates(
        gate_inputs,
        plan_document=plan,
        validation=validation,
        precommit=precommit,
    )
    report = build_acceptance_report(
        inputs=gate_inputs,
        plan_document=plan,
        validation=validation,
        precommit=precommit,
        gates=gates,
        runs=runs,
        apply_report=apply_report,
        candidate_identity=candidate_identity,
        emulator_evidence={
            "status": "user_reported_static_symptom_repaired_runtime_retest_required",
            "blocking": False,
            "note": "User reported the original candidate did not crash but stage names rendered 오오！; corrected candidate requires the same scene to be rechecked.",
        },
    )
    report.update(
        {
            "p2_phase": "P2-1_slot0208_stage_name_repair",
            "parent_candidate": parent_identity,
            "parent_report": identity(args.parent_report),
            "parent_approval": identity(args.parent_approval),
            "approval_report": approval_identity,
            "candidate_save": save_identity,
            "slot0208_stage_name_repair": approval["slot0208_stage_name_repair"],
            "remaining": parent_report.get("remaining"),
            "published": False,
            "main_tip_modified": False,
        }
    )
    _write_json(args.report, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    ap.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    ap.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    ap.add_argument("--parent-approval", type=Path, default=DEFAULT_PARENT_APPROVAL)
    ap.add_argument("--parent-report", type=Path, default=DEFAULT_PARENT_REPORT)
    ap.add_argument("--shared-source-rom", type=Path, default=DEFAULT_SHARED_SOURCE_ROM)
    ap.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--candidate-rom", type=Path, default=DEFAULT_CANDIDATE)
    ap.add_argument("--candidate-save", type=Path, default=DEFAULT_CANDIDATE_SAVE)
    ap.add_argument("--approval-report", type=Path, default=DEFAULT_APPROVAL)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--gate-dir", type=Path, default=DEFAULT_GATE_DIR)
    ap.add_argument("--pre-ext3-rom", type=Path, default=DEFAULT_PRE_EXT3)
    ap.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    ap.add_argument("--prefix-evidence", type=Path, default=DEFAULT_PREFIX_EVIDENCE)
    ap.add_argument("--gate-sheet", type=Path, default=DEFAULT_GATE_SHEET)
    ap.add_argument("--ui-report-dir", type=Path, default=DEFAULT_UI_REPORT_DIR)
    ap.add_argument("--baseline-meta", type=Path, default=DEFAULT_BASELINE_META)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    forbidden = {
        args.parent_rom.resolve(),
        (ROOT / "out/patch/monoeye_ko_expanded.wsc").resolve(),
    }
    if args.candidate_rom.resolve() in forbidden:
        raise SystemExit("refusing to overwrite parent or main TIP")
    report = build_candidate(args)
    print(
        json.dumps(
            {
                "status": report.get("status"),
                "accepted": report.get("accepted"),
                "candidate_rom": (report.get("inputs") or {}).get("candidate_rom"),
                "candidate_save": report.get("candidate_save"),
                "targets": len(report.get("targets") or []),
                "repair": report.get("slot0208_stage_name_repair"),
                "gates": {
                    name: result.get("ok")
                    for name, result in (report.get("gates") or {}).items()
                },
                "remaining": report.get("remaining"),
                "main_tip_modified": False,
                "report": str(args.report),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.get("accepted") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
