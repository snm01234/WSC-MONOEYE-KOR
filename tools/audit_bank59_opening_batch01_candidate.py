#!/usr/bin/env python3
"""Independent static audit for bank59_opening_batch01_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_bank59_opening_batch01_candidate as build
import build_ext3_bank21_probe_candidate as runtime
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import BANK_SIZE, Tbl, le16, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/bank59_opening_batch01_candidate.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/bank59_opening_batch01_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/bank59_opening_batch01_report.json"
OUT = ROOT / "out/patch/bank59_opening_batch01_candidate_audit.json"


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def phrase_at(bank: bytes, local: int) -> bytes:
    pointer = le16(bank, local * 2)
    if not 0 <= pointer < BANK_SIZE:
        raise AuditError(f"pointer out of range: {local:04X} -> {pointer:04X}")
    end = pointer
    while end < BANK_SIZE and bank[end] != 0:
        end += 1
    if end >= BANK_SIZE:
        raise AuditError(f"unterminated phrase at local {local:04X}")
    return bank[pointer:end]


def in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def main() -> int:
    report = load_object(BUILD_REPORT)
    catalog = load_object(build.CATALOG)
    gap = load_object(build.GAP_AUDIT)
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    parent_save = PARENT_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(parent) != runtime.ROM_SIZE or len(candidate) != len(parent):
        raise AuditError("ROM size mismatch")

    sb = stock_base(parent)
    tbl = Tbl.load(runtime.TBL_PATH)
    dictionary = make_dictionary_ext3(
        parent,
        load_ext_meta(runtime.EXT_META_PATH),
        load_ext_meta(runtime.EXT3_META_PATH),
    )
    bank21_start = build.BANK21_SEG * BANK_SIZE
    parent_bank21 = parent[bank21_start:bank21_start + BANK_SIZE]
    candidate_bank21 = candidate[bank21_start:bank21_start + BANK_SIZE]
    applied = report.get("applied") or []
    if len(applied) != build.EXPECTED_RECORDS:
        raise AuditError("build report applied count drifted")

    entry_by_abs = {str(row["abs"]).upper(): row for row in catalog.get("entries") or []}
    gap_by_abs = {str(row["abs"]).upper(): row for row in gap.get("meaningful_records") or []}
    target_checks: list[dict[str, Any]] = []
    all_target_ok = True
    target_body_ranges: list[tuple[int, int]] = []
    expected_alias_hits: list[int] = []

    for applied_row in applied:
        address = str(applied_row["abs"]).upper()
        logical = int(address, 16)
        local = int(str(applied_row["local"]), 16)
        token = build.alias_token(local)
        entry = entry_by_abs[address]
        evidence = gap_by_abs[address]
        parent_got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        candidate_got = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if parent_got is None or candidate_got is None:
            raise AuditError(f"unreadable target: {address}")
        parent_payload = bytes(parent_got[0])
        candidate_payload = bytes(candidate_got[0])
        parent_prefix, parent_body, parent_kind = split_prefix_body(parent_payload)
        candidate_prefix, candidate_body, candidate_kind = split_prefix_body(candidate_payload)
        raw = phrase_at(candidate_bank21, local)
        rendered = dictionary.expand(raw, tbl)
        expected_raw = build.encode_phrase(str(entry["ko"]), tbl, dictionary)
        expected_candidate_body = token + bytes([0x01]) * (len(parent_body) - len(token))
        file_start = sb + logical
        body_start = file_start + len(parent_prefix)
        target_body_ranges.append((body_start, file_start + len(parent_payload)))
        expected_alias_hits.append(body_start)

        checks = {
            "parent_payload_matches_gap": parent_payload.hex().upper()
            == str(evidence["prefix_hex"]).upper() + str(evidence["body_hex"]).upper(),
            "parent_kind_dialogue": parent_kind == "dialogue",
            "candidate_kind_dialogue": candidate_kind == "dialogue",
            "prefix_preserved": candidate_prefix == parent_prefix,
            "payload_capacity_preserved": len(candidate_payload) == len(parent_payload),
            "body_capacity_preserved": len(candidate_body) == len(parent_body),
            "candidate_body_exact": candidate_body == expected_candidate_body,
            "terminator_preserved": int(candidate_got[1]) == int(parent_got[1]),
            "token_exact": candidate_body[:4] == token,
            "padding_exact": candidate_body[4:] == bytes([0x01]) * (len(candidate_body) - 4),
            "bank21_pointer_exact": le16(candidate_bank21, local * 2)
            == int(str(applied_row["pointer"]), 16),
            "bank21_raw_exact": raw == expected_raw,
            "bank21_render_exact": rendered == build.normalize_ko_text(str(entry["ko"])),
            "report_render_exact": str(applied_row["ko"]) == rendered,
        }
        ok = all(checks.values())
        all_target_ok = all_target_ok and ok
        target_checks.append(
            {
                "abs": address,
                "local": f"{local:04X}",
                "source": entry["source"],
                "ko": rendered,
                "raw_sha256": sha256(raw),
                "checks": checks,
                "ok": ok,
            }
        )

    # All unused pointers must resolve to the empty string.  Used locals must
    # resolve to unique in-bank phrases.
    used_locals = {int(str(row["local"]), 16) for row in applied}
    pointer_table_ok = True
    pointer_samples: list[dict[str, Any]] = []
    for local in range(build.POINTER_COUNT):
        pointer = le16(candidate_bank21, local * 2)
        expected = None
        if local not in used_locals:
            expected = build.EMPTY_AT
            pointer_table_ok = pointer_table_ok and pointer == expected
        else:
            pointer_table_ok = pointer_table_ok and build.EMPTY_AT < pointer < BANK_SIZE
        if local in {0, 1, 2, 0x1B, 0x1C, 0x09FF, 0x0FFF}:
            pointer_samples.append(
                {
                    "local": f"{local:04X}",
                    "pointer": f"{pointer:04X}",
                    "expected": None if expected is None else f"{expected:04X}",
                }
            )

    leaf = runtime.build_bank21_leaf()
    checksum_copy = bytearray(candidate)
    recomputed_checksum = update_ws_checksum(checksum_copy)
    checksum_exact = bytes(checksum_copy) == candidate

    allowed = [
        (bank21_start, bank21_start + BANK_SIZE),
        (sb + runtime.FREE_CAVE_START, sb + runtime.FREE_CAVE_START + len(leaf)),
        (sb + runtime.LEAF, sb + runtime.LEAF + 6),
        *target_body_ranges,
        (len(parent) - 2, len(parent)),
    ]
    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    unaccounted = [i for i in changed if not in_ranges(i, allowed)]

    # Verify the entire omitted block is byte-identical outside target bodies.
    block_start = sb + 0x590000
    block_end = sb + 0x590244
    block_unexpected = [
        i
        for i in range(block_start, block_end)
        if parent[i] != candidate[i] and not in_ranges(i, target_body_ranges)
    ]

    parent_hits = runtime.reserved_token_hits(parent)
    candidate_hits = runtime.reserved_token_hits(candidate)
    checks = {
        "parent_identity": sha256(parent) == build.EXPECTED_MAIN_SHA256,
        "candidate_identity_matches_report": sha256(candidate)
        == str(report.get("candidate", {}).get("sha256")),
        "candidate_save_initially_byte_exact": candidate_save == parent_save,
        "catalog_fresh_reviewed": catalog.get("translation_source") == "fresh_llm_reviewed"
        and catalog.get("legacy_machine_translation_used") is False,
        "gap_audit_approved": gap.get("ok") is True,
        "target_count_exact": len(target_checks) == build.EXPECTED_RECORDS,
        "all_targets_exact": all_target_ok,
        "parent_bank21_all_ff": all(byte == 0xFF for byte in parent_bank21),
        "bank21_pointer_table_exact": pointer_table_ok,
        "bank21_empty_string_exact": candidate_bank21[build.EMPTY_AT] == 0,
        "bank21_used_phrases_unique": len(
            {le16(candidate_bank21, local * 2) for local in used_locals}
        )
        == len(used_locals),
        "new_leaf_exact": candidate[
            sb + runtime.FREE_CAVE_START:sb + runtime.FREE_CAVE_START + len(leaf)
        ]
        == leaf,
        "leaf_hook_exact": candidate[sb + runtime.LEAF:sb + runtime.LEAF + 6]
        == runtime.far_jmp(runtime.FREE_CAVE_START & 0xFFFF, runtime.EXT_CAVE_SEG) + b"\x90",
        "accepted_old_leaf_body_unchanged": candidate[
            sb + runtime.OLD_LEAF_START:sb + runtime.OLD_LEAF_END
        ]
        == parent[sb + runtime.OLD_LEAF_START:sb + runtime.OLD_LEAF_END],
        "accepted_walkers_unchanged": candidate[
            sb + runtime.WALKER1_START:sb + runtime.FREE_CAVE_START
        ]
        == parent[sb + runtime.WALKER1_START:sb + runtime.FREE_CAVE_START],
        "site1_hook_unchanged": candidate[sb + runtime.SITE1:sb + runtime.SITE1 + 5]
        == parent[sb + runtime.SITE1:sb + runtime.SITE1 + 5]
        == runtime.EXPECTED_SITE1_HOOK,
        "site2_hook_unchanged": candidate[
            sb + runtime.SITE2_FIXED:sb + runtime.SITE2_FIXED + 5
        ]
        == parent[sb + runtime.SITE2_FIXED:sb + runtime.SITE2_FIXED + 5]
        == runtime.EXPECTED_SITE2_HOOK,
        "ext3_banks_11_20_byte_exact": all(
            candidate[seg * BANK_SIZE:(seg + 1) * BANK_SIZE]
            == parent[seg * BANK_SIZE:(seg + 1) * BANK_SIZE]
            for seg in range(0x11, 0x21)
        ),
        "stock_dictionary_bank_byte_exact": candidate[
            sb + 0x5F0000:sb + 0x600000
        ]
        == parent[sb + 0x5F0000:sb + 0x600000],
        "parent_alias_reference_count_zero": parent_hits == [],
        "candidate_alias_references_exact": candidate_hits == expected_alias_hits,
        "block_non_targets_byte_exact": not block_unexpected,
        "checksum_exact": checksum_exact,
        "unaccounted_changed_bytes_zero": not unaccounted,
        "main_rom_still_untouched": sha256(PARENT.read_bytes()) == build.EXPECTED_MAIN_SHA256,
        "main_save_still_available": PARENT_SAVE.is_file(),
    }
    ok = all(checks.values())
    audit = {
        "schema_version": 1,
        "generated_by": "tools/audit_bank59_opening_batch01_candidate.py",
        "read_only": True,
        "ok": ok,
        "parent": {
            "path": str(PARENT.relative_to(ROOT)),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(CANDIDATE.relative_to(ROOT)),
            "sha256": sha256(candidate),
            "stored_checksum_hex": candidate[-2:].hex().upper(),
            "recomputed_checksum": f"{recomputed_checksum:04X}",
        },
        "counts": {
            "targets": len(target_checks),
            "used_bank21_locals": len(used_locals),
            "changed_bytes": len(changed),
            "unaccounted_changed_bytes": len(unaccounted),
            "block_non_target_changed_bytes": len(block_unexpected),
        },
        "bank21": {
            "pointer_samples": pointer_samples,
            "phrase_room_after": int((report.get("bank21") or {}).get("phrase_room_after") or 0),
        },
        "target_checks": target_checks,
        "checks": checks,
        "unaccounted_sample": [f"{i:07X}" for i in unaccounted[:32]],
        "block_unexpected_sample": [f"{i:07X}" for i in block_unexpected[:32]],
    }
    OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": ok,
        "candidate": audit["candidate"],
        "counts": audit["counts"],
        "checks": checks,
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    if not ok:
        raise AuditError("bank59 opening batch01 candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
