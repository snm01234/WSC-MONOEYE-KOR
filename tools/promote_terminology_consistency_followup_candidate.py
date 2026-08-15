#!/usr/bin/env python3
"""Promote the verified terminology-consistency candidate to current main."""
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
TBL = PATCH / "hangul_patch_pad3.tbl"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "terminology_consistency_followup_candidate.wsc"
BUILD = PATCH / "terminology_consistency_followup_candidate_report.json"
AUDIT = PATCH / "terminology_consistency_followup_candidate_audit.json"
AMBIGUOUS = PATCH / "terminology_consistency_followup_candidate_ambiguous_tbl_audit.json"
FALSE_SEGPTR = PATCH / "terminology_consistency_followup_candidate_false_segptr.json"
REPORT = PATCH / "terminology_consistency_followup_promotion_report.json"
POST = PATCH / "terminology_consistency_followup_postpromotion_audit.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "f16eb1283cd26104c3035376de7ec9bec6700dea691b8de491fab342a68de55b"
EXPECTED_CANDIDATE = "4e1453f0d6bc1ad7be1431b617be8da772104f1a9a49d31261897acd332584db"
EXPECTED_TBL = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
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


def identity(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha_path(path),
    }


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"SHA-256 drift: {path}: {sha_path(path)}")


def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(TBL, sha=EXPECTED_TBL)
    require(SAVE, size=SAVE_SIZE)
    for path in (BUILD, AUDIT, AMBIGUOUS, FALSE_SEGPTR):
        require(path)
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    ambiguous = json.loads(AMBIGUOUS.read_text(encoding="utf-8"))
    segptr = json.loads(FALSE_SEGPTR.read_text(encoding="utf-8"))
    if build.get("ok") is not True or build.get("promotion_allowed") is not True:
        raise PromotionError("candidate build is not promotion-ready")
    if build["outputs"]["candidate_rom"]["sha256"] != EXPECTED_CANDIDATE:
        raise PromotionError("candidate report binding drifted")
    if not all(bool(value) for value in build.get("checks", {}).values()):
        raise PromotionError("candidate build check failed")
    counts = audit.get("counts") or {}
    if audit.get("status") != "clean" or any(int(counts.get(key, -1)) != 0 for key in (
        "active_source_hits", "dictionary_hits", "rendered_record_hits"
    )):
        raise PromotionError("terminology audit is not clean")
    ambiguous_mismatches = int((ambiguous.get("counts") or {}).get("mismatches", -1))
    if ambiguous.get("status") != "clean" or ambiguous_mismatches != 0:
        raise PromotionError("ambiguous TBL raw-code audit failed")
    if segptr.get("ok") is not True or int(segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate checksum invalid")

    save_before = identity(SAVE)
    before = {"tip": identity(MAIN), "tbl": identity(TBL), "saveram": save_before}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_terminology_consistency_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    backup_tbl = backup_dir / TBL.name
    shutil.copy2(MAIN, backup_rom)
    shutil.copy2(TBL, backup_tbl)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(backup_tbl, sha=EXPECTED_TBL)

    try:
        atomic_copy(CANDIDATE, MAIN)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(TBL, sha=EXPECTED_TBL)
        require(SAVE, size=SAVE_SIZE, sha=save_before["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted main checksum invalid")
    except Exception:
        atomic_copy(backup_rom, MAIN)
        raise

    after = {"tip": identity(MAIN), "tbl": identity(TBL), "saveram": identity(SAVE)}
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "active_tbl_unchanged": after["tbl"] == before["tbl"],
        "live_saveram_unchanged": after["saveram"] == save_before,
        "main_checksum_valid": checksum_valid(MAIN),
        "rollback_rom_preserved": sha_path(backup_rom) == EXPECTED_PARENT,
        "rollback_tbl_preserved": sha_path(backup_tbl) == EXPECTED_TBL,
    }
    if not all(checks.values()):
        atomic_copy(backup_rom, MAIN)
        raise PromotionError(f"post-promotion checks failed: {checks}")
    post = {
        "schema_version": 1,
        "ok": True,
        "before": before,
        "after": after,
        "checks": checks,
        "rollback_rom": identity(backup_rom),
        "rollback_tbl": identity(backup_tbl),
    }
    atomic_json(POST, post)
    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_terminology_consistency_followup_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": after,
        "checks": checks,
        "rollback_rom": identity(backup_rom),
        "rollback_tbl": identity(backup_tbl),
        "candidate_build": identity(BUILD),
        "candidate_audit": identity(AUDIT),
        "ambiguous_tbl_audit": identity(AMBIGUOUS),
        "false_segptr_audit": identity(FALSE_SEGPTR),
        "postpromotion_audit": identity(POST),
    }
    atomic_json(REPORT, promotion)
    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
