#!/usr/bin/env python3
"""Summarize the promoted TIP and the pending three-record aux cleanup.

Read-only with respect to ROM and SaveRAM. Schema v5 preserves the earlier
scope corrections and adds the user-confirmed bank59 text-initial exception:
1. A Baoa Qu bank59 event dialogue is distinct from the reviewed 60-63
   scenario population and has now been promoted after user validation.
2. A generic "zero residual" claim cannot cover false-prefix aux records. Two
   bank5D targets are duplicate-proven, while bank59:0A2B is bound to its exact
   original sentence and user-observed runtime residual.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
import build_ext3_bank21_probe_candidate as one
from monoeye_rom import BANK_SIZE, le16, load_rom, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
RUNTIME_PROMOTION = ROOT / "out/patch/ext3_five_bank_runtime_probe_promotion_report.json"
RUNTIME_POST = ROOT / "out/patch/ext3_five_bank_runtime_probe_postpromotion_audit.json"
CHAR_PROMOTION = ROOT / "out/patch/encyclopedia_character_all_remaining_promotion_report.json"
CHAR_POST = ROOT / "out/patch/encyclopedia_character_all_remaining_postpromotion_audit.json"
ABAOA_PROMOTION = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_promotion_report.json"
ABAOA_POST = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_postpromotion_audit.json"
STRUCTURE_INVENTORY = ROOT / "out/patch/bank64_6f_structure_inventory.json"
SCENARIO_AUDIT = ROOT / "out/patch/post_abaoa_qu_scenario_dialogue_audit.json"
SCENARIO_WORKLIST = ROOT / "out/patch/post_abaoa_qu_scenario_production_worklist.json"
AUX_DUP_WORKLIST = ROOT / "out/patch/aux_duplicate_false_prefix_residual_worklist.json"
AUX_DUP_CANDIDATE = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_candidate.wsc"
AUX_DUP_BUILD = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_report.json"
AUX_DUP_AUDIT = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_candidate_audit.json"
AUX_DUP_REGRESSION = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_regression_audit.json"
OUT = ROOT / "out/patch/broad_ext3_expansion_plan.json"

EXPECTED_TIP_SHA256 = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"
EXPECTED_AUX_CANDIDATE_SHA256 = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
PAGES = 5
FIRST_BANK = 0x21
EMPTY_AT = 0x2000
ALIAS_LOCAL_LIMIT = 0x0A00
USABLE_PER_BANK = 2550
EXPECTED_USED_SLOTS = [193, 167, 167, 167, 166]
EXPECTED_REFERENCE_COUNTS = [195, 168, 169, 174, 174]
EXPECTED_FREE_SAFE_TOKENS = 11890
EXPECTED_FREE_PHRASE_BYTES = 264071
EXPECTED_DUPLICATE_AUX_TARGETS = {"5D870B", "5DB42B"}
EXPECTED_AUX_TARGETS = {"590A2B", *EXPECTED_DUPLICATE_AUX_TARGETS}


class PlanError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PlanError(f"JSON root must be object: {path}")
    return value


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha256(payload),
    }


def safe_local(local: int) -> bool:
    return 1 <= local < ALIAS_LOCAL_LIMIT and (local & 0xFF) != 0


def read_phrase_length(bank: bytes, pointer: int) -> int:
    if not 0 <= pointer < BANK_SIZE:
        raise PlanError(f"phrase pointer outside bank: {pointer:04X}")
    end = pointer
    while end < BANK_SIZE and bank[end] != 0:
        end += 1
    if end >= BANK_SIZE:
        raise PlanError(f"unterminated phrase at {pointer:04X}")
    return end - pointer


def bank_state(rom: bytes, page: int) -> dict[str, Any]:
    segment = FIRST_BANK + page
    start = segment * BANK_SIZE
    bank = rom[start : start + BANK_SIZE]
    if len(bank) != BANK_SIZE or bank[EMPTY_AT] != 0:
        raise PlanError(f"bank{segment:02X} layout drifted")
    used: list[dict[str, Any]] = []
    cursor = EMPTY_AT + 1
    for local in range(0x1000):
        pointer = le16(bank, local * 2)
        if pointer == EMPTY_AT:
            continue
        if not safe_local(local):
            raise PlanError(f"bank{segment:02X} unsafe used local {local:04X}")
        length = read_phrase_length(bank, pointer)
        cursor = max(cursor, pointer + length + 1)
        used.append(
            {
                "local": f"{local:04X}",
                "pointer": f"{pointer:04X}",
                "phrase_bytes": length,
                "phrase_sha256": sha256(bank[pointer : pointer + length]),
            }
        )
    hits = five.scan_range_hits(rom, page)
    referenced: list[int] = []
    for pos in hits:
        raw = (rom[pos + 2] << 8) | rom[pos + 3]
        referenced.append((raw & 0x0FFF) - 0x0600)
    used_locals = {int(row["local"], 16) for row in used}
    missing = sorted(set(referenced) - used_locals)
    return {
        "page": page,
        "physical_bank": f"{segment:02X}",
        "used_slots": len(used),
        "free_safe_slots": USABLE_PER_BANK - len(used),
        "reference_count": len(hits),
        "referenced_locals": [f"{local:04X}" for local in referenced],
        "missing_reference_targets": [f"{local:04X}" for local in missing],
        "phrase_tail_end_exclusive": f"{cursor:04X}",
        "phrase_room_after": BANK_SIZE - cursor,
        "used": used,
        "safe": not missing,
    }


def main() -> int:
    rom = bytes(load_rom(TIP))
    if len(rom) != one.ROM_SIZE or sha256(rom) != EXPECTED_TIP_SHA256:
        raise PlanError("promoted main TIP identity drifted")

    runtime_promotion = load_object(RUNTIME_PROMOTION)
    runtime_post = load_object(RUNTIME_POST)
    char_promotion = load_object(CHAR_PROMOTION)
    char_post = load_object(CHAR_POST)
    abaoa_promotion = load_object(ABAOA_PROMOTION)
    abaoa_post = load_object(ABAOA_POST)
    inventory = load_object(STRUCTURE_INVENTORY)
    scenario = load_object(SCENARIO_AUDIT)
    scenario_worklist = load_object(SCENARIO_WORKLIST)
    aux_worklist = load_object(AUX_DUP_WORKLIST)
    aux_build = load_object(AUX_DUP_BUILD)
    aux_audit = load_object(AUX_DUP_AUDIT)
    aux_regression = load_object(AUX_DUP_REGRESSION)
    aux_candidate = AUX_DUP_CANDIDATE.read_bytes()

    states = [bank_state(rom, page) for page in range(PAGES)]
    used_slots = [int(state["used_slots"]) for state in states]
    reference_counts = [int(state["reference_count"]) for state in states]
    free_tokens = sum(int(state["free_safe_slots"]) for state in states)
    free_phrase_bytes = sum(int(state["phrase_room_after"]) for state in states)

    sb = stock_base(rom)
    leaf = five.build_five_bank_leaf()
    current_leaf = rom[
        sb + one.FREE_CAVE_START : sb + one.FREE_CAVE_START + len(leaf)
    ]

    char_checks = char_post.get("checks") or {}
    abaoa_checks = abaoa_post.get("checks") or {}
    scenario_counts = scenario.get("counts") or {}
    aux_duplicate_targets = {
        str(row.get("abs") or "").upper()
        for row in aux_worklist.get("targets") or []
    }
    aux_targets = {
        str(row.get("abs") or "").upper()
        for row in aux_build.get("applied") or []
    }
    aux_audit_checks = aux_audit.get("checks") or {}
    checks = {
        "tip_exact": sha256(rom) == EXPECTED_TIP_SHA256,
        "five_bank_runtime_promoted": runtime_promotion.get("published") is True
        and runtime_post.get("ok") is True,
        "five_bank_leaf_exact": current_leaf == leaf,
        "all_alias_references_resolve": all(state["safe"] for state in states),
        "used_slots_exact": used_slots == EXPECTED_USED_SLOTS,
        "reference_counts_exact": reference_counts == EXPECTED_REFERENCE_COUNTS,
        "free_safe_tokens_exact": free_tokens == EXPECTED_FREE_SAFE_TOKENS,
        "free_phrase_bytes_exact": free_phrase_bytes == EXPECTED_FREE_PHRASE_BYTES,
        "character_encyclopedia_promoted": char_promotion.get("published") is True
        and char_post.get("ok") is True,
        "all_693_character_rows_exact": char_checks.get(
            "all_693_character_catalog_rows_exact"
        )
        is True,
        "abaoa_bank59_promoted": abaoa_promotion.get("published") is True
        and str((abaoa_promotion.get("new_tip") or {}).get("sha256", "")).lower()
        == EXPECTED_TIP_SHA256
        and abaoa_post.get("ok") is True,
        "abaoa_bank59_all_257_exact": abaoa_checks.get(
            "all_257_dialogue_rows_exact"
        )
        is True,
        "reviewed_60_63_approved_458_exact": scenario.get("ok") is True
        and int(scenario_counts.get("approved_rows_exact", -1)) == 458
        and int(scenario_counts.get("approved_failures", -1)) == 0,
        "reviewed_60_63_production_targets_zero": int(
            scenario_counts.get("production_targets", -1)
        )
        == 0
        and int(scenario_worklist.get("production_target_count", -1)) == 0,
        "bank64_6f_production_targets_zero": inventory.get("ok") is True
        and int(
            (inventory.get("authoritative_scope") or {}).get(
                "production_target_count", -1
            )
        )
        == 0,
        "aux_duplicate_worklist_exactly_two": aux_worklist.get("ok") is True
        and int((aux_worklist.get("counts") or {}).get("targets", -1)) == 2
        and aux_duplicate_targets == EXPECTED_DUPLICATE_AUX_TARGETS,
        "aux_combined_targets_exactly_three": aux_targets == EXPECTED_AUX_TARGETS
        and int((aux_build.get("counts") or {}).get("targets", -1)) == 3,
        "aux_duplicate_candidate_identity": len(aux_candidate) == one.ROM_SIZE
        and sha256(aux_candidate) == EXPECTED_AUX_CANDIDATE_SHA256,
        "aux_duplicate_build_bound": aux_build.get("ok") is True
        and str((aux_build.get("parent") or {}).get("sha256", "")).lower()
        == EXPECTED_TIP_SHA256
        and str((aux_build.get("candidate") or {}).get("sha256", "")).lower()
        == EXPECTED_AUX_CANDIDATE_SHA256,
        "aux_duplicate_static_audit_ok": aux_audit.get("ok") is True
        and aux_audit_checks.get("all_targets_exact") is True
        and aux_audit_checks.get("non_target_invariance") is True,
        "aux_duplicate_regression_ok": aux_regression.get("ok") is True,
        "aux_candidate_does_not_change_ext3_capacity": all(
            rom[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
            == aux_candidate[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
            for segment in range(0x21, 0x26)
        ),
    }
    ok = all(checks.values())

    plan = {
        "schema_version": 5,
        "generated_by": "tools/plan_broad_ext3_expansion.py",
        "read_only": True,
        "ok": ok,
        "status": "active_pending_aux_false_prefix_user_visual_test",
        "tip": identity(TIP, rom),
        "runtime": {
            "status": "five_bank_runtime_promoted_and_user_validated",
            "leaf_address": f"{one.FREE_CAVE_START:06X}",
            "leaf_length": len(leaf),
            "leaf_sha256": sha256(leaf),
            "new_token": False,
            "new_wram_state": False,
        },
        "bank_states": states,
        "capacity": {
            "used_safe_slots": sum(used_slots),
            "free_safe_tokens": free_tokens,
            "free_phrase_bytes": free_phrase_bytes,
            "remaining_approved_production_demand": {
                "character_encyclopedia": 0,
                "abaoa_qu_bank59_event_dialogue": 0,
                "reviewed_60_63_scenario_dialogue": 0,
                "bank64_6f_generic_script": 0,
                "safe_false_prefix_aux": 3,
                "total_records": 3,
            },
            "after_pending_candidate_promotion": {
                "free_safe_tokens": free_tokens,
                "free_phrase_bytes": free_phrase_bytes,
                "remaining_records": 0,
                "note": "The pending three-record candidate changes one bank59 and two bank5D payloads plus checksum; it allocates no dictionary slot or phrase bytes.",
            },
        },
        "completed_scopes": {
            "character_encyclopedia": {
                "status": "completed_promoted_user_validated",
                "catalog_rows": 693,
                "promotion": str(CHAR_PROMOTION.relative_to(ROOT)),
                "postpromotion_audit": str(CHAR_POST.relative_to(ROOT)),
            },
            "abaoa_qu_bank59_event_dialogue": {
                "status": "completed_promoted_user_validated",
                "rows": 257,
                "promotion": str(ABAOA_PROMOTION.relative_to(ROOT)),
                "postpromotion_audit": str(ABAOA_POST.relative_to(ROOT)),
            },
            "reviewed_60_63_scenario_dialogue": {
                "status": "already_applied_verified_zero_new_delta_within_this_scope",
                "approved_rows_exact": 458,
                "production_targets": 0,
                "audit": str(SCENARIO_AUDIT.relative_to(ROOT)),
                "worklist": str(SCENARIO_WORKLIST.relative_to(ROOT)),
                "scope_warning": "This 60-63 result does not cover bank59 event dialogue or bank5D/5E battle quotes.",
            },
            "bank64_6f": {
                "status": "completed_zero_production_targets",
                "inventory": str(STRUCTURE_INVENTORY.relative_to(ROOT)),
            },
        },
        "pending_scope": {
            "name": "safe_false_prefix_aux",
            "status": "candidate_static_verified_pending_user_visual_test",
            "targets": [
                {
                    "abs": row.get("abs"),
                    "before": row.get("before_text"),
                    "after": row.get("after_text"),
                    "clean_duplicate_peers": [
                        peer.get("abs") for peer in row.get("peers") or []
                    ],
                }
                for row in aux_audit.get("target_checks") or []
            ],
            "candidate": identity(AUX_DUP_CANDIDATE, aux_candidate),
            "worklist": str(AUX_DUP_WORKLIST.relative_to(ROOT)),
            "build_report": str(AUX_DUP_BUILD.relative_to(ROOT)),
            "static_audit": str(AUX_DUP_AUDIT.relative_to(ROOT)),
            "regression_audit": str(AUX_DUP_REGRESSION.relative_to(ROOT)),
            "next_gate": "User emulator validation of 590A2B, 5D870B, and 5DB42B, then ROM-only promotion.",
        },
        "audit_policy_corrections": [
            "A zero result for 60B57E-63FFFF must not be generalized to bank59 event dialogue.",
            "A zero generic dialogue result must not be generalized to false-prefix event or battle records in banks59/5D/5E.",
            "Mixed Japanese+Korean battle records are not mass-edited; bank5D/5E automatic targets still require byte-identical Original duplicate groups with a clean peer.",
            "Bank59:590A2B is an explicit text-initial exception bound to the exact Original sentence and user-observed runtime residual.",
        ],
        "permanent_guards": [
            "Do not translate 62D650-62FFFF event/graphics data as script text.",
            "Do not translate banks64-6F through the generic zstring parser.",
            "Do not remove arbitrary leading bytes from bank59/5D/5E records without exact Original and runtime evidence; bank5D/5E still require duplicate proof for automatic cleanup.",
            "Do not use E5 2F, compact3 E5 19, a second parser, or new WRAM state.",
            "Do not copy candidate SaveRAM back to the main SaveRAM.",
            "Do not reapply legacy machine-translation files.",
        ],
        "checks": checks,
    }
    OUT.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "status": plan["status"],
                "tip": plan["tip"],
                "capacity": plan["capacity"],
                "pending_scope": plan["pending_scope"],
                "checks": checks,
                "out": str(OUT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not ok:
        raise PlanError("broad expansion plan checks failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
