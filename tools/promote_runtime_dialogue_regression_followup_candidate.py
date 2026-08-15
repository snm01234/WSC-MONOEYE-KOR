#!/usr/bin/env python3
"""Promote the user-validated 2026-08-09 runtime dialogue regression candidate.

ROM-only transaction. The current live SaveRAM is never replaced. Promotion is
bound to the exact parent/candidate hashes and the explicit user approval file.
A verified rollback backup is created first; independent dialogue, 20-cell,
visible-lead, P2 terminator and false-segmented-pointer audits are rerun against
the promoted main TIP. Any post-promotion failure restores the backup.
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
CAND = PATCH / "runtime_dialogue_regression_followup_candidate.wsc"
CAND_SAVE = ROOT / "sram/runtime_dialogue_regression_followup_candidate.sav"
SRAM_MIRROR = ROOT / "sram/runtime_dialogue_regression_followup_candidate.sav"
BUILD = PATCH / "runtime_dialogue_regression_followup_report.json"
PRE_RUNTIME = PATCH / "runtime_dialogue_regression_followup_audit.json"
PRE_WIDTH = PATCH / "runtime_dialogue_regression_20cell_audit.json"
PRE_LEADS = PATCH / "runtime_dialogue_regression_false_lead_audit.json"
PRE_FALSE = PATCH / "runtime_dialogue_regression_false_segptr.json"
APPROVAL = PATCH / "runtime_dialogue_regression_followup_user_validation.json"

POST_RUNTIME = PATCH / "runtime_dialogue_regression_followup_postpromotion_audit.json"
POST_WIDTH = PATCH / "runtime_dialogue_regression_postpromotion_20cell_audit.json"
POST_WIDTH_CSV = SCRIPT / "runtime_dialogue_regression_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "runtime_dialogue_regression_postpromotion_false_lead_audit.json"
POST_TERM = PATCH / "runtime_dialogue_regression_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "runtime_dialogue_regression_postpromotion_false_segptr.json"
REPORT = PATCH / "runtime_dialogue_regression_followup_promotion_report.json"

EXPECTED_PARENT = "8a53737d209ff695fdcd78c0f46f9e61eff9a15d8c4f01b0f387e8dd05488af2"
EXPECTED_CAND = "5c2d4620809274338bda6d46eb6229fa810e6a3ad9b1c58d41ccb5a503abd67f"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 245
EXPECTED_SCENARIO = 165
EXPECTED_BATTLE_5D5E = 5
EXPECTED_BANK5F = 75
EXPECTED_WIDTH_RECORDS = 24_047
EXPECTED_WIDTH_LINES = 24_459
EXPECTED_BATTLE_RECORDS = 9_783
EXPECTED_GUARDED_LEADS = 338


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def checksum_ok(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_checked(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def validate_runtime(doc: dict, expected_sha: str) -> None:
    counts = doc.get("counts") or {}
    target = doc.get("target") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "runtime dialogue audit failed")
    req(str(target.get("sha256") or "").lower() == expected_sha, "runtime audit target SHA mismatch")
    req(int(counts.get("targets", -1)) == EXPECTED_TARGETS, "runtime target population drifted")
    req(int(counts.get("scenario", -1)) == EXPECTED_SCENARIO, "scenario target population drifted")
    req(int(counts.get("battle_5d5e", -1)) == EXPECTED_BATTLE_5D5E, "5D/5E target population drifted")
    req(int(counts.get("bank5f", -1)) == EXPECTED_BANK5F, "bank5F target population drifted")
    req(int(counts.get("bank5f_discovered_active", -1)) == EXPECTED_BANK5F, "bank5F discovery drifted")
    req(int(counts.get("bank5f_canonical", -1)) == EXPECTED_BANK5F, "bank5F canonical drifted")
    req(int(counts.get("failures", -1)) == 0, "runtime dialogue failures remain")
    req(doc.get("bank5f_coverage_ok") is True, "bank5F coverage incomplete")


def validate_width(doc: dict, expected_sha: str) -> None:
    pop = doc.get("population") or {}
    rom = doc.get("rom") or {}
    req(doc.get("ok") is True and doc.get("width_ok") is True and doc.get("terminology_ok") is True, "20-cell audit failed")
    req(str(rom.get("sha256") or "").lower() == expected_sha, "20-cell audit target SHA mismatch")
    req(not doc.get("offenders"), "20-cell offenders remain")
    req(not doc.get("terminology_residuals"), "20-cell terminology residuals remain")
    req(int(pop.get("records", -1)) == EXPECTED_WIDTH_RECORDS, "20-cell record population drifted")
    req(int(pop.get("lines", -1)) == EXPECTED_WIDTH_LINES, "20-cell line population drifted")
    req(int(pop.get("offender_records", -1)) == 0, "20-cell offender count nonzero")
    req(int(pop.get("max_line_cells", -1)) == 20, "20-cell maximum drifted")
    battle = ((pop.get("by_scope") or {}).get("battle_voice") or {})
    req(int(battle.get("records", -1)) == EXPECTED_BATTLE_RECORDS, "battle voice population drifted")
    req(int(battle.get("over_20_records", -1)) == 0, "battle voice width regression")


def validate_leads(doc: dict, expected_sha: str) -> None:
    counts = doc.get("counts") or {}
    target = doc.get("target") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "visible-lead audit failed")
    req(str(target.get("sha256") or "").lower() == expected_sha, "visible-lead audit target SHA mismatch")
    req(int(counts.get("total_guarded_leads", -1)) == EXPECTED_GUARDED_LEADS, "visible-lead population drifted")
    req(int(counts.get("reintroduced", -1)) == 0, "visible-lead recurrence remains")


def validate_false_segptr(doc: dict, expected_sha: str) -> None:
    inputs = doc.get("inputs") or {}
    target = inputs.get("target") or {}
    req(doc.get("ok") is True and int(doc.get("sites_found", -1)) == 0, "false segmented-pointer regression")
    req(str(target.get("sha256") or "").lower() == expected_sha, "false-segptr audit target SHA mismatch")


def validate_terminator(doc: dict) -> None:
    counts = doc.get("counts") or {}
    req(int(counts.get("current_still_expanded", -1)) == 0, "expanded P2 terminator remains")
    req(int(counts.get("separator_nul_lost", -1)) == 0, "separator NUL loss remains")
    req(int(counts.get("runtime_risk", -1)) == 0, "P2 runtime terminator risk remains")


def main() -> int:
    required = (TIP, SAVE, CAND, CAND_SAVE, BUILD, PRE_RUNTIME, PRE_WIDTH, PRE_LEADS, PRE_FALSE, APPROVAL)
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size wrong")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE, "candidate SaveRAM size wrong")
    req(CAND_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM does not match current live SaveRAM")
    req(checksum_ok(CAND), "candidate WonderSwan checksum invalid")

    build = load(BUILD)
    build_counts = build.get("counts") or {}
    req(build.get("ok") is True, "build report not clean")
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_PARENT, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req(int(build_counts.get("records", -1)) == EXPECTED_TARGETS, "build target count drifted")
    req(int(build_counts.get("terminator_changes", -1)) == 0, "build terminator changes detected")
    req(int(build_counts.get("unexpected_diff_offsets", -1)) == 0, "build unexpected diff detected")

    validate_runtime(load(PRE_RUNTIME), EXPECTED_CAND)
    validate_width(load(PRE_WIDTH), EXPECTED_CAND)
    validate_leads(load(PRE_LEADS), EXPECTED_CAND)
    validate_false_segptr(load(PRE_FALSE), EXPECTED_CAND)

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("parent_tip_sha256") or "").lower() == EXPECTED_PARENT, "approval parent mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    save_before = ident(SAVE)
    candidate_ident = ident(CAND)
    candidate_save_ident = ident(CAND_SAVE)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_runtime_dialogue_regression_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "rollback backup verification failed")
    backup_manifest = {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_dialogue_regression_followup_candidate.py",
        "reason": "pre_runtime_dialogue_regression_followup",
        "main_tip": ident(backup),
        "candidate_sha256": EXPECTED_CAND,
    }
    atomic_json(backup_dir / "backup_manifest.json", backup_manifest)

    staged = TIP.with_name(f".{TIP.name}.runtime_dialogue_regression_followup.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP), "promoted TIP checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        run_checked(str(ROOT / "tools/audit_runtime_dialogue_regression_followup.py"), "--target", str(TIP), "--out", str(POST_RUNTIME))
        validate_runtime(load(POST_RUNTIME), EXPECTED_CAND)

        run_checked(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV))
        validate_width(load(POST_WIDTH), EXPECTED_CAND)

        run_checked(str(ROOT / "tools/audit_battle_false_lead_recurrence.py"), "--target", str(TIP), "--out", str(POST_LEADS))
        validate_leads(load(POST_LEADS), EXPECTED_CAND)

        run_checked(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_TERM))
        validate_terminator(load(POST_TERM))

        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE))
        validate_false_segptr(load(POST_FALSE), EXPECTED_CAND)
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_dialogue_regression_followup_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_runtime_dialogue_regression_followup_user_verified",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": save_after,
        "source_candidate_before_cleanup": candidate_ident,
        "source_candidate_saveram_before_cleanup": candidate_save_ident,
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "post_runtime_dialogue_audit": ident(POST_RUNTIME),
        "post_20cell_audit": ident(POST_WIDTH),
        "post_visible_lead_audit": ident(POST_LEADS),
        "post_terminator_audit": ident(POST_TERM),
        "post_false_segptr_audit": ident(POST_FALSE),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "runtime_dialogue_245_exact": int((load(POST_RUNTIME).get("counts") or {}).get("targets", -1)) == EXPECTED_TARGETS,
            "bank5f_75_of_75": int((load(POST_RUNTIME).get("counts") or {}).get("bank5f_discovered_active", -1)) == EXPECTED_BANK5F,
            "post_20cell_zero_offenders": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_visible_lead_reintroduced_zero": int((load(POST_LEADS).get("counts") or {}).get("reintroduced", -1)) == 0,
            "post_p2_runtime_risk_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "promotion report checks failed")
    atomic_json(REPORT, report)

    cleanup: list[dict] = []
    reclaimed = 0
    for path in (CAND, CAND_SAVE, SRAM_MIRROR, POST_WIDTH_CSV):
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            cleanup.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(REPORT, report)

    print(json.dumps({
        "ok": True,
        "main_sha256": after["sha256"],
        "checksum": report["checksum"],
        "save_unchanged": report["checks"]["live_saveram_unchanged"],
        "backup": report["backup"]["path"],
        "cleanup_reclaimed_bytes": reclaimed,
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
