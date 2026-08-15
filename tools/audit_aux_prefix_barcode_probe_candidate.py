#!/usr/bin/env python3
"""Independent audit for the aux-prefix screen-barcode diagnostic ROM."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, DICT_PTR_START, SEG_DICT, Dictionary, Tbl, load_rom, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/aux_prefix_barcode_probe_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/aux_prefix_barcode_probe_candidate.sav"
MANIFEST = ROOT / "out/patch/aux_prefix_barcode_manifest.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/aux_prefix_barcode_probe_audit.json"

EXPECTED_PARENT_SHA256 = "6cf6184e7e989c02b25b3e61fc28fc9e9eca354c20726b5efc7eec10918dce05"
EXPECTED_CANDIDATE_SHA256 = "2d3cda9523815345cc4ea27adc7344eadc5be5b609ad0be7dc54b6ea0e80a0ba"
EXPECTED_ROWS = 2_819
EXPECTED_EXT3_ROWS = 2_714
EXPECTED_STOCK_ROWS = 105
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha256(payload),
    }


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def read_phrase(bank: bytes, pointer: int) -> bytes:
    end = pointer
    while end < len(bank) and bank[end] != 0:
        end += 1
    if end >= len(bank):
        raise AuditError(f"unterminated dictionary phrase at {pointer:04X}")
    return bank[pointer:end]


def main() -> int:
    parent = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    save = CANDIDATE_SAVE.read_bytes()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise AuditError("main TIP identity drifted")
    if len(candidate) != ROM_SIZE or sha256(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("candidate identity drifted")
    if len(save) != SAVE_SIZE:
        raise AuditError("candidate SaveRAM missing or wrong size")
    if str((manifest.get("parent") or {}).get("sha256") or "").lower() != EXPECTED_PARENT_SHA256:
        raise AuditError("manifest parent identity drifted")
    if str((manifest.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("manifest candidate identity drifted")

    records = [dict(row) for row in manifest.get("records") or []]
    if len(records) != EXPECTED_ROWS:
        raise AuditError("manifest population drifted")
    if len({str(row.get("abs")) for row in records}) != EXPECTED_ROWS:
        raise AuditError("manifest address collision")
    if len({str(row.get("code")) for row in records}) != EXPECTED_ROWS:
        raise AuditError("manifest barcode collision")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)
    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    candidate_stock = Dictionary(candidate)

    target_extents: list[tuple[int, int]] = []
    dictionary_extents: list[tuple[int, int]] = []
    failures: list[dict[str, Any]] = []
    expected_stock_sites: dict[int, set[str]] = defaultdict(set)
    stock_indices: set[int] = set()
    ext3_count = 0
    stock_count = 0

    for row in records:
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload_capacity = int(row["payload_capacity"])
        body_capacity = int(row["body_capacity"])
        before = bytes.fromhex(str(row["before_payload_hex"]))
        code = str(row["code"])
        diagnostic = str(row["diagnostic_body"])
        encoded = encode_phrase(diagnostic, tbl)
        reasons: list[str] = []

        parent_payload, parent_term = payload_at(parent, logical)
        candidate_payload, candidate_term = payload_at(candidate, logical)
        if parent_payload != before:
            reasons.append("parent_payload_mismatch")
        if len(candidate_payload) != payload_capacity:
            reasons.append("candidate_payload_length")
        if candidate_payload[: len(prefix)] != prefix:
            reasons.append("prefix_changed")
        if candidate_payload[len(prefix) :].hex().upper().startswith(str(row["token_hex"])) is False:
            reasons.append("token_mismatch")
        actual = strip_pad(candidate_dictionary.expand(candidate_payload[len(prefix) :], tbl))
        if actual != diagnostic:
            reasons.append("render_mismatch")
        if not actual.startswith(code):
            reasons.append("barcode_missing")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_in_diagnostic_body")
        expected_term = sb + logical + payload_capacity
        if parent_term != expected_term or candidate_term != expected_term or candidate[expected_term] != 0:
            reasons.append("terminator_changed")
        target_extents.append(
            (sb + logical + len(prefix), sb + logical + len(prefix) + body_capacity)
        )

        strategy = str(row["strategy"])
        if strategy.startswith("five_bank"):
            ext3_count += 1
            page = int(row["page"])
            local = int(str(row["local"]), 16)
            pointer = int(str(row["pointer"]), 16)
            segment = int(str(row["physical_bank"]), 16)
            if segment != 0x21 + page:
                reasons.append("physical_bank_page_mismatch")
            bank_start = segment * BANK_SIZE
            pointer_actual = int.from_bytes(
                candidate[bank_start + local * 2 : bank_start + local * 2 + 2], "little"
            )
            if pointer_actual != pointer:
                reasons.append("ext3_pointer_mismatch")
            phrase_actual = read_phrase(candidate[bank_start : bank_start + BANK_SIZE], pointer)
            if phrase_actual != encoded:
                reasons.append("ext3_phrase_mismatch")
            if strategy.endswith("_new"):
                dictionary_extents.append((bank_start + local * 2, bank_start + local * 2 + 2))
                dictionary_extents.append(
                    (bank_start + pointer, bank_start + pointer + len(encoded) + 1)
                )
        elif strategy == "strong_retired_stock":
            stock_count += 1
            index = int(str(row["stock_index"]), 16)
            stock_indices.add(index)
            expected_stock_sites[index].add(address)
            pointer = int(candidate_stock.ptrs[index])
            phrase_actual = read_phrase(
                candidate[stock_bank_file : stock_bank_file + BANK_SIZE], pointer
            )
            if phrase_actual != encoded:
                reasons.append("stock_phrase_mismatch")
            dictionary_extents.append(
                (
                    stock_bank_file + DICT_PTR_START + index * 2,
                    stock_bank_file + DICT_PTR_START + index * 2 + 2,
                )
            )
            dictionary_extents.append(
                (stock_bank_file + pointer, stock_bank_file + pointer + len(encoded) + 1)
            )
        else:
            reasons.append("unknown_strategy")

        if reasons:
            failures.append(
                {
                    "abs": address,
                    "code": code,
                    "expected": diagnostic,
                    "actual": actual,
                    "reasons": reasons,
                }
            )

    candidate_external = external_occurrence_map(candidate, ext3_aware=True, wanted=stock_indices)
    candidate_nested = nested_occurrence_map(candidate_dictionary, wanted=stock_indices, ext3_aware=True)
    stock_reference_failures: list[dict[str, Any]] = []
    for index in sorted(stock_indices):
        actual_sites = {
            str(ref.get("record_abs") or "").upper()
            for ref in candidate_external.get(index, [])
        }
        if actual_sites != expected_stock_sites[index] or candidate_nested.get(index):
            stock_reference_failures.append(
                {
                    "stock_index": f"{index:04X}",
                    "expected_sites": sorted(expected_stock_sites[index]),
                    "actual_sites": sorted(actual_sites),
                    "nested": candidate_nested.get(index, []),
                }
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(str(row["abs"]), 16) for row in records},
    )
    runs = diff_runs(parent, candidate)
    allowed = target_extents + dictionary_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        == candidate[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )

    checks = {
        "parent_identity_exact": sha256(parent) == EXPECTED_PARENT_SHA256,
        "candidate_identity_exact": sha256(candidate) == EXPECTED_CANDIDATE_SHA256,
        "targets_exactly_2819": len(records) == EXPECTED_ROWS,
        "ext3_targets_exactly_2714": ext3_count == EXPECTED_EXT3_ROWS,
        "stock_targets_exactly_105": stock_count == EXPECTED_STOCK_ROWS,
        "all_targets_exact": not failures,
        "stock_references_exact": not stock_reference_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "diffs_bounded": not unaccounted,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256,
        "candidate_save_valid": len(save) == SAVE_SIZE,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_aux_prefix_barcode_probe_candidate.py",
        "ok": all(checks.values()),
        "parent": identity(MAIN, parent),
        "candidate": identity(CANDIDATE, candidate),
        "candidate_save": identity(CANDIDATE_SAVE, save),
        "manifest": identity(MANIFEST),
        "counts": {
            "targets": len(records),
            "five_bank_ext3": ext3_count,
            "strong_retired_stock": stock_count,
            "target_failures": len(failures),
            "stock_reference_failures": len(stock_reference_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "checks": checks,
        "target_failures": failures[:100],
        "stock_reference_failures": stock_reference_failures,
        "non_target_invariance": invariance,
        "unaccounted_diff_runs": unaccounted[:100],
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "checks": checks}, ensure_ascii=True, indent=2))
    if not report["ok"]:
        raise AuditError("aux prefix barcode candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
