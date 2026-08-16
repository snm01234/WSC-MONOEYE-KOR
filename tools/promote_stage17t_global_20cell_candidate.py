#!/usr/bin/env python3
"""Promote the user-validated STAGE17t/global 20-cell candidate to the live main TIP.

The candidate is cumulative over the already-promoted STAGE16t/terminology main
and the validated `최후의 승리자` follow-up.  Promotion is ROM-only: the active
TBL is already byte-identical to the candidate TBL and live SaveRAM must remain
unchanged.
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
CANDIDATE = PATCH / "stage17t_global_20cell_followup_candidate.wsc"
CANDIDATE_TBL = PATCH / "stage17t_global_20cell_followup_candidate.tbl"
BUILD = PATCH / "stage17t_global_20cell_followup_candidate_report.json"
WIDTH = PATCH / "stage17t_global_dialogue_20cell_audit.json"
RUNTIME = PATCH / "stage17t_global_runtime_safety.json"
TERM = PATCH / "stage17t_global_terminology_audit.json"
MAPPING = PATCH / "stage17t_global_name_audit.json"
STRUCTURAL = PATCH / "stage17t_global_structural_followup_audit.json"
REPORT = PATCH / "stage17t_global_20cell_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "d5fb5d338875f9a5ff1071f04c3b042fcff1a3f38142aae09b6bf9e44ad0fac5"
EXPECTED_CANDIDATE = "d6b3caa433f174348e885c1eced9dae64a5ac8976a67ae0363a31d5cbe541f2e"
EXPECTED_TBL = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
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
    width = load_json(WIDTH)
    runtime = load_json(RUNTIME)
    term = load_json(TERM)
    mapping = load_json(MAPPING)
    structural = load_json(STRUCTURAL)

    if build.get("ok") is not True:
        raise PromotionError("candidate build report is not OK")
    candidate_id = ((build.get("outputs") or {}).get("candidate_rom") or {})
    if candidate_id.get("sha256") != EXPECTED_CANDIDATE:
        raise PromotionError("build report is not bound to expected candidate")
    checks = build.get("checks") or {}
    if not checks or not all(bool(value) for value in checks.values()):
        raise PromotionError(f"candidate build checks are not all true: {checks}")

    if width.get("status") != "pass":
        raise PromotionError("global 20-cell audit did not pass")
    wc = width.get("counts") or {}
    for key in ("over_20", "unreadable", "semantic_guard_failures"):
        if int(wc.get(key, -1)) != 0:
            raise PromotionError(f"global 20-cell audit finding {key}={wc.get(key)}")

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

    if structural.get("status") != "pass":
        raise PromotionError("global structural follow-up audit did not pass")
    if structural.get("confirmed_regressions"):
        raise PromotionError("global structural audit has confirmed regressions")
    if structural.get("high_confidence_review_addresses"):
        raise PromotionError("global structural audit has high-confidence review items")

    return [identity(path) for path in (BUILD, WIDTH, RUNTIME, TERM, MAPPING, STRUCTURAL)]


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(ACTIVE_TBL, sha=EXPECTED_TBL)
    require(LIVE_SAVE, size=SAVE_SIZE)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(CANDIDATE_TBL, sha=EXPECTED_TBL)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate WonderSwan checksum invalid")
    validated_reports = validate_reports()

    before = {
        "tip": identity(MAIN),
        "tbl": identity(ACTIVE_TBL),
        "saveram": identity(LIVE_SAVE),
    }
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_stage17t_global_20cell"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    shutil.copy2(MAIN, backup_rom)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_PARENT)

    try:
        atomic_copy(CANDIDATE, MAIN)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(ACTIVE_TBL, sha=EXPECTED_TBL)
        require(LIVE_SAVE, size=SAVE_SIZE, sha=before["saveram"]["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted main TIP checksum invalid")
    except Exception:
        atomic_copy(backup_rom, MAIN)
        raise

    after = {
        "tip": identity(MAIN),
        "tbl": identity(ACTIVE_TBL),
        "saveram": identity(LIVE_SAVE),
    }
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "active_tbl_unchanged": after["tbl"] == before["tbl"],
        "main_checksum_valid": checksum_valid(MAIN),
        "live_saveram_unchanged": after["saveram"] == before["saveram"],
        "rollback_rom_preserved": sha_path(backup_rom) == EXPECTED_PARENT,
        "candidate_preserved": sha_path(CANDIDATE) == EXPECTED_CANDIDATE,
        "candidate_tbl_preserved": sha_path(CANDIDATE_TBL) == EXPECTED_TBL,
    }
    if not all(checks.values()):
        atomic_copy(backup_rom, MAIN)
        raise PromotionError(f"post-promotion checks failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/promote_stage17t_global_20cell_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authorization": "사용자가 STAGE17t/global 20-cell candidate 실측 이상 없음 확인 후 v1.1 릴리스 승격을 요청함",
        "before": before,
        "after": after,
        "checks": checks,
        "rollback": {"rom": identity(backup_rom)},
        "validated_reports": validated_reports,
        "scope": [
            "validated 최후의 승리자 follow-up cumulative changes",
            "global 20-cell hard-gate rewrites across bank59 and scenario banks60-63",
            "STAGE17t semantic-spill correction",
            "라디쉬 terminology standardization in active records and dictionaries",
            "five-page alias-safe ext3 allocation and regression guards",
        ],
        "saveram_policy": "live SaveRAM remained byte-identical; candidate SaveRAM was not promoted",
    }
    atomic_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
