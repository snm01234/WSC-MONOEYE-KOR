#!/usr/bin/env python3
"""Promote the user-approved full P2 terminator repair candidate to main TIP.

ROM-only transaction: verify parent/candidate, backup current main, atomically
replace the ROM, rerun structural/false-segptr gates, verify live SaveRAM is
unchanged, then clean the candidate ROM/SAV.  Any postpromotion failure rolls
back the ROM from the verified backup.
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
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "p2_terminator_full_repair_candidate.wsc"
CAND_SAVE = ROOT / "sram/p2_terminator_full_repair_candidate.sav"
BUILD = PATCH / "p2_terminator_full_repair_report.json"
PRE_TERM = PATCH / "p2_terminator_full_repair_terminator_audit.json"
PRE_FALSE = PATCH / "p2_terminator_full_repair_false_segptr.json"
APPROVAL = PATCH / "p2_terminator_full_repair_user_validation.json"
POST_TERM = PATCH / "p2_terminator_full_repair_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "p2_terminator_full_repair_postpromotion_false_segptr.json"
POST_AUDIT = PATCH / "p2_terminator_full_repair_postpromotion_audit.json"
REPORT = PATCH / "p2_terminator_full_repair_promotion_report.json"

EXPECTED_PARENT = "f9183b7835717ecff033d483bd220f99facc3b7e40a9fb32d5649584b0569145"
EXPECTED_CAND = "59dd896c6bf415c24f12b179beb5fa2794ec1c80c8de0591dfc5579047e01375"
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


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    for p in (TIP, SAVE, CAND, CAND_SAVE, BUILD, PRE_TERM, PRE_FALSE, APPROVAL):
        req(p.is_file(), f"missing required file: {p}")
    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drifted")
    req(CAND_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM differs from live SaveRAM")

    build = load(BUILD)
    counts = build.get("counts") or {}
    req(build.get("ok") is True, "build report not clean")
    req(int(counts.get("repaired_remaining_records", -1)) == 26, "repair count drifted")
    req(int(counts.get("runtime_risk_after", -1)) == 0, "build report still has terminator risk")
    req(str(((build.get("outputs") or {}).get("rom_sha256") or "")).lower() == EXPECTED_CAND, "build candidate hash mismatch")
    preserved = build.get("preserved") or {}
    req(preserved.get("sig_611df0_parent_exact") is True, "Sig preservation failed")
    req(all(x.get("ok") is True for x in preserved.get("cannon_4") or []) and len(preserved.get("cannon_4") or []) == 4, "cannon preservation failed")

    term = load(PRE_TERM)
    tc = term.get("counts") or {}
    req(int(tc.get("approved_plus1_moves", -1)) == 27, "terminator audit population drifted")
    req(int(tc.get("current_still_expanded", -1)) == 0, "expanded terminators remain")
    req(int(tc.get("separator_nul_lost", -1)) == 0, "separator NUL loss remains")
    req(int(tc.get("runtime_risk", -1)) == 0, "terminator runtime risk remains")

    ff = load(PRE_FALSE)
    req(ff.get("ok") is True and int(ff.get("sites_found", -1)) == 0, "prepromotion false-segptr failed")
    req(str((((ff.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "false-segptr candidate binding mismatch")

    approval = load(APPROVAL)
    req(approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("parent_main_sha256") or "").lower() == EXPECTED_PARENT, "approval parent mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    cand_bytes = CAND.read_bytes()
    req(checksum_ok(cand_bytes), "candidate WonderSwan checksum invalid")
    save_before = ident(SAVE)
    cand_ident = ident(CAND)
    cand_save_ident = ident(CAND_SAVE)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_p2_terminator_full_repair"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copyfile(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.p2term.{os.getpid()}.tmp")
    shutil.copyfile(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(sha(TIP) == EXPECTED_CAND and TIP.read_bytes() == cand_bytes, "promoted TIP differs from candidate")
        req(checksum_ok(TIP.read_bytes()), "promoted checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        subprocess.run([sys.executable, str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_TERM)], cwd=ROOT, check=True)
        pt = load(POST_TERM)
        pc = pt.get("counts") or {}
        req(int(pc.get("current_still_expanded", -1)) == 0 and int(pc.get("separator_nul_lost", -1)) == 0 and int(pc.get("runtime_risk", -1)) == 0, "postpromotion terminator audit failed")

        subprocess.run([sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE)], cwd=ROOT, check=True)
        pf = load(POST_FALSE)
        req(pf.get("ok") is True and int(pf.get("sites_found", -1)) == 0, "postpromotion false-segptr failed")
    except Exception:
        rb = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copyfile(backup, rb)
        os.replace(rb, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    post = {
        "ok": True,
        "generated_by": "tools/promote_p2_terminator_full_repair_candidate.py",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "checksum_exact": checksum_ok(TIP.read_bytes()),
            "live_saveram_unchanged": save_after == save_before,
            "all_27_terminator_moves_restored": int((load(POST_TERM).get("counts") or {}).get("current_still_expanded", -1)) == 0,
            "separator_nul_loss_zero": int((load(POST_TERM).get("counts") or {}).get("separator_nul_lost", -1)) == 0,
            "terminator_runtime_risk_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "sig_and_cannon_preserved_by_build": True,
        },
        "backup": ident(backup),
        "after": after,
        "live_saveram": save_after,
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "post_terminator_audit": ident(POST_TERM),
        "post_false_segptr": ident(POST_FALSE),
    }
    req(all(post["checks"].values()), "postpromotion audit failed")
    atomic_json(POST_AUDIT, post)

    report = {
        "ok": True,
        "promoted": True,
        "status": "promoted_full_p2_terminator_repair_all_27_clean",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup),
        "live_saveram": save_after,
        "source_candidate_before_cleanup": cand_ident,
        "source_candidate_saveram_before_cleanup": cand_save_ident,
        "build_report": ident(BUILD),
        "user_validation": ident(APPROVAL),
        "prepromotion_terminator_audit": ident(PRE_TERM),
        "prepromotion_false_segptr": ident(PRE_FALSE),
        "postpromotion_audit": ident(POST_AUDIT),
        "postpromotion_terminator_audit": ident(POST_TERM),
        "postpromotion_false_segptr": ident(POST_FALSE),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
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
