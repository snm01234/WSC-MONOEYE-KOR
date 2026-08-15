#!/usr/bin/env python3
"""Independent audit for the eight-record runtime residual text candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import BANK_SIZE, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402
from patch_3byte_dict_token import bank_local_for_index  # noqa: E402
from structured_token_write_guard import PROTECTED_TABLES, validate_protected_table  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/runtime_text_screen_residual_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/runtime_text_screen_residual_candidate.sav"
SPEC = ROOT / "data/runtime_text_screen_residual_ko.json"
BUILD = ROOT / "out/patch/runtime_text_screen_residual_candidate_report.json"
FAMILY_AUDIT = ROOT / "out/patch/runtime_text_screen_residual_candidate_family_audit.json"
OUT = ROOT / "out/patch/runtime_text_screen_residual_candidate_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_PARENT = "29d096e6462194e226b0895a43016d30b38056c1088bffe925571ac8e466b9ea"
EXPECTED_CANDIDATE = "03a6f1c42e9fff43a143c5bc1dd45a0fa23abc7be02e61c207b9e877facfc0d8"
EXPECTED_CHECKSUM = "E7D1"
EXPECTED_TARGETS = 8
EXPECTED_PHRASES = 6
EXPECTED_RUNS = 11
EXPECTED_CHANGED_BYTES = 287
EXPECTED_SEGMENT = 0x1F
EXPECTED_CURSOR_BEFORE = 0xFF1B
EXPECTED_CURSOR_AFTER = 0xFFD5
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
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    live_save = MAIN_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if len(candidate) != ROM_SIZE or sha(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("candidate identity drifted")
    if len(live_save) != SAVE_SIZE or len(candidate_save) != SAVE_SIZE:
        raise AuditError("SaveRAM missing or wrong size")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    family = json.loads(FAMILY_AUDIT.read_text(encoding="utf-8"))
    rows = list(spec.get("records") or [])
    if len(rows) != EXPECTED_TARGETS or len({str(row.get("ko") or "") for row in rows}) != EXPECTED_PHRASES:
        raise AuditError("spec population drifted")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise AuditError("build report candidate binding drifted")
    if str(((family.get("inputs") or {}).get("tip") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise AuditError("family audit candidate binding drifted")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        candidate,
        load_ext_meta(EXT_META_PATH),
        load_ext_meta(EXT3_META_PATH),
    )
    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    target_failures: list[dict[str, Any]] = []
    rendered: list[dict[str, Any]] = []
    for raw in rows:
        start = int(str(raw["record_start"]), 16)
        prefix = bytes.fromhex(str(raw.get("prefix_hex") or ""))
        expected_parent = bytes.fromhex(str(raw["expected_payload_hex"]))
        capacity = int(raw["body_capacity"])
        expected_text = normalize_ko_text(str(raw["ko"]))
        before = read_encoded_z_safe(parent, base + start, max_len=128)
        after = read_encoded_z_safe(candidate, base + start, max_len=128)
        if before is None or after is None:
            target_failures.append({"record_start": f"{start:06X}", "reason": "unreadable"})
            continue
        before_payload, before_term = bytes(before[0]), int(before[1])
        after_payload, after_term = bytes(after[0]), int(after[1])
        actual_text = normalize_ko_text(
            dictionary.expand(after_payload[len(prefix):], tbl).rstrip("\u3000 \t")
        )
        ok = (
            before_payload == expected_parent
            and after_payload[: len(prefix)] == prefix
            and len(after_payload) == len(expected_parent)
            and len(after_payload) - len(prefix) == capacity
            and before_term == after_term
            and candidate[after_term] == 0
            and actual_text == expected_text
            and not any(is_japanese_character(character) for character in actual_text)
        )
        if raw.get("family") == "voice_tagged_run":
            ok = ok and prefix == bytes.fromhex("02F191")
        rendered.append(
            {
                "record_start": f"{start:06X}",
                "family": raw.get("family"),
                "expected": expected_text,
                "actual": actual_text,
                "prefix_hex": prefix.hex().upper(),
                "payload_hex": after_payload.hex().upper(),
                "ok": ok,
            }
        )
        if not ok:
            target_failures.append(rendered[-1])
        body_start = base + start + len(prefix)
        target_extents.append((body_start, body_start + capacity))

    allocation = build.get("allocation") or {}
    if int(str(allocation.get("segment") or "0"), 16) != EXPECTED_SEGMENT:
        raise AuditError("allocation segment drifted")
    if int(str(allocation.get("cursor_before") or "0"), 16) != EXPECTED_CURSOR_BEFORE:
        raise AuditError("allocation cursor-before drifted")
    if int(str(allocation.get("cursor_after") or "0"), 16) != EXPECTED_CURSOR_AFTER:
        raise AuditError("allocation cursor-after drifted")
    assignments = allocation.get("assignments") or {}
    if len(assignments) != EXPECTED_PHRASES:
        raise AuditError("allocation assignment count drifted")

    bank_file = EXPECTED_SEGMENT * BANK_SIZE
    pointer_extents: list[tuple[int, int]] = []
    for shown_index in assignments.values():
        index = int(str(shown_index), 16)
        segment, local = bank_local_for_index(index)
        if segment != EXPECTED_SEGMENT:
            raise AuditError("assignment escaped selected ext3 segment")
        pointer_extents.append((bank_file + local * 2, bank_file + local * 2 + 2))
    pointer_block = (
        (min(start for start, _end in pointer_extents), max(end for _start, end in pointer_extents))
        if pointer_extents
        else (0, 0)
    )
    allowed = target_extents + pointer_extents + [
        pointer_block,
        (bank_file + EXPECTED_CURSOR_BEFORE, bank_file + EXPECTED_CURSOR_AFTER),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate)
    changed = sum(right - left for left, right in runs)
    unaccounted = [
        {"file_start": f"{left:08X}", "file_end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    checksum_copy = bytearray(candidate)
    checksum = update_ws_checksum(checksum_copy)
    checksum_exact = bytes(checksum_copy) == candidate and f"{checksum:04X}" == EXPECTED_CHECKSUM
    protected = [validate_protected_table(candidate, table) for table in PROTECTED_TABLES]
    build_checks = build.get("checks") or {}
    screen_rows = list(family.get("screen_evidence") or [])
    family_screen_ok = (
        len(screen_rows) == EXPECTED_TARGETS
        and all(int(row.get("japanese_count", -1)) == 0 for row in screen_rows)
        and all(row.get("classification") == "clean_korean" for row in screen_rows)
    )

    checks = {
        "all_targets_exact": not target_failures,
        "screen_family_audit_clean": family_screen_ok,
        "diff_run_count_exact": len(runs) == EXPECTED_RUNS,
        "changed_byte_count_exact": changed == EXPECTED_CHANGED_BYTES,
        "all_diff_runs_bounded": not unaccounted,
        "checksum_exact": checksum_exact,
        "protected_tables_exact": all(row.get("ok") is True for row in protected),
        "build_checks_all_true": bool(build_checks) and all(value is True for value in build_checks.values()),
        "candidate_saveram_present_not_a_hash_gate": len(candidate_save) == SAVE_SIZE,
        "main_tip_unchanged": MAIN.read_bytes() == parent,
        "live_saveram_unchanged": MAIN_SAVE.read_bytes() == live_save,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_runtime_text_screen_residual_candidate.py",
        "read_only": True,
        "ok": all(checks.values()),
        "inputs": {
            "parent": identity(MAIN, parent),
            "candidate": identity(CANDIDATE, candidate),
            "live_saveram": identity(MAIN_SAVE, live_save),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "spec": identity(SPEC),
            "build_report": identity(BUILD),
            "family_audit": identity(FAMILY_AUDIT),
        },
        "counts": {
            "targets": len(rows),
            "unique_phrases": len({str(row.get("ko") or "") for row in rows}),
            "target_failures": len(target_failures),
            "diff_runs": len(runs),
            "changed_bytes": changed,
            "unaccounted_diff_runs": len(unaccounted),
        },
        "checksum": f"{checksum:04X}",
        "checks": checks,
        "rendered_targets": rendered,
        "target_failures": target_failures,
        "unaccounted": unaccounted,
        "protected_tables": protected,
        "saveram_policy": {
            "candidate_matches_current_live": candidate_save == live_save,
            "candidate_hash_not_a_gate": True,
            "live_saveram_must_not_be_replaced_on_promotion": True,
        },
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "checksum": report["checksum"], "checks": checks, "out": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
