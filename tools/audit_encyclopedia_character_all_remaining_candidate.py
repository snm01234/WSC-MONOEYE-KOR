#!/usr/bin/env python3
"""Independent static audit for the all-remaining character encyclopedia ROM."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_character_five_bank_batch02_candidate import (
    FIRST_BANK,
    PAGES,
    read_phrase,
)
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Tbl,
    load_rom,
    stock_base,
    update_ws_checksum,
)

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/encyclopedia_character_all_remaining_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/encyclopedia_character_all_remaining_candidate.sav"
CATALOG = ROOT / "data/encyclopedia_character_batch01_ko.json"
RESIDUAL_PARENT = ROOT / "out/patch/encyclopedia_character_current_residual_audit.json"
RESIDUAL_CANDIDATE = ROOT / "out/patch/encyclopedia_character_all_remaining_residual_audit.json"
BUILD_REPORT = ROOT / "out/patch/encyclopedia_character_all_remaining_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/encyclopedia_character_all_remaining_candidate_audit.json"

EXPECTED_PARENT = "05a2124e5402c8e8f46b1ca7af3c131748a034939af7c6381cdbb4242d43f65e"
EXPECTED_CANDIDATE = "186d6d04859146420f4ae826a38a0d35377633d54587046c30046125cbceb241"
EXPECTED_TARGETS = 603
EXPECTED_EXT3 = 596
EXPECTED_SHORT = 7
EXPECTED_UNIQUE_EXT3 = 579
EXPECTED_NEW_LOCALS = [116, 116, 116, 116, 115]
EXPECTED_PAGE_COUNTS = [141, 118, 119, 122, 123]
EXPECTED_RETIRED = {0x0317, 0x0392, 0x0398, 0x03AD, 0x03B9}


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def token_page_local(token: bytes) -> tuple[int, int]:
    if len(token) != 4 or token[:2] != b"\xE5\x18":
        raise AuditError(f"not E5 18 token: {token.hex().upper()}")
    raw = (token[2] << 8) | token[3]
    page = raw >> 12
    local = (raw & 0x0FFF) - 0x0600
    if not 0 <= page < PAGES or not 1 <= local < 0x0A00 or (local & 0xFF) == 0:
        raise AuditError(f"unsafe alias page/local: page={page} local={local:04X}")
    return page, local


def main() -> int:
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    if sha256(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if sha256(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("candidate identity drifted")

    catalog = load_object(CATALOG)
    residual_parent = load_object(RESIDUAL_PARENT)
    residual_candidate = load_object(RESIDUAL_CANDIDATE)
    build = load_object(BUILD_REPORT)
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    catalog_by_abs = {
        str(row.get("abs") or "").upper(): dict(row)
        for row in catalog.get("lines") or []
    }
    target_source = [
        dict(row)
        for row in residual_parent.get("records") or []
        if row.get("status") in {"japanese_residual", "name_alias_mismatch"}
    ]
    target_by_abs = {str(row.get("abs") or "").upper(): row for row in target_source}
    if not (len(target_source) == len(target_by_abs) == EXPECTED_TARGETS):
        raise AuditError("target population is not unique")

    target_failures: list[dict[str, Any]] = []
    target_logicals: set[int] = set()
    target_extents: list[tuple[int, int]] = []
    ext3_phrase_to_token: dict[str, tuple[int, int]] = {}
    ext3_token_to_phrase: dict[tuple[int, int], str] = {}
    ext3_target_locals: dict[int, set[int]] = {page: set() for page in range(PAGES)}
    short_indices: dict[str, int] = {}

    for address in sorted(target_by_abs, key=lambda value: int(value, 16)):
        source = target_by_abs[address]
        line = catalog_by_abs.get(address)
        if line is None:
            target_failures.append({"abs": address, "reasons": ["missing_catalog_row"]})
            continue
        logical = int(address, 16)
        expected = str(line.get("ko") or "")
        before_payload, before_term = payload_at(parent, logical)
        after_payload, after_term = payload_at(candidate, logical)
        capacity = int(source.get("payload_len") or -1)
        reasons: list[str] = []
        if before_payload != bytes.fromhex(str(source.get("current_payload_hex") or "")):
            reasons.append("parent_payload_not_bound")
        if len(before_payload) != capacity or len(after_payload) != capacity:
            reasons.append("payload_length_changed")
        if before_term != after_term or parent[before_term] != 0 or candidate[after_term] != 0:
            reasons.append("terminator_changed")
        rendered = candidate_dictionary.expand(after_payload, tbl).rstrip("\u3000 \t")
        if rendered != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(character) for character in rendered):
            reasons.append("japanese_residual")

        if capacity >= 4:
            try:
                page, local = token_page_local(after_payload[:4])
            except AuditError as exc:
                reasons.append(str(exc))
                page = local = -1
            if after_payload[4:] != b"\x01" * (capacity - 4):
                reasons.append("padding_mismatch")
            if page >= 0:
                segment = FIRST_BANK + page
                bank = candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
                pointer = int.from_bytes(bank[local * 2:local * 2 + 2], "little")
                phrase_raw = read_phrase(bank, pointer)
                phrase_rendered = candidate_dictionary.expand(phrase_raw, tbl).rstrip("\u3000 \t")
                if phrase_rendered != expected:
                    reasons.append("bank_phrase_render_mismatch")
                previous_token = ext3_phrase_to_token.get(expected)
                if previous_token is not None and previous_token != (page, local):
                    reasons.append("same_phrase_uses_multiple_tokens")
                previous_phrase = ext3_token_to_phrase.get((page, local))
                if previous_phrase is not None and previous_phrase != expected:
                    reasons.append("token_aliases_different_phrases")
                ext3_phrase_to_token[expected] = (page, local)
                ext3_token_to_phrase[(page, local)] = expected
                ext3_target_locals[page].add(local)
        else:
            if len(after_payload) not in {2, 3} or after_payload[0] < 0xF0:
                reasons.append("short_record_not_stock_token")
            else:
                index = ((after_payload[0] - 0xF0) << 8) | after_payload[1]
                short_indices[address] = index
                if after_payload[2:] != b"\x01" * (capacity - 2):
                    reasons.append("short_padding_mismatch")
                raw = bytes(candidate_dictionary.raw_entry(index))
                if candidate_dictionary.expand(raw, tbl).rstrip("\u3000 \t") != expected:
                    reasons.append("stock_phrase_render_mismatch")

        target_logicals.add(logical)
        target_extents.append((sb + logical, sb + logical + capacity))
        if reasons:
            target_failures.append(
                {"abs": address, "expected": expected, "actual": rendered, "reasons": reasons}
            )

    ext3_records = [address for address in target_by_abs if int(target_by_abs[address]["payload_len"]) >= 4]
    short_records = [address for address in target_by_abs if int(target_by_abs[address]["payload_len"]) < 4]

    parent_page_hits = {page: five.scan_range_hits(parent, page) for page in range(PAGES)}
    candidate_page_hits = {page: five.scan_range_hits(candidate, page) for page in range(PAGES)}
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    selected_bank_checks: dict[str, dict[str, Any]] = {}
    new_local_counts: list[int] = []
    for page in range(PAGES):
        segment = FIRST_BANK + page
        start = segment * BANK_SIZE
        parent_bank = parent[start:start + BANK_SIZE]
        candidate_bank = candidate[start:start + BANK_SIZE]
        old_used: set[int] = set()
        new_used: set[int] = set()
        old_tail = 0x2001
        new_tail = 0x2001
        for local in range(0x1000):
            old_pointer = int.from_bytes(parent_bank[local * 2:local * 2 + 2], "little")
            new_pointer = int.from_bytes(candidate_bank[local * 2:local * 2 + 2], "little")
            if old_pointer != 0x2000:
                old_used.add(local)
                old_tail = max(old_tail, old_pointer + len(read_phrase(parent_bank, old_pointer)) + 1)
            if new_pointer != 0x2000:
                new_used.add(local)
                new_tail = max(new_tail, new_pointer + len(read_phrase(candidate_bank, new_pointer)) + 1)
        new_locals = new_used - old_used
        new_local_counts.append(len(new_locals))
        pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2) for local in sorted(new_locals)
        )
        if new_tail > old_tail:
            phrase_extents.append((start + old_tail, start + new_tail))
        selected_bank_checks[f"{segment:02X}"] = {
            "new_locals": len(new_locals),
            "target_locals": len(ext3_target_locals[page]),
            "target_locals_match": new_locals == ext3_target_locals[page],
            "old_pointer_entries_exact": all(
                parent_bank[local * 2:local * 2 + 2]
                == candidate_bank[local * 2:local * 2 + 2]
                for local in range(0x1000)
                if local not in new_locals
            ),
            "old_phrase_area_exact": parent_bank[0x2000:old_tail] == candidate_bank[0x2000:old_tail],
            "tail_after_new_exact": parent_bank[new_tail:] == candidate_bank[new_tail:],
            "old_tail": f"{old_tail:04X}",
            "new_tail": f"{new_tail:04X}",
        }

    selected_retired = {index for index in short_indices.values() if index in EXPECTED_RETIRED}
    external = external_occurrence_map(candidate, ext3_aware=True, wanted=EXPECTED_RETIRED)
    nested = nested_occurrence_map(candidate_dictionary, wanted=EXPECTED_RETIRED, ext3_aware=True)
    expected_sites: dict[int, set[str]] = {}
    for address, index in short_indices.items():
        if index in EXPECTED_RETIRED:
            expected_sites.setdefault(index, set()).add(address)
    stock_reference_failures: list[dict[str, Any]] = []
    for index in EXPECTED_RETIRED:
        actual = {str(row.get("record_abs") or "").upper() for row in external.get(index, [])}
        if actual != expected_sites.get(index, set()) or nested.get(index):
            stock_reference_failures.append(
                {
                    "index": f"{index:04X}",
                    "expected": sorted(expected_sites.get(index, set())),
                    "actual": sorted(actual),
                    "nested": nested.get(index, []),
                }
            )

    build_allocation = build.get("allocation") or {}
    stock_cursor_before = int(str(build_allocation.get("stock_cursor_before") or "0"), 16)
    stock_cursor_after = int(str(build_allocation.get("stock_cursor_after") or "0"), 16)
    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (
            stock_bank_file + DICT_PTR_START + index * 2,
            stock_bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in EXPECTED_RETIRED
    ]
    stock_phrase_extent = (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )
    runs = diff_runs(parent, candidate)
    allowed = target_extents + pointer_extents + phrase_extents + stock_pointer_extents + [
        stock_phrase_extent,
        (len(parent) - 2, len(parent)),
    ]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    runtime_exact = (
        parent[sb + 0x7A0000:sb + 0x7B0000] == candidate[sb + 0x7A0000:sb + 0x7B0000]
        and parent[sb + 0x7F0000:sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000:sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        == candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    checksum_copy = bytearray(candidate)
    checksum = update_ws_checksum(checksum_copy)
    residual_counts = residual_candidate.get("counts") or {}

    all_catalog_failures: list[str] = []
    for address, line in sorted(catalog_by_abs.items(), key=lambda item: int(item[0], 16)):
        payload, _term = payload_at(candidate, int(address, 16))
        rendered = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        if rendered != str(line.get("ko") or ""):
            all_catalog_failures.append(address)
            if len(all_catalog_failures) >= 20:
                break

    checks = {
        "parent_identity_exact": sha256(parent) == EXPECTED_PARENT,
        "candidate_identity_exact": sha256(candidate) == EXPECTED_CANDIDATE,
        "build_report_bound": (
            build.get("ok") is True
            and str((build.get("candidate") or {}).get("sha256", "")).lower() == EXPECTED_CANDIDATE
        ),
        "targets_603": len(target_by_abs) == EXPECTED_TARGETS,
        "ext3_records_596": len(ext3_records) == EXPECTED_EXT3,
        "short_records_7": len(short_records) == EXPECTED_SHORT,
        "all_targets_exact": not target_failures,
        "all_693_catalog_rows_exact": not all_catalog_failures,
        "ext3_unique_tokens_579": len(ext3_token_to_phrase) == EXPECTED_UNIQUE_EXT3,
        "new_locals_116_116_116_116_115": new_local_counts == EXPECTED_NEW_LOCALS,
        "page_counts_exact": [len(candidate_page_hits[p]) for p in range(PAGES)] == EXPECTED_PAGE_COUNTS,
        "selected_bank_changes_exact": all(
            all(
                bool(info[name])
                for name in (
                    "target_locals_match",
                    "old_pointer_entries_exact",
                    "old_phrase_area_exact",
                    "tail_after_new_exact",
                )
            )
            for info in selected_bank_checks.values()
        ),
        "kim_exact_stock_03bf": short_indices.get("5C096F") == 0x03BF,
        "kiki_exact_stock_09ec": short_indices.get("5C08BA") == 0x09EC,
        "five_retired_slots_exact": selected_retired == EXPECTED_RETIRED,
        "retired_stock_references_exact": not stock_reference_failures,
        "candidate_residual_zero": (
            int(residual_counts.get("actionable_records", -1)) == 0
            and int(residual_counts.get("unreadable_records", -1)) == 0
        ),
        "non_target_invariance": invariance.get("ok") is True,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "checksum_exact": bytes(checksum_copy) == candidate,
        "candidate_saveram_present_and_sized": CANDIDATE_SAVE.is_file()
        and CANDIDATE_SAVE.stat().st_size == 32768,
        "main_tip_unchanged": sha256(PARENT.read_bytes()) == EXPECTED_PARENT,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_character_all_remaining_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_user_visual_test" if ok else "failed",
        "inputs": {
            "parent": identity(PARENT, parent),
            "candidate": identity(CANDIDATE, candidate),
            "candidate_save": identity(CANDIDATE_SAVE),
            "catalog": identity(CATALOG),
            "parent_residual": identity(RESIDUAL_PARENT),
            "candidate_residual": identity(RESIDUAL_CANDIDATE),
            "build_report": identity(BUILD_REPORT),
        },
        "counts": {
            "targets": len(target_by_abs),
            "ext3_records": len(ext3_records),
            "short_records": len(short_records),
            "unique_ext3_tokens": len(ext3_token_to_phrase),
            "target_failures": len(target_failures),
            "all_catalog_failures": len(all_catalog_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "runtime": {
            "parent_page_counts": {str(page): len(parent_page_hits[page]) for page in range(PAGES)},
            "candidate_page_counts": {str(page): len(candidate_page_hits[page]) for page in range(PAGES)},
            "new_local_counts": new_local_counts,
        },
        "selected_banks": selected_bank_checks,
        "short_indices": {address: f"{index:04X}" for address, index in sorted(short_indices.items())},
        "checksum": {"stored_hex": candidate[-2:].hex().upper(), "recomputed": f"{checksum:04X}"},
        "target_failures": target_failures,
        "all_catalog_failures": all_catalog_failures,
        "stock_reference_failures": stock_reference_failures,
        "invariance": invariance,
        "unaccounted_diff_runs": unaccounted,
        "checks": checks,
        "promotion": "blocked_pending_user_visual_verification",
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "status": report["status"],
                "counts": report["counts"],
                "runtime": report["runtime"],
                "short_indices": report["short_indices"],
                "checks": checks,
                "out": str(OUT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not ok:
        raise AuditError("all-remaining character candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
