#!/usr/bin/env python3
"""Atomically promote the verified name-mapping + spirit-text candidate."""
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
CANDIDATE = PATCH / "main_tip_name_mapping_spirit_combined_candidate.wsc"
BUILD = PATCH / "main_tip_name_mapping_spirit_combined_candidate_report.json"
SPIRIT_AUDIT = PATCH / "main_tip_name_mapping_spirit_combined_candidate_spirit_audit.json"
TERM_AUDIT = PATCH / "main_tip_name_mapping_spirit_combined_candidate_terminology_audit.json"
MAPPING_AUDIT = PATCH / "main_tip_name_mapping_spirit_combined_candidate_mapping_audit.json"
AMBIGUOUS_AUDIT = PATCH / "main_tip_name_mapping_spirit_combined_candidate_ambiguous_tbl_audit.json"
FALSE_SEGPTR = PATCH / "main_tip_name_mapping_spirit_combined_candidate_false_segptr.json"
REPORT = PATCH / "main_tip_name_mapping_spirit_combined_promotion_report.json"
POST = PATCH / "main_tip_name_mapping_spirit_combined_postpromotion_audit.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"
EXPECTED_CANDIDATE = "528f28e1050257e9f3698f27cf9aa577b217c67cd8951d6030cc5592fc6e0e85"
EXPECTED_NAME = "15d34aa387b78e87110b43723b2ccd3cccf9301601a2a57f165a6e652e86e590"
EXPECTED_SPIRIT = "f730c831a70a6f55fe563c121b645eb6e54a088671d4411191ae0f5ed8518dfe"
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


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha_path(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"SHA drift: {path}: {sha_path(path)}")


def load(path: Path) -> dict[str, Any]:
    require(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"report root is not an object: {path}")
    return value


def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def validate_reports() -> list[dict[str, Any]]:
    build = load(BUILD)
    spirit = load(SPIRIT_AUDIT)
    term = load(TERM_AUDIT)
    mapping = load(MAPPING_AUDIT)
    ambiguous = load(AMBIGUOUS_AUDIT)
    segptr = load(FALSE_SEGPTR)

    if build.get("ok") is not True or build.get("promotion_allowed") is not True:
        raise PromotionError("combined build is not promotion-ready")
    if (build.get("parent") or {}).get("sha256") != EXPECTED_PARENT:
        raise PromotionError("combined parent binding failed")
    if (build.get("candidate") or {}).get("sha256") != EXPECTED_CANDIDATE:
        raise PromotionError("combined candidate binding failed")
    sources = build.get("sources") or {}
    if (sources.get("name_mapping") or {}).get("sha256") != EXPECTED_NAME:
        raise PromotionError("name source binding failed")
    if (sources.get("spirit_mental_cmd") or {}).get("sha256") != EXPECTED_SPIRIT:
        raise PromotionError("spirit source binding failed")
    if not all(bool(value) for value in (build.get("checks") or {}).values()):
        raise PromotionError("combined build check failed")

    if (
        spirit.get("ok") is not True
        or spirit.get("promotion_allowed") is not True
        or int(spirit.get("applied_count", -1)) != 54
        or spirit.get("render_failures")
        or spirit.get("invariance_failures")
        or spirit.get("unaccounted_diff_runs")
    ):
        raise PromotionError("spirit record audit failed")
    tc = term.get("counts") or {}
    if term.get("status") != "clean" or any(
        int(tc.get(key, -1)) != 0
        for key in (
            "active_source_hits",
            "dictionary_hits",
            "five_bank_dictionary_hits",
            "rendered_record_hits",
        )
    ):
        raise PromotionError("terminology audit failed")
    mc = mapping.get("counts") or {}
    if mapping.get("status") != "clean" or any(
        int(mc.get(key, -1)) != 0
        for key in (
            "catalog_conflicts_actionable",
            "active_source_forbidden_hits",
            "current_tip_dictionary_forbidden_hits",
            "current_tip_five_bank_dictionary_forbidden_hits",
            "current_tip_inventory_forbidden_hits",
            "current_tip_complete_bank5c_forbidden_hits",
        )
    ):
        raise PromotionError("name-mapping audit failed")
    if ambiguous.get("status") != "clean" or int((ambiguous.get("counts") or {}).get("mismatches", -1)) != 0:
        raise PromotionError("ambiguous TBL audit failed")
    if segptr.get("ok") is not True or int(segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    return [identity(path) for path in (BUILD, SPIRIT_AUDIT, TERM_AUDIT, MAPPING_AUDIT, AMBIGUOUS_AUDIT, FALSE_SEGPTR)]


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(TBL, sha=EXPECTED_TBL)
    require(SAVE, size=SAVE_SIZE)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate checksum invalid")
    reports = validate_reports()

    before = {"tip": identity(MAIN), "tbl": identity(TBL), "saveram": identity(SAVE)}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_name_mapping_spirit_combined"
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
            raise PromotionError("promoted checksum invalid")
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

    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_main_tip_name_mapping_spirit_combined_candidate.py",
        "ok": True,
        "before": before,
        "after": after,
        "checks": checks,
        "rollback_rom": identity(backup_rom),
    }
    atomic_json(POST, post)
    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_main_tip_name_mapping_spirit_combined_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": after,
        "checks": checks,
        "rollback_rom": identity(backup_rom),
        "validated_reports": reports,
        "postpromotion_audit": identity(POST),
        "saveram_policy": "live SaveRAM remained byte-identical; only the main TIP was promoted",
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
