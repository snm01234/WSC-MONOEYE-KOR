#!/usr/bin/env python3
"""Promote the user-verified save/load/general UI repair to the main TIP.

SaveRAM is deliberately outside the promotion contract.  The live
``monoeye_ko_expanded.sav`` is mutable validation data and is neither hashed nor
restored.  Only the ROM is backed up and atomically replaced.
"""
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
CANDIDATE = PATCH / "ui_compact3_rollback_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ui_compact3_rollback_candidate.sav"
BUILD_REPORT = PATCH / "ui_compact3_rollback_report.json"
AUDIT_REPORT = PATCH / "ui_compact3_rollback_audit.json"
STRUCTURE_REPORTS = (
    PATCH / "ui_compact3_rollback_structure_5f_targets.json",
    PATCH / "ui_compact3_rollback_structure_60_targets.json",
    PATCH / "ui_compact3_rollback_structure_75b.json",
)
SEGPTR_REPORT = PATCH / "ui_compact3_rollback_false_segptr.json"
PROMOTION_REPORT = PATCH / "ui_compact3_rollback_promotion_report.json"

PARENT_SHA = "ec295935607b4843bc654c2709995262bade543d6c0be64556a45b6b240d4833"
CANDIDATE_SHA = "971665a2fa5d571dd04500b520fb41bcc7d4929e571ca2632c9253a4e51b35ae"
ROM_SIZE = 16_777_216


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing report: {rel(path)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"invalid report root: {rel(path)}")
    return value


def require_rom(path: Path, expected_sha: str) -> None:
    if not path.is_file() or path.stat().st_size != ROM_SIZE:
        raise PromotionError(f"invalid ROM: {rel(path)}")
    actual = digest(path)
    if actual != expected_sha:
        raise PromotionError(f"ROM SHA drifted for {rel(path)}: {actual}")


def validate() -> dict[str, Any]:
    require_rom(TIP, PARENT_SHA)
    require_rom(CANDIDATE, CANDIDATE_SHA)

    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT_REPORT)
    if build.get("ok") is not True:
        raise PromotionError("build report is not accepted")
    if ((build.get("parent") or {}).get("sha256")) != PARENT_SHA:
        raise PromotionError("build parent binding mismatch")
    if ((build.get("candidate") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("build candidate binding mismatch")
    verification = build.get("verification") or {}
    if verification.get("decode_failures") or verification.get("target_e519_residuals"):
        raise PromotionError("target decode or E519 residual failure")
    if verification.get("record_lengths_preserved") is not True:
        raise PromotionError("record lengths were not preserved")
    if verification.get("terminators_preserved") is not True:
        raise PromotionError("terminators were not preserved")
    if verification.get("dialogue_prefixes_preserved") is not True:
        raise PromotionError("dialogue prefixes were not preserved")
    if verification.get("unaccounted_changed_bytes") != 0:
        raise PromotionError("unaccounted ROM changes remain")

    if audit.get("ok") is not True:
        raise PromotionError("independent audit failed")
    counts = audit.get("counts") or {}
    if counts.get("target_records") != 10 or counts.get("target_exact") != 10:
        raise PromotionError("target audit count mismatch")
    if counts.get("target_e519_residuals") != 0 or counts.get("consumer_failures") != 0:
        raise PromotionError("target audit residual/consumer failure")
    if counts.get("failures") != 0 or (audit.get("walker") or {}).get("ok") is not True:
        raise PromotionError("walker or audit failure")

    structures = []
    for path in STRUCTURE_REPORTS:
        report = load_json(path)
        if report.get("ok") is not True or report.get("issues") != 0:
            raise PromotionError(f"structure gate failed: {rel(path)}")
        structures.append({"path": rel(path), "records_walked": report.get("records_walked")})
    segptr = load_json(SEGPTR_REPORT)
    if segptr.get("ok") is not True or segptr.get("sites_found") != 0:
        raise PromotionError("false segmented-pointer gate failed")

    return {
        "parent_sha256": PARENT_SHA,
        "candidate_sha256": CANDIDATE_SHA,
        "target_records": 10,
        "runtime_verified_by_user": ["저장하기", "불러오기", "범용"],
        "structures": structures,
        "false_segmented_pointer_sites": 0,
        "saveram_policy": "mutable_live_test_data_not_a_gate",
    }


def atomic_replace_tip() -> None:
    temporary = TIP.with_name(f".{TIP.name}.ui-compact3-promote.tmp")
    temporary.unlink(missing_ok=True)
    with CANDIDATE.open("rb") as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    require_rom(temporary, CANDIDATE_SHA)
    os.replace(temporary, TIP)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_ui_compact3_rollback"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_rom(backup_rom, PARENT_SHA)

    atomic_replace_tip()
    require_rom(TIP, CANDIDATE_SHA)

    candidate_before_cleanup = identity(CANDIDATE)
    CANDIDATE.unlink()
    candidate_save_removed = False
    if CANDIDATE_SAVE.exists():
        CANDIDATE_SAVE.unlink()
        candidate_save_removed = True

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_ui_compact3_rollback_candidate.py",
        "mode": "commit",
        "ok": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": {"size": ROM_SIZE, "sha256": PARENT_SHA},
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "validation": validation,
        "candidate_before_cleanup": candidate_before_cleanup,
        "candidate_rom_removed": True,
        "candidate_save_removed": candidate_save_removed,
        "main_saveram": {
            "path": "sram/monoeye_ko_expanded.sav",
            "action": "left_untouched",
            "hash_verification": "skipped_by_policy",
            "restore": "never",
        },
        "evidence": {
            "build_report": identity(BUILD_REPORT),
            "audit_report": identity(AUDIT_REPORT),
            "structure_reports": [identity(path) for path in STRUCTURE_REPORTS],
            "false_segptr_report": identity(SEGPTR_REPORT),
        },
    }
    temporary = PROMOTION_REPORT.with_name(f".{PROMOTION_REPORT.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, PROMOTION_REPORT)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
