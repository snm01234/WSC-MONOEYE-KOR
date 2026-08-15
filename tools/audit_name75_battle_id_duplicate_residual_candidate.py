#!/usr/bin/env python3
"""Independent audit for the 128-record Name75 battle/ID duplicate candidate."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402
from structured_token_write_guard import PROTECTED_TABLES, validate_protected_table  # noqa: E402

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/name75_battle_id_duplicate_residual_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/name75_battle_id_duplicate_residual_candidate.sav"
SHEET = ROOT / "out/script/name75_battle_id_duplicate_residual_sheet.csv"
ANALYSIS = ROOT / "out/patch/name75_battle_id_duplicate_residual_audit.json"
BUILD = ROOT / "out/patch/name75_battle_id_duplicate_residual_candidate_report.json"
OUT = ROOT / "out/patch/name75_battle_id_duplicate_residual_candidate_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_PARENT = "b24d72bcc18058ad248fbfdb9359948bf1bc3e06e23db6eba89623a143719180"
EXPECTED_CANDIDATE = "29d096e6462194e226b0895a43016d30b38056c1088bffe925571ac8e466b9ea"
EXPECTED_RECORDS = 128
EXPECTED_PHRASES = 113
PREFIX = bytes.fromhex("173418")
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def token_length(payload: bytes) -> int:
    if len(payload) >= 4 and payload[:2] == bytes.fromhex("E518"):
        return 4
    if len(payload) >= 3 and payload[:2] == bytes.fromhex("E519"):
        return 3
    if len(payload) >= 2 and 0xF0 <= payload[0] <= 0xFF:
        return 2
    raise AuditError(f"unsupported canonical encoding {payload.hex().upper()}")


def diff_runs(left: bytes, right: bytes) -> list[tuple[int, int]]:
    if len(left) != len(right):
        raise AuditError("ROM sizes differ")
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            result.append((start, index))
            start = None
    if start is not None:
        result.append((start, len(left)))
    return result


def covered(run: tuple[int, int], extents: list[tuple[int, int]]) -> bool:
    lo, hi = run
    return any(start <= lo and hi <= end for start, end in extents)


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if len(candidate) != ROM_SIZE or sha(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("candidate identity drifted")
    if len(live_save) != SAVE_SIZE or len(candidate_save) != SAVE_SIZE:
        raise AuditError("SaveRAM missing or wrong size")

    analysis = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    with SHEET.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_RECORDS:
        raise AuditError("sheet population drifted")
    if int((analysis.get("counts") or {}).get("new_residual_records") or -1) != EXPECTED_RECORDS:
        raise AuditError("analysis population drifted")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise AuditError("build report candidate binding drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    base = stock_base(parent)

    target_failures: list[dict[str, Any]] = []
    target_extents: list[tuple[int, int]] = []
    original_patterns: set[bytes] = set()
    seen_phrases: set[str] = set()
    screenshot_records: list[dict[str, Any]] = []

    for source in rows:
        record_start = int(source["record_start"], 16)
        body_start = int(source["body_start"], 16)
        original_body = bytes.fromhex(source["body_hex"])
        capacity = int(source["body_capacity"])
        expected = normalize_ko_text(source["ko"])
        source_sites = [int(value, 16) for value in source["source_name75_sites"].split(";") if value]
        seen_phrases.add(source["jp"])
        original_patterns.add(PREFIX + original_body + b"\x00")

        before = read_encoded_z_safe(parent, base + record_start, max_len=128)
        after = read_encoded_z_safe(candidate, base + record_start, max_len=128)
        if before is None or after is None:
            target_failures.append({"record_start": f"{record_start:06X}", "reason": "unreadable"})
            continue
        before_payload, before_term = bytes(before[0]), int(before[1])
        after_payload, after_term = bytes(after[0]), int(after[1])
        after_body = after_payload[len(PREFIX):]
        try:
            rendered = normalize_ko_text(
                candidate_dictionary.expand(after_body, tbl).rstrip("\u3000 \t")
            )
        except Exception as exc:  # noqa: BLE001
            rendered = f"<{type(exc).__name__}>"

        canonical_encodings: set[bytes] = set()
        for logical in source_sites:
            current = read_encoded_z_safe(parent, base + logical, max_len=128)
            same = read_encoded_z_safe(candidate, base + logical, max_len=128)
            if current is None or same is None or bytes(current[0]) != bytes(same[0]):
                continue
            payload = bytes(current[0])
            try:
                length = token_length(payload)
                canonical_render = normalize_ko_text(
                    parent_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
                )
            except Exception:
                continue
            if canonical_render == expected and all(value == 0x01 for value in payload[length:]):
                canonical_encodings.add(payload[:length])

        encoding_match = any(
            after_body == encoding + b"\x01" * (capacity - len(encoding))
            for encoding in canonical_encodings
            if len(encoding) <= capacity
        )
        ok = (
            before_payload == PREFIX + original_body
            and before_term == after_term
            and len(after_payload) == len(before_payload)
            and after_payload.startswith(PREFIX)
            and rendered == expected
            and not any(is_japanese_character(character) for character in rendered)
            and encoding_match
        )
        if not ok:
            target_failures.append(
                {
                    "record_start": f"{record_start:06X}",
                    "before_payload": before_payload.hex().upper(),
                    "after_payload": after_payload.hex().upper(),
                    "expected": expected,
                    "actual": rendered,
                    "terminator_preserved": before_term == after_term,
                    "canonical_encoding_match": encoding_match,
                }
            )
        if source["jp"] == "キサマに用はない……！！":
            screenshot_records.append(
                {
                    "record_start": f"{record_start:06X}",
                    "rendered": rendered,
                    "payload_hex": after_payload.hex().upper(),
                    "ok": ok,
                }
            )
        target_extents.append((base + body_start, base + body_start + capacity))

    residuals: list[dict[str, Any]] = []
    file_lo = base + 0x5C0000
    file_hi = base + 0x5D0000
    for pattern in sorted(original_patterns):
        found = candidate.find(pattern, file_lo, file_hi)
        if found >= 0:
            residuals.append(
                {"logical": f"{found - base:06X}", "pattern_hex": pattern.hex().upper()}
            )

    runs = diff_runs(parent, candidate)
    allowed = target_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]
    protected = [validate_protected_table(candidate, table) for table in PROTECTED_TABLES]
    checksum_copy = bytearray(candidate)
    calculated_checksum = update_ws_checksum(checksum_copy)
    checksum_exact = bytes(checksum_copy) == candidate

    build_checks = build.get("checks") or {}
    checks = {
        "sheet_record_population_exact": len(rows) == EXPECTED_RECORDS,
        "sheet_unique_phrase_population_exact": len(seen_phrases) == EXPECTED_PHRASES,
        "all_targets_exact_and_canonical": not target_failures,
        "all_original_patterns_removed": not residuals,
        "all_diff_runs_bounded": not unaccounted,
        "protected_tables_exact": all(row.get("ok") is True for row in protected),
        "checksum_exact": checksum_exact,
        "build_report_checks_all_true": bool(build_checks) and all(value is True for value in build_checks.values()),
        "screenshot_phrase_four_records_exact": (
            len(screenshot_records) == 4 and all(row["ok"] for row in screenshot_records)
        ),
        "candidate_saveram_present_but_not_a_promotion_gate": len(candidate_save) == SAVE_SIZE,
        "main_tip_unchanged": PARENT.read_bytes() == parent,
        "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_name75_battle_id_duplicate_residual_candidate.py",
        "read_only": True,
        "ok": all(checks.values()),
        "inputs": {
            "parent": identity(PARENT, parent),
            "candidate": identity(CANDIDATE, candidate),
            "live_saveram": identity(LIVE_SAVE, live_save),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "sheet": identity(SHEET),
            "analysis": identity(ANALYSIS),
            "build_report": identity(BUILD),
        },
        "counts": {
            "records": len(rows),
            "unique_phrases": len(seen_phrases),
            "target_failures": len(target_failures),
            "residual_patterns": len(residuals),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "checksum": f"{calculated_checksum:04X}",
        "saveram_policy": {
            "candidate_matches_current_live": candidate_save == live_save,
            "candidate_saveram_hash_is_not_a_gate": True,
            "live_saveram_is_mutable_and_must_not_be_replaced_on_promotion": True
        },
        "checks": checks,
        "screenshot_phrase_records": screenshot_records,
        "target_failure_sample": target_failures[:20],
        "residuals": residuals,
        "unaccounted": unaccounted,
        "protected_tables": protected,
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
