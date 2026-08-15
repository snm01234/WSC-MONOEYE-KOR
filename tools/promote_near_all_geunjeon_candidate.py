#!/usr/bin/env python3
"""Promote the user-requested 75:B3FD 近全 -> 근전 candidate (ROM-only)."""
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
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "near_all_geunjeon_candidate.wsc"
BUILD_REPORT = PATCH / "near_all_geunjeon_candidate_report.json"
AUDIT_REPORT = PATCH / "near_all_geunjeon_candidate_audit.json"
PROMOTION_REPORT = PATCH / "near_all_geunjeon_promotion_report.json"
POST_AUDIT = PATCH / "near_all_geunjeon_postpromotion_audit.json"
POST_FALSE = PATCH / "near_all_geunjeon_postpromotion_false_segptr.json"

EXPECTED_OLD = "92fea67dc128d28a6c95e91faaeb21c8632547d23b8baace57cf904f3df3a40c"
EXPECTED_NEW = "b490dcbd87afa816475f3024d2d55d96fe77897afb82601b8939dce3e7321ed0"
EXPECTED_CHECKSUM = "1DCE"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_path(path: Path) -> str:
    return sha(path.read_bytes())


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def checksum_info(data: bytes) -> dict[str, Any]:
    stored = int.from_bytes(data[-2:], "little")
    computed = sum(data[:-2]) & 0xFFFF
    return {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": stored == computed}


def run_false_segptr() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cp = subprocess.run(
        [sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--out", str(POST_FALSE)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    if cp.returncode != 0:
        raise PromotionError("false-segptr scan failed: " + (cp.stderr or cp.stdout)[-1000:])
    doc = json.loads(POST_FALSE.read_text(encoding="utf-8"))
    if doc.get("ok") is not True or int(doc.get("sites_found", -1)) != 0:
        raise PromotionError(f"false-segptr not clean: {doc}")
    return {"ok": True, "sites_found": 0, "report": identity(POST_FALSE)}


def rebuild_xdelta() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cp = subprocess.run([sys.executable, str(ROOT / "tools/make_main_tip_xdelta.py")], cwd=ROOT, env=env, capture_output=True, text=True)
    if cp.returncode != 0:
        raise PromotionError("xdelta rebuild failed: " + (cp.stderr or cp.stdout)[-1000:])
    meta_path = ROOT / "out/dist/monoeye_ko_expanded_xdelta.json"
    patch_path = ROOT / "out/dist/monoeye_ko_expanded.xdelta"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    main_sha = str(((meta.get("main_tip") or {}).get("sha256") or "")).lower()
    roundtrip = meta.get("roundtrip_matches_main_tip") is True
    if main_sha != EXPECTED_NEW or not roundtrip:
        raise PromotionError(f"xdelta verification failed main={main_sha} roundtrip={roundtrip}")
    return {
        "ok": True,
        "path": rel(patch_path),
        "size": patch_path.stat().st_size,
        "sha256": sha_path(patch_path),
        "metadata": rel(meta_path),
        "roundtrip_matches_main_tip": True,
        "result_sha256": main_sha,
    }


def main() -> int:
    old = TIP.read_bytes()
    new = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    if len(old) != ROM_SIZE or sha(old) != EXPECTED_OLD:
        raise PromotionError(f"main identity drifted: {sha(old)}")
    if len(new) != ROM_SIZE or sha(new) != EXPECTED_NEW:
        raise PromotionError(f"candidate identity drifted: {sha(new)}")
    if len(save_before) != SAVE_SIZE:
        raise PromotionError("live SaveRAM size drifted")

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    if build.get("ok") is not True or audit.get("ok") is not True:
        raise PromotionError("candidate build/audit not clean")
    if str(((build.get("parent") or {}).get("sha256") or "")).lower() != EXPECTED_OLD:
        raise PromotionError("build parent mismatch")
    if str(((build.get("candidate") or {}).get("sha256") or "")).lower() != EXPECTED_NEW:
        raise PromotionError("build candidate mismatch")
    chk = checksum_info(new)
    if not chk["valid"] or chk["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {chk}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_near_all_geunjeon"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    if sha_path(backup) != EXPECTED_OLD:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        checks = {
            "tip_matches_candidate": sha(promoted) == EXPECTED_NEW,
            "checksum_valid": checksum_info(promoted)["valid"] and checksum_info(promoted)["stored"] == EXPECTED_CHECKSUM,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
            "rollback_exact": sha_path(backup) == EXPECTED_OLD,
        }
        false_segptr = run_false_segptr()
        checks["false_segptr_clean"] = false_segptr["ok"] is True
        if not all(checks.values()):
            raise PromotionError(f"post checks failed: {checks}")
    except Exception:
        atomic_bytes(TIP, old)
        raise

    xdelta = rebuild_xdelta()
    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_near_all_geunjeon_candidate.py",
        "ok": True,
        "tip": identity(TIP, promoted),
        "checksum": checksum_info(promoted),
        "checks": checks,
        "false_segptr": false_segptr,
        "target": build.get("target"),
        "neighbor_75B401": build.get("neighbor_75B401"),
        "rollback": identity(backup),
    }
    atomic_json(POST_AUDIT, post)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_near_all_geunjeon_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authorization": "사용자가 메인 TIP에서 近全을 근전으로 번역하도록 요청함",
        "old_tip": {"path": rel(TIP), "size": ROM_SIZE, "sha256": EXPECTED_OLD},
        "new_tip": identity(TIP, promoted),
        "new_tip_checksum": checksum_info(promoted),
        "tested_candidate": identity(CANDIDATE, new),
        "target": build.get("target"),
        "neighbor_75B401": build.get("neighbor_75B401"),
        "glyphs": build.get("glyphs"),
        "false_segptr": false_segptr,
        "backup_rom": identity(backup),
        "xdelta": xdelta,
        "postpromotion_audit": identity(POST_AUDIT),
        "live_saveram": identity(SAVE, save_before),
        "main_saveram_policy": "ROM-only promotion; live SaveRAM preserved byte-exact",
    }
    atomic_json(PROMOTION_REPORT, promotion)
    print(json.dumps({
        "ok": True,
        "old_tip_sha256": EXPECTED_OLD,
        "new_tip": promotion["new_tip"],
        "checksum": promotion["new_tip_checksum"],
        "target": promotion["target"],
        "neighbor_75B401": promotion["neighbor_75B401"],
        "false_segptr": false_segptr,
        "xdelta": xdelta,
        "rollback": rel(backup),
        "live_saveram_unchanged": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
