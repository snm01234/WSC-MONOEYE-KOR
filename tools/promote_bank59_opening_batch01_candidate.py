#!/usr/bin/env python3
"""Promote the emulator-verified bank59 opening batch01 candidate ROM."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "bank59_opening_batch01_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/bank59_opening_batch01_candidate.sav"
BUILD_REPORT = PATCH / "bank59_opening_batch01_report.json"
AUDIT_REPORT = PATCH / "bank59_opening_batch01_candidate_audit.json"
USER_VALIDATION = PATCH / "bank59_opening_batch01_user_validation.json"
POST_REPORT = PATCH / "bank59_opening_batch01_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "bank59_opening_batch01_promotion_report.json"

EXPECTED_TIP_SHA = "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825"
EXPECTED_CANDIDATE_SHA = "0e060c6ab73d62acdf307afd9ddcc8cbf5853365b9f22196c52497937c23ea89"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
TARGETS = 27


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, size: int, expected_sha: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise PromotionError(f"missing or wrong-size file: {rel(path)}")
    if expected_sha is not None and digest(path) != expected_sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing JSON evidence: {rel(path)}")
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


def validate() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP_SHA)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require(CANDIDATE_SAVE, SAVE_SIZE)

    build = load_object(BUILD_REPORT)
    audit = load_object(AUDIT_REPORT)
    validation = load_object(USER_VALIDATION)
    if build.get("ok") is not True or build.get("status") != "candidate_static_verified":
        raise PromotionError("build report is not approved")
    if str((build.get("candidate") or {}).get("sha256")) != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build report candidate identity drifted")
    if int((build.get("translation") or {}).get("records") or 0) != TARGETS:
        raise PromotionError("build report target count drifted")
    if (build.get("translation") or {}).get("legacy_machine_translation_used") is not False:
        raise PromotionError("legacy machine translation is not explicitly rejected")
    if audit.get("ok") is not True:
        raise PromotionError("independent candidate audit did not pass")
    if int((audit.get("counts") or {}).get("targets") or 0) != TARGETS:
        raise PromotionError("independent audit target count drifted")
    checks = audit.get("checks") or {}
    required_checks = (
        "candidate_identity_matches_report",
        "catalog_fresh_reviewed",
        "gap_audit_approved",
        "target_count_exact",
        "all_targets_exact",
        "bank21_pointer_table_exact",
        "new_leaf_exact",
        "leaf_hook_exact",
        "accepted_old_leaf_body_unchanged",
        "accepted_walkers_unchanged",
        "ext3_banks_11_20_byte_exact",
        "stock_dictionary_bank_byte_exact",
        "candidate_alias_references_exact",
        "block_non_targets_byte_exact",
        "checksum_exact",
        "unaccounted_changed_bytes_zero",
    )
    if not all(checks.get(name) is True for name in required_checks):
        raise PromotionError("required independent audit checks are incomplete")
    if validation.get("status") != "user_emulator_validation_passed":
        raise PromotionError("user emulator validation is missing")
    if str((validation.get("candidate") or {}).get("sha256")) != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("user validation is bound to another candidate")
    if validation.get("authorization") != "promote candidate ROM to main TIP":
        raise PromotionError("user promotion authorization is missing")

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "build_report": identity(BUILD_REPORT),
        "independent_audit": identity(AUDIT_REPORT),
        "user_validation": identity(USER_VALIDATION),
        "candidate_test_saveram": {
            **identity(CANDIDATE_SAVE),
            "action": "not_copied",
            "reason": "mutable emulator test data",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_bank59_opening_batch01"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(TIP_SAVE, backup_save)
    require(backup_rom, ROM_SIZE, EXPECTED_TIP_SHA)
    require(backup_save, SAVE_SIZE, digest(TIP_SAVE))

    old_tip = identity(TIP)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "bank59-opening-batch01-promote")
        require(TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
        if identity(TIP_SAVE) != save_before:
            raise PromotionError("live main SaveRAM changed during ROM promotion")
    except Exception:
        atomic_copy(backup_rom, TIP, "bank59-opening-batch01-rollback")
        raise

    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_bank59_opening_batch01_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_rom": identity(backup_rom),
        "backup_saveram_snapshot": identity(backup_save),
        "main_saveram_before": save_before,
        "main_saveram_after": identity(TIP_SAVE),
        "evidence": {
            "build_report": identity(BUILD_REPORT),
            "independent_audit": identity(AUDIT_REPORT),
            "user_validation": identity(USER_VALIDATION),
        },
        "checks": {
            "tip_matches_verified_candidate": digest(TIP) == EXPECTED_CANDIDATE_SHA,
            "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP_SHA,
            "main_saveram_unchanged": identity(TIP_SAVE) == save_before,
            "candidate_test_saveram_not_promoted": digest(TIP_SAVE) != digest(CANDIDATE_SAVE)
            or save_before["sha256"] == digest(CANDIDATE_SAVE),
        },
    }
    if not all(post["checks"].values()):
        atomic_copy(backup_rom, TIP, "bank59-opening-batch01-postcheck-rollback")
        raise PromotionError("post-promotion audit failed")
    atomic_json(POST_REPORT, post)

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_bank59_opening_batch01_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "backup_saveram_snapshot": identity(backup_save),
        "validation": validation,
        "postpromotion_audit": identity(POST_REPORT),
        "main_saveram": {
            "before": save_before,
            "after": identity(TIP_SAVE),
            "action": "left_untouched",
        },
        "candidate_test_saveram": {
            "path": rel(CANDIDATE_SAVE),
            "sha256_after_emulator_test": digest(CANDIDATE_SAVE),
            "action": "not_copied",
        },
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
