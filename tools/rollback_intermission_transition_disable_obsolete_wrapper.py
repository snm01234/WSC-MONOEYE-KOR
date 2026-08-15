#!/usr/bin/env python3
"""Rollback the rejected intermission wrapper-disable promotion."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BACKUP = (
    PATCH
    / "backup/20260809_071007_pre_intermission_transition_wrapper_disable"
    / "monoeye_ko_expanded.wsc"
)
REPORT = PATCH / "intermission_transition_disable_obsolete_wrapper_candidate/rollback_report.json"

REJECTED_SHA256 = "f4e483ed17919bb233ab44b3f71265239e284c29540093544c28527e6ede692d"
RESTORED_SHA256 = "163e8e6e4984e866b1a64d92f44765197df30c6281c92adf75acd6e552ad928a"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def main() -> int:
    rejected = MAIN.read_bytes()
    restored = BACKUP.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    if digest(rejected) != REJECTED_SHA256:
        raise RuntimeError("current main is not the rejected promotion")
    if digest(restored) != RESTORED_SHA256:
        raise RuntimeError("rollback backup identity drifted")

    atomic_bytes(MAIN, restored)
    main_after = MAIN.read_bytes()
    save_after = MAIN_SAVE.read_bytes()
    checks = {
        "main_restored_byte_identical_to_backup": main_after == restored,
        "restored_main_hash_exact": digest(main_after) == RESTORED_SHA256,
        "main_saveram_unchanged": save_after == save_before,
    }
    if not all(checks.values()):
        raise RuntimeError(f"rollback checks failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/rollback_intermission_transition_disable_obsolete_wrapper.py",
        "ok": True,
        "rolled_back": True,
        "reason": "User runtime capture proved the wrapper-disable candidate reverted the intended clean rearranged UI and increased corruption.",
        "timestamp_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rejected_main_sha256": REJECTED_SHA256,
        "restored_main_sha256": RESTORED_SHA256,
        "rollback_source": str(BACKUP.relative_to(ROOT)).replace("\\", "/"),
        "main_saveram_sha256": digest(save_after),
        "checks": checks,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
