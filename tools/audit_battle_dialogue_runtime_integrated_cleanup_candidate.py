#!/usr/bin/env python3
"""Independent static audit for battle_dialogue_runtime_integrated_cleanup_candidate."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_p2_stock_spill_candidate import _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, DICT_PTR_START, SEG_DICT, Tbl, Dictionary, load_rom, stock_base

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
PARENT = PATCH / "battle_dialogue_short_fixed_structure_repair_candidate.wsc"
CANDIDATE = PATCH / "battle_dialogue_runtime_integrated_cleanup_candidate.wsc"
SAVE = ROOT / "sram/battle_dialogue_runtime_integrated_cleanup_candidate.sav"
SRAM = ROOT / "sram/battle_dialogue_runtime_integrated_cleanup_candidate.sav"
BUILD_REPORT = PATCH / "battle_dialogue_runtime_integrated_cleanup_report.json"
FALSE_SEGPTR = PATCH / "battle_dialogue_runtime_integrated_cleanup_false_segptr.json"
INVENTORY = SCRIPT / "battle_dialogue_structure_inventory.csv"
SHORT_META = SCRIPT / "battle_dialogue_short_fixed_metadata_targets.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT = PATCH / "battle_dialogue_runtime_integrated_cleanup_audit.json"

EXPECTED_MAIN_SHA = "56b1ed5b81d9878bed01383f68abfffc876ad04eea5dd1d4d29525c833c83898"
EXPECTED_PARENT_SHA = "75e840e0782e2bb22c35ea6d52eec7705bad6e91d87fa56bf536ad6d531fe890"
EXPECTED_CANDIDATE_SHA = "64ade267ea6f5153e0d19bbdc308ed3f07b1da0891fcb485cc70dcd3100b2464"
EXPECTED_TARGETS = 356
EXPECTED_BATTLE_RECORDS = 9783
EXPECTED_SHORT_META = 104
SYSTEM = 0x6106D5
SYSTEM_PREFIX = bytes.fromhex("17280118")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ident(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "size": len(payload), "sha256": sha(payload)}


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in text)


def read_inventory() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    main_tip = bytes(load_rom(MAIN))
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    save = SAVE.read_bytes()
    sram = SRAM.read_bytes()
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    segptr = json.loads(FALSE_SEGPTR.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dict = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    cand_dict = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)

    failures: list[dict[str, Any]] = []
    identity = {
        "main_exact": sha(main_tip) == EXPECTED_MAIN_SHA,
        "parent_exact": sha(parent) == EXPECTED_PARENT_SHA,
        "candidate_exact": sha(candidate) == EXPECTED_CANDIDATE_SHA,
        "build_candidate_exact": str((((build.get("outputs") or {}).get("candidate_rom") or {}).get("sha256") or "")).lower() == sha(candidate),
        "saveram_mirror_exact": save == sram and len(save) == 32768,
    }
    if not all(identity.values()):
        failures.append({"kind": "identity", "gates": identity})

    checksum_expected = sum(candidate[:-2]) & 0xFFFF
    checksum_actual = candidate[-2] | (candidate[-1] << 8)
    checksum_exact = checksum_expected == checksum_actual
    if not checksum_exact:
        failures.append({"kind": "checksum", "expected": f"{checksum_expected:04X}", "actual": f"{checksum_actual:04X}"})

    applied = build.get("applied") or []
    if len(applied) != EXPECTED_TARGETS:
        failures.append({"kind": "target_count", "actual": len(applied), "expected": EXPECTED_TARGETS})
    target_abs = {int(row["abs"], 16) for row in applied}
    target_failures = []
    for row in applied:
        logical = int(row["abs"], 16)
        before = bytes.fromhex(row["before_hex"])
        after = bytes.fromhex(row["after_hex"])
        p = parent[sb + logical:sb + logical + len(before)]
        c = candidate[sb + logical:sb + logical + len(after)]
        offset = len(SYSTEM_PREFIX) if logical == SYSTEM else 0
        render = clean(cand_dict.expand(c[offset:], tbl))
        expected = str(row["expected_render"])
        slot = int(row["stock_index"], 16)
        slot_render = clean(cand_dict.expand(bytes(cand_dict.raw_entry(slot)), tbl))
        item = {
            "abs": row["abs"],
            "parent_exact": p == before,
            "candidate_exact": c == after,
            "record_length_exact": len(before) == len(after),
            "terminator_parent_zero": parent[sb + logical + len(before)] == 0,
            "terminator_candidate_zero": candidate[sb + logical + len(after)] == 0,
            "render_exact": render == expected,
            "stock_render_exact": slot_render == expected,
            "visible_japanese_zero": not has_japanese(render),
            "system_prefix_exact": logical != SYSTEM or c.startswith(SYSTEM_PREFIX),
            "record_lead_not_e518": logical == SYSTEM or c[:2] != bytes.fromhex("E518"),
        }
        if not all(item[k] for k in item if k != "abs"):
            target_failures.append(item)
    if target_failures:
        failures.append({"kind": "targets", "count": len(target_failures), "sample": target_failures[:20]})

    inventory = read_inventory()
    if len(inventory) != EXPECTED_BATTLE_RECORDS:
        failures.append({"kind": "inventory_count", "actual": len(inventory), "expected": EXPECTED_BATTLE_RECORDS})
    non_target_failures = []
    for row in inventory:
        logical = int(row["record_start"], 16)
        if logical in target_abs:
            continue
        length = len(bytes.fromhex(row["current_payload_hex"]))
        p = parent[sb + logical:sb + logical + length + 1]
        c = candidate[sb + logical:sb + logical + length + 1]
        if p != c:
            non_target_failures.append(row["record_start"])
    if non_target_failures:
        failures.append({"kind": "non_target_battle_structure", "count": len(non_target_failures), "sample": non_target_failures[:30]})

    with SHORT_META.open(encoding="utf-8-sig", newline="") as handle:
        short_rows = list(csv.DictReader(handle))
    short_failures = []
    for row in short_rows:
        logical = int(row["abs"], 16)
        inv = next((x for x in inventory if int(x["record_start"], 16) == logical), None)
        if inv is None:
            short_failures.append(row["abs"])
            continue
        length = len(bytes.fromhex(inv["current_payload_hex"]))
        if parent[sb + logical:sb + logical + length + 1] != candidate[sb + logical:sb + logical + length + 1]:
            short_failures.append(row["abs"])
    if len(short_rows) != EXPECTED_SHORT_META or short_failures:
        failures.append({"kind": "stage1_short_metadata", "count": len(short_rows), "changed": short_failures[:30]})

    retired = set(current_strong_retired_slots(original, parent, parent_dict))
    p_stock = Dictionary(parent)
    c_stock = Dictionary(candidate)
    changed_slots = {i for i, (a, b) in enumerate(zip(p_stock.ptrs, c_stock.ptrs)) if a != b}
    pointer_guard = bool(changed_slots) and changed_slots <= retired
    if not pointer_guard:
        failures.append({"kind": "stock_pointer_guard", "changed": len(changed_slots), "outside_retired": sorted(changed_slots - retired)[:30]})

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    retired_phrase_extents = []
    for index in retired:
        raw = bytes(parent_dict.raw_entry(index))
        if not raw:
            continue
        start = stock_bank_file + p_stock.ptrs[index]
        retired_phrase_extents.append((start, start + len(raw) + 1))
    pointer_extents = [
        (stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2)
        for index in changed_slots
    ]
    parent_cursor = _stock_phrase_cursor(parent)
    candidate_cursor = _stock_phrase_cursor(candidate)
    tail_extents = [] if candidate_cursor <= parent_cursor else [(stock_bank_file + parent_cursor, stock_bank_file + candidate_cursor)]
    target_extents = [
        (sb + int(row["abs"], 16), sb + int(row["abs"], 16) + len(bytes.fromhex(row["after_hex"])))
        for row in applied
    ]
    allowed = target_extents + pointer_extents + retired_phrase_extents + tail_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in diff_runs(parent, candidate)
        if not covered((left, right), allowed)
    ]
    if unaccounted:
        failures.append({"kind": "unaccounted_diff", "count": len(unaccounted), "sample": unaccounted[:30]})

    runtime = {
        "old_ext3_11_20_exact": parent[0x11 * BANK_SIZE:0x21 * BANK_SIZE] == candidate[0x11 * BANK_SIZE:0x21 * BANK_SIZE],
        "five_bank_21_25_exact": parent[0x21 * BANK_SIZE:0x26 * BANK_SIZE] == candidate[0x21 * BANK_SIZE:0x26 * BANK_SIZE],
        "runtime_7a_exact": parent[0x7A * BANK_SIZE:0x7B * BANK_SIZE] == candidate[0x7A * BANK_SIZE:0x7B * BANK_SIZE],
        "runtime_7f_exact_except_checksum": parent[0x7F * BANK_SIZE:0x80 * BANK_SIZE - 2] == candidate[0x7F * BANK_SIZE:0x80 * BANK_SIZE - 2],
    }
    if not all(runtime.values()):
        failures.append({"kind": "runtime_preservation", "gates": runtime})

    false_segptr = (
        segptr.get("ok") is True
        and int(segptr.get("sites_found") or 0) == 0
        and str((((segptr.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower() == sha(candidate)
    )
    if not false_segptr:
        failures.append({"kind": "false_segptr", "sites": segptr.get("sites_found")})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_dialogue_runtime_integrated_cleanup_candidate.py",
        "ok": not failures,
        "inputs": {
            "main": ident(MAIN, main_tip),
            "parent": ident(PARENT, parent),
            "candidate": ident(CANDIDATE, candidate),
            "build_report": ident(BUILD_REPORT),
            "false_segptr": ident(FALSE_SEGPTR),
            "save": ident(SAVE, save),
            "sram": ident(SRAM, sram),
        },
        "counts": {
            "battle_records": len(inventory),
            "targets": len(applied),
            "non_target_battle_records": len(inventory) - len(target_abs & {int(r['record_start'], 16) for r in inventory}),
            "target_failures": len(target_failures),
            "non_target_battle_changes": len(non_target_failures),
            "stage1_short_metadata": len(short_rows),
            "stage1_short_metadata_changes": len(short_failures),
            "changed_stock_pointer_slots": len(changed_slots),
            "strong_retired_slots": len(retired),
            "unaccounted_diff_runs": len(unaccounted),
            "false_segmented_pointer_writes": int(segptr.get("sites_found") or 0),
        },
        "gates": {
            **identity,
            "checksum_exact": checksum_exact,
            "target_render_exact": not target_failures,
            "target_terminators_exact": not target_failures,
            "portrait_speaker_stage1_metadata_exact": not short_failures and len(short_rows) == EXPECTED_SHORT_META,
            "non_target_battle_structure_exact": not non_target_failures,
            "stock_pointer_changes_only_strong_retired": pointer_guard,
            "whole_rom_diff_confined": not unaccounted,
            **runtime,
            "false_segmented_pointer_write_zero": false_segptr,
        },
        "checksum": {"expected": f"{checksum_expected:04X}", "actual": f"{checksum_actual:04X}"},
        "failures": failures,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "checksum": report["checksum"], "failures": failures[:10]}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
