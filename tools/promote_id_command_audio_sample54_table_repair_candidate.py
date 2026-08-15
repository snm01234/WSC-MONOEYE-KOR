#!/usr/bin/env python3
"""Promote the user-validated ID-command PCM sample-table repair candidate.

Only the ROM is promoted. The live main SaveRAM is backed up and left untouched.
The candidate restores sample 54's FFFF chain terminator and relocates the
Hangul primary runtime from 7F:FC4C to 7F:FC4E.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "id_command_audio_sample54_table_repair_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/id_command_audio_sample54_table_repair_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BUILD = PATCH / "id_command_audio_sample54_table_repair_report.json"
STRUCTURE = PATCH / "id_command_audio_sample54_table_repair_structure.json"
FALSE_SEGPTR = PATCH / "id_command_audio_sample54_table_repair_false_segptr.json"
USER = PATCH / "id_command_audio_sample54_table_repair_user_validation.json"
POST = PATCH / "id_command_audio_sample54_table_repair_postpromotion_audit.json"
PROMOTION = PATCH / "id_command_audio_sample54_table_repair_promotion_report.json"

EXPECTED_TIP = "ed44538a78491a1bd93022930ff6c3ec67da0b03b9e5fb5666dd1ef4df05b692"
EXPECTED_CANDIDATE = "6cf6184e7e989c02b25b3e61fc28fc9e9eca354c20726b5efc7eec10918dce05"
EXPECTED_ORIGINAL = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_CHECKSUM = "F768"
EXPECTED_STATUS = "user_runtime_validated_id_command_noise_removed"
EXPECTED_AUTH = "promote id_command_audio_sample54_table_repair candidate to main TIP"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

TRAMPOLINE = 0x7AFFB5
TRAMPOLINE_OLD = bytes.fromhex("EA4CFC00F0")
TRAMPOLINE_NEW = bytes.fromhex("EA4EFC00F0")
SAMPLE_TABLE = 0x7FFA96
SAMPLE_COUNT = 55
SAMPLE_ENTRY_SIZE = 8
SAMPLE54 = SAMPLE_TABLE + 54 * SAMPLE_ENTRY_SIZE
SAMPLE54_ORIGINAL = bytes.fromhex("30BAEF009715FFFF")
SAMPLE54_BROKEN = bytes.fromhex("30BAEF0097159AF1")
PRIMARY_OLD = 0x7FFC4C
PRIMARY_NEW = 0x7FFC4E
PRIMARY_LEN = 53
EXPECTED_PRIMARY = bytes.fromhex(
    "9AF1FC00F0"
    "F7C30080"
    "7420"
    "81E3FF7F"
    "81EB2008"
    "81FB6000"
    "730D"
    "C1E304"
    "BAF8F9"
    "03D3"
    "EA2B0500A0"
    "EAABFC00F0"
    "C1E304"
    "03D3"
    "EA2B0500A0"
)
DICT_HELPER = 0x7FFC8C
RUNTIME_END = 0x7FFD10


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, size: int, expected: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise PromotionError(f"missing or wrong-size file: {rel(path)}")
    if expected is not None and digest(path).lower() != expected.lower():
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing evidence: {rel(path)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root must be object: {rel(path)}")
    return value


def atomic_copy(source: Path, target: Path, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    require(temporary, ROM_SIZE, digest(source))
    os.replace(temporary, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def checksum_state(data: bytes) -> tuple[str, bool]:
    copy = bytearray(data)
    checksum = update_ws_checksum(copy)
    return f"{checksum:04X}", bytes(copy) == data


def occurrences(data: bytes, needle: bytes) -> list[int]:
    result: list[int] = []
    cursor = 0
    while True:
        found = data.find(needle, cursor)
        if found < 0:
            return result
        result.append(found)
        cursor = found + 1


def logical_slice(data: bytes, logical: int, length: int) -> bytes:
    base = stock_base(data)
    return data[base + logical : base + logical + length]


def validate_runtime_layout(parent: bytes, candidate: bytes, original: bytes) -> dict[str, bool]:
    original_table = logical_slice(
        original, SAMPLE_TABLE, SAMPLE_COUNT * SAMPLE_ENTRY_SIZE
    )
    candidate_table = logical_slice(
        candidate, SAMPLE_TABLE, SAMPLE_COUNT * SAMPLE_ENTRY_SIZE
    )
    return {
        "parent_sample54_is_known_broken": logical_slice(parent, SAMPLE54, 8) == SAMPLE54_BROKEN,
        "candidate_sample54_exact_original": logical_slice(candidate, SAMPLE54, 8) == SAMPLE54_ORIGINAL,
        "all_55_sample_entries_exact_original": candidate_table == original_table,
        "parent_trampoline_is_old": logical_slice(parent, TRAMPOLINE, 5) == TRAMPOLINE_OLD,
        "candidate_trampoline_is_new": logical_slice(candidate, TRAMPOLINE, 5) == TRAMPOLINE_NEW,
        "candidate_primary_at_fc4e_exact": logical_slice(candidate, PRIMARY_NEW, PRIMARY_LEN) == EXPECTED_PRIMARY,
        "parent_primary_at_fc4c_exact": logical_slice(parent, PRIMARY_OLD, PRIMARY_LEN) == EXPECTED_PRIMARY,
        "ext_dict_and_later_runtime_preserved": (
            logical_slice(candidate, DICT_HELPER, RUNTIME_END - DICT_HELPER)
            == logical_slice(parent, DICT_HELPER, RUNTIME_END - DICT_HELPER)
        ),
        "old_trampoline_target_removed": not occurrences(candidate, TRAMPOLINE_OLD),
        "new_trampoline_target_exactly_one": len(occurrences(candidate, TRAMPOLINE_NEW)) == 1,
    }


def validate_evidence() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE)
    require(CANDIDATE_SAVE, SAVE_SIZE)
    require(ORIGINAL, 8_388_608, EXPECTED_ORIGINAL)

    parent = bytes(load_rom(TIP))
    candidate = bytes(load_rom(CANDIDATE))
    original = bytes(load_rom(ORIGINAL))
    build = load_object(BUILD)
    structure = load_object(STRUCTURE)
    false_segptr = load_object(FALSE_SEGPTR)
    user = load_object(USER)

    if build.get("ok") is not True:
        raise PromotionError("candidate build report failed")
    if str((build.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("build report candidate binding drifted")
    build_checks = build.get("checks") or {}
    if not build_checks or not all(value is True for value in build_checks.values()):
        raise PromotionError("candidate build checks are incomplete")
    if str((build.get("diff") or {}).get("checksum", "")).upper() != EXPECTED_CHECKSUM:
        raise PromotionError("candidate checksum drifted in build report")

    if str(((structure.get("inputs") or {}).get("target") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("structure report candidate binding drifted")
    if int(structure.get("issues", -1)) != 27 or structure.get("by_kind") != {"terminator_moved_later": 27}:
        raise PromotionError("structure issue baseline changed")

    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    if str((((false_segptr.get("inputs") or {}).get("target") or {}).get("sha256", ""))).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("false segmented-pointer report binding drifted")

    if user.get("status") != EXPECTED_STATUS or user.get("authorization") != EXPECTED_AUTH:
        raise PromotionError("explicit user runtime authorization is missing")
    if str((user.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("user validation candidate binding drifted")
    runtime_result = user.get("runtime_result") or {}
    if runtime_result.get("id_command_activation_effect_present") is not True:
        raise PromotionError("user did not validate the intended ID effect")
    if runtime_result.get("subsequent_noise_absent") is not True:
        raise PromotionError("user did not validate noise removal")

    layout = validate_runtime_layout(parent, candidate, original)
    if not all(layout.values()):
        raise PromotionError("runtime layout validation failed: " + json.dumps(layout))
    checksum, checksum_exact = checksum_state(candidate)
    if checksum != EXPECTED_CHECKSUM or not checksum_exact:
        raise PromotionError("candidate checksum bytes are not exact")
    # The paired candidate SaveRAM may change during emulator validation.
    # It is test-only and is never copied to the live main SaveRAM.

    return {
        "current_tip": identity(TIP),
        "current_saveram": identity(TIP_SAVE),
        "candidate": identity(CANDIDATE),
        "candidate_test_saveram": {**identity(CANDIDATE_SAVE), "action": "not_copied"},
        "original": identity(ORIGINAL),
        "build_report": identity(BUILD),
        "structure_report": identity(STRUCTURE),
        "false_segptr_report": identity(FALSE_SEGPTR),
        "user_validation": identity(USER),
        "runtime_layout": layout,
        "checksum": checksum,
    }


def post_audit(
    *,
    backup_rom: Path,
    backup_save: Path,
    save_before: dict[str, Any],
    protected_runtime: bytes,
) -> dict[str, Any]:
    tip = bytes(load_rom(TIP))
    original = bytes(load_rom(ORIGINAL))
    checksum, checksum_exact = checksum_state(tip)
    checks = {
        "tip_matches_verified_candidate": digest(TIP) == EXPECTED_CANDIDATE,
        "sample54_entry_exact_original": logical_slice(tip, SAMPLE54, 8) == SAMPLE54_ORIGINAL,
        "all_55_sample_entries_exact_original": (
            logical_slice(tip, SAMPLE_TABLE, SAMPLE_COUNT * SAMPLE_ENTRY_SIZE)
            == logical_slice(original, SAMPLE_TABLE, SAMPLE_COUNT * SAMPLE_ENTRY_SIZE)
        ),
        "trampoline_retargeted_exact": logical_slice(tip, TRAMPOLINE, 5) == TRAMPOLINE_NEW,
        "primary_relocated_exact": logical_slice(tip, PRIMARY_NEW, PRIMARY_LEN) == EXPECTED_PRIMARY,
        "ext_dict_and_later_runtime_exact": (
            logical_slice(tip, DICT_HELPER, RUNTIME_END - DICT_HELPER) == protected_runtime
        ),
        "old_target_reference_removed": not occurrences(tip, TRAMPOLINE_OLD),
        "new_target_reference_exactly_one": len(occurrences(tip, TRAMPOLINE_NEW)) == 1,
        "checksum_exact": checksum_exact and checksum == EXPECTED_CHECKSUM,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP,
        "rollback_saveram_preserved": digest(backup_save) == save_before["sha256"],
        "main_saveram_unchanged": identity(TIP_SAVE) == save_before,
        "candidate_test_saveram_not_copied": digest(TIP_SAVE) == save_before["sha256"],
        "structure_evidence_bound_to_published_sha": (
            str((((load_object(STRUCTURE).get("inputs") or {}).get("target") or {}).get("sha256", ""))).lower()
            == digest(TIP)
        ),
        "false_segptr_evidence_bound_to_published_sha": (
            str(((((load_object(FALSE_SEGPTR).get("inputs") or {}).get("target") or {}).get("sha256", "")))).lower()
            == digest(TIP)
            and load_object(FALSE_SEGPTR).get("ok") is True
            and int(load_object(FALSE_SEGPTR).get("sites_found", -1)) == 0
        ),
    }
    return {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_audio_sample54_table_repair_candidate.py",
        "ok": all(checks.values()),
        "tip": identity(TIP),
        "rollback_rom": identity(backup_rom),
        "rollback_saveram_snapshot": identity(backup_save),
        "main_saveram_after": identity(TIP_SAVE),
        "checksum": checksum,
        "sample54_entry": logical_slice(tip, SAMPLE54, 8).hex().upper(),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate_evidence()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    parent_bytes = bytes(load_rom(TIP))
    protected_runtime = logical_slice(parent_bytes, DICT_HELPER, RUNTIME_END - DICT_HELPER)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_id_command_audio_sample54_table_repair"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(TIP_SAVE, backup_save)
    require(backup_rom, ROM_SIZE, EXPECTED_TIP)
    require(backup_save, SAVE_SIZE, digest(TIP_SAVE))

    old_tip = identity(TIP)
    save_before = identity(TIP_SAVE)
    candidate_before = identity(CANDIDATE)
    candidate_save_before = identity(CANDIDATE_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "sample54-table-repair-promote")
        require(TIP, ROM_SIZE, EXPECTED_CANDIDATE)
        if identity(TIP_SAVE) != save_before:
            raise PromotionError("live main SaveRAM changed during ROM promotion")
        post = post_audit(
            backup_rom=backup_rom,
            backup_save=backup_save,
            save_before=save_before,
            protected_runtime=protected_runtime,
        )
        if post.get("ok") is not True:
            raise PromotionError("post-promotion audit failed: " + json.dumps(post["checks"]))
        atomic_json(POST, post)
    except Exception:
        atomic_copy(backup_rom, TIP, "sample54-table-repair-rollback")
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_audio_sample54_table_repair_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root_cause": {
            "sample": 54,
            "sample_entry": "7F:FC46-FC4D",
            "broken_next_sample": "F19A",
            "restored_next_sample": "FFFF",
            "runtime_relocation": "Hangul primary 7F:FC4C -> 7F:FC4E",
        },
        "user_runtime_validation": {
            "id_command_activation_effect_present": True,
            "subsequent_noise_absent": True,
        },
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "backup_saveram_snapshot": identity(backup_save),
        "validation": validation,
        "postpromotion_audit": identity(POST),
        "main_saveram": {
            "before": save_before,
            "after": identity(TIP_SAVE),
            "action": "left_untouched",
        },
        "candidate": candidate_before,
        "candidate_test_saveram": {**candidate_save_before, "action": "not_copied"},
    }
    atomic_json(PROMOTION, report)

    cleaned: list[str] = []
    for path in (CANDIDATE, CANDIDATE_SAVE):
        if path.exists():
            path.unlink()
            cleaned.append(rel(path))
    report["cleanup"] = {
        "removed": cleaned,
        "kept_reports": [rel(BUILD), rel(STRUCTURE), rel(FALSE_SEGPTR), rel(USER), rel(POST)],
    }
    atomic_json(PROMOTION, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
