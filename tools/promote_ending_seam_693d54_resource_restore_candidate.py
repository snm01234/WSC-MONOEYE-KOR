#!/usr/bin/env python3
"""Promote the user-runtime-confirmed 69:3D54 ending resource restore.

The tested candidate restores exactly three non-checksum bytes at logical
69:3D54-56 from the false-positive Korean text rewrite F3 3F 01 back to the
original graphics/animation resource entry F2 44 03.

Promotion is ROM-only.  The live main SaveRAM is guarded byte-exact, a rollback
ROM is created, false segmented-pointer scan is required clean, and the main
xdelta is rebuilt with round-trip verification.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "ending_seam_693d54_resource_restore_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PROMOTION_REPORT = PATCH / "ending_seam_693d54_resource_restore_promotion_report.json"
POST_FALSE = PATCH / "ending_seam_693d54_resource_restore_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
EXPECTED_CANDIDATE_SHA = "d925ee07b4ea844a1bd89deff52bc88ec21e04bf8ab5a0df8d9fecdd651bd8b1"
EXPECTED_LIVE_SAVE_SHA = "e462c548186b911ed0c936badf53cc7737e8855ba6ff4286e0d56e01539ee010"
EXPECTED_CHECKSUM = "1DD3"
LOGICAL = 0x693D54
FILE_OFF = 0xE93D54
BEFORE = bytes.fromhex("F33F01")
AFTER = bytes.fromhex("F24403")
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha_path(path: Path) -> str:
    return sha(path.read_bytes())


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data)}


def checksum_info(rom: bytes) -> dict[str, Any]:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": stored == computed}


def atomic_copy(source: Path, target: Path) -> None:
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with source.open("rb") as src, temp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(temp, target)


def atomic_bytes(target: Path, payload: bytes) -> None:
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, target)


def atomic_json(target: Path, payload: dict[str, Any]) -> None:
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, target)


def run_false_segptr() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cp = subprocess.run(
        [sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--out", str(POST_FALSE)],
        cwd=ROOT, env=env, check=False, capture_output=True, text=True,
    )
    if cp.returncode != 0:
        raise PromotionError("false-segptr scan failed: " + (cp.stderr or cp.stdout)[-1000:])
    obj = json.loads(POST_FALSE.read_text(encoding="utf-8"))
    if obj.get("ok") is not True or int(obj.get("sites_found", -1)) != 0:
        raise PromotionError(f"false-segptr not clean: {obj.get('sites_found')}")
    return {"ok": True, "sites_found": 0, "report": identity(POST_FALSE)}


def rebuild_xdelta() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cp = subprocess.run(
        [sys.executable, str(ROOT / "tools/make_main_tip_xdelta.py")],
        cwd=ROOT, env=env, check=False, capture_output=True, text=True,
    )
    if cp.returncode != 0:
        raise PromotionError("xdelta rebuild failed: " + (cp.stderr or cp.stdout)[-1000:])
    meta_path = ROOT / "out/dist/monoeye_ko_expanded_xdelta.json"
    delta_path = ROOT / "out/dist/monoeye_ko_expanded.xdelta"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    result_sha = str(((meta.get("main_tip") or {}).get("sha256") or "")).lower()
    roundtrip = meta.get("roundtrip_matches_main_tip") is True
    if result_sha != EXPECTED_CANDIDATE_SHA or not roundtrip:
        raise PromotionError(f"xdelta verification failed: sha={result_sha}, roundtrip={roundtrip}")
    return {
        "ok": True,
        "path": rel(delta_path),
        "sha256": sha_path(delta_path),
        "metadata": rel(meta_path),
        "roundtrip_matches_main_tip": True,
    }


def main() -> int:
    if not TIP.is_file() or TIP.stat().st_size != ROM_SIZE or sha_path(TIP) != EXPECTED_TIP_SHA:
        raise PromotionError("main TIP identity drifted")
    if not CANDIDATE.is_file() or CANDIDATE.stat().st_size != ROM_SIZE or sha_path(CANDIDATE) != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("tested candidate identity drifted")
    if not SAVE.is_file() or SAVE.stat().st_size != SAVE_SIZE or sha_path(SAVE) != EXPECTED_LIVE_SAVE_SHA:
        raise PromotionError("live SaveRAM identity drifted")

    old = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    if old[FILE_OFF:FILE_OFF+3] != BEFORE:
        raise PromotionError(f"old bytes drifted: {old[FILE_OFF:FILE_OFF+3].hex().upper()}")
    if candidate[FILE_OFF:FILE_OFF+3] != AFTER:
        raise PromotionError(f"candidate restore drifted: {candidate[FILE_OFF:FILE_OFF+3].hex().upper()}")
    ci = checksum_info(candidate)
    if not ci["valid"] or ci["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {ci}")

    changed = [i for i, (a, b) in enumerate(zip(old, candidate)) if a != b]
    non_checksum = [i for i in changed if i not in (ROM_SIZE - 2, ROM_SIZE - 1)]
    if non_checksum != [FILE_OFF, FILE_OFF + 1, FILE_OFF + 2]:
        raise PromotionError(f"candidate scope drifted: {[hex(x) for x in non_checksum[:20]]}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_ending_seam_693d54_resource_restore"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        if sha(promoted) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("promoted TIP differs from runtime-tested candidate")
        if promoted[FILE_OFF:FILE_OFF+3] != AFTER:
            raise PromotionError("post-promotion resource bytes mismatch")
        if SAVE.read_bytes() != save_before:
            raise PromotionError("live SaveRAM changed during promotion")
        false_segptr = run_false_segptr()
        xdelta = rebuild_xdelta()
    except Exception:
        atomic_bytes(TIP, old)
        raise

    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_ending_seam_693d54_resource_restore_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "authorization": "사용자가 ending_seam_693d54_resource_restore_candidate.wsc 실측에서 엔딩 그래픽 어긋남 수정됨을 확인하고 메인TIP 승격을 요청함",
        "old_tip": {"path": rel(TIP), "size": ROM_SIZE, "sha256": EXPECTED_TIP_SHA},
        "new_tip": identity(TIP, promoted),
        "checksum": checksum_info(promoted),
        "tested_candidate": identity(CANDIDATE, candidate),
        "rollback_rom": identity(backup_rom),
        "change": {
            "logical": "69:3D54-3D56",
            "file": "E9:3D54-3D56",
            "before": BEFORE.hex().upper(),
            "after": AFTER.hex().upper(),
            "non_checksum_byte_count": 3,
            "reason": "restore active 4-byte graphics/animation resource entry misclassified by raw duplicate-text sweep",
        },
        "runtime_user_validation": "ending seam/misalignment confirmed fixed",
        "state35_oracle": "candidate frame6 BG problem band matched stock 80/80 words before promotion",
        "false_segptr": false_segptr,
        "xdelta": xdelta,
        "live_saveram": identity(SAVE, save_before),
        "live_saveram_unchanged": SAVE.read_bytes() == save_before,
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
