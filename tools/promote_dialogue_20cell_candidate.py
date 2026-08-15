#!/usr/bin/env python3
"""Promote the user-approved 20-cell dialogue candidate to main TIP.

ROM-only transaction: validate exact parent/candidate/final gate/user approval,
backup the parent, atomically replace the main ROM, rerun the 20-cell/P2/false
segmented-pointer gates against the promoted main, and roll back on failure.
Live SaveRAM is never replaced.
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
CAND = PATCH / "dialogue_20cell_candidate.wsc"
CAND_SAVE = ROOT / "sram/dialogue_20cell_candidate.sav"
FINAL = PATCH / "dialogue_20cell_final_status.json"
BUILD = PATCH / "dialogue_20cell_report.json"
APPROVAL = PATCH / "dialogue_20cell_user_validation.json"
POST_WIDTH = PATCH / "dialogue_20cell_postpromotion_width_audit.json"
POST_WIDTH_CSV = SCRIPT / "dialogue_20cell_postpromotion_width_offenders.csv"
POST_TERM = PATCH / "dialogue_20cell_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "dialogue_20cell_postpromotion_false_segptr.json"
REPORT = PATCH / "dialogue_20cell_promotion_report.json"

EXPECTED_PARENT = "bbd14e0792264787985462c14d75cc77af168b90efc45b3a01d58b9a1de3d1ec"
EXPECTED_CAND = "8e80bc7e722652b9c6b31282c272966ae92f9d3c82975344c577556bf5b9145a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
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


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    for p in (TIP, SAVE, CAND, CAND_SAVE, FINAL, BUILD, APPROVAL):
        req(p.is_file(), f"missing required artifact: {p}")
    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size wrong")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE, "candidate SaveRAM size wrong")

    final = load(FINAL)
    req(final.get("ok") is True and final.get("status") == "candidate_ready_for_runtime_validation", "final candidate gate not clean")
    req(str(((final.get("candidate") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "final gate candidate hash mismatch")
    fc = final.get("counts") or {}
    req(int(fc.get("target_records", -1)) == 7923, "target population drifted")
    req(int(fc.get("candidate_audit_offenders", -1)) == 0, "20-cell offenders remain")
    req(int(fc.get("candidate_audit_max_line_cells", 999)) <= 20, "candidate max line exceeds 20")

    build = load(BUILD)
    bc = build.get("counts") or {}
    req(build.get("ok") is True, "build report not clean")
    req(int(bc.get("target_records", -1)) == 7923, "build target population drifted")
    req(int(bc.get("terminator_changes", -1)) == 0, "terminator changes exist")
    req(int(bc.get("unexpected_diff_offsets", -1)) == 0, "unexpected diff offsets exist")
    req(int(bc.get("max_after_cells", 999)) <= 20, "build max line exceeds 20")

    approval = load(APPROVAL)
    req(approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("parent_main_sha256") or "").lower() == EXPECTED_PARENT, "approval parent mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    candidate_bytes = CAND.read_bytes()
    req(checksum_ok(candidate_bytes), "candidate WonderSwan checksum invalid")
    save_before = ident(SAVE)
    cand_ident = ident(CAND)
    cand_save_ident = ident(CAND_SAVE)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_dialogue_20cell"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copyfile(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.dialogue20.{os.getpid()}.tmp")
    shutil.copyfile(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(TIP.read_bytes() == candidate_bytes, "promoted TIP differs from candidate")
        req(checksum_ok(TIP.read_bytes()), "promoted checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        subprocess.run([
            sys.executable, str(ROOT / "tools/audit_dialogue_20cell_candidate.py"),
            "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV),
        ], cwd=ROOT, check=True)
        width = load(POST_WIDTH)
        wp = width.get("population") or {}
        req(width.get("ok") is True, "postpromotion 20-cell audit failed")
        req(str(((width.get("rom") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "post width hash mismatch")
        req(int(wp.get("records", -1)) == 15405, "post width population drifted")
        req(int(wp.get("offender_records", -1)) == 0, "post width offenders remain")
        req(int(wp.get("max_line_cells", 999)) <= 20, "post width max exceeds 20")

        subprocess.run([
            sys.executable, str(ROOT / "tools/audit_p2_local_terminator_moves.py"),
            "--target", str(TIP), "--out", str(POST_TERM),
        ], cwd=ROOT, check=True)
        term = load(POST_TERM)
        tc = term.get("counts") or {}
        req(int(tc.get("current_still_expanded", -1)) == 0, "post P2 expanded terminators remain")
        req(int(tc.get("separator_nul_lost", -1)) == 0, "post P2 separator NUL loss remains")
        req(int(tc.get("runtime_risk", -1)) == 0, "post P2 runtime risk remains")

        subprocess.run([
            sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE),
        ], cwd=ROOT, check=True)
        false = load(POST_FALSE)
        req(false.get("ok") is True and int(false.get("sites_found", -1)) == 0, "post false-segptr failed")
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copyfile(backup, rollback)
        os.replace(rollback, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    report = {
        "schema_version": 1,
        "ok": True,
        "promoted": True,
        "status": "promoted_dialogue_20cell",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup),
        "live_saveram": save_after,
        "source_candidate_before_cleanup": cand_ident,
        "source_candidate_saveram_before_cleanup": cand_save_ident,
        "user_validation": ident(APPROVAL),
        "final_candidate_gate": ident(FINAL),
        "build_report": ident(BUILD),
        "post_width_audit": ident(POST_WIDTH),
        "post_terminator_audit": ident(POST_TERM),
        "post_false_segptr": ident(POST_FALSE),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "post_width_offenders_zero": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_width_max_le_20": int((load(POST_WIDTH).get("population") or {}).get("max_line_cells", 999)) <= 20,
            "post_p2_runtime_risk_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "postpromotion checks failed")
    atomic_json(REPORT, report)

    cleanup = []
    reclaimed = 0
    for p in (CAND, CAND_SAVE):
        if p.exists():
            size = p.stat().st_size
            p.unlink()
            cleanup.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
