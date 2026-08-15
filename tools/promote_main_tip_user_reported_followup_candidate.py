#!/usr/bin/env python3
"""Atomically promote the verified user-reported follow-up ROM and TBL.

The ROM and active TBL are one transaction because the candidate introduces
EC82=윕 and EC83=팬.  Live SaveRAM is read-only and must remain byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
ACTIVE_TBL = PATCH / "hangul_patch_pad3.tbl"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "main_tip_user_reported_followup_candidate.wsc"
CANDIDATE_TBL = PATCH / "main_tip_user_reported_followup_candidate.tbl"
BUILD_REPORT = PATCH / "main_tip_user_reported_followup_candidate_report.json"
AUDIT_REPORT = PATCH / "main_tip_user_reported_followup_candidate_audit.json"
FALSE_SEGPTR = PATCH / "main_tip_user_reported_followup_candidate_false_segptr.json"
PROMOTION_REPORT = PATCH / "main_tip_user_reported_followup_promotion_report.json"
POST_AUDIT = PATCH / "main_tip_user_reported_followup_postpromotion_audit.json"
BACKUP_ROOT = PATCH / "backup"

EXPECTED_MAIN = "e22ccc450c64f7751d61a80d6cd52f94363d981e5d5a7e1802afa57dfd224862"
EXPECTED_ACTIVE_TBL = "d539fdd70a36a67a3a0183f09596b5b535b2501c51f7580190dc73a22543b98d"
EXPECTED_CANDIDATE = "f16eb1283cd26104c3035376de7ec9bec6700dea691b8de491fab342a68de55b"
EXPECTED_CANDIDATE_TBL = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha_path(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"SHA-256 drift: {path}: {sha_path(path)}")


def atomic_copy(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(temporary, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def validate() -> dict[str, Any]:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_MAIN)
    require(ACTIVE_TBL, sha=EXPECTED_ACTIVE_TBL)
    # SaveRAM is live user data and may legitimately advance after the
    # candidate was built.  Bind to its promotion-time identity instead of an
    # older build-time digest, then prove that this transaction did not alter
    # it.
    require(MAIN_SAVE, size=SAVE_SIZE)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(CANDIDATE_TBL, sha=EXPECTED_CANDIDATE_TBL)
    for path in (BUILD_REPORT, AUDIT_REPORT, FALSE_SEGPTR):
        require(path)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    segptr = json.loads(FALSE_SEGPTR.read_text(encoding="utf-8"))
    if build.get("ok") is not True or build.get("promotion_allowed") is not True:
        raise PromotionError("candidate build report is not promotion-ready")
    if build["inputs"]["main_tip"]["sha256"] != EXPECTED_MAIN:
        raise PromotionError("build report parent binding drifted")
    if build["outputs"]["candidate_rom"]["sha256"] != EXPECTED_CANDIDATE:
        raise PromotionError("build report candidate binding drifted")
    if build["outputs"]["candidate_tbl"]["sha256"] != EXPECTED_CANDIDATE_TBL:
        raise PromotionError("build report TBL binding drifted")
    if not all(bool(value) for value in (build.get("checks") or {}).values()):
        raise PromotionError("one or more build checks failed")
    if audit.get("ok") is not True or not all(bool(value) for value in (audit.get("checks") or {}).values()):
        raise PromotionError("independent candidate audit failed")
    if segptr.get("ok") is not True or int(segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate WonderSwan checksum invalid")
    return {
        "main_tip": identity(MAIN),
        "active_tbl": identity(ACTIVE_TBL),
        "main_saveram": identity(MAIN_SAVE),
        "candidate_rom": identity(CANDIDATE),
        "candidate_tbl": identity(CANDIDATE_TBL),
        "build_report": identity(BUILD_REPORT),
        "independent_audit": identity(AUDIT_REPORT),
        "false_segptr_audit": identity(FALSE_SEGPTR),
    }


def main() -> int:
    validation = validate()
    save_before = identity(MAIN_SAVE)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}_pre_main_tip_user_reported_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    backup_tbl = backup_dir / ACTIVE_TBL.name
    shutil.copy2(MAIN, backup_rom)
    shutil.copy2(ACTIVE_TBL, backup_tbl)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_MAIN)
    require(backup_tbl, sha=EXPECTED_ACTIVE_TBL)

    before = {"tip": identity(MAIN), "tbl": identity(ACTIVE_TBL), "saveram": save_before}
    try:
        atomic_copy(CANDIDATE, MAIN)
        atomic_copy(CANDIDATE_TBL, ACTIVE_TBL)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(ACTIVE_TBL, sha=EXPECTED_CANDIDATE_TBL)
        require(MAIN_SAVE, size=SAVE_SIZE, sha=save_before["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted main checksum invalid")
    except Exception:
        atomic_copy(backup_rom, MAIN)
        atomic_copy(backup_tbl, ACTIVE_TBL)
        raise

    after = {
        "tip": identity(MAIN),
        "tbl": identity(ACTIVE_TBL),
        "saveram": identity(MAIN_SAVE),
    }
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "active_tbl_matches_candidate": after["tbl"]["sha256"] == EXPECTED_CANDIDATE_TBL,
        "main_checksum_valid": checksum_valid(MAIN),
        "live_saveram_unchanged": after["saveram"] == save_before,
        "rollback_rom_preserved": sha_path(backup_rom) == EXPECTED_MAIN,
        "rollback_tbl_preserved": sha_path(backup_tbl) == EXPECTED_ACTIVE_TBL,
        "candidate_rom_preserved": sha_path(CANDIDATE) == EXPECTED_CANDIDATE,
        "candidate_tbl_preserved": sha_path(CANDIDATE_TBL) == EXPECTED_CANDIDATE_TBL,
    }
    if not all(checks.values()):
        atomic_copy(backup_rom, MAIN)
        atomic_copy(backup_tbl, ACTIVE_TBL)
        raise PromotionError(f"post-promotion checks failed: {checks}")

    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_main_tip_user_reported_followup_candidate.py",
        "ok": True,
        "before": before,
        "after": after,
        "rollback_rom": identity(backup_rom),
        "rollback_tbl": identity(backup_tbl),
        "checks": checks,
    }
    atomic_json(POST_AUDIT, post)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    build["status"] = "promoted_to_current_main"
    build["promoted_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_json(BUILD_REPORT, build)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_main_tip_user_reported_followup_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "validation": validation,
        "before": before,
        "after": after,
        "rollback_rom": identity(backup_rom),
        "rollback_tbl": identity(backup_tbl),
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": checks,
        "saveram_policy": "live main SaveRAM remained byte-identical and was never replaced",
    }
    atomic_json(PROMOTION_REPORT, promotion)
    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
