#!/usr/bin/env python3
"""Promote the user-authorized 1,893-row uncovered auto-draft candidate.

Only the ROM is promoted. The live main SaveRAM is backed up and left untouched.
The candidate is accepted despite deferred translation-quality review because
explicit user authorization is recorded in a bound validation JSON.

This revision promotes the LLM-literal retranslation rebuild over the previous
Google-draft tip.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_ms_batch01_candidate import payload_at
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, update_ws_checksum
from normalize_ko_text import normalize_ko_text

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "uncovered_auto_draft_all_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/uncovered_auto_draft_all_candidate.sav"
SHEET = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
BUILD = PATCH / "uncovered_auto_draft_all_candidate_report.json"
AUDIT = PATCH / "uncovered_auto_draft_all_candidate_audit.json"
USER = PATCH / "uncovered_auto_draft_all_user_validation.json"
POST = PATCH / "uncovered_auto_draft_all_postpromotion_audit.json"
PROMOTION = PATCH / "uncovered_auto_draft_all_promotion_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_TIP = "33bd176fb8afd3869acacdfd48fbf0459718dcc5f16d310309565313a48aae52"
EXPECTED_CANDIDATE = "9d5607ec320829ca0dc2dd8247fe2ca7da9040edef2cea4aa8fbd16f139ef358"
EXPECTED_CHECKSUM = "FA2C"
EXPECTED_AUTH = (
    "rebuild and promote uncovered auto-draft LLM-literal sheet candidate "
    "to main TIP"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 1_893
DRAFT_WORKFLOWS = {"draft_auto", "draft_llm_literal"}


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, size: int, expected: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise PromotionError(f"missing or wrong-size file: {rel(path)}")
    if expected is not None and digest(path) != expected:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing evidence: {rel(path)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root must be object: {rel(path)}")
    return value


def atomic_copy(source: Path, target: Path, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    require(temporary, ROM_SIZE, digest(source))
    os.replace(temporary, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def validate_evidence() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE)
    require(CANDIDATE_SAVE, SAVE_SIZE)
    build = load_object(BUILD)
    audit = load_object(AUDIT)
    user = load_object(USER)

    if build.get("ok") is not True:
        raise PromotionError("build report failed")
    if str((build.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("build report is bound to another candidate")
    if str(build.get("checksum") or (build.get("diff") or {}).get("checksum") or "").upper() != EXPECTED_CHECKSUM:
        raise PromotionError("build report checksum drift")
    if audit.get("ok") is not True:
        raise PromotionError("independent audit failed")
    checks = audit.get("checks") or {}
    if not checks or not all(value is True for value in checks.values()):
        raise PromotionError("independent audit has incomplete checks")
    counts = audit.get("counts") or {}
    if int(counts.get("targets", -1)) != EXPECTED_ROWS or int(counts.get("target_failures", -1)) != 0:
        raise PromotionError("audited target population is not exact")
    if user.get("status") != "user_requested_llm_literal_rebuild_promotion":
        raise PromotionError("user validation status is missing")
    if str((user.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("user validation is bound to another candidate")
    if user.get("authorization") != EXPECTED_AUTH:
        raise PromotionError("explicit promotion authorization is missing")

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "candidate_test_saveram": {**identity(CANDIDATE_SAVE), "action": "not_copied"},
        "build_report": identity(BUILD),
        "independent_audit": identity(AUDIT),
        "user_validation": identity(USER),
        "translation_quality": "deferred_by_user_authorization_llm_literal_rebuild",
    }


def audit_promoted_tip() -> dict[str, Any]:
    rom = bytes(load_rom(TIP))
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    with SHEET.open(encoding="utf-8-sig", newline="") as stream:
        rows = [dict(row) for row in csv.DictReader(stream)]
    failures: list[dict[str, Any]] = []
    draft_count = 0
    preserved_count = 0
    for row in rows:
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        prefix_len = len(bytes.fromhex(str(row.get("prefix_hex") or "")))
        payload, _term = payload_at(rom, logical)
        actual = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row.get("ko") or ""))
        reasons: list[str] = []
        if actual != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")
        if len(payload) != int(row["payload_capacity"]):
            reasons.append("payload_length_changed")
        if row.get("workflow_status") in DRAFT_WORKFLOWS:
            draft_count += 1
        else:
            preserved_count += 1
        if reasons:
            failures.append(
                {"abs": address, "expected": expected, "actual": actual, "reasons": reasons}
            )
            if len(failures) >= 30:
                break
    checksum_copy = bytearray(rom)
    checksum = update_ws_checksum(checksum_copy)
    return {
        "rows": len(rows),
        "draft_auto": draft_count,
        "preserved": preserved_count,
        "failures": failures,
        "all_exact": (
            len(rows) == EXPECTED_ROWS
            and draft_count == 1_858
            and preserved_count == 35
            and not failures
        ),
        "checksum": f"{checksum:04X}",
        "checksum_exact": bytes(checksum_copy) == rom,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate_evidence()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_uncovered_llm_literal_rebuild"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(TIP_SAVE, backup_save)
    require(backup_rom, ROM_SIZE, EXPECTED_TIP)
    require(backup_save, SAVE_SIZE, digest(TIP_SAVE))

    old_tip = identity(TIP)
    save_before = identity(TIP_SAVE)
    candidate_before = identity(CANDIDATE)
    candidate_save_before = identity(CANDIDATE_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "uncovered-llm-literal-promote")
        require(TIP, ROM_SIZE, EXPECTED_CANDIDATE)
        if identity(TIP_SAVE) != save_before:
            raise PromotionError("live main SaveRAM changed during ROM promotion")
        target_audit = audit_promoted_tip()
        checks = {
            "tip_matches_verified_candidate": digest(TIP) == EXPECTED_CANDIDATE,
            "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP,
            "rollback_saveram_preserved": digest(backup_save) == save_before["sha256"],
            "main_saveram_unchanged": identity(TIP_SAVE) == save_before,
            "all_1893_rows_exact": target_audit["all_exact"],
            "checksum_exact": target_audit["checksum_exact"] and target_audit["checksum"] == EXPECTED_CHECKSUM,
            "candidate_test_saveram_not_copied": digest(TIP_SAVE) == save_before["sha256"],
        }
        post = {
            "schema_version": 1,
            "generated_by": "tools/promote_uncovered_auto_draft_all_candidate.py",
            "ok": all(checks.values()),
            "tip": identity(TIP),
            "rollback_rom": identity(backup_rom),
            "rollback_saveram_snapshot": identity(backup_save),
            "main_saveram_after": identity(TIP_SAVE),
            "targets": target_audit,
            "checks": checks,
        }
        if post.get("ok") is not True:
            raise PromotionError("post-promotion audit failed")
        atomic_json(POST, post)
    except Exception:
        atomic_copy(backup_rom, TIP, "uncovered-llm-literal-rollback")
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_uncovered_auto_draft_all_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "backup_saveram_snapshot": identity(backup_save),
        "validation": validation,
        "postpromotion_audit": identity(POST),
        "main_saveram": {
            "before": save_before,
            "after": identity(TIP_SAVE),
            "action": "left_untouched",
        },
        "candidate": candidate_before,
        "candidate_test_saveram": {**candidate_save_before, "action": "not_copied"},
        "translation_quality": "deferred_future_improvement_by_user_authorization_llm_literal",
    }
    atomic_json(PROMOTION, report)

    cleaned: list[str] = []
    for path in (CANDIDATE, CANDIDATE_SAVE):
        if path.exists():
            path.unlink()
            cleaned.append(rel(path))
    report["cleanup"] = {"removed": cleaned, "kept_reports": [rel(BUILD), rel(AUDIT), rel(POST)]}
    atomic_json(PROMOTION, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
