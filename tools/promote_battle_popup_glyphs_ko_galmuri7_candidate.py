#!/usr/bin/env python3
"""Promote the user-approved Galmuri7 Korean battle-popup candidate.

ROM-only: preserve the live main SaveRAM, back up the previous TIP, replace the
TIP atomically, rerun the independent 11-record audit, and remove superseded
font-comparison ROM/SaveRAM pairs after success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
SRAM = ROOT / "sram"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = SRAM / "monoeye_ko_expanded.sav"

CANDIDATE = PATCH / "battle_popup_glyphs_ko_galmuri7_sample.wsc"
CANDIDATE_SAVE = SRAM / "battle_popup_glyphs_ko_galmuri7_sample.sav"
CONDENSED_CANDIDATE = PATCH / "battle_popup_glyphs_ko_galmuri11_condensed_sample.wsc"
CONDENSED_SAVE = SRAM / "battle_popup_glyphs_ko_galmuri11_condensed_sample.sav"

SPEC = ROOT / "data/battle_popup_glyph_translations_ko.json"
FONT = ROOT / "assets/fonts/Galmuri7.ttf"
BUILD_REPORT = PATCH / "battle_popup_glyphs_ko_galmuri7_sample_report.json"
AUDIT_TOOL = ROOT / "tools/audit_battle_popup_glyphs_ko_sample.py"
APPROVAL = PATCH / "battle_popup_glyphs_ko_galmuri7_user_validation.json"
PRE_AUDIT = PATCH / "battle_popup_glyphs_ko_galmuri7_prepromotion_audit.json"
POST_AUDIT = PATCH / "battle_popup_glyphs_ko_galmuri7_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "battle_popup_glyphs_ko_galmuri7_promotion_report.json"

EXPECTED_MAIN = "42051b189eff4d23d509b83da7aad81384ee932adbc06964990dc1a8578608ad"
EXPECTED_CANDIDATE = "27321bdd4ed7fd6b35d56f80745d47946e2b517aadd83689d34c31b59694a483"
EXPECTED_CONDENSED = "88fa56b83b919cc15475dad35284cd2a1dd678120bb7e01e66e48f6b97066ed7"
EXPECTED_BUILD_REPORT = "53747d291a0ca4aa9461e72d5e66239cc31d1faaa4eb479f58557a69d473dde3"
EXPECTED_SPEC = "fb36fcd5ba1bb7406bf384e281501b59912151f3f6c0310084582fe405cb4d16"
EXPECTED_FONT = "3882bd35066c26b0392cd4963ff9b3c151041dec34adc9d5633d137d1d9b9855"
EXPECTED_CHECKSUM = "6E71"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

EXPECTED_TRANSLATIONS = {
    "Ｉフィールド": "I-필드",
    "ＩＦキャンセラー": "IF캔슬러",
    "Ｆバリア": "F배리어",
    "Ｐディフェンサー": "P디펜서",
    "ビームコート": "빔코트",
    "バイオフィールド": "바이오필드",
    "分身": "분신",
    "クリティカル!": "크리티컬!",
    "ミス!": "미스!",
    "月光蝶": "월광접",
    "光発動": "빛발동",
}


class PromotionError(RuntimeError):
    pass


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def ident(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha(path)}


def require_file(path: Path, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if expected_sha is not None and sha(path).lower() != expected_sha.lower():
        raise PromotionError(f"SHA-256 drift: {path}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path, label: str, expected_sha: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    require_file(temporary, ROM_SIZE, expected_sha)
    os.replace(temporary, target)


def checksum(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def run_independent_audit(parent: Path, candidate: Path, output: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="battle_popup_promotion_", dir=PATCH) as temp_name:
        paired = Path(temp_name) / f"{candidate.stem}.sav"
        shutil.copy2(SAVE, paired)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [
                sys.executable,
                str(AUDIT_TOOL),
                "--parent",
                str(parent),
                "--save",
                str(SAVE),
                "--candidate",
                str(candidate),
                "--paired-save",
                str(paired),
                "--spec",
                str(SPEC),
                "--build-report",
                str(BUILD_REPORT),
                "--out",
                str(output),
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
    if result.returncode != 0:
        raise PromotionError(f"independent battle-popup audit failed: {result.returncode}")
    report = load_json(output)
    checks = report.get("checks") or {}
    if report.get("ok") is not True or not checks or not all(value is True for value in checks.values()):
        raise PromotionError(f"independent battle-popup audit gates failed: {checks}")
    if len(report.get("records") or []) != 11:
        raise PromotionError("independent audit record count drift")
    return report


def validate() -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, EXPECTED_MAIN)
    require_file(SAVE, SAVE_SIZE)
    require_file(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE)
    require_file(CANDIDATE_SAVE, SAVE_SIZE)
    require_file(CONDENSED_CANDIDATE, ROM_SIZE, EXPECTED_CONDENSED)
    require_file(CONDENSED_SAVE, SAVE_SIZE)
    require_file(BUILD_REPORT, expected_sha=EXPECTED_BUILD_REPORT)
    require_file(SPEC, expected_sha=EXPECTED_SPEC)
    require_file(FONT, expected_sha=EXPECTED_FONT)
    require_file(AUDIT_TOOL)
    require_file(APPROVAL)

    candidate_checksum = checksum(CANDIDATE)
    if not candidate_checksum["valid"] or candidate_checksum["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {candidate_checksum}")

    build = load_json(BUILD_REPORT)
    build_checks = build.get("checks") or {}
    if build.get("ok") is not True or not build_checks or not all(value is True for value in build_checks.values()):
        raise PromotionError(f"candidate build gates failed: {build_checks}")
    if str((build.get("parent") or {}).get("sha256") or "").lower() != EXPECTED_MAIN:
        raise PromotionError("build report parent binding mismatch")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("build report candidate binding mismatch")
    if str((build.get("candidate") or {}).get("ws_checksum") or "").upper() != EXPECTED_CHECKSUM:
        raise PromotionError("build report checksum binding mismatch")

    spec = load_json(SPEC)
    font = spec.get("font") or {}
    if font.get("path") != "assets/fonts/Galmuri7.ttf" or int(font.get("size", -1)) != 8:
        raise PromotionError(f"approved Galmuri7 font specification drift: {font}")
    translations = {row["jp"]: row["ko"] for row in spec.get("records") or []}
    if translations != EXPECTED_TRANSLATIONS:
        raise PromotionError(f"pronunciation-preserving translation drift: {translations}")

    approval = load_json(APPROVAL)
    if approval.get("approved") is not True or approval.get("promotion_authorized") is not True:
        raise PromotionError("user promotion approval missing")
    if str(approval.get("main_tip_sha256") or "").lower() != EXPECTED_MAIN:
        raise PromotionError("approval main binding mismatch")
    if str(approval.get("candidate_sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("approval candidate binding mismatch")

    pre_audit = run_independent_audit(TIP, CANDIDATE, PRE_AUDIT)
    return {
        "main_tip": ident(TIP),
        "live_saveram": ident(SAVE),
        "approved_candidate": ident(CANDIDATE),
        "runtime_modified_candidate_saveram": ident(CANDIDATE_SAVE),
        "superseded_condensed_candidate": ident(CONDENSED_CANDIDATE),
        "superseded_condensed_saveram": ident(CONDENSED_SAVE),
        "build_report": ident(BUILD_REPORT),
        "spec": ident(SPEC),
        "font": ident(FONT),
        "approval": ident(APPROVAL),
        "prepromotion_audit": ident(PRE_AUDIT),
        "prepromotion_checks": pre_audit["checks"],
        "candidate_checksum": candidate_checksum,
    }


def archive_saveram(path: Path, backup_dir: Path) -> dict[str, Any]:
    archived = backup_dir / f"{path.stem}_runtime.sav"
    shutil.copy2(path, archived)
    if sha(archived) != sha(path):
        raise PromotionError(f"candidate SaveRAM archive verification failed: {path}")
    return ident(archived)


def cleanup(paths: tuple[Path, ...]) -> dict[str, Any]:
    removed: list[str] = []
    reclaimed = 0
    for path in paths:
        if path.is_file():
            reclaimed += path.stat().st_size
            path.unlink()
            removed.append(rel(path))
    return {"files": removed, "reclaimed_bytes": reclaimed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_battle_popup_glyphs_ko_galmuri7"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_file(backup_rom, ROM_SIZE, EXPECTED_MAIN)

    old_tip = ident(TIP)
    save_before = ident(SAVE)
    galmuri7_save_archive = archive_saveram(CANDIDATE_SAVE, backup_dir)
    condensed_save_archive = archive_saveram(CONDENSED_SAVE, backup_dir)
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "generated_by": "tools/promote_battle_popup_glyphs_ko_galmuri7_candidate.py",
            "reason": "pre_battle_popup_glyphs_ko_galmuri7",
            "rollback_rom": ident(backup_rom),
            "approved_candidate": ident(CANDIDATE),
            "user_validation": ident(APPROVAL),
            "archived_candidate_saverams": [galmuri7_save_archive, condensed_save_archive],
        },
    )

    try:
        atomic_copy(CANDIDATE, TIP, "battle-popup-galmuri7-promote", EXPECTED_CANDIDATE)
        if ident(SAVE) != save_before:
            raise PromotionError("live main SaveRAM changed during ROM promotion")
        post_audit = run_independent_audit(backup_rom, TIP, POST_AUDIT)
        if sha(TIP) != EXPECTED_CANDIDATE or checksum(TIP)["stored"] != EXPECTED_CHECKSUM:
            raise PromotionError("promoted TIP identity/checksum mismatch")
        if ident(SAVE) != save_before:
            raise PromotionError("live main SaveRAM changed during post-promotion audit")
    except Exception:
        atomic_copy(backup_rom, TIP, "battle-popup-galmuri7-rollback", EXPECTED_MAIN)
        raise

    cleaned = cleanup((CANDIDATE, CANDIDATE_SAVE, CONDENSED_CANDIDATE, CONDENSED_SAVE))
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_popup_glyphs_ko_galmuri7_candidate.py",
        "ok": True,
        "promoted": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "promoted_user_approved_galmuri7_battle_popup_glyphs",
        "before": old_tip,
        "after": ident(TIP),
        "checksum": checksum(TIP),
        "rollback_rom": ident(backup_rom),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": ident(SAVE),
        "archived_candidate_saverams": [galmuri7_save_archive, condensed_save_archive],
        "approval": ident(APPROVAL),
        "build_report": ident(BUILD_REPORT),
        "spec": ident(SPEC),
        "prepromotion_audit": ident(PRE_AUDIT),
        "postpromotion_audit": ident(POST_AUDIT),
        "postpromotion_checks": post_audit["checks"],
        "cleanup": cleaned,
    }
    if report["live_saveram_before"] != report["live_saveram_after"]:
        raise PromotionError("live main SaveRAM identity drifted")
    atomic_json(PROMOTION_REPORT, report)
    print(
        json.dumps(
            {
                "promoted": True,
                "tip_sha256": report["after"]["sha256"],
                "checksum": report["checksum"]["stored"],
                "rollback": report["rollback_rom"]["path"],
                "post_audit_ok": post_audit["ok"],
                "cleanup_files": len(cleaned["files"]),
                "reclaimed_bytes": cleaned["reclaimed_bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
