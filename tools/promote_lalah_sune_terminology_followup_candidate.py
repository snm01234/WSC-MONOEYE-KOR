#!/usr/bin/env python3
"""Promote the verified Lalah Sune terminology follow-up candidate."""
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
CANDIDATE = PATCH / "main_tip_name_mapping_consistency_candidate.wsc"
TBL = PATCH / "hangul_patch_pad3.tbl"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD = PATCH / "main_tip_name_mapping_consistency_candidate_report.json"
TERM = PATCH / "lalah_sune_candidate_terminology_audit.json"
MAPPING = PATCH / "lalah_sune_candidate_mapping_audit.json"
SEGPTR = PATCH / "lalah_sune_candidate_false_segptr.json"
AMBIG = PATCH / "lalah_sune_candidate_ambiguous_tbl_audit.json"
REPORT = PATCH / "lalah_sune_terminology_followup_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "9321489d0c61144dda41036a268c29a419973fb3b00918bb37b8d47548a10ca3"
EXPECTED_CANDIDATE = "d7543ad4a62d9e7a9687583e85005dc4ca137e6fa62238eb70e58492248985c9"
EXPECTED_TBL = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha_path(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size mismatch: {path}")
    actual = sha_path(path)
    if sha is not None and actual != sha:
        raise PromotionError(f"SHA mismatch: {path}: {actual}")


def load(path: Path) -> dict[str, Any]:
    require(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"report is not an object: {path}")
    return value


def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def validate_reports() -> list[dict[str, Any]]:
    build = load(BUILD)
    term = load(TERM)
    mapping = load(MAPPING)
    segptr = load(SEGPTR)
    ambig = load(AMBIG)

    if build.get("ok") is not True or build.get("status") != "candidate_static_verified":
        raise PromotionError("candidate build report is not verified")
    candidate_id = ((build.get("outputs") or {}).get("candidate_rom") or {})
    if candidate_id.get("sha256") != EXPECTED_CANDIDATE:
        raise PromotionError("candidate build report SHA binding failed")
    if not all(bool(v) for v in (build.get("checks") or {}).values()):
        raise PromotionError("candidate build checks failed")

    tc = term.get("counts") or {}
    if term.get("status") != "clean" or any(int(tc.get(k, -1)) != 0 for k in (
        "active_source_hits", "dictionary_hits", "five_bank_dictionary_hits", "rendered_record_hits"
    )):
        raise PromotionError("terminology audit is not clean")

    mc = mapping.get("counts") or {}
    if mapping.get("status") != "clean" or any(int(mc.get(k, -1)) != 0 for k in (
        "catalog_conflicts_actionable", "active_source_forbidden_hits",
        "current_tip_dictionary_forbidden_hits", "current_tip_five_bank_dictionary_forbidden_hits",
        "current_tip_inventory_forbidden_hits", "current_tip_complete_bank5c_forbidden_hits"
    )):
        raise PromotionError("name mapping audit is not clean")

    if segptr.get("ok") is not True or int(segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    if ambig.get("status") != "clean" or int((ambig.get("counts") or {}).get("mismatches", -1)) != 0:
        raise PromotionError("ambiguous TBL preservation audit failed")
    return [identity(p) for p in (BUILD, TERM, MAPPING, SEGPTR, AMBIG)]


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(TBL, sha=EXPECTED_TBL)
    require(SAVE, size=SAVE_SIZE)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate WonderSwan checksum invalid")
    reports = validate_reports()

    before = {"tip": identity(MAIN), "tbl": identity(TBL), "saveram": identity(SAVE)}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_lalah_sune_terminology_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    shutil.copy2(MAIN, backup_rom)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_PARENT)

    try:
        atomic_copy(CANDIDATE, MAIN)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(TBL, sha=EXPECTED_TBL)
        require(SAVE, size=SAVE_SIZE, sha=before["saveram"]["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted WonderSwan checksum invalid")
    except Exception:
        atomic_copy(backup_rom, MAIN)
        raise

    after = {"tip": identity(MAIN), "tbl": identity(TBL), "saveram": identity(SAVE)}
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "main_checksum_valid": checksum_valid(MAIN),
        "active_tbl_unchanged": after["tbl"] == before["tbl"],
        "live_saveram_unchanged": after["saveram"] == before["saveram"],
        "rollback_rom_preserved": sha_path(backup_rom) == EXPECTED_PARENT,
        "candidate_preserved": sha_path(CANDIDATE) == EXPECTED_CANDIDATE,
    }
    if not all(checks.values()):
        atomic_copy(backup_rom, MAIN)
        raise PromotionError(f"post-promotion checks failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/promote_lalah_sune_terminology_followup_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": after,
        "checks": checks,
        "rollback_rom": identity(backup_rom),
        "validated_reports": reports,
        "scope": [
            "도감/보조 레코드 라라아・순 -> 라라아・슨",
            "시나리오 전체명 라라 슨 -> 라라아 슨",
            "번역 소스/용어 표준/시트 재적용 데이터 라라아 순 계열 -> 라라아 슨",
        ],
        "saveram_policy": "live SaveRAM remained byte-identical; only the main TIP ROM was promoted",
    }
    atomic_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
