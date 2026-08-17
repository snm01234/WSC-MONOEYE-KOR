#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "scenario_continuation_native_followup_v4_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD = PATCH / "scenario_continuation_native_followup_v4_candidate_report.json"
RUNTIME = PATCH / "scenario_continuation_native_followup_v4_runtime_safety.json"
BATTLE = PATCH / "scenario_continuation_native_followup_v4_battle_audit.json"
TERM = PATCH / "scenario_continuation_native_followup_v4_terminology_audit.json"
REPORT = PATCH / "scenario_continuation_native_followup_v4_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "2bccff99a36cd453bf01225365a40ed4d744049e69d46e726ab427851afa1799"
EXPECTED_CANDIDATE = "fc7c3a426c866f8b60f5056571349c79d6ba11a2632beee4209dfebbf8a0c5e9"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

class PromotionError(RuntimeError):
    pass

def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")

def identity(path: Path) -> dict:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha_path(path)}

def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size mismatch: {path}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"SHA mismatch: {path}: {sha_path(path)} != {sha}")

def load_json(path: Path) -> dict:
    require(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"invalid report object: {path}")
    return value

def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")

def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush(); os.fsync(dst.fileno())
    os.replace(tmp, target)

def atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)

def validate_reports() -> list[dict]:
    build = load_json(BUILD)
    runtime = load_json(RUNTIME)
    battle = load_json(BATTLE)
    term = load_json(TERM)
    if ((build.get("candidate") or {}).get("sha256")) != EXPECTED_CANDIDATE:
        raise PromotionError("build report not bound to candidate")
    katejina = build.get("katejina") or {}
    if katejina.get("abs") != "63463A" or katejina.get("after_hex") != "173418F10EFF08":
        raise PromotionError("Katejina runtime fix not present in build report")
    doctor = build.get("doctor_j") or {}
    if doctor.get("wrapper_after") != "F05E01EC8DF5C5F418":
        raise PromotionError("Doctor J wrapper fix not present in build report")
    if runtime.get("ok") is not True:
        raise PromotionError("runtime safety not clean")
    rc = runtime.get("counts") or {}
    if int(rc.get("hard_failures", -1)) != 0 or int(rc.get("review_items", -1)) != 0:
        raise PromotionError(f"runtime findings: {rc}")
    if battle.get("ok") is not True or int((battle.get("counts") or {}).get("failures", -1)) != 0:
        raise PromotionError("battle regression audit not clean")
    if term.get("status") != "clean":
        raise PromotionError("terminology audit not clean")
    return [identity(p) for p in (BUILD, RUNTIME, BATTLE, TERM)]

def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(LIVE_SAVE, size=SAVE_SIZE)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate checksum invalid")
    reports = validate_reports()
    before = {"tip": identity(MAIN), "saveram": identity(LIVE_SAVE)}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_scenario_continuation_native_followup_v4"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    shutil.copy2(MAIN, backup_rom)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_PARENT)
    try:
        atomic_copy(CANDIDATE, MAIN)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(LIVE_SAVE, size=SAVE_SIZE, sha=before["saveram"]["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted checksum invalid")
    except Exception:
        atomic_copy(backup_rom, MAIN)
        raise
    after = {"tip": identity(MAIN), "saveram": identity(LIVE_SAVE)}
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "main_checksum_valid": checksum_valid(MAIN),
        "live_saveram_unchanged": after["saveram"] == before["saveram"],
        "rollback_preserved": sha_path(backup_rom) == EXPECTED_PARENT,
    }
    if not all(checks.values()):
        atomic_copy(backup_rom, MAIN)
        raise PromotionError(f"post-promotion checks failed: {checks}")
    payload = {
        "schema_version": 1,
        "generated_by": "tools/promote_scenario_continuation_native_followup_v4_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": after,
        "checks": checks,
        "rollback": identity(backup_rom),
        "validated_reports": reports,
        "scope": [
            "STAGE21t Katejina 63463A: restore two-token native scenario-first grammar",
            "STAGE21t Doctor J 635866/635C0C: explicit Hangul restart after ideographic space in 0EF3 wrapper",
            "runtime contract guards for 63463A and Doctor J follow-up rows",
        ],
        "saveram_policy": "live SaveRAM preserved; candidate SaveRAM not promoted",
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
