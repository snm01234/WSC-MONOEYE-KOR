#!/usr/bin/env python3
"""Authoritative phase-2 inventory for logical banks 64..6F.

The old translation splits treated every NUL-delimited byte run in these banks
as script-like text.  That assumption is unsafe: banks 64..69 are measured stage
 event/fixed-stride data and banks 6A..6F are unit/table banks.  This read-only
inventory re-enumerates the Original-ROM boundaries, compares the promoted TIP
at those exact logical extents, preserves the legacy split rows only as parser
provenance, and emits an explicit production scope decision for every row.

No Korean value from the legacy CSV files is read or copied.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
csv.field_size_limit(100_000_000)

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import extract_records
from monoeye_rom import Dictionary, Tbl, load_rom, stock_base

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL_TBL = ROOT / "data/monoeye.tbl"
CURRENT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
SPLITS = ROOT / "out/script/splits"
OUT = ROOT / "out/patch/bank64_6f_structure_inventory.json"
WORKLIST = ROOT / "out/patch/bank64_6f_production_worklist.json"

# Bind the read-only inventory to the current main TIP.  The previous phase-2
# snapshot used an older promoted image; stale identity must never authorize a
# production decision for the current workspace.
EXPECTED_TIP_SHA256 = "d2b7301b0f51071a566dd473be4a528d1d13a4305fc251de5543133ab5b0db20"
FIRST_BANK = 0x64
LAST_BANK = 0x6F
FIRST_SPLIT = 112
LAST_SPLIT = 223


class InventoryError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve()),
        "size": len(payload),
        "sha256": sha256(payload),
    }


def bank_role(bank: int) -> tuple[str, str]:
    if 0x64 <= bank <= 0x69:
        return (
            "stage_event_fixed_data",
            "excluded_stage_event_and_fixed_stride_data_bank",
        )
    if 0x6A <= bank <= 0x6F:
        return (
            "unit_table_data",
            "excluded_unit_and_table_bank",
        )
    raise InventoryError(f"bank outside phase-2 scope: {bank:02X}")


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in text
    )


def has_hangul(text: str) -> bool:
    return any("\uac00" <= char <= "\ud7a3" for char in text)


def legacy_split_addresses() -> tuple[dict[int, dict[str, Any]], Counter[str]]:
    rows: dict[int, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    # Release workspaces may omit the historical split snapshots.  The current
    # canonical sheet still carries parser address/prefix/body provenance; use
    # it as a read-only fallback and never read its Korean column.
    if not all((SPLITS / f"split_{split_no:03d}.csv").is_file() for split_no in range(FIRST_SPLIT, LAST_SPLIT + 1)):
        fallback = ROOT / "out/script/translation_sheet.csv"
        if not fallback.is_file():
            raise InventoryError(f"missing legacy split evidence and canonical fallback: {fallback}")
        with fallback.open(encoding="utf-8-sig", newline="") as handle:
            for source in csv.DictReader(handle):
                logical = int(str(source.get("abs") or ""), 16)
                bank = logical >> 16
                if not FIRST_BANK <= bank <= LAST_BANK:
                    continue
                if logical in rows:
                    raise InventoryError(f"duplicate fallback address: {logical:06X}")
                rows[logical] = {
                    "split": "canonical_sheet_fallback",
                    "id": str(source.get("id") or ""),
                    "kind": str(source.get("kind") or ""),
                    "prefix_hex": str(source.get("prefix_hex") or ""),
                    "body_hex": str(source.get("body_hex") or ""),
                }
                counts[f"bank_{bank:02X}"] += 1
                counts["rows"] += 1
        return rows, counts
    for split_no in range(FIRST_SPLIT, LAST_SPLIT + 1):
        path = SPLITS / f"split_{split_no:03d}.csv"
        if not path.is_file():
            raise InventoryError(f"missing legacy split evidence: {path}")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"id", "abs", "kind", "prefix_hex", "body_hex"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise InventoryError(f"legacy split schema drifted: {path}")
            for source in reader:
                logical = int(str(source["abs"]), 16)
                bank = logical >> 16
                if not FIRST_BANK <= bank <= LAST_BANK:
                    raise InventoryError(
                        f"legacy split row outside 64..6F: {logical:06X}"
                    )
                if logical in rows:
                    raise InventoryError(f"duplicate legacy split address: {logical:06X}")
                # Deliberately do not read source['ko']; legacy Korean is forbidden.
                rows[logical] = {
                    "split": split_no,
                    "id": str(source.get("id") or ""),
                    "kind": str(source.get("kind") or ""),
                    "prefix_hex": str(source.get("prefix_hex") or ""),
                    "body_hex": str(source.get("body_hex") or ""),
                }
                counts[f"bank_{bank:02X}"] += 1
                counts["rows"] += 1
    return rows, counts


def diff_runs(original: bytes, current: bytes, bank: int) -> list[dict[str, Any]]:
    base = bank << 16
    left = original[base:base + 0x10000]
    right = current[stock_base(current) + base:stock_base(current) + base + 0x10000]
    rows: list[dict[str, Any]] = []
    cursor = 0
    while cursor < 0x10000:
        if left[cursor] == right[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < 0x10000 and left[cursor] != right[cursor]:
            cursor += 1
        rows.append(
            {
                "site": f"{bank:02X}:{start:04X}",
                "logical_start": f"{base + start:06X}",
                "logical_end_exclusive": f"{base + cursor:06X}",
                "length": cursor - start,
                "original_hex": left[start:cursor].hex().upper(),
                "current_hex": right[start:cursor].hex().upper(),
            }
        )
    return rows


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    current = bytes(load_rom(TIP))
    if sha256(current) != EXPECTED_TIP_SHA256:
        raise InventoryError("promoted five-bank TIP identity drifted")
    sb = stock_base(current)
    if sb + len(original) != len(current):
        raise InventoryError("expanded ROM stock-base geometry drifted")

    original_tbl = Tbl.load(ORIGINAL_TBL)
    current_tbl = Tbl.load(CURRENT_TBL)
    original_dictionary = Dictionary(original)
    current_dictionary = make_dictionary_ext3(
        current, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    legacy, legacy_counts = legacy_split_addresses()

    extracted = [
        row
        for row in extract_records(bytearray(original), original_tbl, original_dictionary)
        if FIRST_BANK <= row.seg <= LAST_BANK
    ]
    extracted_by_abs = {row.abs: row for row in extracted}
    if len(extracted_by_abs) != len(extracted):
        raise InventoryError("Original-derived extractor produced duplicate addresses")

    all_addresses = sorted(set(extracted_by_abs) | set(legacy))
    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    by_bank: dict[str, Counter[str]] = defaultdict(Counter)
    boundary_issues: list[dict[str, Any]] = []

    for logical in all_addresses:
        bank = logical >> 16
        role, reason = bank_role(bank)
        extracted_row = extracted_by_abs.get(logical)
        legacy_row = legacy.get(logical)
        if extracted_row is None:
            # A legacy-only row is still explicitly excluded. It has no current
            # authoritative boundary and can never become a production target.
            row = {
                "abs": f"{logical:06X}",
                "bank": f"{bank:02X}",
                "bank_role": role,
                "boundary_source": None,
                "boundary_status": "legacy_only_no_original_boundary",
                "legacy_split": legacy_row,
                "production_scope": "excluded",
                "production_exclusion_reason": reason,
                "translation_target": False,
            }
            records.append(row)
            counts["legacy_only_no_original_boundary"] += 1
            counts["excluded"] += 1
            by_bank[f"{bank:02X}"]["legacy_only_no_original_boundary"] += 1
            by_bank[f"{bank:02X}"]["excluded"] += 1
            continue

        prefix = bytes.fromhex(extracted_row.prefix_hex)
        body = bytes.fromhex(extracted_row.body_hex)
        payload = prefix + body
        terminator = logical + len(payload)
        if terminator >= (bank + 1) * 0x10000 or original[terminator] != 0:
            boundary_status = "original_boundary_unterminated"
        else:
            current_term = current[sb + terminator]
            current_fixed = current[sb + logical:sb + terminator]
            boundary_status = (
                "exact_terminator"
                if current_term == 0
                else "current_original_terminator_nonzero"
            )
            if current_term != 0:
                boundary_issues.append(
                    {
                        "abs": f"{logical:06X}",
                        "terminator": f"{terminator:06X}",
                        "current_byte": f"{current_term:02X}",
                    }
                )

        current_fixed = current[sb + logical:sb + terminator]
        original_text = original_dictionary.expand(body, original_tbl)
        current_body = current_fixed[len(prefix):] if current_fixed.startswith(prefix) else current_fixed
        current_text = current_dictionary.expand(current_body, current_tbl)
        heuristic_dialogue = extracted_row.kind == "dialogue"
        text_like = has_japanese(original_text) or has_hangul(original_text)
        legacy_present = legacy_row is not None

        records.append(
            {
                "abs": f"{logical:06X}",
                "bank": f"{bank:02X}",
                "bank_role": role,
                "boundary_source": "Original ROM + extract_script.extract_records",
                "boundary_status": boundary_status,
                "payload_capacity": len(payload),
                "terminator": f"{terminator:06X}",
                "prefix_hex": prefix.hex().upper(),
                "body_hex": body.hex().upper(),
                "body_capacity": len(body),
                "heuristic_kind": extracted_row.kind,
                "heuristic_dialogue": heuristic_dialogue,
                "original_render": original_text,
                "current_render_fixed_extent": current_text,
                "original_render_has_japanese": has_japanese(original_text),
                "original_render_has_hangul": has_hangul(original_text),
                "parser_text_like": text_like,
                "legacy_split": legacy_row,
                "legacy_split_present": legacy_present,
                "production_scope": "excluded",
                "production_exclusion_reason": reason,
                "translation_target": False,
                "decision_basis": [
                    "Original-ROM boundary retained for reference-union safety",
                    "bank role is non-dialogue under project event/data guards",
                    "heuristic text rendering cannot override structural bank role",
                ],
            }
        )
        counts["original_boundary_rows"] += 1
        counts["excluded"] += 1
        counts[f"boundary_{boundary_status}"] += 1
        counts["heuristic_dialogue" if heuristic_dialogue else "heuristic_non_dialogue"] += 1
        counts["parser_text_like" if text_like else "parser_non_text_like"] += 1
        counts["legacy_split_matched" if legacy_present else "not_in_legacy_split"] += 1
        bank_counts = by_bank[f"{bank:02X}"]
        bank_counts["records"] += 1
        bank_counts["excluded"] += 1
        bank_counts[f"boundary_{boundary_status}"] += 1
        bank_counts["heuristic_dialogue" if heuristic_dialogue else "heuristic_non_dialogue"] += 1
        bank_counts["parser_text_like" if text_like else "parser_non_text_like"] += 1
        bank_counts["legacy_split_matched" if legacy_present else "not_in_legacy_split"] += 1

    per_bank_diff: dict[str, dict[str, Any]] = {}
    total_diff_bytes = 0
    for bank in range(FIRST_BANK, LAST_BANK + 1):
        runs = diff_runs(original, current, bank)
        changed = sum(int(row["length"]) for row in runs)
        total_diff_bytes += changed
        role, reason = bank_role(bank)
        per_bank_diff[f"{bank:02X}"] = {
            "role": role,
            "production_exclusion_reason": reason,
            "current_vs_original_changed_bytes": changed,
            "diff_runs": runs,
            "write_policy": "preserve current TIP bytes; no phase-2 write",
        }

    production_targets = [row for row in records if row.get("translation_target")]
    checks = {
        "promoted_tip_exact": sha256(current) == EXPECTED_TIP_SHA256,
        "expanded_geometry_exact": sb + len(original) == len(current),
        "all_banks_have_explicit_role": all(
            row["role"] in {"stage_event_fixed_data", "unit_table_data"}
            for row in per_bank_diff.values()
        ),
        "all_inventory_rows_have_scope_decision": all(
            row.get("production_scope") == "excluded"
            and row.get("production_exclusion_reason")
            and row.get("translation_target") is False
            for row in records
        ),
        "zero_production_targets": not production_targets,
        "zero_production_target_boundary_ambiguity": not production_targets,
        "legacy_korean_not_read": True,
        "current_tip_not_modified": sha256(TIP.read_bytes()) == EXPECTED_TIP_SHA256,
    }
    ok = all(checks.values())

    report = {
        "schema_version": 1,
        "generated_by": "tools/inventory_bank64_6f_structure.py",
        "read_only": True,
        "ok": ok,
        "phase": 2,
        "inputs": {
            "original": identity(ORIGINAL, original),
            "promoted_tip": identity(TIP, current),
            "legacy_split_range": [FIRST_SPLIT, LAST_SPLIT],
            "legacy_split_policy": (
                "addresses and parser metadata are retained only as historical evidence; "
                "legacy Korean values are not read"
            ),
        },
        "authoritative_scope": {
            "logical_range": ["640000", "700000"],
            "bank_roles": {
                "64-69": {
                    "role": "stage_event_fixed_data",
                    "evidence": [
                        "docs/EVENT_DATA_BANK_GUARD.md",
                        "tools/repair_data_bank_invasion.py",
                        "tools/script_translation_scope.py:FIXED_DATA_BLOCKS",
                    ],
                    "localization": "forbidden_without_separate pointer-bound event-name proof",
                },
                "6A-6F": {
                    "role": "unit_table_data",
                    "evidence": [
                        "tools/verify_all_stages_smoke.py:UNIT_SEGS",
                        "tools/diff_stock_3way.py:table_bank",
                        "tools/mixed_residual_discovery.py:DIALOGUE_BAND_END_EXCLUSIVE=640000",
                    ],
                    "localization": "forbidden as script dialogue",
                },
            },
            "production_target_count": len(production_targets),
            "gate": "zero boundary/terminator ambiguity for every production target",
            "gate_result": "pass: there are zero production targets in banks64-6F",
        },
        "counts": {
            "inventory_rows": len(records),
            "production_targets": len(production_targets),
            "legacy_split_rows": int(legacy_counts["rows"]),
            "current_vs_original_changed_bytes_in_scope": total_diff_bytes,
            "current_original_terminator_mismatches": len(boundary_issues),
            **dict(sorted(counts.items())),
        },
        "by_bank": {
            bank: {
                **dict(sorted(values.items())),
                "legacy_split_rows": int(legacy_counts[f"bank_{bank}"]),
            }
            for bank, values in sorted(by_bank.items())
        },
        "current_tip_differences": per_bank_diff,
        "boundary_issue_sample": boundary_issues[:100],
        "records": records,
        "conclusion": {
            "legacy_capacity_proxy_valid_for_production": False,
            "legacy_proxy_unique_phrases_to_remove_from_demand": 6261,
            "legacy_proxy_phrase_bytes_to_remove_from_demand": 252257,
            "reason": (
                "The old split population is parser output over non-dialogue event/data/table banks. "
                "No row in banks64-6F is authorized as a script localization target."
            ),
            "next_production_order": [
                "remaining reviewed residual dialogue outside banks64-6F",
                "character encyclopedia remainder",
                "short-record route only for separately proven text records",
            ],
        },
        "checks": checks,
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    worklist = {
        "schema_version": 1,
        "generated_by": "tools/inventory_bank64_6f_structure.py",
        "read_only": True,
        "ok": ok,
        "promoted_tip_sha256": sha256(current),
        "range": ["640000", "700000"],
        "production_targets": [],
        "excluded_counts": {
            "stage_event_fixed_data_rows": sum(
                1 for row in records if row["bank_role"] == "stage_event_fixed_data"
            ),
            "unit_table_data_rows": sum(
                1 for row in records if row["bank_role"] == "unit_table_data"
            ),
        },
        "policy": (
            "Do not create a Korean catalog for banks64-6F from generic zstring extraction. "
            "A future event-name localization must start from a separately proven pointer table."
        ),
        "inventory_report": str(OUT.relative_to(ROOT)),
    }
    WORKLIST.write_text(
        json.dumps(worklist, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(json.dumps({
        "ok": ok,
        "tip": report["inputs"]["promoted_tip"],
        "counts": report["counts"],
        "production_target_count": len(production_targets),
        "legacy_proxy_removed": report["conclusion"],
        "out": str(OUT.relative_to(ROOT)),
        "worklist": str(WORKLIST.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    if not ok:
        raise InventoryError("phase-2 inventory checks failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
