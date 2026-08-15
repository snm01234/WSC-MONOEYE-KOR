#!/usr/bin/env python3
"""Promote the user-verified A Baoa Qu bank59 dialogue ROM only."""
from __future__ import annotations

import argparse
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
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "abaoa_qu_bank59_event_dialogue_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/abaoa_qu_bank59_event_dialogue_candidate.sav"
WORKLIST = PATCH / "abaoa_qu_bank59_event_dialogue_worklist.json"
CATALOG = ROOT / "data/abaoa_qu_bank59_event_dialogue_ko.json"
BUILD = PATCH / "abaoa_qu_bank59_event_dialogue_report.json"
AUDIT = PATCH / "abaoa_qu_bank59_event_dialogue_candidate_audit.json"
RESIDUAL = PATCH / "abaoa_qu_bank59_event_dialogue_residual_audit.json"
REGRESSION = PATCH / "abaoa_qu_bank59_event_dialogue_regression_audit.json"
USER = PATCH / "abaoa_qu_bank59_event_dialogue_user_validation.json"
POST = PATCH / "abaoa_qu_bank59_event_dialogue_postpromotion_audit.json"
PROMOTION = PATCH / "abaoa_qu_bank59_event_dialogue_promotion_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_TIP = "186d6d04859146420f4ae826a38a0d35377633d54587046c30046125cbceb241"
EXPECTED_CANDIDATE = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"
EXPECTED_AUTH = "promote candidate ROM to main TIP and continue auditing battle dialogue for leading Japanese glyph residuals"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


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


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing JSON evidence: {rel(path)}")
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
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def validate_evidence() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE)
    require(CANDIDATE_SAVE, SAVE_SIZE)
    build = load(BUILD)
    audit = load(AUDIT)
    residual = load(RESIDUAL)
    regression = load(REGRESSION)
    user = load(USER)
    if build.get("ok") is not True or str((build.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("build evidence failed")
    if audit.get("ok") is not True or regression.get("ok") is not True or residual.get("ok") is not True:
        raise PromotionError("static, residual, or regression audit failed")
    checks = audit.get("checks") or {}
    required = (
        "candidate_identity_exact",
        "targets_257",
        "all_targets_exact",
        "screenshot_anchors_exact",
        "selected_bank_changes_exact",
        "non_target_invariance",
        "runtime_banks_7a_7f_exact",
        "old_ext3_banks_11_20_exact",
        "diffs_bounded",
        "checksum_exact",
        "main_tip_unchanged",
    )
    if not all(checks.get(name) is True for name in required):
        raise PromotionError("required audit checks are incomplete")
    counts = residual.get("counts") or {}
    if int(counts.get("exact_targets", -1)) != 257 or int(counts.get("japanese_residual_records", -1)) != 0:
        raise PromotionError("candidate residual count is not zero")
    if user.get("status") != "user_emulator_validation_passed":
        raise PromotionError("user emulator validation is missing")
    if str((user.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("user validation is bound to another candidate")
    if user.get("authorization") != EXPECTED_AUTH:
        raise PromotionError("user promotion authorization is missing")
    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "build": identity(BUILD),
        "audit": identity(AUDIT),
        "residual": identity(RESIDUAL),
        "regression": identity(REGRESSION),
        "user_validation": identity(USER),
        "candidate_test_saveram": {**identity(CANDIDATE_SAVE), "action": "not_copied"},
    }


def audit_promoted_tip() -> dict[str, Any]:
    rom = bytes(load_rom(TIP))
    work = load(WORKLIST)
    catalog = load(CATALOG)
    sources = {str(row["abs"]).upper(): row for row in work.get("records") or []}
    targets = {str(row["abs"]).upper(): row for row in catalog.get("lines") or []}
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)
    failures: list[dict[str, str]] = []
    for address in sorted(sources, key=lambda value: int(value, 16)):
        source = sources[address]
        logical = int(address, 16)
        prefix_len = len(bytes.fromhex(str(source.get("prefix_hex") or "")))
        capacity = int(source["payload_capacity"])
        payload = rom[sb + logical : sb + logical + capacity]
        actual = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(targets[address]["ko"]))
        if actual != expected:
            failures.append({"abs": address, "expected": expected, "actual": actual})
            if len(failures) >= 20:
                break
    return {"rows": len(sources), "failures": failures, "all_exact": len(sources) == 257 and not failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate_evidence()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_abaoa_qu_bank59_event_dialogue"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(TIP_SAVE, backup_save)
    require(backup_rom, ROM_SIZE, EXPECTED_TIP)
    require(backup_save, SAVE_SIZE, digest(TIP_SAVE))

    old_tip = identity(TIP)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "abaoa-qu-bank59-promote")
        require(TIP, ROM_SIZE, EXPECTED_CANDIDATE)
        if identity(TIP_SAVE) != save_before:
            raise PromotionError("main SaveRAM changed during ROM promotion")
        catalog = audit_promoted_tip()
        checks = {
            "tip_matches_verified_candidate": digest(TIP) == EXPECTED_CANDIDATE,
            "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP,
            "main_saveram_unchanged": identity(TIP_SAVE) == save_before,
            "all_257_dialogue_rows_exact": catalog["all_exact"],
            "candidate_test_saveram_not_copied": digest(TIP_SAVE) == save_before["sha256"],
        }
        post = {
            "schema_version": 1,
            "generated_by": "tools/promote_abaoa_qu_bank59_event_dialogue_candidate.py",
            "ok": all(checks.values()),
            "tip": identity(TIP),
            "rollback_rom": identity(backup_rom),
            "main_saveram_after": identity(TIP_SAVE),
            "dialogue": catalog,
            "checks": checks,
        }
        if post.get("ok") is not True:
            raise PromotionError("post-promotion audit failed")
        atomic_json(POST, post)
    except Exception:
        atomic_copy(backup_rom, TIP, "abaoa-qu-bank59-rollback")
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_abaoa_qu_bank59_event_dialogue_candidate.py",
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
        "main_saveram": {"before": save_before, "after": identity(TIP_SAVE), "action": "left_untouched"},
        "candidate_test_saveram": {"path": rel(CANDIDATE_SAVE), "sha256_after_emulator_test": digest(CANDIDATE_SAVE), "action": "not_copied"},
    }
    atomic_json(PROMOTION, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
