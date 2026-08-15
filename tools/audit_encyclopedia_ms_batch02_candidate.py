#!/usr/bin/env python3
"""Independent static audit for the widened rear encyclopedia candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union
from monoeye_rom import BANK_SIZE, SEG_DICT, Tbl, read_encoded_z_safe, slice_expansion_bank, stock_base, token_from_dict_index
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CANDIDATE = ROOT / "out/patch/encyclopedia_ms_batch02_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/encyclopedia_ms_batch02_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/encyclopedia_ms_batch02_report.json"
WORKLIST = ROOT / "out/patch/encyclopedia_ms_batch02_worklist.json"
CATALOG = ROOT / "data/encyclopedia_ms_batch02_ko.json"
POST_AUDIT = ROOT / "out/patch/encyclopedia_ms_batch02_candidate_residual_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/encyclopedia_ms_batch02_candidate_audit.json"

EXPECTED_PARENT_SHA = "853f42f0f3d0d82fbbe9ee713cc9964e12c6e9884d7d99c1d3dfed65bdbbd68c"
EXPECTED_CANDIDATE_SHA = "c8d3b308299da3b2354aac70ff65a3b439da3d0ed97660946b39fd97341aa821"
EXPECTED_TARGETS = 565
EXPECTED_EXT3 = 556
EXPECTED_SHORT = 9
SELECTED_SEGMENTS = (0x19, 0x1C)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


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


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    result = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if result is None:
        raise AuditError(f"unreadable record {logical:06X}")
    return bytes(result[0]), int(result[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    candidate = CANDIDATE.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    build = load(BUILD_REPORT)
    worklist = load(WORKLIST)
    catalog = load(CATALOG)
    post = load(POST_AUDIT)

    identity_checks = {
        "rom_sizes": len(parent) == len(candidate) == ROM_SIZE,
        "save_sizes": len(main_save) == len(candidate_save) == SAVE_SIZE,
        "parent_sha": sha(parent) == EXPECTED_PARENT_SHA,
        "candidate_sha": sha(candidate) == EXPECTED_CANDIDATE_SHA,
        "parent_matches_build": sha(parent) == str((build.get("parent") or {}).get("sha256") or ""),
        "candidate_matches_build": sha(candidate) == str((build.get("candidate") or {}).get("sha256") or ""),
        "build_ok_unpublished": build.get("ok") is True and build.get("published") is False,
        "worklist_parent_bound": str((((worklist.get("inputs") or {}).get("tip") or {}).get("sha256") or "")).lower() == EXPECTED_PARENT_SHA,
        "catalog_legacy_mt_forbidden": ((catalog.get("provenance") or {}).get("legacy_machine_translation_used") is False),
        "compact3_disabled": (load(EXT3_META_PATH).get("compact3") is False),
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    after_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    true_free = set(inventory.ext3_free)

    source_rows = [dict(row) for row in (worklist.get("records") or []) if row.get("status") in {"japanese_residual", "name_alias_mismatch"}]
    source_by_abs = {str(row.get("abs") or "").upper(): row for row in source_rows}
    build_by_abs = {str(row.get("abs") or "").upper(): dict(row) for row in (build.get("applied") or [])}
    translations = list(catalog.get("lines") or [])

    target_checks: list[dict[str, Any]] = []
    target_failures: list[dict[str, Any]] = []
    target_addresses: set[str] = set()
    target_logicals: set[int] = set()
    target_extents: list[tuple[int, int]] = []
    target_ext3: dict[int, bytes] = {}
    target_stock: dict[int, str] = {}
    base = stock_base(parent)

    for translation in translations:
        address = str(translation.get("abs") or "").upper()
        target_addresses.add(address)
        source = source_by_abs.get(address)
        applied = build_by_abs.get(address)
        if source is None or applied is None:
            target_failures.append({"abs": address, "reason": "source_or_build_row_missing"})
            continue
        logical = int(address, 16)
        target_logicals.add(logical)
        before_payload, before_term = payload_at(parent, logical)
        after_payload, after_term = payload_at(candidate, logical)
        expected_source_payload = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        expected = normalize_ko_text(str(translation.get("ko") or "")).rstrip("\u3000 \t")
        actual = after_dictionary.expand(after_payload, tbl).rstrip("\u3000 \t")
        source_bound = before_payload == expected_source_payload and len(before_payload) == int(source.get("payload_len") or 0)
        structure_ok = len(after_payload) == len(before_payload) and before_term == after_term and parent[before_term] == candidate[after_term] == 0
        report_bound = str(applied.get("after") or "").rstrip("\u3000 \t") == expected and int(applied.get("payload_len") or 0) == len(before_payload)
        strategy = str(applied.get("strategy") or "")
        token_ok = False
        storage_ok = False
        if strategy == "private_ext3_after_compaction":
            index = int(str(applied.get("ext3_index") or "0"), 16)
            encoded = encode_phrase(expected, tbl)
            token = token_from_ext3_index(index, num_banks=num_banks)
            segment = bank_local_for_index(index)[0]
            token_ok = after_payload[:4] == token and after_payload[4:] == b"\x01" * (len(after_payload) - 4)
            storage_ok = index in true_free and segment in SELECTED_SEGMENTS and bytes(after_dictionary.raw_entry(index)) == encoded
            previous = target_ext3.setdefault(index, encoded)
            if previous != encoded:
                target_failures.append({"abs": address, "reason": "ext3_phrase_conflict"})
        elif strategy == "existing_exact_stock":
            index = int(str(applied.get("stock_index") or "0"), 16)
            token = token_from_dict_index(index)
            token_ok = after_payload[:2] == token and after_payload[2:] == b"\x01" * (len(after_payload) - 2)
            before_text = before_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
            after_text = after_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
            storage_ok = before_text == after_text == expected and bytes(before_dictionary.raw_entry(index)) == bytes(after_dictionary.raw_entry(index))
            previous = target_stock.setdefault(index, expected)
            if previous != expected:
                target_failures.append({"abs": address, "reason": "stock_phrase_conflict"})
        else:
            target_failures.append({"abs": address, "reason": f"unexpected_strategy:{strategy}"})
        japanese = sum(is_japanese_character(character) for character in actual)
        ok = source_bound and structure_ok and report_bound and token_ok and storage_ok and actual == expected and japanese == 0 and len(actual) <= 13
        check = {
            "abs": address,
            "expected": expected,
            "actual": actual,
            "strategy": strategy,
            "source_bound": source_bound,
            "structure_ok": structure_ok,
            "report_bound": report_bound,
            "token_ok": token_ok,
            "storage_ok": storage_ok,
            "japanese": japanese,
            "visual_cells": len(actual),
            "ok": ok,
        }
        target_checks.append(check)
        if not ok:
            target_failures.append(check)
        target_extents.append((base + logical, base + logical + len(before_payload)))

    population_ok = (
        len(source_rows) == len(translations) == len(build_by_abs) == EXPECTED_TARGETS
        and len(source_by_abs) == len(target_addresses) == EXPECTED_TARGETS
        and set(source_by_abs) == target_addresses == set(build_by_abs)
        and len(target_checks) == EXPECTED_TARGETS
    )

    # Independently prove that compaction preserved every pre-existing live
    # phrase in the two rewritten banks.  New target slots were true-free in the
    # parent and are excluded from this live set by construction.
    live_checks: list[dict[str, Any]] = []
    live_failures: list[dict[str, Any]] = []
    for segment in SELECTED_SEGMENTS:
        bank_i = segment - EXP3_SEG0
        for local in range(0x1000):
            index = 0x1000 + (bank_i << 12) + local
            if index in true_free:
                continue
            before_raw = bytes(before_dictionary.raw_entry(index))
            after_raw = bytes(after_dictionary.raw_entry(index))
            ok = before_raw == after_raw
            if not ok:
                live_failures.append({"index": f"{index:05X}", "segment": f"{segment:02X}", "before": before_raw.hex().upper(), "after": after_raw.hex().upper()})
            live_checks.append({"index": f"{index:05X}", "segment": f"{segment:02X}", "ok": ok})

    non_target = verify_non_target_invariance(parent, candidate, before_dictionary=before_dictionary, after_dictionary=after_dictionary, tbl=tbl, excluded=target_logicals)
    stock_start = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_unchanged = parent[stock_start : stock_start + BANK_SIZE] == candidate[stock_start : stock_start + BANK_SIZE]
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate[runtime_start:runtime_end]
    other_ext3_unchanged = all(slice_expansion_bank(parent, segment) == slice_expansion_bank(candidate, segment) for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks) if segment not in SELECTED_SEGMENTS)

    runs = diff_runs(parent, candidate)
    allowed = target_extents + [(segment * BANK_SIZE, (segment + 1) * BANK_SIZE) for segment in SELECTED_SEGMENTS] + [(len(parent) - 2, len(parent))]
    unaccounted = [{"start": f"{left:08X}", "end_exclusive": f"{right:08X}"} for left, right in runs if not covered((left, right), allowed)]
    diff_gate = {
        "ok": not unaccounted and sum(right-left for left,right in runs) == int(((build.get("diff") or {}).get("changed_bytes_from_parent") or 0)) and len(runs) == int(((build.get("diff") or {}).get("runs") or 0)),
        "changed_bytes": sum(right-left for left,right in runs),
        "runs": len(runs),
        "unaccounted": unaccounted,
    }

    post_counts = post.get("counts") or {}
    post_gate = {
        "ok": str((((post.get("inputs") or {}).get("tip") or {}).get("sha256") or "")).lower() == EXPECTED_CANDIDATE_SHA and int(post_counts.get("actionable_records", -1)) == 0 and int(post_counts.get("japanese_residual_records", -1)) == 0 and int(post_counts.get("unreadable_records", -1)) == 0,
        "counts": post_counts,
    }

    checks = {
        "identities": all(identity_checks.values()),
        "catalog_population": population_ok,
        "targets_exact": not target_failures,
        "targets_within_13_cells": all(int(row.get("visual_cells") or 99) <= 13 for row in target_checks),
        "target_ext3_count": sum(row.get("strategy") == "private_ext3_after_compaction" for row in target_checks) == EXPECTED_EXT3,
        "target_short_count": sum(row.get("strategy") == "existing_exact_stock" for row in target_checks) == EXPECTED_SHORT,
        "all_parent_live_ext3_raw_exact": not live_failures,
        "non_target_invariance": non_target.get("ok") is True,
        "stock_bank_unchanged": stock_unchanged,
        "runtime_hook_unchanged": runtime_unchanged,
        "other_ext3_banks_unchanged": other_ext3_unchanged,
        "diffs_bounded": diff_gate["ok"],
        "post_residual_zero": post_gate["ok"],
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_ms_batch02_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_user_visual_test" if ok else "failed",
        "inputs": {
            "parent": identity(MAIN, parent),
            "main_save": identity(MAIN_SAVE, main_save),
            "candidate": identity(CANDIDATE, candidate),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "build_report": identity(BUILD_REPORT),
            "worklist": identity(WORKLIST),
            "catalog": identity(CATALOG),
            "post_audit": identity(POST_AUDIT),
        },
        "checks": checks,
        "identity_checks": identity_checks,
        "counts": {
            "targets": len(target_checks),
            "target_failures": len(target_failures),
            "ext3_records": sum(row.get("strategy") == "private_ext3_after_compaction" for row in target_checks),
            "short_stock_records": sum(row.get("strategy") == "existing_exact_stock" for row in target_checks),
            "target_ext3_unique_slots": len(target_ext3),
            "short_stock_unique_slots": len(target_stock),
            "preexisting_live_slots_checked": len(live_checks),
            "preexisting_live_slot_failures": len(live_failures),
            "non_target_records": int(non_target.get("records_checked") or 0),
            "post_actionable_records": int(post_counts.get("actionable_records") or 0),
        },
        "target_failures": target_failures,
        "target_checks": target_checks,
        "live_failures": live_failures,
        "non_target": non_target,
        "diff_gate": diff_gate,
        "post_gate": post_gate,
        "saveram_note": "candidate SaveRAM is a convenience copy from build time; live main SaveRAM remains authoritative",
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(args.out, report)
    print(json.dumps({"ok": ok, "status": report["status"], "checks": checks, "counts": report["counts"], "out": str(args.out.resolve())}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
