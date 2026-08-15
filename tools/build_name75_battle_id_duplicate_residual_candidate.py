#!/usr/bin/env python3
"""Build a candidate translating all exact Name75 dialogue duplicates in bank 5C.

The reviewed Korean text already exists in the canonical Name75 records.  This
builder reuses each canonical record's live dictionary token, so no new phrase
storage or dictionary slot is consumed.  Only complete records discovered by
``analyze_name75_battle_id_duplicate_residuals.py`` are changed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import (  # noqa: E402
    covered,
    diff_runs,
    verify_non_target_invariance,
)
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402
from structured_token_write_guard import (  # noqa: E402
    PROTECTED_TABLES,
    classify_structured_token_site,
    validate_protected_table,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SHEET = ROOT / "out/script/name75_battle_id_duplicate_residual_sheet.csv"
AUDIT = ROOT / "out/patch/name75_battle_id_duplicate_residual_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/name75_battle_id_duplicate_residual_candidate.wsc"
OUT_SAVE = ROOT / "sram/name75_battle_id_duplicate_residual_candidate.sav"
REPORT = ROOT / "out/patch/name75_battle_id_duplicate_residual_candidate_report.json"

EXPECTED_PARENT_SHA256 = "b24d72bcc18058ad248fbfdb9359948bf1bc3e06e23db6eba89623a143719180"
EXPECTED_RECORDS = 128
EXPECTED_PHRASES = 113
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
PREFIX = bytes.fromhex("173418")
BANK5C_LO = 0x5C0000
BANK5C_HI = 0x5D0000


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": digest(data),
    }


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def token_length(payload: bytes) -> int:
    if len(payload) >= 4 and payload[:2] == bytes.fromhex("E518"):
        return 4
    if len(payload) >= 3 and payload[:2] == bytes.fromhex("E519"):
        return 3
    if len(payload) >= 2 and 0xF0 <= payload[0] <= 0xFF:
        return 2
    raise BuildError(f"canonical Name75 record does not begin with a reusable token: {payload.hex().upper()}")


def load_rows(
    parent: bytes,
    original_dictionary: Dictionary,
    current_dictionary: Any,
    tbl: Tbl,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    bound = ((audit.get("inputs") or {}).get("tip") or {}).get("sha256")
    if str(bound).lower() != EXPECTED_PARENT_SHA256:
        raise BuildError(f"analysis is bound to {bound}, not current parent")
    if int((audit.get("counts") or {}).get("new_residual_records") or -1) != EXPECTED_RECORDS:
        raise BuildError("analysis record population drifted")

    with SHEET.open(encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    if len(source_rows) != EXPECTED_RECORDS:
        raise BuildError(f"sheet population drifted: {len(source_rows)}")

    base = stock_base(parent)
    rows: list[dict[str, Any]] = []
    token_by_phrase: dict[str, bytes] = {}
    record_starts: set[int] = set()
    body_starts: set[int] = set()
    intervals: list[tuple[int, int]] = []

    for source in source_rows:
        if source.get("safe_record_contract") != "True":
            raise BuildError("sheet contains a non-approved record")
        allowed_sources = {
            "current_main_name75_canonical": "approved_current_main_canonical",
            "data/name75_terms_ko.json": "approved_existing_name75_catalog",
        }
        source_name = str(source.get("translation_source") or "")
        if source_name not in allowed_sources:
            raise BuildError("sheet translation source drifted")
        if source.get("review_status") != allowed_sources[source_name]:
            raise BuildError("sheet contains an unreviewed translation")

        record_start = int(source["record_start"], 16)
        body_start = int(source["body_start"], 16)
        body = bytes.fromhex(source["body_hex"])
        prefix = bytes.fromhex(source["prefix_hex"])
        capacity = int(source["body_capacity"])
        jp = str(source["jp"])
        ko = normalize_ko_text(str(source["ko"]))
        if prefix != PREFIX or body_start != record_start + len(prefix):
            raise BuildError(f"record contract drift at {record_start:06X}")
        if capacity != len(body) or capacity < 4:
            raise BuildError(f"body capacity drift at {record_start:06X}")
        if record_start in record_starts or body_start in body_starts:
            raise BuildError(f"duplicate record address at {record_start:06X}")
        record_starts.add(record_start)
        body_starts.add(body_start)

        got = read_encoded_z_safe(parent, base + record_start, max_len=128)
        if got is None:
            raise BuildError(f"unreadable parent record at {record_start:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        if payload != prefix + body:
            raise BuildError(f"parent payload drift at {record_start:06X}")
        if terminator != base + record_start + len(payload) or parent[terminator] != 0:
            raise BuildError(f"terminator drift at {record_start:06X}")
        if original_dictionary.expand(body, tbl).rstrip("\u3000 \t") != jp:
            raise BuildError(f"source text drift at {record_start:06X}")
        if any(is_japanese_character(character) for character in ko):
            raise BuildError(f"translation still contains Japanese at {record_start:06X}")

        structure = classify_structured_token_site(parent, body_start, length=capacity)
        if structure is not None:
            raise BuildError(f"target overlaps structured data at {body_start:06X}: {structure}")

        file_interval = (base + body_start, base + body_start + capacity)
        if any(not (file_interval[1] <= lo or hi <= file_interval[0]) for lo, hi in intervals):
            raise BuildError(f"overlapping target extent at {record_start:06X}")
        intervals.append(file_interval)

        source_sites = [int(value, 16) for value in source["source_name75_sites"].split(";") if value]
        if not source_sites:
            raise BuildError(f"missing canonical Name75 source for {record_start:06X}")
        candidates: list[bytes] = []
        for logical in source_sites:
            source_got = read_encoded_z_safe(parent, base + logical, max_len=128)
            if source_got is None:
                continue
            source_payload = bytes(source_got[0])
            try:
                length = token_length(source_payload)
            except BuildError:
                continue
            token = source_payload[:length]
            if any(value != 0x01 for value in source_payload[length:]):
                continue
            rendered = current_dictionary.expand(source_payload, tbl).rstrip("\u3000 \t")
            if normalize_ko_text(rendered) == ko:
                candidates.append(token)
        if not candidates:
            raise BuildError(f"no reusable canonical token for {jp!r}")
        token = sorted(set(candidates), key=lambda value: (len(value), value))[0]
        previous = token_by_phrase.get(ko)
        if previous is not None and previous != token:
            # Multiple canonical tokens may render identically; keep the first
            # stable token chosen by sorted record order.
            token = previous
        else:
            token_by_phrase[ko] = token
        if len(token) > capacity:
            raise BuildError(f"canonical token does not fit at {record_start:06X}")

        rows.append(
            {
                "record_start": record_start,
                "body_start": body_start,
                "prefix": prefix,
                "body": body,
                "capacity": capacity,
                "jp": jp,
                "ko": ko,
                "token": token,
                "terminator": terminator,
                "source_sites": source_sites,
            }
        )

    rows.sort(key=lambda row: int(row["record_start"]))
    if len(rows) != EXPECTED_RECORDS or len(token_by_phrase) != EXPECTED_PHRASES:
        raise BuildError(
            f"final population drifted: records={len(rows)}, phrases={len(token_by_phrase)}"
        )
    return rows, token_by_phrase


def main() -> int:
    parent = MAIN.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    rows, token_by_phrase = load_rows(
        parent,
        original_dictionary,
        parent_dictionary,
        tbl,
    )

    candidate = bytearray(parent)
    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        token = bytes(row["token"])
        capacity = int(row["capacity"])
        replacement = token + b"\x01" * (capacity - len(token))
        start = base + int(row["body_start"])
        candidate[start : start + capacity] = replacement
        target_extents.append((start, start + capacity))
        applied.append(
            {
                "record_start": f"{int(row['record_start']):06X}",
                "body_start": f"{int(row['body_start']):06X}",
                "jp": row["jp"],
                "ko": row["ko"],
                "before_body_hex": bytes(row["body"]).hex().upper(),
                "after_body_hex": replacement.hex().upper(),
                "token_hex": token.hex().upper(),
                "token_bytes": len(token),
                "body_capacity": capacity,
                "source_name75_sites": [f"{value:06X}" for value in row["source_sites"]],
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    for row in rows:
        record_start = int(row["record_start"])
        got = read_encoded_z_safe(candidate_bytes, base + record_start, max_len=128)
        if got is None:
            failures.append({"record_start": f"{record_start:06X}", "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        rendered = candidate_dictionary.expand(payload[len(PREFIX):], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        if not (
            payload.startswith(PREFIX)
            and len(payload) == len(PREFIX) + int(row["capacity"])
            and terminator == int(row["terminator"])
            and candidate_bytes[terminator] == 0
            and normalize_ko_text(rendered) == expected
            and not any(is_japanese_character(character) for character in rendered)
        ):
            failures.append(
                {
                    "record_start": f"{record_start:06X}",
                    "expected": expected,
                    "actual": rendered,
                    "payload_hex": payload.hex().upper(),
                }
            )

    residual_patterns: list[dict[str, Any]] = []
    file_lo = base + BANK5C_LO
    file_hi = base + BANK5C_HI
    for body in sorted({bytes(row["body"]) for row in rows}):
        pattern = PREFIX + body + b"\x00"
        found = candidate_bytes.find(pattern, file_lo, file_hi)
        if found >= 0:
            residual_patterns.append(
                {
                    "logical": f"{found - base:06X}",
                    "pattern_hex": pattern.hex().upper(),
                }
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["record_start"]) for row in rows},
    )
    protected_after = [validate_protected_table(candidate_bytes, table) for table in PROTECTED_TABLES]

    runs = diff_runs(parent, candidate_bytes)
    allowed = target_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]
    runtime_start = base + 0x7A0600
    runtime_end = base + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]

    checks = {
        "all_targets_render_exact_korean": not failures,
        "all_original_duplicate_patterns_removed": not residual_patterns,
        "non_target_invariance": invariance.get("ok") is True,
        "diffs_bounded_to_target_bodies_and_checksum": not unaccounted,
        "protected_structured_tables_exact": all(row.get("ok") is True for row in protected_after),
        "dictionary_and_runtime_hooks_unchanged": runtime_unchanged,
        "main_tip_unchanged": MAIN.read_bytes() == parent,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": failures[:10],
                    "residual_patterns": residual_patterns[:10],
                    "unaccounted": unaccounted[:10],
                    "invariance": invariance,
                },
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_name75_battle_id_duplicate_residual_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_user_runtime_test",
        "inputs": {
            "parent": identity(MAIN, parent),
            "original": identity(ORIGINAL, original),
            "live_saveram": identity(MAIN_SAVE, save_before),
            "sheet": identity(SHEET),
            "analysis": identity(AUDIT),
        },
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "counts": {
            "target_records": len(rows),
            "unique_phrases": len(token_by_phrase),
            "reused_canonical_tokens": len(set(token_by_phrase.values())),
            "new_dictionary_slots": 0,
            "new_phrase_bytes": 0,
            "target_failures": len(failures),
            "residual_patterns": len(residual_patterns),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "strategy": {
            "record_contract": "173418 + exact original Name75 payload + 00",
            "translation_source": "current main Name75 canonical output, with reviewed catalog fallback",
            "storage": "reuse live canonical Name75 token; no dictionary allocation",
            "raw_pair_rewrites": False,
            "structured_table_guard": True,
        },
        "checks": checks,
        "verification": {
            "target_failures": failures,
            "residual_patterns": residual_patterns,
            "non_target_invariance": invariance,
            "protected_tables": protected_after,
            "unaccounted_diff_runs": unaccounted,
        },
        "diff": {
            "changed_bytes_vs_parent": sum(hi - lo for lo, hi in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "records": applied,
        "promotion": "blocked_pending_independent_audit_and_user_runtime_confirmation",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "counts": report["counts"],
                "diff": report["diff"],
                "report": str(REPORT.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
