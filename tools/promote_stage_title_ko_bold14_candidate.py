#!/usr/bin/env python3
"""Promote the user-approved Korean stage-title Bold 14px candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"D:\monoeye")
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "stage_title_ko_bold14_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/stage_title_ko_bold14_candidate.sav"
BUILD_REPORT = PATCH / "stage_title_ko_bold14_candidate_report.json"
CANONICAL_SPEC = ROOT / "data/stage_title_translations_ko.json"
BOLD14_SPEC = ROOT / "data/stage_title_translations_ko_bold14.json"
PROMOTION_REPORT = PATCH / "stage_title_ko_bold14_promotion_report.json"
POST_AUDIT = PATCH / "stage_title_ko_bold14_postpromotion_audit.json"

EXPECTED_TIP_SHA = "9402f7efc1c557746015eb6352799a79f7f66febf1eb0ad4039734028a16a9f2"
EXPECTED_CANDIDATE_SHA = "87bd754d3f4af65f3d02a274d94e962e0bf2f0313c491096407dfc9c8d1a4f93"
EXPECTED_BUILD_REPORT_SHA = "9773bd5b882153bf6c007dc112781cbd67048078851d3f8fa57e42c71f99312f"
EXPECTED_CANONICAL_SPEC_SHA = "2965b06ddb78b22863abe5e4ca8e248601009469428c4c854e5cb61b0cd0b4ee"
EXPECTED_BOLD14_SPEC_SHA = "de0702e230bc9de4dacf9b2302c73d8f74527663efb0c99a8166e0b2e9e1426f"
EXPECTED_MAIN_SAVE_SHA = "589f47d18cbe245e544f62a92542eedaed87895794aaf072b3071d7442cde4a4"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"wrong size: {path}")
    if sha is not None and digest(path).lower() != sha.lower():
        raise PromotionError(f"SHA-256 drift: {path}")


def checksum_details(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {
        "computed": f"{computed:04X}",
        "stored": f"{stored:04X}",
        "valid": computed == stored,
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path, label: str, expected_size: int, expected_sha: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    require(temporary, expected_size, expected_sha)
    os.replace(temporary, target)


def find_stage(spec: dict[str, Any], stage_id: str) -> dict[str, Any] | None:
    for package in spec.get("packages", []):
        for title in package.get("titles", []):
            if title.get("stage_id") == stage_id:
                return title
    return None


def validate() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP_SHA)
    require(TIP_SAVE, SAVE_SIZE, EXPECTED_MAIN_SAVE_SHA)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require(CANDIDATE_SAVE, SAVE_SIZE)
    require(BUILD_REPORT, sha=EXPECTED_BUILD_REPORT_SHA)
    require(CANONICAL_SPEC, sha=EXPECTED_CANONICAL_SPEC_SHA)
    require(BOLD14_SPEC, sha=EXPECTED_BOLD14_SPEC_SHA)

    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    report_checks = report.get("checks") or {}
    if not report_checks or not all(value is True for value in report_checks.values()):
        raise PromotionError("candidate build report did not pass every static gate")
    candidate_info = report.get("candidate") or {}
    parent_info = report.get("parent") or {}
    if str(candidate_info.get("sha256", "")).lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("candidate report binding mismatch")
    if str(parent_info.get("sha256", "")).lower() != EXPECTED_TIP_SHA:
        raise PromotionError("parent report binding mismatch")
    if candidate_info.get("checksum") != "95F5":
        raise PromotionError("candidate report checksum binding mismatch")

    checksum = checksum_details(CANDIDATE)
    if not checksum["valid"] or checksum["stored"] != "95F5":
        raise PromotionError("candidate WonderSwan checksum is invalid")

    spec = json.loads(BOLD14_SPEC.read_text(encoding="utf-8"))
    font = spec.get("font") or {}
    expected_font = {
        "path": "assets/fonts/galmuri_tmp/Galmuri11-Bold.ttf",
        "size": 14,
        "letter_spacing": 0,
        "line_gap": 4,
        "vertical_offset": 0,
    }
    if font != expected_font:
        raise PromotionError(f"Bold 14px font specification drift: {font}")
    special03 = find_stage(spec, "SPECIAL03")
    if special03 is None or special03.get("ko_lines") != ["블루를 계승하는 자"]:
        raise PromotionError("SPECIAL03 translation drift")

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "main_saveram": identity(TIP_SAVE),
        "candidate_saveram": identity(CANDIDATE_SAVE),
        "candidate_build_report": identity(BUILD_REPORT),
        "canonical_spec_before": identity(CANONICAL_SPEC),
        "approved_spec": identity(BOLD14_SPEC),
        "candidate_checksum": checksum,
        "candidate_report_checks": report_checks,
        "approved_font": font,
        "approved_special03": special03["ko_lines"][0],
        "user_runtime_validation": {
            "approved": True,
            "date": "2026-08-10",
            "statement": "실측 이상 없습니다.",
        },
        "saveram_policy": (
            "Only the WSC TIP is promoted. The live main SaveRAM is immutable during promotion; "
            "the runtime-modified candidate SaveRAM is preserved separately."
        ),
    }


def audit(
    backup_rom: Path,
    backup_spec: Path,
    main_save_before: dict[str, Any],
) -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require(backup_rom, ROM_SIZE, EXPECTED_TIP_SHA)
    require(CANONICAL_SPEC, sha=EXPECTED_BOLD14_SPEC_SHA)
    require(backup_spec, sha=EXPECTED_CANONICAL_SPEC_SHA)
    checksum = checksum_details(TIP)
    checks = {
        "tip_matches_approved_candidate": digest(TIP) == EXPECTED_CANDIDATE_SHA,
        "tip_checksum_valid": checksum["valid"] and checksum["stored"] == "95F5",
        "main_saveram_unchanged": identity(TIP_SAVE) == main_save_before,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP_SHA,
        "canonical_spec_matches_approved_bold14_spec": digest(CANONICAL_SPEC) == EXPECTED_BOLD14_SPEC_SHA,
        "previous_canonical_spec_preserved": digest(backup_spec) == EXPECTED_CANONICAL_SPEC_SHA,
        "runtime_modified_candidate_saveram_preserved": CANDIDATE_SAVE.is_file(),
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    result = {
        "schema_version": 1,
        "generated_by": "tools/promote_stage_title_ko_bold14_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "tip_checksum": checksum,
        "rollback_rom": identity(backup_rom),
        "canonical_spec": identity(CANONICAL_SPEC),
        "previous_canonical_spec": identity(backup_spec),
        "main_saveram_before": main_save_before,
        "main_saveram_after": identity(TIP_SAVE),
        "candidate_saveram_preserved": identity(CANDIDATE_SAVE),
        "checks": checks,
    }
    atomic_json(POST_AUDIT, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_stage_title_ko_bold14"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_spec = backup_dir / CANONICAL_SPEC.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(CANONICAL_SPEC, backup_spec)
    require(backup_rom, ROM_SIZE, EXPECTED_TIP_SHA)
    require(backup_spec, sha=EXPECTED_CANONICAL_SPEC_SHA)

    old_tip = identity(TIP)
    old_spec = identity(CANONICAL_SPEC)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "stage-title-bold14-promote", ROM_SIZE, EXPECTED_CANDIDATE_SHA)
        atomic_copy(
            BOLD14_SPEC,
            CANONICAL_SPEC,
            "stage-title-bold14-spec-promote",
            BOLD14_SPEC.stat().st_size,
            EXPECTED_BOLD14_SPEC_SHA,
        )
        post = audit(backup_rom, backup_spec, save_before)
    except Exception:
        atomic_copy(backup_rom, TIP, "stage-title-bold14-rollback", ROM_SIZE, EXPECTED_TIP_SHA)
        atomic_copy(
            backup_spec,
            CANONICAL_SPEC,
            "stage-title-bold14-spec-rollback",
            backup_spec.stat().st_size,
            EXPECTED_CANONICAL_SPEC_SHA,
        )
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_stage_title_ko_bold14_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "old_canonical_spec": old_spec,
        "new_canonical_spec": identity(CANONICAL_SPEC),
        "backup_canonical_spec": identity(backup_spec),
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": post["checks"],
        "main_saveram_policy": "live main SaveRAM remained byte-identical and was never replaced",
        "candidate_saveram_policy": "preserved because runtime validation changed it after candidate creation",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
