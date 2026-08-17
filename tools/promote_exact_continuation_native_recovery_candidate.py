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
CANDIDATE = PATCH / "exact_continuation_native_recovery_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STATIC_GATE = PATCH / "exact_continuation_native_recovery_static_gate.json"
REPORT = PATCH / "exact_continuation_native_recovery_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "fc7c3a426c866f8b60f5056571349c79d6ba11a2632beee4209dfebbf8a0c5e9"
EXPECTED_CANDIDATE = "f68b3261beecc32047d17952e36bc2b891cd5d66410f9fc9293487571a0fc8e2"
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

def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(LIVE_SAVE, size=SAVE_SIZE)
    require(STATIC_GATE)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate checksum invalid")
    gate = json.loads(STATIC_GATE.read_text(encoding="utf-8"))
    if gate.get("ok") is not True or int((gate.get("counts") or {}).get("failed", -1)) != 0:
        raise PromotionError("static gate is not clean")
    if ((gate.get("candidate") or {}).get("sha256")) != EXPECTED_CANDIDATE:
        raise PromotionError("static gate is not bound to candidate")

    before = {"tip": identity(MAIN), "saveram": identity(LIVE_SAVE)}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_exact_continuation_native_recovery"
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
        "static_gate_clean": gate.get("ok") is True and int((gate.get("counts") or {}).get("failed", -1)) == 0,
    }
    if not all(checks.values()):
        atomic_copy(backup_rom, MAIN)
        raise PromotionError(f"post-promotion checks failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/promote_exact_continuation_native_recovery_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authorization": "user explicitly requested promotion after runtime measurement of 60D194 / 큭……",
        "runtime_validation_recorded": [
            {"abs": "60D194", "bundle": "scenario_60D17C", "expected": "큭……", "result": "pass"}
        ],
        "before": before,
        "after": after,
        "checks": checks,
        "rollback": identity(backup_rom),
        "static_gate": identity(STATIC_GATE),
        "scope": [
            "9 selected exact-continuation records restored to native 18+dict2+dict2 grammar",
            "5 bank10 helper IDs allocated/reclaimed with canonical duplicate retargeting",
            "21 duplicate syntactic script consumers retargeted length-preservingly",
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
