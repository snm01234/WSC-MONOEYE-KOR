#!/usr/bin/env python3
"""Independent static audit for the five-bank E5 18 runtime probe candidate."""
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
import build_ext3_five_bank_runtime_probe_candidate as build
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import BANK_SIZE, Tbl, le16, load_rom, stock_base, update_ws_checksum

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/ext3_five_bank_runtime_probe_candidate.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/ext3_five_bank_runtime_probe_candidate.sav"
BANK59_REPORT = ROOT / "out/patch/bank59_opening_batch01_report.json"
BUILD_REPORT = ROOT / "out/patch/ext3_five_bank_runtime_probe_report.json"
OUT = ROOT / "out/patch/ext3_five_bank_runtime_probe_candidate_audit.json"


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def diff_offsets(before: bytes, after: bytes) -> list[int]:
    return [offset for offset, (left, right) in enumerate(zip(before, after)) if left != right]


def in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def main() -> int:
    report = load_object(BUILD_REPORT)
    bank59 = load_object(BANK59_REPORT)
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    if len(parent) != len(candidate) or len(parent) != build.ROM_SIZE:
        raise AuditError("ROM size mismatch")

    parent_save = PARENT_SAVE.read_bytes() if PARENT_SAVE.is_file() else b""
    candidate_save = CANDIDATE_SAVE.read_bytes() if CANDIDATE_SAVE.is_file() else b""
    sb = stock_base(parent)
    current_leaf = one.build_bank21_leaf()
    expected_leaf = five.build_five_bank_leaf()
    leaf_start = sb + one.FREE_CAVE_START

    applied = {
        str(row["abs"]).upper(): row
        for row in (bank59.get("applied") or [])
        if isinstance(row, dict) and "abs" in row
    }
    bank21_start = 0x21 * BANK_SIZE
    parent_bank21 = parent[bank21_start:bank21_start + BANK_SIZE]
    candidate_bank21 = candidate[bank21_start:bank21_start + BANK_SIZE]

    ext_meta = load_ext_meta(one.EXT_META_PATH)
    ext3_meta = load_ext_meta(one.EXT3_META_PATH)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    tbl = Tbl.load(one.TBL_PATH)

    probe_checks: list[dict[str, Any]] = []
    expected_banks: dict[int, bytes] = {}
    target_ranges: list[tuple[int, int]] = []
    expected_page_hits: dict[int, list[int]] = {0: []}
    probes_ok = True

    for spec in build.PROBES:
        address = str(spec["abs"]).upper()
        row = applied.get(address)
        if row is None:
            raise AuditError(f"missing bank59 evidence for {address}")
        local = int(spec["local"])
        page = int(spec["page"])
        segment = int(spec["bank"])
        logical = int(address, 16)
        file_start = sb + logical
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        parent_payload = bytes.fromhex(str(row["after_payload"]))
        body_start = file_start + len(prefix)
        target_token = build.alias_token(page, local)
        expected_payload = bytearray(parent_payload)
        expected_payload[len(prefix):len(prefix) + 4] = target_token
        target_ranges.append((body_start, body_start + 4))
        expected_page_hits[page] = [body_start]

        source_pointer, source_phrase = build.raw_phrase(parent_bank21, local)
        expected_bank, bank_meta = build.format_probe_bank(source_phrase, local)
        expected_banks[segment] = expected_bank
        candidate_bank = candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        target_pointer, target_phrase = build.raw_phrase(candidate_bank, local)
        rendered_source = dictionary.expand(source_phrase, tbl)
        rendered_target = dictionary.expand(target_phrase, tbl)
        terminator = sb + int(str(row["terminator"]), 16)

        checks = {
            "parent_payload_exact": parent[file_start:file_start + len(parent_payload)] == parent_payload,
            "candidate_payload_exact": candidate[file_start:file_start + len(expected_payload)] == bytes(expected_payload),
            "prefix_unchanged": candidate[file_start:body_start] == parent[file_start:body_start] == prefix,
            "padding_unchanged": candidate[body_start + 4:file_start + len(parent_payload)]
            == parent[body_start + 4:file_start + len(parent_payload)],
            "terminator_unchanged": candidate[terminator] == parent[terminator] == 0,
            "source_token_exact": parent[body_start:body_start + 4] == build.alias_token(0, local),
            "target_token_exact": candidate[body_start:body_start + 4] == target_token,
            "target_bank_byte_exact": candidate_bank == expected_bank,
            "pointer_table_local_exact": target_pointer == build.EMPTY_AT + 1,
            "source_phrase_byte_exact": target_phrase == source_phrase,
            "rendered_text_exact": rendered_target == rendered_source == str(row["ko"]),
            "empty_string_exact": candidate_bank[build.EMPTY_AT] == 0,
            "all_nonprobe_pointers_empty": all(
                le16(candidate_bank, index * 2) == (
                    build.EMPTY_AT + 1 if index == local else build.EMPTY_AT
                )
                for index in range(build.POINTER_COUNT)
            ),
        }
        row_ok = all(checks.values())
        probes_ok = probes_ok and row_ok
        probe_checks.append(
            {
                "abs": address,
                "page": page,
                "bank": f"{segment:02X}",
                "local": f"{local:04X}",
                "source_pointer": f"{source_pointer:04X}",
                "target_pointer": f"{target_pointer:04X}",
                "source_token": build.alias_token(0, local).hex().upper(),
                "target_token": target_token.hex().upper(),
                "phrase_sha256": sha256(source_phrase),
                "rendered_text": rendered_target,
                "bank_meta": bank_meta,
                "checks": checks,
                "ok": row_ok,
            }
        )

    checksum_copy = bytearray(candidate)
    recomputed_checksum = update_ws_checksum(checksum_copy)
    checksum_exact = bytes(checksum_copy) == candidate

    allowed = [
        (leaf_start, leaf_start + len(current_leaf)),
        *((segment * BANK_SIZE, (segment + 1) * BANK_SIZE) for segment in range(0x22, 0x26)),
        *target_ranges,
        (len(parent) - 2, len(parent)),
    ]
    changed = diff_offsets(parent, candidate)
    unaccounted = [offset for offset in changed if not in_ranges(offset, allowed)]
    page_hits = {page: five.scan_range_hits(candidate, page) for page in range(five.PAGE_COUNT)}

    stock_start = sb + 0x5F0000
    checks = {
        "parent_identity": sha256(parent) == build.EXPECTED_MAIN_SHA256,
        "candidate_identity_matches_report": sha256(candidate)
        == str((report.get("candidate") or {}).get("sha256", "")),
        "parent_save_present": len(parent_save) > 0,
        "candidate_save_present": len(candidate_save) > 0,
        "candidate_save_size_matches_main": len(candidate_save) == len(parent_save),
        "save_hash_not_used_as_promotion_gate": True,
        "parent_one_bank_leaf_exact": parent[leaf_start:leaf_start + len(current_leaf)] == current_leaf,
        "candidate_five_bank_leaf_exact": candidate[leaf_start:leaf_start + len(expected_leaf)] == expected_leaf,
        "shorter_leaf_tail_cleared_to_ff": candidate[
            leaf_start + len(expected_leaf):leaf_start + len(current_leaf)
        ] == b"\xFF" * (len(current_leaf) - len(expected_leaf)),
        "remaining_cave_tail_byte_exact": candidate[
            leaf_start + len(current_leaf):sb + one.FREE_CAVE_END
        ] == parent[leaf_start + len(current_leaf):sb + one.FREE_CAVE_END],
        "leaf_hook_unchanged": candidate[sb + one.LEAF:sb + one.LEAF + 6]
        == parent[sb + one.LEAF:sb + one.LEAF + 6]
        == one.far_jmp(one.FREE_CAVE_START & 0xFFFF, one.EXT_CAVE_SEG) + b"\x90",
        "site1_hook_unchanged": candidate[sb + one.SITE1:sb + one.SITE1 + 5]
        == parent[sb + one.SITE1:sb + one.SITE1 + 5]
        == one.EXPECTED_SITE1_HOOK,
        "site2_hook_unchanged": candidate[sb + one.SITE2_FIXED:sb + one.SITE2_FIXED + 5]
        == parent[sb + one.SITE2_FIXED:sb + one.SITE2_FIXED + 5]
        == one.EXPECTED_SITE2_HOOK,
        "accepted_walkers_unchanged": candidate[sb + one.WALKER1_START:sb + one.FREE_CAVE_START]
        == parent[sb + one.WALKER1_START:sb + one.FREE_CAVE_START],
        "accepted_old_leaf_body_unchanged": candidate[sb + one.OLD_LEAF_START:sb + one.OLD_LEAF_END]
        == parent[sb + one.OLD_LEAF_START:sb + one.OLD_LEAF_END],
        "bank21_byte_exact": candidate_bank21 == parent_bank21,
        "all_four_probe_banks_exact": all(
            candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE] == expected_banks[segment]
            for segment in range(0x22, 0x26)
        ),
        "all_probe_records_exact": probes_ok,
        "page0_reference_count_23": len(page_hits[0]) == 23,
        "page1_4_each_exactly_one_probe": all(
            page_hits[page] == expected_page_hits[page]
            for page in range(1, five.PAGE_COUNT)
        ),
        "ext3_banks_11_20_byte_exact": all(
            candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
            == parent[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
            for segment in range(0x11, 0x21)
        ),
        "stock_dictionary_bank_byte_exact": candidate[stock_start:stock_start + BANK_SIZE]
        == parent[stock_start:stock_start + BANK_SIZE],
        "checksum_exact": checksum_exact,
        "unaccounted_changed_bytes_zero": not unaccounted,
        "main_rom_still_untouched": sha256(PARENT.read_bytes()) == build.EXPECTED_MAIN_SHA256,
    }
    ok = all(checks.values())

    audit = {
        "schema_version": 1,
        "generated_by": "tools/audit_ext3_five_bank_runtime_probe_candidate.py",
        "read_only": True,
        "ok": ok,
        "phase": 1,
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
        "save": {
            "main_size": len(parent_save),
            "candidate_size": len(candidate_save),
            "main_sha256_at_audit": sha256(parent_save) if parent_save else None,
            "candidate_sha256_at_audit": sha256(candidate_save) if candidate_save else None,
            "byte_exact_at_audit": candidate_save == parent_save,
            "gate_policy": "candidate SaveRAM content/hash is mutable and is not a promotion blocker",
        },
        "runtime": {
            "one_bank_leaf_length": len(current_leaf),
            "five_bank_leaf_length": len(expected_leaf),
            "five_bank_leaf_sha256": sha256(expected_leaf),
            "page_reference_counts": {str(page): len(hits) for page, hits in page_hits.items()},
            "new_token_added": False,
            "new_parser_added": False,
            "new_wram_state_added": False,
        },
        "probes": probe_checks,
        "changes": {
            "changed_bytes": len(changed),
            "unaccounted_changed_bytes": len(unaccounted),
            "unaccounted_sample": [f"{offset:07X}" for offset in unaccounted[:32]],
        },
        "checks": checks,
    }
    OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": ok,
        "candidate": audit["candidate"],
        "page_reference_counts": audit["runtime"]["page_reference_counts"],
        "probe_results": [
            {"abs": row["abs"], "bank": row["bank"], "ok": row["ok"]}
            for row in probe_checks
        ],
        "changed_bytes": len(changed),
        "unaccounted_changed_bytes": len(unaccounted),
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    if not ok:
        raise AuditError("five-bank runtime probe candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
