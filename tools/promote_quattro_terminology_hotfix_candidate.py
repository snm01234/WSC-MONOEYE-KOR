#!/usr/bin/env python3
"""Promote user-authorized 콰트로→크와트로 terminology hotfix (ROM-only)."""
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
PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
SRAM = ROOT / "sram"

TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = SRAM / "monoeye_ko_expanded.sav"
CAND = PATCH / "quattro_terminology_hotfix_candidate.wsc"
CAND_SAVE = SRAM / "quattro_terminology_hotfix_candidate.sav"
BUILD = PATCH / "quattro_terminology_hotfix_candidate_report.json"
APPROVAL = PATCH / "quattro_terminology_hotfix_user_validation.json"

POST_WIDTH = PATCH / "quattro_terminology_hotfix_postpromotion_20cell.json"
POST_WIDTH_CSV = SCRIPT / "quattro_terminology_hotfix_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "quattro_terminology_hotfix_postpromotion_false_lead.json"
POST_FALSE = PATCH / "quattro_terminology_hotfix_postpromotion_false_segptr.json"
POST_TERM = PATCH / "quattro_terminology_hotfix_postpromotion_terminology.json"
PROMOTION = PATCH / "quattro_terminology_hotfix_promotion_report.json"
PARENT_WIDTH = PATCH / "dialogue_legacy_mt_batch018_main_width_baseline.json"

EXPECTED_MAIN = "edb0b2502753a6682b63ea535f65fd3fa017923b21cdb8ed06d8a30f32edf248"
EXPECTED_CAND = "93de328215eec7d4162279e5956e6cf110741b0ad3a311e9f499019ce6c5f81e"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(ok: bool, msg: str) -> None:
    if not ok:
        raise RuntimeError(msg)


def checksum_ok(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, doc: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_audit(args: list[str], *, allow_nonzero: bool = False) -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=False)
    if allow_nonzero:
        req(proc.returncode in (0, 1), f"audit crashed: {' '.join(args)} rc={proc.returncode}")
    else:
        req(proc.returncode == 0, f"audit failed: {' '.join(args)} rc={proc.returncode}")
    return proc.returncode


