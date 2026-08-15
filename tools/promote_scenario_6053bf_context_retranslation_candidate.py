#!/usr/bin/env python3
"""Promote the approved scenario-6053BF contextual retranslation candidate.

ROM-only transaction.  The current live SaveRAM is preserved byte-exactly.
The candidate contains the approved 14K main-carry rebase plus the 57-line
6053BF..605824 contextual retranslation and the 605410 '내 배짱 얘기는 됐고。'
follow-up correction.
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

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "scenario_6053bf_context_retranslation_candidate.wsc"
AUDIT = PATCH / "scenario_6053bf_context_retranslation_candidate_audit.json"
BUILD = PATCH / "scenario_6053bf_context_retranslation_candidate_report.json"
PROMOTION = PATCH / "scenario_6053bf_context_retranslation_promotion_report.json"
EXPECTED_MAIN = "9468c74624483a7b4a3b55c896d5277b73afc1b80fd5270e8e4b9d69fbfa197a"
EXPECTED_CAND = "b6192a05fbfc37dc021ff2ccc9f1ee89ee50c0375c6ddfe807edc381f20e0662"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def req(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def checksum(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    return int.from_bytes(data[-2:], "little"), sum(data[:-2]) & 0xFFFF


def atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_checked(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def main() -> int:
    for p in (TIP, SAVE, CAND, AUDIT, BUILD):
        req(p.is_file(), f"missing required artifact: {p}")
    req(TIP.stat().st_size == ROM_SIZE, "main TIP size drift")
    req(CAND.stat().st_size == ROM_SIZE, "candidate size drift")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drift")
    req(sha(TIP) == EXPECTED_MAIN, f"main TIP identity drifted: {sha(TIP)}")
    req(sha(CAND) == EXPECTED_CAND, f"candidate identity drifted: {sha(CAND)}")

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    req(audit.get("ok") is True, "candidate independent audit is not green")
    req(str(audit.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "audit candidate SHA mismatch")
    req(int(audit.get("runtime_contract_hard_failures", -1)) == 0, "runtime contract hard failures remain")
    req(int(audit.get("text_failures", -1)) == 0, "candidate text verification failure")
    req(int(audit.get("width_failures", -1)) == 0, "candidate width failure")
    req(int(audit.get("protected_runtime_body_changes", -1)) == 0, "protected native runtime body changed")
    req(int(audit.get("contract_boundary_changed", -1)) == 0, "record boundary changed")

    build = json.loads(BUILD.read_text(encoding="utf-8"))
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build report candidate mismatch")
    req(int((build.get("counts") or {}).get("targets", -1)) == 57, "57-line target population drift")
    req(int((build.get("counts") or {}).get("max_cells", -1)) <= 20, "20-cell rule drift")

    stored, computed = checksum(CAND)
    req(stored == computed, "candidate WonderSwan checksum mismatch")

    save_before = SAVE.read_bytes()
    save_sha_before = hashlib.sha256(save_before).hexdigest()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_scenario_6053bf_context_retranslation"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "reason": "pre_scenario_6053bf_context_retranslation",
        "main_tip_sha256": EXPECTED_MAIN,
        "candidate_sha256": EXPECTED_CAND,
        "live_saveram_sha256": save_sha_before,
        "rom_only": True,
    })

    tmp_tip = TIP.with_name(f".{TIP.name}.{os.getpid()}.tmp")
    shutil.copy2(CAND, tmp_tip)
    req(sha(tmp_tip) == EXPECTED_CAND, "temporary promoted ROM verification failed")
    os.replace(tmp_tip, TIP)

    req(sha(TIP) == EXPECTED_CAND, "post-promotion main SHA mismatch")
    req(SAVE.read_bytes() == save_before, "live SaveRAM changed during ROM-only promotion")
    post_stored, post_computed = checksum(TIP)
    req(post_stored == post_computed, "post-promotion WonderSwan checksum mismatch")

    # Regenerate the authoritative current-main runtime contract and rerun its tests.
    run_checked("tools/dialogue_runtime_contracts.py", "--target", "out/patch/monoeye_ko_expanded.wsc", "--out", "out/script/dialogue_runtime_contracts.json", "--audit")
    run_checked("tools/test_dialogue_runtime_contracts.py")
    run_checked("tools/test_dialogue_runtime_safety_gate.py")

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_scenario_6053bf_context_retranslation_candidate.py",
        "status": "promoted",
        "user_authorized": True,
        "rom_only": True,
        "pre_main_sha256": EXPECTED_MAIN,
        "candidate_sha256": EXPECTED_CAND,
        "post_main_sha256": sha(TIP),
        "wonder_swan_checksum": f"{post_stored:04X}",
        "live_saveram_sha256_before": save_sha_before,
        "live_saveram_sha256_after": hashlib.sha256(SAVE.read_bytes()).hexdigest(),
        "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        "backup": str(backup.relative_to(ROOT)).replace("\\", "/"),
        "candidate_audit_ok": bool(audit.get("ok")),
        "candidate_targets": 57,
        "candidate_max_cells": int((build.get("counts") or {}).get("max_cells", 0)),
        "runtime_contract_hard_failures": 0,
        "notes": [
            "Promoted the full approved candidate, including the 14K main-carry rebase.",
            "Scenario 6053C8-605824 contextual retranslation uses 57 private ext3 slots.",
            "605410 wording is '내 배짱 얘기는 됐고。'.",
            "Protected native iteration records remain unchanged by the candidate audit.",
        ],
    }
    atomic_json(PROMOTION, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
