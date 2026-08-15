#!/usr/bin/env python3
"""Promote the verified terminology + scenario false-lead candidate."""
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
CANDIDATE = PATCH / "user_terminology_scenario_followup_candidate.wsc"
TERM_BUILD = PATCH / "terminology_consistency_followup_candidate_report.json"
BUILD = PATCH / "user_terminology_scenario_followup_candidate_report.json"
TERM_AUDIT = PATCH / "user_terminology_scenario_followup_terminology_audit.json"
SEMANTIC_AUDIT = PATCH / "user_terminology_scenario_followup_semantic_audit.json"
AMBIGUOUS_AUDIT = PATCH / "user_terminology_scenario_followup_ambiguous_tbl_audit.json"
FALSE_SEGPTR = PATCH / "user_terminology_scenario_followup_false_segptr.json"
RUNTIME_GATE = PATCH / "user_terminology_scenario_followup_runtime_safety_gate.json"
REPORT = PATCH / "user_terminology_scenario_followup_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "4e1453f0d6bc1ad7be1431b617be8da772104f1a9a49d31261897acd332584db"
EXPECTED_TERM_CANDIDATE = "80ab1e531cda254479266f8a6f43008d27857a14be5ae5dd35d904303015b00e"
EXPECTED_CANDIDATE = "07fc69179b097d6f351b9155c93c454fb820c4ec4fd9347ae270f2d0e413232c"
EXPECTED_TBL = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha_path(path),
    }


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"SHA drift: {path}: {sha_path(path)}")


def load(path: Path) -> dict[str, Any]:
    require(path)
    return json.loads(path.read_text(encoding="utf-8"))


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


def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(TBL, sha=EXPECTED_TBL)
    require(SAVE, size=SAVE_SIZE)

    term_build = load(TERM_BUILD)
    build = load(BUILD)
    term_audit = load(TERM_AUDIT)
    semantic = load(SEMANTIC_AUDIT)
    ambiguous = load(AMBIGUOUS_AUDIT)
    segptr = load(FALSE_SEGPTR)
    runtime = load(RUNTIME_GATE)
    if term_build.get("ok") is not True or term_build.get("outputs", {}).get("candidate_rom", {}).get("sha256") != EXPECTED_TERM_CANDIDATE:
        raise PromotionError("terminology build binding failed")
    if build.get("ok") is not True or build.get("promotion_allowed") is not True:
        raise PromotionError("combined build is not promotion-ready")
    if build.get("inputs", {}).get("terminology_parent", {}).get("sha256") != EXPECTED_TERM_CANDIDATE:
        raise PromotionError("combined parent chain failed")
    if build.get("outputs", {}).get("candidate_rom", {}).get("sha256") != EXPECTED_CANDIDATE:
        raise PromotionError("combined candidate binding failed")
    if not all(bool(value) for value in build.get("checks", {}).values()):
        raise PromotionError("combined build check failed")
    if term_audit.get("status") != "clean" or any(int(term_audit.get("counts", {}).get(key, -1)) != 0 for key in (
        "active_source_hits", "dictionary_hits", "rendered_record_hits"
    )):
        raise PromotionError("terminology audit failed")
    sc = semantic.get("counts", {})
    if semantic.get("status") != "clean" or int(sc.get("semantic_false_lead_candidates", -1)) != 5 or int(sc.get("fixed", -1)) != 5 or int(sc.get("failures", -1)) != 0 or int(sc.get("unresolved_semantic_candidates", -1)) != 0:
        raise PromotionError("semantic false-lead audit failed")
    if ambiguous.get("status") != "clean" or int(ambiguous.get("counts", {}).get("mismatches", -1)) != 0:
        raise PromotionError("ambiguous TBL audit failed")
    if segptr.get("ok") is not True or int(segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    rc = runtime.get("counts", {})
    if runtime.get("ok") is not True or int(rc.get("hard_failures", -1)) != 0 or int(rc.get("review_items", -1)) != 0:
        raise PromotionError("runtime dialogue safety gate failed")
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate checksum invalid")

    save_before = identity(SAVE)
    before = {"tip": identity(MAIN), "tbl": identity(TBL), "saveram": save_before}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_user_terminology_scenario_followup"
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
        raise PromotionError(f"post-promotion check failed: {checks}")
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_user_terminology_scenario_followup_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": after,
        "checks": checks,
        "rollback_rom": identity(backup_rom),
        "rollback_tbl": identity(backup_tbl),
        "validated_reports": [identity(path) for path in (
            TERM_BUILD, BUILD, TERM_AUDIT, SEMANTIC_AUDIT, AMBIGUOUS_AUDIT, FALSE_SEGPTR, RUNTIME_GATE
        )],
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
