#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CAND = PATCH / "diana_original_control_restore_candidate.wsc"
ACTIVE_TBL = PATCH / "hangul_patch_pad3.tbl"
CAND_TBL = PATCH / "diana_original_control_restore_candidate.tbl"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = PATCH / "diana_original_control_restore_v1_1_promotion_report.json"

EXPECTED_MAIN = "d6b3caa433f174348e885c1eced9dae64a5ac8976a67ae0363a31d5cbe541f2e"
EXPECTED_CAND = "f62f14d15f3d76ad2eb33e2b55531ab4781d230cb63bcc2348bbd703d8c39be3"
EXPECTED_ACTIVE_TBL = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
EXPECTED_CAND_TBL = "9d31d1fd1a5022b54f83866d285f4d656461b30d4046ef2b6261a9441abca914"
ROM_SIZE = 16_777_216
TBL_SIZE = 30_404
SAVE_SIZE = 32_768
EXPECTED_CHECKSUM = 0x6564


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def main() -> int:
    main_before = MAIN.read_bytes()
    cand = CAND.read_bytes()
    tbl_before = ACTIVE_TBL.read_bytes()
    cand_tbl = CAND_TBL.read_bytes()
    save_before = LIVE_SAVE.read_bytes()

    checks = {
        "main_sha": sha(main_before),
        "candidate_sha": sha(cand),
        "active_tbl_sha": sha(tbl_before),
        "candidate_tbl_sha": sha(cand_tbl),
        "live_save_sha": sha(save_before),
    }
    if len(main_before) != ROM_SIZE or checks["main_sha"] != EXPECTED_MAIN:
        raise SystemExit(f"main drifted: {checks['main_sha']}")
    if len(cand) != ROM_SIZE or checks["candidate_sha"] != EXPECTED_CAND:
        raise SystemExit(f"candidate drifted: {checks['candidate_sha']}")
    if checks["active_tbl_sha"] != EXPECTED_ACTIVE_TBL:
        raise SystemExit(f"active TBL drifted: {checks['active_tbl_sha']}")
    if len(cand_tbl) != TBL_SIZE or checks["candidate_tbl_sha"] != EXPECTED_CAND_TBL:
        raise SystemExit(f"candidate TBL drifted: {checks['candidate_tbl_sha']}")
    if len(save_before) != SAVE_SIZE:
        raise SystemExit(f"live SaveRAM size drifted: {len(save_before)}")
    stored = int.from_bytes(cand[-2:], "little")
    calc = sum(cand[:-2]) & 0xFFFF
    if stored != calc or stored != EXPECTED_CHECKSUM:
        raise SystemExit(f"candidate checksum mismatch: stored={stored:04X} calc={calc:04X}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = PATCH / "backup" / f"{stamp}_pre_v1_1_20260816_final"
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(MAIN, backup / MAIN.name)
    shutil.copy2(ACTIVE_TBL, backup / ACTIVE_TBL.name)

    if sha((backup / MAIN.name).read_bytes()) != EXPECTED_MAIN:
        raise SystemExit("main backup verification failed")
    if sha((backup / ACTIVE_TBL.name).read_bytes()) != EXPECTED_ACTIVE_TBL:
        raise SystemExit("TBL backup verification failed")

    try:
        atomic_bytes(MAIN, cand)
        atomic_bytes(ACTIVE_TBL, cand_tbl)
        if sha(MAIN.read_bytes()) != EXPECTED_CAND:
            raise RuntimeError("main promotion verification failed")
        if sha(ACTIVE_TBL.read_bytes()) != EXPECTED_CAND_TBL:
            raise RuntimeError("TBL promotion verification failed")
        if LIVE_SAVE.read_bytes() != save_before:
            raise RuntimeError("live SaveRAM changed during promotion")
    except Exception:
        atomic_bytes(MAIN, main_before)
        atomic_bytes(ACTIVE_TBL, tbl_before)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_diana_original_control_restore_v1_1.py",
        "status": "promoted",
        "version": "1.1",
        "backup_dir": str(backup.relative_to(ROOT)).replace("\\", "/"),
        "before": {
            "main_sha256": EXPECTED_MAIN,
            "tbl_sha256": EXPECTED_ACTIVE_TBL,
        },
        "after": {
            "main_sha256": EXPECTED_CAND,
            "tbl_sha256": EXPECTED_CAND_TBL,
            "ws_checksum": f"{EXPECTED_CHECKSUM:04X}",
        },
        "live_saveram": {
            "path": "sram/monoeye_ko_expanded.sav",
            "size": len(save_before),
            "sha256_before": checks["live_save_sha"],
            "sha256_after": sha(LIVE_SAVE.read_bytes()),
            "byte_exact_preserved": LIVE_SAVE.read_bytes() == save_before,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
