#!/usr/bin/env python3
"""Promote the user-validated Heero quote-index restore candidate to main TIP.

ROM-only. Live SaveRAM is never replaced. Rebuilds the distribution xdelta.
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
sys.path.insert(0, str(ROOT / "tools"))

from build_battle_heero_quote_index_restore_candidate import (  # noqa: E402
    HEERO_NATIVE_PREFIX,
    HEERO_PTRS,
    HEERO_PTR_SITE,
    HEERO_PTR_VALUE,
    HEERO_RECORD,
    TABLE_END,
    TABLE_SLICE_SHA,
    TABLE_START,
)
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "battle_heero_quote_index_restore_candidate.wsc"
BUILD_REPORT = PATCH / "battle_heero_quote_index_restore_candidate_report.json"
AUDIT_REPORT = PATCH / "battle_heero_quote_index_restore_candidate_audit.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
PROMOTION_REPORT = PATCH / "battle_heero_quote_index_restore_promotion_report.json"
POST_AUDIT = PATCH / "battle_heero_quote_index_restore_postpromotion_audit.json"
POST_FALSE = PATCH / "battle_heero_quote_index_restore_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "528f28e1050257e9f3698f27cf9aa577b217c67cd8951d6030cc5592fc6e0e85"
EXPECTED_CANDIDATE_SHA = "1886d04e697baba17b454089dbd07cc556d3e49c5808e23e321cf6863773bc3d"
EXPECTED_CHECKSUM = "3214"
EXPECTED_HEERO_RENDER = "작전　미스의　대가는　죽음이다……"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha256(payload)}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {rel(path)}")
    if expected_sha is not None and sha_path(path) != expected_sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, target)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def checksum_info(rom: bytes) -> dict[str, Any]:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {
        "stored": f"{stored:04X}",
        "computed": f"{computed:04X}",
        "valid": stored == computed,
    }


def le16_at(rom: bytes, logical: int) -> int:
    start = stock_base(rom) + logical
    return int.from_bytes(rom[start : start + 2], "little")


def verify_heero_index(rom: bytes) -> dict[str, Any]:
    original = bytes(load_rom(ORIGINAL))
    orig_sb = stock_base(original)
    sb = stock_base(rom)
    good = bytes(original[orig_sb + TABLE_START : orig_sb + TABLE_END])
    got = bytes(rom[sb + TABLE_START : sb + TABLE_END])
    if sha256(good) != TABLE_SLICE_SHA or got != good:
        raise PromotionError("quote-index slice does not match original")
    if le16_at(rom, HEERO_PTR_SITE) != HEERO_PTR_VALUE:
        raise PromotionError("5EC37D is not 00C8")
    for addr, value in HEERO_PTRS.items():
        if le16_at(rom, addr) != value:
            raise PromotionError(f"{addr:06X} pointer drifted")
    rec = read_encoded_z_safe(rom, sb + HEERO_RECORD, max_len=32)
    live = bytes(rec[0]) if rec else b""
    if not live.startswith(HEERO_NATIVE_PREFIX) or live[1:3] == b"\xE5\x18":
        raise PromotionError("5E00C8 native Heero body lost")
    tbl = Tbl.load(TBL_PATH)
    rendered = Dictionary(rom).expand(live[1:], tbl).rstrip("\u3000 ")
    if rendered != EXPECTED_HEERO_RENDER:
        raise PromotionError(f"5E00C8 render drifted: {rendered!r}")
    return {
        "table_matches_original": True,
        "heero_ptr_00c8": True,
        "heero_render": rendered,
        "pointers": {f"{addr:06X}": f"{value:04X}" for addr, value in HEERO_PTRS.items()},
    }


def run_false_segptr(target: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "scan_false_segptr_writes.py"),
            "--target",
            str(target),
            "--out",
            str(POST_FALSE),
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise PromotionError(f"false-segptr scan failed: {completed.returncode}")
    report = json.loads(POST_FALSE.read_text(encoding="utf-8"))
    sites_found = int(report.get("sites_found", -1))
    if report.get("ok") is not True or sites_found != 0:
        raise PromotionError(f"false-segptr sites found: {sites_found}")
    return {
        "ok": True,
        "sites_found": sites_found,
        "ext3_token_prefixes_ignored": int(report.get("ext3_token_prefixes_ignored") or 0),
        "report": identity(POST_FALSE),
    }


def rebuild_xdelta() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_main_tip_xdelta.py")],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PromotionError(
            "xdelta rebuild failed: "
            + (completed.stderr or completed.stdout or str(completed.returncode))[-800:]
        )
    meta_path = ROOT / "out/dist/monoeye_ko_expanded_xdelta.json"
    if not meta_path.is_file():
        raise PromotionError("xdelta metadata missing after rebuild")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    patch = ROOT / "out/dist/monoeye_ko_expanded.xdelta"
    return {
        "ok": True,
        "path": rel(patch),
        "size": patch.stat().st_size,
        "sha256": sha_path(patch),
        "metadata": rel(meta_path),
        "result_sha256": str(((meta.get("main_tip") or {}).get("sha256") or "")).lower(),
        "roundtrip_matches_main_tip": meta.get("roundtrip_matches_main_tip") is True,
    }


def main() -> int:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE_SHA)
    require(SAVE, size=SAVE_SIZE)
    require(BUILD_REPORT)
    require(AUDIT_REPORT)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    if build.get("ok") is not True or audit.get("ok") is not True:
        raise PromotionError("candidate report/audit is not ok")
    parent_sha = str((((build.get("parent") or {}).get("main_tip") or {}).get("sha256") or "")).lower()
    cand_sha = str(((build.get("candidate") or {}).get("sha256") or "")).lower()
    if parent_sha != EXPECTED_TIP_SHA:
        raise PromotionError("build parent SHA drifted")
    if cand_sha != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build candidate SHA drifted")
    if str(audit.get("candidate_sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("audit candidate SHA drifted")
    if str(build.get("checksum") or "") != EXPECTED_CHECKSUM:
        raise PromotionError("build checksum drifted")
    if str(audit.get("heero_render") or "") != EXPECTED_HEERO_RENDER:
        raise PromotionError("audit Heero render drifted")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    info = checksum_info(candidate)
    if not info["valid"] or info["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {info}")
    heero = verify_heero_index(candidate)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_heero_quote_index_restore"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        if sha256(promoted) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("promoted TIP does not match candidate")
        heero_after = verify_heero_index(promoted)
        false_segptr = run_false_segptr(TIP)
        post_checks = {
            "tip_matches_candidate": sha256(promoted) == EXPECTED_CANDIDATE_SHA,
            "checksum_valid": checksum_info(promoted)["valid"]
            and checksum_info(promoted)["stored"] == EXPECTED_CHECKSUM,
            "table_matches_original": heero_after["table_matches_original"] is True,
            "heero_ptr_00c8": heero_after["heero_ptr_00c8"] is True,
            "heero_render_exact": heero_after["heero_render"] == EXPECTED_HEERO_RENDER,
            "false_segptr_clean": false_segptr["ok"] is True,
            "rollback_rom_exact": sha_path(backup_rom) == EXPECTED_TIP_SHA,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        }
        if not all(post_checks.values()):
            raise PromotionError(f"post-promotion audit failed: {post_checks}")
    except Exception:
        atomic_bytes(TIP, parent)
        raise

    xdelta = rebuild_xdelta()
    if xdelta["result_sha256"] != EXPECTED_CANDIDATE_SHA:
        raise PromotionError(f"xdelta result SHA drifted: {xdelta['result_sha256']}")
    if xdelta.get("roundtrip_matches_main_tip") is not True:
        raise PromotionError("xdelta round-trip did not match the new TIP")

    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_heero_quote_index_restore_candidate.py",
        "ok": True,
        "tip": identity(TIP, promoted),
        "tip_checksum": checksum_info(promoted),
        "rollback_rom": identity(backup_rom),
        "heero": heero_after,
        "false_segptr": false_segptr,
        "checks": post_checks,
    }
    atomic_json(POST_AUDIT, post)

    build["status"] = "promoted_to_current_main"
    build["promotion"] = "promoted"
    build["published"] = True
    build["promoted_at"] = promoted_at
    atomic_json(BUILD_REPORT, build)
    audit["status"] = "promoted_to_current_main"
    audit["promotion"] = "promoted"
    audit["promoted_at"] = promoted_at
    atomic_json(AUDIT_REPORT, audit)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_heero_quote_index_restore_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "authorization": "사용자가 히이로 전투 초상·대사 quote-index 복원 테스트 ROM의 메인 TIP 승격을 요청함",
        "old_tip": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "size": ROM_SIZE,
            "sha256": EXPECTED_TIP_SHA,
        },
        "new_tip": identity(TIP, promoted),
        "new_tip_checksum": checksum_info(promoted),
        "backup_rom": identity(backup_rom),
        "candidate": identity(CANDIDATE, candidate),
        "proof": heero,
        "false_segptr": false_segptr,
        "xdelta": xdelta,
        "postpromotion_audit": identity(POST_AUDIT),
        "main_saveram_policy": "ROM-only promotion; live main SaveRAM remained byte-identical",
        "live_saveram": identity(SAVE, save_before),
    }
    atomic_json(PROMOTION_REPORT, promotion)
    print(
        json.dumps(
            {
                "ok": True,
                "old_tip_sha256": EXPECTED_TIP_SHA,
                "new_tip": promotion["new_tip"],
                "checksum": promotion["new_tip_checksum"],
                "proof": promotion["proof"],
                "xdelta": xdelta,
                "live_saveram_unchanged": True,
                "rollback": rel(backup_rom),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
