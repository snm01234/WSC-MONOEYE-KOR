#!/usr/bin/env python3
"""Promote the user-approved 變形→변형 status-icon candidate into the main TIP.

ROM-only. Live SaveRAM is never replaced.
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
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "henkei_icon_ko_candidate.wsc"
BUILD_REPORT = PATCH / "henkei_icon_ko_candidate_report.json"
PROMOTION_REPORT = PATCH / "henkei_icon_ko_candidate_promotion_report.json"
POST_AUDIT = PATCH / "henkei_icon_ko_candidate_postpromotion_audit.json"
BACKUP_ROOT = PATCH / "backup"

EXPECTED_TIP_SHA = "7da763eb36fe86c3f0741459246098dbab3b3a49a8a76a95e9d842dc25857397"
EXPECTED_CANDIDATE_SHA = "e22ccc450c64f7751d61a80d6cd52f94363d981e5d5a7e1802afa57dfd224862"
EXPECTED_CHECKSUM = "A761"
EXPECTED_SOURCE_BLOB = "9c19ad401f3d61fef71d750b8a2db580d0f8877496a8c07a4a9b084b3f65424f"
EXPECTED_TARGET_BLOB = "39036ef286796b17a892a5dfdca347c0ba0b2f53e14dbcb1422d79961e582016"
PHYSICAL = 0xC0F638
BLOB = 0x120
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha256_path(path)}


def checksum_details(data: bytes) -> dict[str, Any]:
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}: {path.stat().st_size} != {size}")
    if expected_sha is not None and sha256_path(path).lower() != expected_sha.lower():
        raise PromotionError(f"SHA-256 drift: {path}: {sha256_path(path)}")


def atomic_copy(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(temporary, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def validate() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE_SHA)
    require(BUILD_REPORT)
    require(TIP_SAVE, size=SAVE_SIZE)

    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    if report.get("ok") is not True:
        raise PromotionError("candidate build report is not ok")
    if str(report["parent"]["sha256"]).lower() != EXPECTED_TIP_SHA:
        raise PromotionError("build report parent SHA drift")
    if str(report["candidate"]["sha256"]).lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build report candidate SHA drift")
    if report["candidate"].get("ws_checksum") != EXPECTED_CHECKSUM:
        raise PromotionError("build report checksum drift")
    if str(report["patch"]["source_sha256"]).lower() != EXPECTED_SOURCE_BLOB:
        raise PromotionError("source blob SHA drift")
    if str(report["patch"]["target_sha256"]).lower() != EXPECTED_TARGET_BLOB:
        raise PromotionError("target blob SHA drift")
    if report["patch"].get("logical") != "40F638":
        raise PromotionError("logical offset drift")
    if report["diff"].get("allowlist_clean") is not True:
        raise PromotionError("allowlist was not clean")
    guards = report.get("guards") or {}
    if not all(bool(value) for value in guards.values()):
        raise PromotionError(f"build guards failed: {guards}")

    candidate = CANDIDATE.read_bytes()
    parent = TIP.read_bytes()
    cand_blob = candidate[PHYSICAL : PHYSICAL + BLOB]
    parent_blob = parent[PHYSICAL : PHYSICAL + BLOB]
    if sha256_bytes(parent_blob) != EXPECTED_SOURCE_BLOB:
        raise PromotionError("parent still does not have stock 變形 blob")
    if sha256_bytes(cand_blob) != EXPECTED_TARGET_BLOB:
        raise PromotionError("candidate 24x24 blob drift")
    checksum = checksum_details(candidate)
    if not checksum["valid"] or checksum["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {checksum}")

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "main_saveram": identity(TIP_SAVE),
        "candidate_build_report": identity(BUILD_REPORT),
        "candidate_checksum": checksum,
        "user_runtime_validation": {
            "approved": True,
            "date": "2026-08-13",
            "statement": "사용자 실화면 확인 후 메인 승격 지시",
        },
        "saveram_policy": "ROM-only promotion; live main SaveRAM is never replaced",
    }


def audit(backup_rom: Path, save_before: dict[str, Any]) -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE_SHA)
    require(backup_rom, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    tip = TIP.read_bytes()
    checksum = checksum_details(tip)
    blob = tip[PHYSICAL : PHYSICAL + BLOB]
    save_after = identity(TIP_SAVE)
    checks = {
        "tip_matches_approved_candidate": sha256_path(TIP) == EXPECTED_CANDIDATE_SHA,
        "tip_checksum_valid": checksum["valid"] and checksum["stored"] == EXPECTED_CHECKSUM,
        "promoted_blob_matches_candidate": sha256_bytes(blob) == EXPECTED_TARGET_BLOB,
        "rollback_rom_preserved": sha256_path(backup_rom) == EXPECTED_TIP_SHA,
        "main_saveram_unchanged": save_after["sha256"] == save_before["sha256"],
        "backup_blob_still_stock": sha256_bytes(backup_rom.read_bytes()[PHYSICAL : PHYSICAL + BLOB])
        == EXPECTED_SOURCE_BLOB,
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    result = {
        "schema_version": 1,
        "generated_by": "tools/promote_henkei_icon_ko_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "tip_checksum": checksum,
        "rollback_rom": identity(backup_rom),
        "main_saveram_before": save_before,
        "main_saveram_after": save_after,
        "checks": checks,
    }
    atomic_json(POST_AUDIT, result)
    return result


def main() -> int:
    validation = validate()
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}_pre_henkei_icon_ko"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require(backup_rom, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)

    old_tip = identity(TIP)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP)
        post = audit(backup_rom, save_before)
    except Exception:
        atomic_copy(backup_rom, TIP)
        raise

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    build["status"] = "promoted_to_current_main"
    build["promotion"] = "promoted"
    build["promoted_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_json(BUILD_REPORT, build)

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_henkei_icon_ko_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": post["checks"],
        "main_saveram_policy": "live main SaveRAM remained byte-identical and was never replaced",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
