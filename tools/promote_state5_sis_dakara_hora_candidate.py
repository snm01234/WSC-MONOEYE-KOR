#!/usr/bin/env python3
"""Promote the user-approved state5 Sis hidden-line candidate into the main TIP.

ROM-only. Live SaveRAM is never replaced.
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

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from build_state5_sis_dakara_hora_candidate import (  # noqa: E402
    EXPECTED_ORIGINAL,
    EXPECTED_PARENT,
    JP,
    KO,
    PREFIX,
    RECORD_START,
    atomic_bytes,
    atomic_json,
    identity,
)
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "state5_sis_dakara_hora_candidate.wsc"
BUILD_REPORT = PATCH / "state5_sis_dakara_hora_candidate_report.json"
PROMOTION_REPORT = PATCH / "state5_sis_dakara_hora_candidate_promotion_report.json"
POST_AUDIT = PATCH / "state5_sis_dakara_hora_candidate_postpromotion_audit.json"
FALSE_SEGPTR = PATCH / "state5_sis_dakara_hora_postpromotion_false_segptr.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"

EXPECTED_CANDIDATE = "3012695f01cab7a12f022efe897a8fca90a244648570dd6fd2d05f036d8f807f"
EXPECTED_TOKEN = bytes.fromhex("E518F2DD")
EXPECTED_CHECKSUM = "CD37"
SMILE_ABS = 0x63B473
SMILE = "미소、　미소！"
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def checksum(data: bytes) -> dict[str, Any]:
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if expected_sha is not None and sha_path(path).lower() != expected_sha.lower():
        raise PromotionError(f"SHA drift: {path}: {sha_path(path)}")


def proof_renders(rom: bytes) -> dict[str, Any]:
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, EXT_META, EXT3_META)
    base = stock_base(rom)
    got = read_encoded_z_safe(rom, base + RECORD_START, max_len=128)
    if got is None:
        raise PromotionError("target unreadable after promotion")
    payload, term = bytes(got[0]), int(got[1])
    if not payload.startswith(PREFIX):
        raise PromotionError("prefix lost after promotion")
    body = payload[len(PREFIX) :]
    if body[:4] != EXPECTED_TOKEN:
        raise PromotionError(f"portal token drift: {body[:4].hex().upper()}")
    if any(byte != 0x01 for byte in body[4:]):
        raise PromotionError("padding drift after portal")
    rendered = dictionary.expand(body, tbl).rstrip("\u3000 \t")
    expected = normalize_ko_text(KO)
    if rendered != expected:
        raise PromotionError(f"target render drift: {rendered!r}")
    smile = read_encoded_z_safe(rom, base + SMILE_ABS, max_len=64)
    if smile is None:
        raise PromotionError("미소 continuation unreadable")
    smile_text = dictionary.expand(bytes(smile[0]), tbl).rstrip("\u3000 \t")
    if smile_text != SMILE:
        raise PromotionError(f"미소 continuation drifted: {smile_text!r}")
    if rom[term] != 0:
        raise PromotionError("terminator lost")
    return {
        "abs": f"{RECORD_START:06X}",
        "jp": JP,
        "ko": rendered,
        "token_hex": EXPECTED_TOKEN.hex().upper(),
        "continuation_abs": f"{SMILE_ABS:06X}",
        "continuation_text": smile_text,
        "terminator": f"{term - base:06X}",
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
            str(FALSE_SEGPTR),
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise PromotionError(f"false-segptr scan failed: {completed.returncode}")
    report = json.loads(FALSE_SEGPTR.read_text(encoding="utf-8"))
    sites_found = int(report.get("sites_found", -1))
    if report.get("ok") is not True or sites_found != 0:
        raise PromotionError(f"false-segptr sites found: {sites_found}")
    return {
        "ok": True,
        "sites_found": int(report["sites_found"]),
        "ext3_token_prefixes_ignored": int(report.get("ext3_token_prefixes_ignored") or 0),
        "report": identity(FALSE_SEGPTR),
    }


def main() -> int:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE)
    require(SAVE, size=SAVE_SIZE)
    require(BUILD_REPORT)
    require(ORIGINAL, size=8_388_608, expected_sha=EXPECTED_ORIGINAL)

    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    if report.get("ok") is not True:
        raise PromotionError("candidate report is not ok")
    if str((report.get("parent") or {}).get("sha256") or "").lower() != EXPECTED_PARENT:
        raise PromotionError("build report parent SHA drift")
    if str((report.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("build report candidate SHA drift")
    checks = report.get("checks") or {}
    if not checks or not all(value is True for value in checks.values()):
        raise PromotionError(f"build checks failed: {checks}")
    if str((report.get("allocation") or {}).get("token_hex") or "").upper() != EXPECTED_TOKEN.hex().upper():
        raise PromotionError("allocated token drift")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    cand_sum = checksum(candidate)
    if not cand_sum["valid"] or cand_sum["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {cand_sum}")
    proof_before = proof_renders(candidate)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_state5_sis_dakara_hora"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if digest(backup_rom.read_bytes()) != digest(parent):
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_bytes(TIP, candidate)
        promoted = TIP.read_bytes()
        proof = proof_renders(promoted)
        false_segptr = run_false_segptr(TIP)
        post_checks = {
            "tip_matches_candidate": digest(promoted) == digest(candidate),
            "checksum_valid": checksum(promoted)["valid"] and checksum(promoted)["stored"] == EXPECTED_CHECKSUM,
            "target_render_exact": proof["ko"] == normalize_ko_text(KO),
            "smile_continuation_unchanged": proof["continuation_text"] == SMILE,
            "false_segptr_clean": false_segptr["ok"] is True,
            "rollback_rom_exact": digest(backup_rom.read_bytes()) == digest(parent),
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        }
        if not all(post_checks.values()):
            raise PromotionError(f"post-promotion audit failed: {post_checks}")
    except Exception:
        atomic_bytes(TIP, parent)
        raise

    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report["status"] = "promoted_to_current_main"
    report["promotion"] = "promoted"
    report["published"] = True
    report["promoted_at"] = promoted_at
    atomic_json(BUILD_REPORT, report)

    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_state5_sis_dakara_hora_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "tip_checksum": checksum(TIP.read_bytes()),
        "rollback_rom": identity(backup_rom),
        "proof": proof,
        "false_segptr": false_segptr,
        "checks": post_checks,
        "candidate_proof_before_copy": proof_before,
    }
    atomic_json(POST_AUDIT, audit)
    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_state5_sis_dakara_hora_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "old_tip": {"path": identity(backup_rom)["path"].replace(f"backup/{stamp}_pre_state5_sis_dakara_hora/", ""), "size": len(parent), "sha256": digest(parent)},
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "postpromotion_checks": post_checks,
        "proof": proof,
        "false_segptr": false_segptr,
        "main_saveram_policy": "ROM-only promotion; live main SaveRAM remained byte-identical",
        "user_runtime_validation": {
            "approved": True,
            "date": "2026-08-14",
            "statement": "사용자 실화면 확인 후 메인 승격 지시",
        },
    }
    # Keep old_tip path as the live TIP path, not the backup path.
    promotion["old_tip"]["path"] = "out/patch/monoeye_ko_expanded.wsc"
    atomic_json(PROMOTION_REPORT, promotion)
    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
