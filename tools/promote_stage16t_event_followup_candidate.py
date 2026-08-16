#!/usr/bin/env python3
"""Promote the user-validated STAGE16t event/terminology candidate.

The STAGE16t candidate is built on top of the terminology candidate that adds
생크 킹덤 / 팝티머스 시로코 / 드렌 / 바다뱀 and the EC84 뱀 glyph.  Promotion
therefore updates both the main TIP ROM and the active TBL.  The live SaveRAM is
never replaced.
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
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "stage16t_event_followup_candidate.wsc"
CANDIDATE_TBL = PATCH / "stage16t_event_followup_candidate.tbl"
BUILD = PATCH / "stage16t_event_followup_candidate_report.json"
RUNTIME = PATCH / "stage16t_event_followup_runtime_safety.json"
TERM = PATCH / "stage16t_event_followup_terminology_audit.json"
MAPPING = PATCH / "stage16t_event_followup_name_audit.json"
REPORT = PATCH / "stage16t_event_followup_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "4033c2bdc8f9d627beabaae65e69c43010f0523448ba30ec08f610529e0feb33"
EXPECTED_ACTIVE_TBL = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
EXPECTED_CANDIDATE = "d5fb5d338875f9a5ff1071f04c3b042fcff1a3f38142aae09b6bf9e44ad0fac5"
EXPECTED_CANDIDATE_TBL = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
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


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha_path(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size mismatch: {path}: {path.stat().st_size} != {size}")
    actual = sha_path(path)
    if sha is not None and actual != sha:
        raise PromotionError(f"SHA mismatch: {path}: {actual} != {sha}")


def load_json(path: Path) -> dict[str, Any]:
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
    build = load_json(BUILD)
    runtime = load_json(RUNTIME)
    term = load_json(TERM)
    mapping = load_json(MAPPING)

    if build.get("ok") is not True or build.get("status") != "candidate_static_verified":
        raise PromotionError("candidate build report is not statically verified")
    candidate_id = ((build.get("outputs") or {}).get("candidate_rom") or {})
    candidate_tbl_id = ((build.get("outputs") or {}).get("candidate_tbl") or {})
    if candidate_id.get("sha256") != EXPECTED_CANDIDATE:
        raise PromotionError("build report is not bound to the expected candidate ROM")
    if candidate_tbl_id.get("sha256") != EXPECTED_CANDIDATE_TBL:
        raise PromotionError("build report is not bound to the expected candidate TBL")
    checks = build.get("checks") or {}
    if not checks or not all(bool(value) for value in checks.values()):
        raise PromotionError(f"candidate build checks are not all true: {checks}")

    if runtime.get("ok") is not True:
        raise PromotionError("runtime safety gate is not clean")
    rc = runtime.get("counts") or {}
    if int(rc.get("hard_failures", -1)) != 0 or int(rc.get("review_items", -1)) != 0:
        raise PromotionError(f"runtime safety gate has findings: {rc}")

    if term.get("status") != "clean":
        raise PromotionError("terminology audit is not clean")
    tc = term.get("counts") or {}
    for key in ("active_source_hits", "dictionary_hits", "five_bank_dictionary_hits", "rendered_record_hits"):
        if int(tc.get(key, -1)) != 0:
            raise PromotionError(f"terminology audit finding {key}={tc.get(key)}")

    if mapping.get("status") != "clean":
        raise PromotionError("name mapping audit is not clean")
    mc = mapping.get("counts") or {}
    for key in (
        "catalog_conflicts_actionable",
        "active_source_forbidden_hits",
        "current_tip_dictionary_forbidden_hits",
        "current_tip_five_bank_dictionary_forbidden_hits",
        "current_tip_inventory_forbidden_hits",
        "current_tip_complete_bank5c_forbidden_hits",
    ):
        if int(mc.get(key, -1)) != 0:
            raise PromotionError(f"name mapping audit finding {key}={mc.get(key)}")

    return [identity(path) for path in (BUILD, RUNTIME, TERM, MAPPING)]


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(ACTIVE_TBL, sha=EXPECTED_ACTIVE_TBL)
    require(LIVE_SAVE, size=SAVE_SIZE)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(CANDIDATE_TBL, sha=EXPECTED_CANDIDATE_TBL)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate WonderSwan checksum invalid")
    validated_reports = validate_reports()

    before = {
        "tip": identity(MAIN),
        "tbl": identity(ACTIVE_TBL),
        "saveram": identity(LIVE_SAVE),
    }
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_stage16t_event_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    backup_tbl = backup_dir / ACTIVE_TBL.name
    shutil.copy2(MAIN, backup_rom)
    shutil.copy2(ACTIVE_TBL, backup_tbl)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(backup_tbl, sha=EXPECTED_ACTIVE_TBL)

    try:
        atomic_copy(CANDIDATE, MAIN)
        atomic_copy(CANDIDATE_TBL, ACTIVE_TBL)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(ACTIVE_TBL, sha=EXPECTED_CANDIDATE_TBL)
        require(LIVE_SAVE, size=SAVE_SIZE, sha=before["saveram"]["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted main TIP checksum invalid")
    except Exception:
        atomic_copy(backup_rom, MAIN)
        atomic_copy(backup_tbl, ACTIVE_TBL)
        raise

    after = {
        "tip": identity(MAIN),
        "tbl": identity(ACTIVE_TBL),
        "saveram": identity(LIVE_SAVE),
    }
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "active_tbl_matches_candidate_tbl": after["tbl"]["sha256"] == EXPECTED_CANDIDATE_TBL,
        "main_checksum_valid": checksum_valid(MAIN),
        "live_saveram_unchanged": after["saveram"] == before["saveram"],
        "rollback_rom_preserved": sha_path(backup_rom) == EXPECTED_PARENT,
        "rollback_tbl_preserved": sha_path(backup_tbl) == EXPECTED_ACTIVE_TBL,
        "candidate_preserved": sha_path(CANDIDATE) == EXPECTED_CANDIDATE,
        "candidate_tbl_preserved": sha_path(CANDIDATE_TBL) == EXPECTED_CANDIDATE_TBL,
    }
    if not all(checks.values()):
        atomic_copy(backup_rom, MAIN)
        atomic_copy(backup_tbl, ACTIVE_TBL)
        raise PromotionError(f"post-promotion checks failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/promote_stage16t_event_followup_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": after,
        "checks": checks,
        "rollback": {"rom": identity(backup_rom), "tbl": identity(backup_tbl)},
        "validated_reports": validated_reports,
        "scope": [
            "terminology bundle: 생크 킹덤 / 팝티머스 시로코 / 드렌 / 바다뱀",
            "EC84 뱀 glyph and candidate TBL activation",
            "STAGE16t 33-context retranslation",
            "623DC6 / 623DD7 / 624271 native-only runtime structure repair",
            "runtime contract regression guards for the three user-validated addresses",
        ],
        "saveram_policy": "live SaveRAM remained byte-identical; candidate SaveRAM was not promoted",
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
