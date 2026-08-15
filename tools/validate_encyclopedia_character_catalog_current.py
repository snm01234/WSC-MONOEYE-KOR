#!/usr/bin/env python3
"""Validate the full character-encyclopedia catalog against the promoted TIP.

Read-only.  The 693 approved catalog rows must partition exactly into the 90
previously promoted safe rows and the 603 current Japanese residual rows.  Every
translation is rechecked for provenance, source identity, width, encoding, and
current Original-derived record boundary.  The next E5 18 five-bank worklist is
cut at a complete character-entry boundary so a name can never be separated
from its following description.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_remaining_dialogue_candidate import encode_phrase
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CATALOG = ROOT / "data/encyclopedia_character_batch01_ko.json"
RESIDUAL = ROOT / "out/patch/encyclopedia_character_current_residual_audit.json"
SAFE_BUILD = ROOT / "out/patch/encyclopedia_character_safe_batch01_report.json"
SAFE_PROMOTION = ROOT / "out/patch/encyclopedia_character_safe_batch01_promotion_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/encyclopedia_character_current_catalog_validation.json"
WORKLIST = ROOT / "out/patch/encyclopedia_character_five_bank_batch02_worklist.json"

EXPECTED_TIP_SHA256 = "05a2124e5402c8e8f46b1ca7af3c131748a034939af7c6381cdbb4242d43f65e"
EXPECTED_CATALOG_ROWS = 693
EXPECTED_PROMOTED_ROWS = 90
EXPECTED_RESIDUAL_ROWS = 603
BATCH_START = 0x5C064B
BATCH_END_EXCLUSIVE = 0x5C0B98  # next character entry; includes all Gihren Zabi description rows
BATCH_ROWS = 70


class ValidationError(RuntimeError):
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


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationError(f"JSON root must be object: {path}")
    return value


def main() -> int:
    tip = bytes(load_rom(TIP))
    if sha256(tip) != EXPECTED_TIP_SHA256:
        raise ValidationError("promoted five-bank TIP identity drifted")
    catalog = load_object(CATALOG)
    residual = load_object(RESIDUAL)
    safe_build = load_object(SAFE_BUILD)
    safe_promotion = load_object(SAFE_PROMOTION)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )

    provenance = catalog.get("provenance") or {}
    catalog_rows = [dict(row) for row in catalog.get("lines") or []]
    catalog_by_abs = {str(row.get("abs") or "").upper(): row for row in catalog_rows}
    residual_rows = [
        dict(row)
        for row in residual.get("records") or []
        if row.get("status") in {"japanese_residual", "name_alias_mismatch"}
    ]
    residual_by_abs = {str(row.get("abs") or "").upper(): row for row in residual_rows}
    applied_rows = [dict(row) for row in safe_build.get("applied") or []]
    applied_by_abs = {str(row.get("abs") or "").upper(): row for row in applied_rows}

    catalog_set = set(catalog_by_abs)
    residual_set = set(residual_by_abs)
    applied_set = set(applied_by_abs)
    population_checks = {
        "catalog_693_unique": len(catalog_rows) == len(catalog_by_abs) == EXPECTED_CATALOG_ROWS,
        "promoted_90_unique": len(applied_rows) == len(applied_by_abs) == EXPECTED_PROMOTED_ROWS,
        "residual_603_unique": len(residual_rows) == len(residual_by_abs) == EXPECTED_RESIDUAL_ROWS,
        "promoted_and_residual_disjoint": not (applied_set & residual_set),
        "promoted_plus_residual_equals_catalog": applied_set | residual_set == catalog_set,
        "safe_batch_was_published": safe_promotion.get("published") is True,
        "safe_batch_candidate_matches_promoted_tip_history": str(
            (safe_promotion.get("new_tip") or {}).get("sha256", "")
        ).lower() == "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825",
    }

    failures: list[dict[str, Any]] = []
    validated: list[dict[str, Any]] = []
    for address in sorted(catalog_by_abs, key=lambda value: int(value, 16)):
        line = catalog_by_abs[address]
        logical = int(address, 16)
        ko = normalize_ko_text(str(line.get("ko") or ""))
        jp = str(line.get("jp") or "")
        visual_cells = int(line.get("visual_cells") or -1)
        encoded = encode_phrase(ko, tbl) if ko else b""
        reasons: list[str] = []
        if line.get("translation_source") != "llm":
            reasons.append("line_translation_source_not_llm")
        if line.get("review_status") != "approved":
            reasons.append("line_review_status_not_approved")
        if not ko or any(is_japanese_character(character) for character in ko):
            reasons.append("invalid_or_japanese_korean_text")
        if visual_cells != len(ko) or visual_cells > 13:
            reasons.append("visual_cells_mismatch_or_over_13")
        if int(line.get("encoded_bytes") or -1) != len(encoded):
            reasons.append("encoded_byte_count_mismatch")
        if not encoded or b"\x00" in encoded:
            reasons.append("encoding_invalid")

        current_payload, terminator = payload_at(tip, logical)
        if terminator != stock_base(tip) + logical + len(current_payload) or tip[terminator] != 0:
            reasons.append("current_boundary_or_terminator_invalid")
        state: str
        source_row: dict[str, Any]
        if address in applied_by_abs:
            state = "previously_promoted"
            source_row = applied_by_abs[address]
            rendered = dictionary.expand(current_payload, tbl).rstrip("\u3000 \t")
            if rendered != ko:
                reasons.append("promoted_render_mismatch")
            if str(source_row.get("after") or "") != ko:
                reasons.append("promoted_report_text_mismatch")
        else:
            state = "current_residual"
            source_row = residual_by_abs[address]
            if bytes.fromhex(str(source_row.get("current_payload_hex") or "")) != current_payload:
                reasons.append("residual_payload_not_bound_to_tip")
            if int(source_row.get("payload_len") or -1) != len(current_payload):
                reasons.append("residual_payload_length_mismatch")
            if str(source_row.get("jp") or "") != jp:
                reasons.append("catalog_japanese_source_mismatch")
            if int(source_row.get("japanese_count") or 0) <= 0:
                reasons.append("residual_no_japanese")

        record = {
            "abs": address,
            "state": state,
            "jp": jp,
            "ko": ko,
            "visual_cells": visual_cells,
            "encoded_bytes": len(encoded),
            "payload_len": len(current_payload),
            "current_payload_hex": current_payload.hex().upper(),
            "strategy": (
                "five_bank_e518_alias" if state == "current_residual" and len(current_payload) >= 4
                else "short_record_route" if state == "current_residual"
                else "already_promoted"
            ),
            "checks_ok": not reasons,
        }
        validated.append(record)
        if reasons:
            failures.append({**record, "reasons": reasons})

    residual_ext3 = [
        row for row in validated
        if row["state"] == "current_residual" and int(row["payload_len"]) >= 4
    ]
    residual_short = [
        row for row in validated
        if row["state"] == "current_residual" and int(row["payload_len"]) < 4
    ]
    selected = [
        row
        for row in residual_ext3
        if BATCH_START <= int(row["abs"], 16) < BATCH_END_EXCLUSIVE
    ]
    selected_addresses = [row["abs"] for row in selected]
    checks = {
        **population_checks,
        "catalog_provenance_approved_nonlegacy": (
            provenance.get("translation_source") == "llm"
            and provenance.get("review_status") == "approved"
            and provenance.get("legacy_machine_translation_used") is False
        ),
        "residual_report_bound_to_tip": str(
            ((residual.get("inputs") or {}).get("tip") or {}).get("sha256", "")
        ).lower() == EXPECTED_TIP_SHA256,
        "all_693_rows_valid": not failures,
        "residual_ext3_count_596": len(residual_ext3) == 596,
        "residual_short_count_7": len(residual_short) == 7,
        "batch02_has_70_unique_ext3_rows": (
            len(selected) == len(set(selected_addresses)) == BATCH_ROWS
            and all(int(row["payload_len"]) >= 4 for row in selected)
        ),
        "batch02_complete_character_boundary": (
            selected_addresses[0] == "5C064B"
            and selected_addresses[-1] == "5C0B85"
            and all(
                address in selected_addresses
                for address in (
                    "5C0B37", "5C0B3E", "5C0B47", "5C0B59",
                    "5C0B68", "5C0B76", "5C0B85",
                )
            )
        ),
        "tip_unchanged": sha256(TIP.read_bytes()) == EXPECTED_TIP_SHA256,
    }
    ok = all(checks.values())

    report = {
        "schema_version": 1,
        "generated_by": "tools/validate_encyclopedia_character_catalog_current.py",
        "read_only": True,
        "ok": ok,
        "tip": identity(TIP, tip),
        "catalog": identity(CATALOG),
        "catalog_provenance": provenance,
        "previous_safe_promotion": identity(SAFE_PROMOTION),
        "current_residual_audit": identity(RESIDUAL),
        "counts": {
            "catalog_rows": len(catalog_rows),
            "previously_promoted": len(applied_rows),
            "current_residual": len(residual_rows),
            "current_residual_ext3": len(residual_ext3),
            "current_residual_short": len(residual_short),
            "batch02_selected": len(selected),
            "validation_failures": len(failures),
            "remaining_after_batch02_ext3": len(residual_ext3) - len(selected),
        },
        "capacity_demand": {
            "residual_unique_phrases_worst_case": len({row["ko"] for row in residual_ext3}),
            "residual_phrase_bytes_including_nul": sum(
                len(encode_phrase(text, tbl)) + 1 for text in {row["ko"] for row in residual_ext3}
            ),
            "batch02_unique_phrases": len({row["ko"] for row in selected}),
            "batch02_phrase_bytes_including_nul": sum(
                len(encode_phrase(text, tbl)) + 1 for text in {row["ko"] for row in selected}
            ),
        },
        "short_records": residual_short,
        "batch02": selected,
        "failures": failures[:100],
        "checks": checks,
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    worklist = {
        "schema_version": 1,
        "generated_by": "tools/validate_encyclopedia_character_catalog_current.py",
        "read_only": True,
        "ok": ok,
        "tip": identity(TIP, tip),
        "catalog": identity(CATALOG),
        "catalog_validation": identity(OUT),
        "batch": "encyclopedia_character_five_bank_batch02",
        "policy": {
            "records": BATCH_ROWS,
            "selection": "all current residual records with payload_len >= 4 in 5C064B..5C0B97; complete through the Gihren Zabi description and stop before the next character entry",
            "token": "existing E5 18 xx yy five-bank alias only",
            "physical_banks": ["21", "22", "23", "24", "25"],
            "new_token": False,
            "runtime_change": False,
            "stock_dictionary_change": False,
            "short_records_included": False,
        },
        "records": selected,
    }
    WORKLIST.write_text(
        json.dumps(worklist, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(json.dumps({
        "ok": ok,
        "counts": report["counts"],
        "capacity_demand": report["capacity_demand"],
        "short_records": [row["abs"] for row in residual_short],
        "batch02_range": [selected[0]["abs"], selected[-1]["abs"]] if selected else [],
        "out": str(OUT.relative_to(ROOT)),
        "worklist": str(WORKLIST.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    if not ok:
        raise ValidationError("current character catalog validation failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
