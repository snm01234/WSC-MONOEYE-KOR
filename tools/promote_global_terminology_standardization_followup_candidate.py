#!/usr/bin/env python3
"""Promote the runtime-tested v3 + whole-game terminology follow-up to main TIP.

Fail-closed promotion:
* candidate identity and pre-promotion audit reports are pinned;
* live main is backed up;
* ROM-only promotion (SaveRAM is preserved byte-exact);
* the promoted ROM is identity-checked immediately;
* any exception after replacement restores the previous main automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "global_terminology_standardization_followup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/global_terminology_standardization_followup_candidate.sav"
BUILD_REPORT = PATCH / "global_terminology_standardization_followup_candidate_report.json"
RUNTIME_REPORT = PATCH / "global_terminology_standardization_followup_runtime_safety.json"
BATTLE_REPORT = PATCH / "global_terminology_standardization_followup_battle_exact.json"
TERM_REPORT = PATCH / "global_terminology_standardization_followup_terminology_audit.json"
PROMOTION_REPORT = PATCH / "global_terminology_standardization_followup_promotion_report.json"

EXPECTED_MAIN_SHA = "dbc0e567fb3d7d3cd207a7e1dc6a737fbca5d131c299e8b0efc977daee546458"
EXPECTED_CANDIDATE_SHA = "2bccff99a36cd453bf01225365a40ed4d744049e69d46e726ab427851afa1799"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise PromotionError(f"required report missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    main_before = MAIN.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()

    if len(main_before) != ROM_SIZE or sha(main_before) != EXPECTED_MAIN_SHA:
        raise PromotionError(f"live main identity drifted: {sha(main_before)}")
    if len(candidate) != ROM_SIZE or sha(candidate) != EXPECTED_CANDIDATE_SHA:
        raise PromotionError(f"candidate identity drifted: {sha(candidate)}")
    if len(save_before) != SAVE_SIZE or candidate_save != save_before:
        raise PromotionError("candidate SaveRAM is not byte-exact current live SaveRAM")

    build = read_json(BUILD_REPORT)
    runtime = read_json(RUNTIME_REPORT)
    battle = read_json(BATTLE_REPORT)
    term = read_json(TERM_REPORT)
    if not build.get("ok") or build.get("candidate", {}).get("sha256") != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("candidate build report not approved")
    if not runtime.get("ok") or int(runtime.get("counts", {}).get("hard_failures", -1)) != 0 or int(runtime.get("counts", {}).get("review_items", -1)) != 0:
        raise PromotionError("runtime safety report not clean")
    if not battle.get("ok") or int(battle.get("counts", {}).get("failures", -1)) != 0:
        raise PromotionError("battle exact report not clean")
    if term.get("status") != "clean" or any(int(term.get("counts", {}).get(key, -1)) != 0 for key in ("active_source_hits", "dictionary_hits", "five_bank_dictionary_hits", "rendered_record_hits")):
        raise PromotionError("terminology report not clean")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_global_terminology_standardization_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    backup_save = backup_dir / MAIN_SAVE.name
    backup_rom.write_bytes(main_before)
    backup_save.write_bytes(save_before)

    promoted = False
    try:
        atomic_bytes(MAIN, candidate)
        promoted = True
        live = MAIN.read_bytes()
        if live != candidate or sha(live) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("post-promotion main identity mismatch")
        if MAIN_SAVE.read_bytes() != save_before:
            raise PromotionError("SaveRAM changed during ROM-only promotion")

        report = {
            "schema_version": 1,
            "generated_by": "tools/promote_global_terminology_standardization_followup_candidate.py",
            "ok": True,
            "status": "promoted_pending_postpromotion_audits",
            "main_before": {"sha256": EXPECTED_MAIN_SHA, "size": len(main_before)},
            "main_after": {"sha256": EXPECTED_CANDIDATE_SHA, "size": len(candidate)},
            "save": {"sha256": sha(save_before), "size": len(save_before), "changed": False},
            "backup": {
                "directory": str(backup_dir.relative_to(ROOT)).replace("\\", "/"),
                "rom": str(backup_rom.relative_to(ROOT)).replace("\\", "/"),
                "save": str(backup_save.relative_to(ROOT)).replace("\\", "/"),
            },
            "prepromotion_audits": {
                "runtime_safety": True,
                "battle_exact": True,
                "terminology": True,
            },
        }
        atomic_json(PROMOTION_REPORT, report)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    except Exception:
        if promoted:
            atomic_bytes(MAIN, main_before)
            if MAIN_SAVE.read_bytes() != save_before:
                MAIN_SAVE.write_bytes(save_before)
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
