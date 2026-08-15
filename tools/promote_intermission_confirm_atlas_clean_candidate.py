#!/usr/bin/env python3
"""Promote the user-approved 16-label transition + 12-label confirm atlas ROM."""
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
CANDIDATE_DIR = PATCH / "intermission_confirm_atlas_clean_candidate_16_focus"
CANDIDATE = CANDIDATE_DIR / "intermission_confirm_atlas_clean_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/intermission_confirm_atlas_clean_candidate.sav"
BUILD_REPORT = CANDIDATE_DIR / "confirm_focus_atlas_candidate_report.json"
COMBINED_REPORT = CANDIDATE_DIR / "combined_validation_report.json"
PROMOTION_REPORT = PATCH / "intermission_confirm_atlas_clean_promotion_report.json"
POST_AUDIT = PATCH / "intermission_confirm_atlas_clean_postpromotion_audit.json"

EXPECTED_TIP_SHA = "5bd6ac50ae7a80b922c79dfa43eaa3b43af053005467f53d9a57dc4c8e7444fc"
EXPECTED_CANDIDATE_SHA = "3b0a07f82d97a90055957dc310b6a9dc713c4d4c6aa4c75586b286e255412da9"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
BASE = 0x800000
ALLOWED_LOGICAL_RANGES = (
    (0x547CFC, 0x549A1C, "confirmation focus atlas"),
    (0x54B780, 0x54E7D4, "full-screen transition overlay"),
)


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"wrong size: {rel(path)}")
    if sha is not None and digest(path).lower() != sha.lower():
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    require(temporary, ROM_SIZE, digest(source))
    os.replace(temporary, target)


def validate() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP_SHA)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require(CANDIDATE_SAVE, SAVE_SIZE)
    require(BUILD_REPORT)
    require(COMBINED_REPORT)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    combined = json.loads(COMBINED_REPORT.read_text(encoding="utf-8"))
    verification = build.get("verification") or {}
    required_build_checks = (
        "all_12_labels_processed",
        "all_japanese_fill_removed",
        "all_changes_allowlisted",
        "full_16_label_asset_preserved",
        "focus_atlas_542000_544400_preserved",
        "saveram_byte_identical",
    )
    if not all(verification.get(name) is True for name in required_build_checks):
        raise PromotionError("confirmation atlas report did not pass every required check")
    if str(build.get("candidate_rom_sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build report candidate binding mismatch")
    if str(combined.get("candidate_rom_sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("combined report candidate binding mismatch")
    runtime = combined.get("runtime_checks") or {}
    if runtime.get("all_checks_pass") is not True or int(runtime.get("confirmation_cases_captured") or 0) != 12:
        raise PromotionError("combined runtime verification is incomplete")
    if int((combined.get("static_checks") or {}).get("full_screen_labels_processed") or 0) != 16:
        raise PromotionError("combined report does not cover all sixteen transition labels")

    before = TIP.read_bytes()
    after = CANDIDATE.read_bytes()
    if not checksum_ok(after):
        raise PromotionError("candidate WonderSwan checksum is invalid")
    if CANDIDATE_SAVE.read_bytes() != TIP_SAVE.read_bytes():
        raise PromotionError("candidate SaveRAM differs from live main SaveRAM")

    allowed = bytearray(ROM_SIZE)
    for start, end, _ in ALLOWED_LOGICAL_RANGES:
        allowed[BASE + start : BASE + end] = b"\x01" * (end - start)
    allowed[-2:] = b"\x01\x01"
    changed = [index for index, (left, right) in enumerate(zip(before, after)) if left != right]
    outside = [index for index in changed if not allowed[index]]
    if outside:
        raise PromotionError(f"candidate differs outside allowlist at {outside[0]:08X}")
    if len(changed) != 15440:
        raise PromotionError(f"changed-byte count drift: {len(changed)}")
    normal_focus = slice(BASE + 0x542000, BASE + 0x544400)
    runtime_hook = slice(BASE + 0x7A0600, BASE + 0x7A1000)
    if before[normal_focus] != after[normal_focus]:
        raise PromotionError("normal focus atlas changed")
    if before[runtime_hook] != after[runtime_hook]:
        raise PromotionError("runtime hook region changed")

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "main_saveram": identity(TIP_SAVE),
        "candidate_saveram": identity(CANDIDATE_SAVE),
        "build_report": identity(BUILD_REPORT),
        "combined_validation_report": identity(COMBINED_REPORT),
        "changed_bytes_including_checksum": len(changed),
        "allowed_logical_ranges": [
            {"start": f"{start:06X}", "end_exclusive": f"{end:06X}", "role": role}
            for start, end, role in ALLOWED_LOGICAL_RANGES
        ],
        "checksum_stored": after[-2:].hex().upper(),
        "all_differences_allowlisted": True,
        "normal_focus_atlas_preserved": True,
        "runtime_hook_preserved": True,
        "user_authorization": "2026-08-06 request to promote the current TIP candidate",
    }


def audit(backup: Path, save_before: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require(backup, ROM_SIZE, EXPECTED_TIP_SHA)
    checks = {
        "tip_matches_verified_candidate": digest(TIP) == EXPECTED_CANDIDATE_SHA,
        "tip_checksum_valid": checksum_ok(TIP.read_bytes()),
        "main_saveram_unchanged": identity(TIP_SAVE) == save_before,
        "rollback_rom_preserved": digest(backup) == EXPECTED_TIP_SHA,
        "all_differences_allowlisted": validation["all_differences_allowlisted"] is True,
        "normal_focus_atlas_preserved": validation["normal_focus_atlas_preserved"] is True,
        "runtime_hook_preserved": validation["runtime_hook_preserved"] is True,
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    result = {
        "schema_version": 1,
        "generated_by": "tools/promote_intermission_confirm_atlas_clean_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_rom": identity(backup),
        "main_saveram_before": save_before,
        "main_saveram_after": identity(TIP_SAVE),
        "checks": checks,
    }
    atomic_json(POST_AUDIT, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_intermission_confirm_atlas_clean"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    require(backup, ROM_SIZE, EXPECTED_TIP_SHA)
    old_tip = identity(TIP)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "intermission-confirm-atlas-promote")
        post = audit(backup, save_before, validation)
    except Exception:
        atomic_copy(backup, TIP, "intermission-confirm-atlas-rollback")
        raise
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_intermission_confirm_atlas_clean_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup),
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": post["checks"],
        "main_saveram_policy": "live main SaveRAM remained byte-identical",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
