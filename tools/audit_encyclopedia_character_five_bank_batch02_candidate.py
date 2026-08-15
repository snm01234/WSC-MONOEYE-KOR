#!/usr/bin/env python3
"""Independent static audit for character five-bank batch02 candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, SEG_DICT, Tbl, load_rom, stock_base, update_ws_checksum

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/encyclopedia_character_five_bank_batch02_candidate.sav"
WORKLIST = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_worklist.json"
VALIDATION = ROOT / "out/patch/encyclopedia_character_current_catalog_validation.json"
BUILD_REPORT = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_candidate_audit.json"

EXPECTED_PARENT_SHA256 = "05a2124e5402c8e8f46b1ca7af3c131748a034939af7c6381cdbb4242d43f65e"
EXPECTED_CANDIDATE_SHA256 = "67f0e7401ec44e9c267ed0e86a010d078edea22afac2370b576fbf169fff26af"
EXPECTED_ROWS = 64
EXPECTED_PAGE_COUNTS = [36, 14, 14, 14, 13]
FIRST_BANK = 0x21
EMPTY_AT = 0x2000


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}


def read_phrase(bank: bytes, pointer: int) -> bytes:
    if not 0 <= pointer < BANK_SIZE:
        raise AuditError(f"pointer outside bank: {pointer:04X}")
    end = pointer
    while end < BANK_SIZE and bank[end] != 0:
        end += 1
    if end >= BANK_SIZE:
        raise AuditError(f"unterminated phrase: {pointer:04X}")
    return bank[pointer:end]


def token_page_local(token: bytes) -> tuple[int, int]:
    if len(token) != 4 or token[:2] != b"\xE5\x18":
        raise AuditError(f"not E5 18 token: {token.hex().upper()}")
    raw = (token[2] << 8) | token[3]
    page = raw >> 12
    local = (raw & 0x0FFF) - 0x0600
    if not 0 <= page < 5 or not 1 <= local < 0x0A00 or (local & 0xFF) == 0:
        raise AuditError(f"unsafe page/local: page={page} local={local:04X}")
    return page, local


def main() -> int:
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    if sha256(parent) != EXPECTED_PARENT_SHA256:
        raise AuditError("parent identity drifted")
    if sha256(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("candidate identity drifted")

    worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
    validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    rows = [dict(row) for row in worklist.get("records") or []]
    if len(rows) != EXPECTED_ROWS or len({str(row.get("abs")) for row in rows}) != EXPECTED_ROWS:
        raise AuditError("worklist population drifted")
    if validation.get("ok") is not True or build.get("ok") is not True:
        raise AuditError("source validation/build evidence is incomplete")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    target_logicals: set[int] = set()
    target_extents: list[tuple[int, int]] = []
    target_locals: dict[int, set[int]] = {page: set() for page in range(5)}
    target_indices: set[tuple[int, int]] = set()
    target_failures: list[dict[str, Any]] = []
    phrase_by_token: dict[tuple[int, int], str] = {}

    for row in sorted(rows, key=lambda item: int(str(item["abs"]), 16)):
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        expected = str(row["ko"])
        before_payload, before_term = payload_at(parent, logical)
        after_payload, after_term = payload_at(candidate, logical)
        capacity = int(row["payload_len"])
        reasons: list[str] = []
        if before_payload != bytes.fromhex(str(row["current_payload_hex"])):
            reasons.append("parent_payload_not_bound")
        if len(before_payload) != len(after_payload) or len(after_payload) != capacity:
            reasons.append("payload_length_changed")
        if before_term != after_term or parent[before_term] != 0 or candidate[after_term] != 0:
            reasons.append("terminator_changed")
        if len(after_payload) < 4 or after_payload[:2] != b"\xE5\x18":
            reasons.append("not_e518")
            page = local = -1
        else:
            try:
                page, local = token_page_local(after_payload[:4])
            except AuditError as exc:
                reasons.append(str(exc))
                page = local = -1
        if after_payload[4:] != b"\x01" * (capacity - 4):
            reasons.append("padding_mismatch")
        rendered = candidate_dictionary.expand(after_payload, tbl).rstrip("\u3000 \t")
        if rendered != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(char) for char in rendered):
            reasons.append("japanese_residual")

        pointer = None
        raw = b""
        if page >= 0:
            segment = FIRST_BANK + page
            bank = candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
            pointer = int.from_bytes(bank[local * 2:local * 2 + 2], "little")
            if pointer == EMPTY_AT:
                reasons.append("pointer_still_empty")
            else:
                raw = read_phrase(bank, pointer)
                if candidate_dictionary.expand(raw, tbl).rstrip("\u3000 \t") != expected:
                    reasons.append("bank_phrase_render_mismatch")
            if local in target_locals[page]:
                previous = phrase_by_token.get((page, local))
                if previous != expected:
                    reasons.append("token_aliases_different_phrases")
            target_locals[page].add(local)
            target_indices.add((page, local))
            phrase_by_token[(page, local)] = expected

        target_logicals.add(logical)
        target_extents.append((sb + logical, sb + logical + capacity))
        if reasons:
            target_failures.append(
                {
                    "abs": address,
                    "expected": expected,
                    "actual": rendered,
                    "page": page,
                    "local": f"{local:04X}" if local >= 0 else None,
                    "pointer": f"{pointer:04X}" if pointer is not None else None,
                    "raw_sha256": sha256(raw) if raw else None,
                    "reasons": reasons,
                }
            )

    page_hits = {page: five.scan_range_hits(candidate, page) for page in range(5)}
    parent_page_hits = {page: five.scan_range_hits(parent, page) for page in range(5)}
    page_reference_counts_exact = [len(page_hits[p]) for p in range(5)] == EXPECTED_PAGE_COUNTS
    page_new_reference_counts = [
        len(page_hits[p]) - len(parent_page_hits[p]) for p in range(5)
    ]

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )

    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    selected_bank_checks: dict[str, dict[str, Any]] = {}
    for page in range(5):
        segment = FIRST_BANK + page
        start = segment * BANK_SIZE
        parent_bank = parent[start:start + BANK_SIZE]
        candidate_bank = candidate[start:start + BANK_SIZE]
        # Derive old/new phrase tails only from populated pointer entries.
        old_tail = EMPTY_AT + 1
        new_tail = EMPTY_AT + 1
        old_used: set[int] = set()
        new_used: set[int] = set()
        for local in range(0x1000):
            old_pointer = int.from_bytes(parent_bank[local * 2:local * 2 + 2], "little")
            new_pointer = int.from_bytes(candidate_bank[local * 2:local * 2 + 2], "little")
            if old_pointer != EMPTY_AT:
                old_used.add(local)
                phrase = read_phrase(parent_bank, old_pointer)
                old_tail = max(old_tail, old_pointer + len(phrase) + 1)
            if new_pointer != EMPTY_AT:
                new_used.add(local)
                phrase = read_phrase(candidate_bank, new_pointer)
                new_tail = max(new_tail, new_pointer + len(phrase) + 1)
        new_locals = new_used - old_used
        for local in new_locals:
            pointer_extents.append((start + local * 2, start + local * 2 + 2))
        if new_tail > old_tail:
            phrase_extents.append((start + old_tail, start + new_tail))
        outside_pointer_exact = all(
            parent_bank[local * 2:local * 2 + 2]
            == candidate_bank[local * 2:local * 2 + 2]
            for local in range(0x1000)
            if local not in new_locals
        )
        prefix_exact = parent_bank[0x2000:old_tail] == candidate_bank[0x2000:old_tail]
        tail_bounded = candidate_bank[new_tail:] == parent_bank[new_tail:]
        selected_bank_checks[f"{segment:02X}"] = {
            "new_locals": len(new_locals),
            "expected_target_locals": len(target_locals[page]),
            "new_locals_exact": new_locals == target_locals[page],
            "non_target_pointers_exact": outside_pointer_exact,
            "existing_phrase_area_exact": prefix_exact,
            "tail_after_new_phrases_exact": tail_bounded,
            "old_tail": f"{old_tail:04X}",
            "new_tail": f"{new_tail:04X}",
        }

    stock_start = sb + SEG_DICT * BANK_SIZE
    stock_exact = parent[stock_start:stock_start + BANK_SIZE] == candidate[stock_start:stock_start + BANK_SIZE]
    old_ext3_exact = all(
        parent[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        == candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    runtime_exact = (
        parent[sb + 0x7A0000:sb + 0x7B0000] == candidate[sb + 0x7A0000:sb + 0x7B0000]
        and parent[sb + 0x7F0000:sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000:sb + 0x800000 - 2]
    )

    runs = diff_runs(parent, candidate)
    allowed = target_extents + pointer_extents + phrase_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    checksum_copy = bytearray(candidate)
    recomputed_checksum = update_ws_checksum(checksum_copy)

    checks = {
        "parent_identity_exact": sha256(parent) == EXPECTED_PARENT_SHA256,
        "candidate_identity_exact": sha256(candidate) == EXPECTED_CANDIDATE_SHA256,
        "source_validation_ok": validation.get("ok") is True,
        "build_report_bound_to_candidate": (
            build.get("ok") is True
            and str((build.get("candidate") or {}).get("sha256", "")).lower()
            == EXPECTED_CANDIDATE_SHA256
        ),
        "population_64": len(rows) == EXPECTED_ROWS,
        "all_targets_exact": not target_failures,
        "unique_target_tokens_64": len(target_indices) == EXPECTED_ROWS,
        "page_reference_counts_exact": page_reference_counts_exact,
        "new_page_references_13_13_13_13_12": page_new_reference_counts == [13, 13, 13, 13, 12],
        "selected_bank_changes_exact": all(
            all(
                bool(info[name])
                for name in (
                    "new_locals_exact",
                    "non_target_pointers_exact",
                    "existing_phrase_area_exact",
                    "tail_after_new_phrases_exact",
                )
            )
            for info in selected_bank_checks.values()
        ),
        "non_target_invariance": invariance.get("ok") is True,
        "stock_dictionary_exact": stock_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "diffs_bounded": not unaccounted,
        "checksum_exact": bytes(checksum_copy) == candidate,
        "candidate_saveram_present_and_sized": CANDIDATE_SAVE.is_file()
        and CANDIDATE_SAVE.stat().st_size == 32768,
        "main_tip_unchanged": sha256(PARENT.read_bytes()) == EXPECTED_PARENT_SHA256,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_character_five_bank_batch02_candidate.py",
        "read_only_rom": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_user_visual_test" if ok else "failed",
        "inputs": {
            "parent": identity(PARENT, parent),
            "candidate": identity(CANDIDATE, candidate),
            "candidate_save": identity(CANDIDATE_SAVE),
            "worklist": identity(WORKLIST),
            "validation": identity(VALIDATION),
            "build_report": identity(BUILD_REPORT),
        },
        "counts": {
            "targets": len(rows),
            "target_failures": len(target_failures),
            "unique_target_tokens": len(target_indices),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "runtime": {
            "page_reference_counts_parent": {
                str(page): len(parent_page_hits[page]) for page in range(5)
            },
            "page_reference_counts_candidate": {
                str(page): len(page_hits[page]) for page in range(5)
            },
            "new_page_references": page_new_reference_counts,
            "new_token": False,
            "runtime_change": False,
            "new_wram_state": False,
        },
        "selected_banks": selected_bank_checks,
        "checksum": {
            "stored_hex": candidate[-2:].hex().upper(),
            "recomputed": f"{recomputed_checksum:04X}",
        },
        "target_failures": target_failures,
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
    print(json.dumps({
        "ok": ok,
        "status": report["status"],
        "counts": report["counts"],
        "runtime": report["runtime"],
        "selected_banks": report["selected_banks"],
        "checks": checks,
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    if not ok:
        raise AuditError("character five-bank batch02 audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