def validate_post(expected_sha: str, parent_offender_abs: set[str]) -> dict[str, Any]:
    w = load(POST_WIDTH)
    wp = w.get("population") or {}
    req(str((w.get("rom") or {}).get("sha256") or "").lower() == expected_sha, "20-cell SHA mismatch")
    cand_offenders = {
        str(row.get("abs") or row.get("address") or "").upper()
        for row in (w.get("offenders") or [])
    }
    cand_offenders.discard("")
    new_offenders = sorted(cand_offenders - parent_offender_abs)
    req(not new_offenders, f"new 20-cell offenders introduced: {new_offenders}")
    req(int(wp.get("records", -1)) == 24047, "20-cell record count drift")

    l = load(POST_LEADS)
    lc = l.get("counts") or {}
    req(l.get("ok") is True and not l.get("failures"), "visible-lead audit failed")
    req(str((l.get("target") or {}).get("sha256") or "").lower() == expected_sha, "visible-lead SHA mismatch")
    req(int(lc.get("total_guarded_leads", -1)) == 340 and int(lc.get("reintroduced", -1)) == 0, "visible-lead regression")

    f = load(POST_FALSE)
    req(f.get("ok") is True and int(f.get("sites_found", -1)) == 0, "false-segptr failed")
    fsha = str((((f.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower()
    req(fsha == expected_sha, "false-segptr SHA mismatch")

    t = load(POST_TERM)
    tc = t.get("counts") or {}
    req(t.get("status") == "clean", "terminology not clean")
    req(str((t.get("tip") or {}).get("sha256") or "").lower() == expected_sha, "terminology SHA mismatch")
    req(
        int(tc.get("active_source_hits", -1)) == 0
        and int(tc.get("dictionary_hits", -1)) == 0
        and int(tc.get("rendered_record_hits", -1)) == 0,
        "terminology residual",
    )
    return {
        "width_offenders_total": int(wp.get("offender_records", len(cand_offenders))),
        "width_offenders_preexisting": sorted(parent_offender_abs),
        "width_offenders_new": new_offenders,
        "visible_lead_reintroduced": int(lc["reintroduced"]),
        "false_segptr_sites": int(f["sites_found"]),
        "terminology_hits": [
            int(tc["active_source_hits"]),
            int(tc["dictionary_hits"]),
            int(tc["rendered_record_hits"]),
        ],
    }


def main() -> int:
    for path in (TIP, SAVE, CAND, CAND_SAVE, BUILD, APPROVAL, PARENT_WIDTH):
        req(path.is_file(), f"missing {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drift")
    req(checksum_ok(CAND), "candidate checksum invalid")

    build = load(BUILD)
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req(build.get("ok") is True, "build not ok")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    parent_width = load(PARENT_WIDTH)
    parent_offender_abs = {
        str(row.get("abs") or row.get("address") or "").upper()
        for row in (parent_width.get("offenders") or [])
    }
    parent_offender_abs.discard("")
    req(parent_offender_abs == {
        "63CF8A", "63CFF8", "63D00A", "63D321", "63E226", "63E55C", "63F64C"
    }, "parent 20-cell baseline drifted")

    save_before = ident(SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_quattro_terminology_hotfix"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / "monoeye_ko_expanded.wsc"
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "backup verification failed")
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "created_at": stamp,
            "tip": ident(backup),
            "reason": "pre_quattro_terminology_hotfix_promotion",
        },
    )

    staged = TIP.with_name(f".{TIP.name}.quattro.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(sha(TIP) == EXPECTED_CAND and checksum_ok(TIP), "promoted TIP identity/checksum failure")
        req(ident(SAVE) == save_before, "live SaveRAM changed")

        width_rc = run_audit(
            [
                str(ROOT / "tools/audit_dialogue_20cell_candidate.py"),
                "--rom",
                str(TIP),
                "--out",
                str(POST_WIDTH),
                "--out-csv",
                str(POST_WIDTH_CSV),
            ],
            allow_nonzero=True,
        )
        req(POST_WIDTH.is_file(), "20-cell audit report missing")
        run_audit(
            [
                str(ROOT / "tools/audit_battle_false_lead_recurrence.py"),
                "--target",
                str(TIP),
                "--out",
                str(POST_LEADS),
            ]
        )
        run_audit(
            [
                str(ROOT / "tools/scan_false_segptr_writes.py"),
                "--target",
                str(TIP),
                "--out",
                str(POST_FALSE),
            ]
        )
        run_audit(
            [
                str(ROOT / "tools/audit_gundam_terminology_standard.py"),
                "--tip",
                str(TIP),
                "--out",
                str(POST_TERM),
            ]
        )
        post_checks = validate_post(EXPECTED_CAND, parent_offender_abs)
        post_checks["p2_audit"] = "skipped_missing_p2_local_ext3_expansion_approval"
        post_checks["width_gate"] = "no_new_offenders_vs_parent_baseline"
        post_checks["width_audit_returncode"] = width_rc
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_quattro_terminology_hotfix_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_authorized_quattro_terminology_hotfix",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": ident(TIP),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": ident(SAVE),
        "source_candidate": ident(CAND),
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "post_checks": post_checks,
        "scope_note": {
            "rewrote_ext3_slots": ["01594", "015C2"],
            "neutralized_orphan": "0DFD313",
            "canonical": "크와트로",
            "forbidden_removed": "콰트로",
        },
    }
    req(report["live_saveram_after"] == save_before, "SaveRAM post identity drift")
    atomic_json(PROMOTION, report)
    print(json.dumps({k: report[k] for k in ("status", "after", "checksum", "backup", "post_checks", "scope_note")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
