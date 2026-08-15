#!/usr/bin/env python3
"""Promote the user-validated runtime regression bundle to the main TIP.

ROM-only transaction. The current live SaveRAM is preserved. The script verifies
parent/candidate identities, the focused 53-check candidate audit, user runtime
approval and pre-promotion structural gates; creates a verified rollback backup;
atomically replaces the main TIP; reruns the focused screen-contract audit and
structural/terminology/width gates against the promoted main; rolls back on any
failure; records the transaction; and removes the promoted candidate ROM/SAV.
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
CAND = PATCH / "runtime_regression_bundle_candidate.wsc"
CAND_SAVE = ROOT / "sram/runtime_regression_bundle_candidate.sav"
BUILD = PATCH / "runtime_regression_bundle_report.json"
AUDIT = PATCH / "runtime_regression_bundle_audit.json"
APPROVAL = PATCH / "runtime_regression_bundle_user_validation.json"
PRE_TERM = PATCH / "runtime_regression_bundle_prepromotion_terminator_audit.json"
PRE_TERMINOLOGY = PATCH / "runtime_regression_bundle_prepromotion_terminology_audit.json"
PRE_FALSE = PATCH / "runtime_regression_bundle_prepromotion_false_segptr.json"
POST_RUNTIME = PATCH / "runtime_regression_bundle_postpromotion_runtime_audit.json"
POST_TERM = PATCH / "runtime_regression_bundle_postpromotion_terminator_audit.json"
POST_TERMINOLOGY = PATCH / "runtime_regression_bundle_postpromotion_terminology_audit.json"
POST_FALSE = PATCH / "runtime_regression_bundle_postpromotion_false_segptr.json"
POST_WIDTH = PATCH / "runtime_regression_bundle_postpromotion_width_audit.json"
POST_WIDTH_CSV = SCRIPT / "runtime_regression_bundle_postpromotion_width_offenders.csv"
REPORT = PATCH / "runtime_regression_bundle_promotion_report.json"
TBL = PATCH / "hangul_patch_pad3.tbl"

EXPECTED_PARENT = "b192ad1ed2e24b709bfa14e5ae7d72405e58a3eac8ae746f41864961148d2746"
EXPECTED_CAND = "6425767be35813bf09e1fd2b223b98a9cd05d804cba254456e5d93f00a0a4f3c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
KNOWN_WIDTH_OFFENDERS = {"630695", "63CFEA"}


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


def req(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_checked(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> int:
    required = (TIP, SAVE, CAND, CAND_SAVE, BUILD, AUDIT, APPROVAL, PRE_TERM, PRE_TERMINOLOGY, PRE_FALSE, TBL)
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size wrong")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE, "candidate SaveRAM size wrong")
    req(CAND_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM is not the current live SaveRAM")
    req(checksum_ok(CAND.read_bytes()), "candidate WonderSwan checksum invalid")

    build = load(BUILD)
    req(build.get("ok") is True, "build report is not clean")
    req(str((((build.get("inputs") or {}).get("main") or {}).get("sha256") or "")).lower() == EXPECTED_PARENT, "build parent mismatch")
    req(str((((build.get("outputs") or {}).get("rom") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "build candidate mismatch")

    audit = load(AUDIT)
    ac = audit.get("counts") or {}
    req(audit.get("ok") is True, "focused candidate audit failed")
    req(str(((audit.get("candidate") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "focused audit candidate mismatch")
    req(int(ac.get("checks", -1)) == 53 and int(ac.get("failures", -1)) == 0, "focused candidate audit count drifted")

    term = load(PRE_TERM)
    tc = term.get("counts") or {}
    req(int(tc.get("runtime_risk", -1)) == 0, "pre-promotion terminator runtime risk remains")
    req(int(tc.get("separator_nul_lost", -1)) == 0, "pre-promotion separator NUL loss remains")
    req(int(tc.get("current_still_expanded", -1)) == 0, "pre-promotion expanded terminator remains")

    terminology = load(PRE_TERMINOLOGY)
    tcounts = terminology.get("counts") or {}
    req(terminology.get("status") == "clean", "pre-promotion terminology audit failed")
    req(int(tcounts.get("active_source_hits", -1)) == 0, "pre-promotion active terminology hits remain")
    req(int(tcounts.get("dictionary_hits", -1)) == 0, "pre-promotion dictionary terminology hits remain")
    req(int(tcounts.get("rendered_record_hits", -1)) == 0, "pre-promotion rendered terminology hits remain")

    false = load(PRE_FALSE)
    req(false.get("ok") is True and int(false.get("sites_found", -1)) == 0, "pre-promotion false segmented pointer remains")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(approval.get("runtime_validation_status") == "all_reported_cases_verified", "runtime validation status not complete")
    req(str(approval.get("parent_tip_sha256") or "").lower() == EXPECTED_PARENT, "approval parent mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    save_before = ident(SAVE)
    candidate_ident = ident(CAND)
    candidate_save_ident = ident(CAND_SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_runtime_regression_bundle"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "rollback backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.runtime_regression.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP.read_bytes()), "promoted TIP checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        run_checked(
            str(ROOT / "tools/audit_runtime_regression_bundle_candidate.py"),
            "--parent", str(backup),
            "--candidate", str(TIP),
            "--parent-sav", str(SAVE),
            "--candidate-sav", str(SAVE),
            "--out", str(POST_RUNTIME),
        )
        post_runtime = load(POST_RUNTIME)
        prc = post_runtime.get("counts") or {}
        req(post_runtime.get("ok") is True, "post-promotion focused runtime audit failed")
        req(int(prc.get("checks", -1)) == 53 and int(prc.get("failures", -1)) == 0, "post runtime audit count drifted")

        run_checked(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_TERM))
        post_term = load(POST_TERM)
        ptc = post_term.get("counts") or {}
        req(int(ptc.get("runtime_risk", -1)) == 0, "post terminator runtime risk")
        req(int(ptc.get("separator_nul_lost", -1)) == 0, "post separator NUL loss")
        req(int(ptc.get("current_still_expanded", -1)) == 0, "post expanded terminator remains")

        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--tbl", str(TBL), "--out", str(POST_TERMINOLOGY))
        post_terminology = load(POST_TERMINOLOGY)
        pt = post_terminology.get("counts") or {}
        req(post_terminology.get("status") == "clean", "post terminology status not clean")
        req(int(pt.get("active_source_hits", -1)) == 0 and int(pt.get("dictionary_hits", -1)) == 0 and int(pt.get("rendered_record_hits", -1)) == 0, "post terminology residual")

        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE))
        post_false = load(POST_FALSE)
        req(post_false.get("ok") is True and int(post_false.get("sites_found", -1)) == 0, "post false segmented pointer regression")

        width_proc = subprocess.run([
            sys.executable, str(ROOT / "tools/audit_dialogue_20cell_candidate.py"),
            "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV),
        ], cwd=ROOT, check=False)
        req(width_proc.returncode in {0, 1}, f"post width audit execution failed: {width_proc.returncode}")
        width = load(POST_WIDTH)
        wp = width.get("population") or {}
        offenders = {str(row.get("abs") or "").upper() for row in (width.get("offenders") or [])}
        by_scope = wp.get("by_scope") or {}
        req(int(wp.get("records", -1)) == 15405, "post width population drifted")
        req(int(wp.get("offender_records", -1)) == 2 and offenders == KNOWN_WIDTH_OFFENDERS, f"new post width offender set: {sorted(offenders)}")
        req(int(((by_scope.get("battle_voice") or {}).get("over_20_records", -1))) == 0, "battle voice width regression")
        req(int(((by_scope.get("id_indirect_ui") or {}).get("over_20_records", -1))) == 0, "ID/indirect width regression")

        marker = subprocess.run([sys.executable, str(ROOT / "tools/hangul_marker.py")], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip().upper()
        req(marker == "EC8D", f"Hangul marker drifted: {marker}")
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_regression_bundle_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_runtime_regression_bundle_user_verified",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup),
        "live_saveram": save_after,
        "source_candidate_before_cleanup": candidate_ident,
        "source_candidate_saveram_before_cleanup": candidate_save_ident,
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "pre_runtime_audit": ident(AUDIT),
        "post_runtime_audit": ident(POST_RUNTIME),
        "post_terminator_audit": ident(POST_TERM),
        "post_terminology_audit": ident(POST_TERMINOLOGY),
        "post_false_segptr_audit": ident(POST_FALSE),
        "post_width_audit": ident(POST_WIDTH),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "post_runtime_53_of_53": int((load(POST_RUNTIME).get("counts") or {}).get("checks", -1)) == 53 and int((load(POST_RUNTIME).get("counts") or {}).get("failures", -1)) == 0,
            "post_p2_runtime_risk_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_terminology_clean": load(POST_TERMINOLOGY).get("status") == "clean",
            "post_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "post_width_only_two_inherited_scenario_walkers": {str(row.get("abs") or "").upper() for row in (load(POST_WIDTH).get("offenders") or [])} == KNOWN_WIDTH_OFFENDERS,
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "promotion report checks failed")
    atomic_json(REPORT, report)

    cleanup = []
    reclaimed = 0
    for path in (CAND, CAND_SAVE, POST_WIDTH_CSV):
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            cleanup.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
