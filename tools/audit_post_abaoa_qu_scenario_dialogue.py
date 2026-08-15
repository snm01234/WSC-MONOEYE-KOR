#!/usr/bin/env python3
"""Audit the post-A Baoa Qu scenario dialogue scope on the promoted TIP.

This is read-only.  It joins Original-ROM dialogue extraction rows to the
Original-derived manifest, applies the central script translation exclusions,
and verifies the approved post-anchor catalog against the current ROM.  It does
not consume legacy midgame Korean translation files.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from mixed_residual_classification import (
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text
from script_translation_scope import translation_exclusion_reason

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL_DIALOGUE = ROOT / "out/script/dialogue_db.json"
MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"
APPROVED = ROOT / "data/mixed_residual_translations.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/post_abaoa_qu_scenario_dialogue_audit.json"
WORKLIST = ROOT / "out/patch/post_abaoa_qu_scenario_production_worklist.json"

EXPECTED_TIP_SHA256 = "186d6d04859146420f4ae826a38a0d35377633d54587046c30046125cbceb241"
ANCHOR = 0x60B57E
END_EXCLUSIVE = 0x640000
EXPECTED_APPROVED = 458
MAX_PLAUSIBLE_BODY_BYTES = 32
MAX_PLAUSIBLE_RENDERED_CHARS = 64


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


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def walk_manifest_records(root: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    stack = [root]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            address = value.get("abs")
            boundary = value.get("boundary")
            if (
                value.get("region") == "script"
                and isinstance(address, str)
                and isinstance(boundary, dict)
                and "payload_capacity" in boundary
            ):
                logical = int(address, 16)
                previous = result.get(logical)
                if previous is not None and previous != value:
                    raise AuditError(f"duplicate manifest record {logical:06X}")
                result[logical] = value
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return result


def decode_current_body(
    rom: bytes,
    logical: int,
    record: dict[str, Any],
    dictionary: Any,
    tbl: Tbl,
) -> tuple[bytes, bytes, str, str]:
    sb = stock_base(rom)
    capacity = int(record["boundary"]["payload_capacity"])
    payload = rom[sb + logical : sb + logical + capacity]
    if len(payload) != capacity:
        raise AuditError(f"record outside ROM: {logical:06X}")
    manifest_prefix = bytes.fromhex(str(record.get("prefix_hex") or ""))
    if manifest_prefix and payload.startswith(manifest_prefix):
        prefix = manifest_prefix
        body = payload[len(prefix) :]
        prefix_source = "manifest_exact"
    elif not manifest_prefix:
        prefix, body, _kind = split_prefix_body(payload)
        prefix_source = "current_split_no_manifest_prefix"
    else:
        prefix, body, _kind = split_prefix_body(payload)
        prefix_source = "fallback_current_split"
    rendered = dictionary.expand(body, tbl).rstrip("\u3000 \t")
    return prefix, body, rendered, prefix_source


def main() -> int:
    rom = bytes(load_rom(TIP))
    if sha256(rom) != EXPECTED_TIP_SHA256:
        raise AuditError("promoted main TIP identity drifted")

    dialogue_doc = load_object(ORIGINAL_DIALOGUE)
    manifest = walk_manifest_records(load_object(MANIFEST))
    approved_doc = load_object(APPROVED)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )

    approved_rows = [
        dict(row)
        for row in approved_doc.get("entries") or []
        if row.get("region") == "script"
        and ANCHOR <= int(str(row.get("abs") or "0"), 16) < END_EXCLUSIVE
        and row.get("review_status") == "approved"
    ]
    approved_by_abs = {
        int(str(row["abs"]), 16): row for row in approved_rows
    }
    if len(approved_rows) != len(approved_by_abs):
        raise AuditError("duplicate approved post-anchor addresses")

    source_rows = [
        dict(row)
        for row in dialogue_doc.get("dialogue") or []
        if ANCHOR <= int(row.get("abs") or 0) < END_EXCLUSIVE
    ]

    counts: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    approved_failures: list[dict[str, Any]] = []
    approved_manifest_missing: list[int] = []
    approved_results: dict[int, dict[str, Any]] = {}
    production_targets: list[dict[str, Any]] = []
    short_structural: list[dict[str, Any]] = []

    # Validate every approved entry directly from the manifest.  Six approved
    # records are intentionally outside the conservative dialogue-db plausibility
    # filter, so catalog verification must not depend on that extractor view.
    for logical, approved in sorted(approved_by_abs.items()):
        record = manifest.get(logical)
        if record is None:
            approved_manifest_missing.append(logical)
            continue
        prefix, body, current, prefix_source = decode_current_body(
            rom, logical, record, dictionary, tbl
        )
        expected = normalize_ko_text(str(approved.get("ko") or ""))
        exact = current == expected
        counts["approved_rows_checked"] += 1
        if exact:
            counts["approved_rows_exact"] += 1
        else:
            counts["approved_rows_mismatch"] += 1
            approved_failures.append(
                {
                    "abs": f"{logical:06X}",
                    "expected": expected,
                    "current": current,
                }
            )
        approved_results[logical] = {
            "expected": expected,
            "current": current,
            "exact": exact,
            "body_capacity": len(body),
            "prefix_hex": prefix.hex().upper(),
            "prefix_source": prefix_source,
        }

    for source in source_rows:
        logical = int(source["abs"])
        counts["original_dialogue_rows_in_range"] += 1
        record = manifest.get(logical)
        if record is None:
            counts["manifest_missing"] += 1
            continue

        original_body = bytes.fromhex(
            str(source.get("body_hex") or "").replace(" ", "")
        )
        original_text = str(source.get("jp") or "")
        plausible = (
            1 <= len(original_body) <= MAX_PLAUSIBLE_BODY_BYTES
            and "<" not in original_text
            and len(original_text) <= MAX_PLAUSIBLE_RENDERED_CHARS
        )
        if not plausible:
            counts["parser_noise_or_oversized_excluded"] += 1
            continue

        exclusion = translation_exclusion_reason(logical)
        if exclusion:
            counts[exclusion] += 1
            continue

        prefix, body, current, prefix_source = decode_current_body(
            rom, logical, record, dictionary, tbl
        )
        japanese_count = japanese_character_count(current)
        hangul_count = hangul_character_count(current)
        approved = approved_by_abs.get(logical)
        approved_result = approved_results.get(logical)
        approved_exact = (
            bool(approved_result.get("exact")) if approved_result is not None else None
        )
        expected = (
            str(approved_result.get("expected")) if approved_result is not None else None
        )

        status: str
        if approved is not None and approved_exact is True:
            status = "approved_exact"
            counts[status] += 1
        elif japanese_count == 0:
            status = "localized_or_non_japanese"
            counts[status] += 1
        elif len(body) < 4 and approved is None:
            status = "structural_short_fragment"
            counts[status] += 1
        else:
            status = "production_target"
            counts[status] += 1

        row = {
            "abs": f"{logical:06X}",
            "original_jp": original_text,
            "current": current,
            "payload_capacity": int(record["boundary"]["payload_capacity"]),
            "prefix_hex": prefix.hex().upper(),
            "prefix_source": prefix_source,
            "body_capacity": len(body),
            "body_hex": body.hex().upper(),
            "japanese_count": japanese_count,
            "hangul_count": hangul_count,
            "approved_catalog": approved is not None,
            "approved_expected": expected,
            "approved_exact": approved_exact,
            "status": status,
        }
        records.append(row)
        if status == "structural_short_fragment":
            short_structural.append(row)
        elif status == "production_target":
            production_targets.append(row)

    checks = {
        "main_tip_exact": sha256(rom) == EXPECTED_TIP_SHA256,
        "approved_catalog_count_458": len(approved_by_abs) == EXPECTED_APPROVED,
        "approved_catalog_manifest_complete": not approved_manifest_missing,
        "approved_catalog_all_exact": not approved_failures
        and len(approved_results) == EXPECTED_APPROVED,
        "manifest_missing_zero": counts["manifest_missing"] == 0,
        "event_graphics_block_excluded": counts[
            "excluded_script_graphics_block"
        ] > 0,
        "meaningful_japanese_production_targets_zero": not production_targets,
        "remaining_japanese_are_only_unapproved_under4_fragments": all(
            int(row["body_capacity"]) < 4
            and row["approved_catalog"] is False
            for row in short_structural
        ),
    }
    ok = all(checks.values())

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_post_abaoa_qu_scenario_dialogue.py",
        "read_only": True,
        "ok": ok,
        "inputs": {
            "tip": identity(TIP, rom),
            "original_dialogue": identity(ORIGINAL_DIALOGUE),
            "manifest": identity(MANIFEST),
            "approved_catalog": identity(APPROVED),
        },
        "scope": {
            "anchor": f"{ANCHOR:06X}",
            "anchor_label": "A Baoa Qu scenario entry",
            "end_exclusive": f"{END_EXCLUSIVE:06X}",
            "banks": ["60", "61", "62", "63"],
            "central_exclusion": "62D650-62FFFF event/graphics structure block",
            "legacy_korean_sources_consumed": False,
        },
        "counts": {
            **dict(sorted(counts.items())),
            "approved_catalog_entries": len(approved_by_abs),
            "approved_failures": len(approved_failures),
            "approved_manifest_missing": len(approved_manifest_missing),
            "production_targets": len(production_targets),
            "structural_short_fragments": len(short_structural),
            "audited_records_after_filters": len(records),
        },
        "approved_failures": approved_failures,
        "approved_manifest_missing": [
            f"{address:06X}" for address in approved_manifest_missing
        ],
        "production_targets": production_targets,
        "structural_short_fragments": short_structural,
        "checks": checks,
        "conclusion": {
            "approved_post_abaoa_qu_dialogue_already_applied": not approved_failures
            and len(approved_by_abs) == EXPECTED_APPROVED,
            "new_rom_delta_required": False,
            "reason": (
                "All 458 approved post-anchor script entries render exactly on the promoted TIP. "
                "After the central 62D650-62FFFF event/graphics exclusion, every remaining "
                "Japanese-looking row is an unapproved 1-3 byte extractor fragment; no "
                "body-capacity >=4 scenario dialogue remains to patch."
            ),
        },
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    worklist = {
        "schema_version": 1,
        "generated_by": "tools/audit_post_abaoa_qu_scenario_dialogue.py",
        "read_only": True,
        "ok": ok,
        "tip": identity(TIP, rom),
        "scope": report["scope"],
        "policy": {
            "approved_catalog_only": True,
            "exclude_script_graphics_and_fixed_data": True,
            "legacy_machine_translation_used": False,
            "under4_without_approved_target": "exclude_as_structural_fragment",
        },
        "records": production_targets,
        "production_target_count": len(production_targets),
        "status": "no_rom_delta_required_already_applied" if ok else "audit_failed",
    }
    WORKLIST.write_text(
        json.dumps(worklist, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            {
                "ok": ok,
                "counts": report["counts"],
                "checks": checks,
                "conclusion": report["conclusion"],
                "out": str(OUT.relative_to(ROOT)),
                "worklist": str(WORKLIST.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not ok:
        raise AuditError("post-A Baoa Qu scenario dialogue audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
