#!/usr/bin/env python3
"""Promote the user-approved dialogue runtime-contract candidate ROM only.

The script is deliberately bound to the exact parent and candidate hashes.
It backs up the current main TIP, performs contract audits before and after the
atomic ROM replacement, preserves the live main SaveRAM, and records the
candidate SaveRAM actually used during measurement.
"""
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

from audit_dialogue_runtime_safety_gate import audit  # noqa: E402
from dialogue_runtime_contracts import DEFAULT_MANIFEST  # noqa: E402
from monoeye_rom import find_rom, load_rom  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "dialogue_runtime_contract_candidate.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/dialogue_runtime_contract_candidate.sav"
BUILD_REPORT = PATCH / "dialogue_runtime_contract_candidate_report.json"
RUNTIME_VALIDATION = PATCH / "dialogue_runtime_contract_runtime_validation.json"
USER_VALIDATION = PATCH / "dialogue_runtime_contract_user_validation.json"
PROMOTION_REPORT = PATCH / "dialogue_runtime_contract_promotion_report.json"
POST_GATE = PATCH / "dialogue_runtime_contract_postpromotion_safety.json"

EXPECTED_PARENT_SHA = "27321bdd4ed7fd6b35d56f80745d47946e2b517aadd83689d34c31b59694a483"
EXPECTED_CANDIDATE_SHA = "d2b7301b0f51071a566dd473be4a528d1d13a4305fc251de5543133ab5b0db20"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def ident(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def verify_checksum(rom: bytes) -> str:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    if stored != computed:
        raise PromotionError(f"checksum mismatch: stored={stored:04X}, computed={computed:04X}")
    return f"{stored:04X}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--approved",
        action="store_true",
        help="required: confirms explicit user runtime approval for this exact candidate",
    )
    args = parser.parse_args(argv)
    if not args.approved:
        raise PromotionError("promotion requires explicit --approved")

    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if len(parent) != ROM_SIZE or len(candidate) != ROM_SIZE:
        raise PromotionError("main or candidate ROM size drifted")
    if sha(parent) != EXPECTED_PARENT_SHA:
        raise PromotionError(f"main TIP drifted: {sha(parent)}")
    if sha(candidate) != EXPECTED_CANDIDATE_SHA:
        raise PromotionError(f"candidate drifted: {sha(candidate)}")
    checksum = verify_checksum(candidate)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    if not build.get("ok") or build["outputs"]["candidate_rom"]["sha256"] != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("candidate build report is missing or not bound to the approved ROM")

    original = bytes(load_rom(find_rom(ROOT)))
    pre_manifest = PATCH / "dialogue_runtime_contract_prepromotion_manifest.json"
    pre = audit(candidate, original, target_path=CANDIDATE, manifest_path=pre_manifest)
    if not pre["ok"]:
        raise PromotionError(f"pre-promotion contract gate failed: {pre['counts']}")

    main_save_before = MAIN_SAVE.read_bytes()
    if len(main_save_before) != SAVE_SIZE:
        raise PromotionError(f"live main SaveRAM size drifted: {len(main_save_before)}")
    measured_save = CANDIDATE_SAVE.read_bytes()
    if len(measured_save) != SAVE_SIZE:
        raise PromotionError(f"candidate SaveRAM size drifted: {len(measured_save)}")

    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_dialogue_runtime_contract_promotion"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / MAIN.name
    shutil.copy2(MAIN, backup)
    if sha(backup.read_bytes()) != EXPECTED_PARENT_SHA:
        raise PromotionError("backup verification failed")

    temporary_main = MAIN.with_suffix(MAIN.suffix + ".promotion.tmp")
    temporary_main.write_bytes(candidate)
    os.replace(temporary_main, MAIN)
    if sha(MAIN.read_bytes()) != EXPECTED_CANDIDATE_SHA:
        shutil.copy2(backup, MAIN)
        raise PromotionError("post-copy main verification failed; parent restored")

    post = audit(bytes(load_rom(MAIN)), original, target_path=MAIN, manifest_path=DEFAULT_MANIFEST)
    write_json(POST_GATE, post)
    if not post["ok"]:
        shutil.copy2(backup, MAIN)
        raise PromotionError("post-promotion contract gate failed; parent restored")
    if MAIN_SAVE.read_bytes() != main_save_before:
        shutil.copy2(backup, MAIN)
        raise PromotionError("live main SaveRAM changed during ROM promotion; parent restored")

    user_validation = {
        "schema_version": 1,
        "status": "approved",
        "approved_at": promoted_at,
        "approval_source": "user_runtime_measurement_in_current_codex_task",
        "user_summary": "실측에서 큰 문제는 없는 거 같으니 메인 TIP으로 승격",
        "candidate_rom": ident(CANDIDATE, candidate),
        "measured_candidate_saveram": ident(CANDIDATE_SAVE, measured_save),
        "promotion_authorized": True,
    }
    write_json(USER_VALIDATION, user_validation)

    validation = json.loads(RUNTIME_VALIDATION.read_text(encoding="utf-8"))
    validation.update(
        {
            "status": "approved_and_promoted",
            "promotion_allowed": True,
            "approved_at": promoted_at,
            "measured_candidate_saveram": ident(CANDIDATE_SAVE, measured_save),
            "results": [
                {
                    "source": "user_runtime_measurement",
                    "result": "no_major_issue_observed",
                    "promotion_decision": "approved",
                }
            ],
        }
    )
    write_json(RUNTIME_VALIDATION, validation)

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_dialogue_runtime_contract_candidate.py",
        "ok": True,
        "promoted_at": promoted_at,
        "inputs": {
            "parent_main": ident(backup, parent),
            "approved_candidate": ident(CANDIDATE, candidate),
            "measured_candidate_saveram": ident(CANDIDATE_SAVE, measured_save),
        },
        "outputs": {
            "main_tip": ident(MAIN),
            "live_main_saveram": ident(MAIN_SAVE),
            "backup": ident(backup),
            "postpromotion_contract": ident(DEFAULT_MANIFEST),
            "postpromotion_gate": ident(POST_GATE),
            "user_validation": ident(USER_VALIDATION),
        },
        "gates": {
            "explicit_user_approval": True,
            "approved_candidate_sha_exact": True,
            "backup_sha_exact": True,
            "prepromotion_contract_hard_failures": pre["counts"]["hard_failures"],
            "postpromotion_contract_hard_failures": post["counts"]["hard_failures"],
            "quarantine_changes_zero": post["counts"]["hard_by_reason"].get(
                "quarantine_record_changed", 0
            )
            == 0,
            "live_main_saveram_unchanged": True,
            "legacy_audit_used": False,
        },
        "checksum": checksum,
        "rollback": str(backup.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
    }
    write_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
