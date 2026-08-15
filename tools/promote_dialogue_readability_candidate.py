#!/usr/bin/env python3
"""Promote the user-approved dialogue readability candidate to the main TIP.

ROM-only transaction. The live SaveRAM is preserved. The script validates the
exact parent/candidate/final gate/user approval, creates a verified rollback
backup, atomically replaces the main ROM, reruns independent post-promotion
runtime-structure audits, rolls back on any failure, records the transaction,
and removes the promoted candidate ROM/SAV pair after success.
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
CAND = PATCH / "dialogue_readability_candidate.wsc"
CAND_SAVE = ROOT / "sram/dialogue_readability_candidate.sav"
BUILD = PATCH / "dialogue_readability_report.json"
FINAL = PATCH / "dialogue_readability_final_status.json"
APPROVAL = PATCH / "dialogue_readability_user_validation.json"
POST_WIDTH = PATCH / "dialogue_readability_postpromotion_width_audit.json"
POST_WIDTH_CSV = SCRIPT / "dialogue_readability_postpromotion_width_offenders.csv"
POST_SINGLETON = PATCH / "dialogue_readability_postpromotion_singleton_audit.json"
POST_FALSE_LEAD = PATCH / "dialogue_readability_postpromotion_false_lead_audit.json"
POST_TERM = PATCH / "dialogue_readability_postpromotion_terminator_audit.json"
POST_COLL = PATCH / "dialogue_readability_postpromotion_speaker_collision_audit.json"
POST_FALSE_SEGPTR = PATCH / "dialogue_readability_postpromotion_false_segptr_audit.json"
REPORT = PATCH / "dialogue_readability_promotion_report.json"

EXPECTED_PARENT = "8287c930a2193d5842783a5f49167aa77550e16139bdc76674c61e2602f2cff1"
EXPECTED_CAND = "be5cdb102a589faecd487780b99d3c30dd358e938e66cdb5aeb76ebcc8f4959c"
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


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> int:
    for path in (TIP, SAVE, CAND, CAND_SAVE, BUILD, FINAL, APPROVAL):
        req(path.is_file(), f"missing required artifact: {path}")
    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size wrong")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE, "candidate SaveRAM size wrong")

    build = load(BUILD)
    bc = build.get("counts") or {}
    req(build.get("ok") is True, "build report not clean")
    req(str(((build.get("parent") or {}).get("sha256") or "")).lower() == EXPECTED_PARENT, "build parent mismatch")
    req(str(((build.get("candidate") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "build candidate mismatch")
    req(int(bc.get("targets", -1)) == 1979, "combined target population drifted")
    req(int(bc.get("two_row_readability_records", -1)) == 1412, "two-row target population drifted")
    req(int(bc.get("singleton_source_rewrite_records", -1)) == 567, "singleton target population drifted")
    req(int(bc.get("false_lead_cleanup_records", -1)) == 264, "false-lead target population drifted")
    req(int(bc.get("compact3_records", -1)) == 0, "compact3 unexpectedly used")
    req(int(bc.get("terminator_changes", -1)) == 0, "terminator changes exist")
    req(int(bc.get("prefix_changes", -1)) == 0, "prefix changes exist")
    req(int(bc.get("record_extent_changes", -1)) == 0, "record extent changes exist")
    req(int(bc.get("unexpected_diff_offsets", -1)) == 0, "unexpected diff offsets exist")
    req(int(bc.get("max_after_cells", 999)) <= 20, "build width exceeds 20")

    final = load(FINAL)
    fc = final.get("counts") or {}
    req(final.get("ok") is True and final.get("status") == "candidate_ready_for_runtime_validation", "final candidate gate not clean")
    req(str(((final.get("main_unchanged") or {}).get("sha256") or "")).lower() == EXPECTED_PARENT, "final parent mismatch")
    req(str(((final.get("candidate") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "final candidate mismatch")
    req(int(fc.get("runtime_width_records", -1)) == 15405, "final width population drifted")
    req(int(fc.get("runtime_width_offenders", -1)) == 0, "final width offenders remain")
    req(int(fc.get("singleton_dense_no_spacing_17plus", -1)) == 0, "dense singleton remains")
    req(int(fc.get("singleton_max_word_cells", 999)) <= 20, "singleton word exceeds 20")
    req(int(fc.get("false_lead_reintroduced", -1)) == 0, "false lead reintroduced")
    req(int(fc.get("terminator_runtime_risk", -1)) == 0, "terminator runtime risk remains")
    req(int(fc.get("speaker_hidden_japanese", -1)) == 0, "speaker hidden Japanese remains")
    req(int(fc.get("false_segmented_pointer_sites", -1)) == 0, "false segmented pointer remains")

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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_dialogue_readability"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copyfile(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.readability.{os.getpid()}.tmp")
    shutil.copyfile(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(TIP.read_bytes() == candidate_bytes, "promoted TIP differs from candidate")
        req(checksum_ok(TIP.read_bytes()), "promoted checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        run(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV))
        width = load(POST_WIDTH)
        wp = width.get("population") or {}
        req(width.get("ok") is True, "post width audit failed")
        req(str(((width.get("rom") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "post width hash mismatch")
        req(int(wp.get("records", -1)) == 15405, "post width population drifted")
        req(int(wp.get("offender_records", -1)) == 0 and int(wp.get("max_line_cells", 999)) <= 20, "post width regression")

        run(str(ROOT / "tools/audit_dialogue_singleton_rewrites.py"), "--rom", str(TIP), "--out", str(POST_SINGLETON))
        singleton = load(POST_SINGLETON)
        sc = singleton.get("counts") or {}
        req(singleton.get("ok") is True, "post singleton audit failed")
        req(int(sc.get("decoded", -1)) == 567 and int(sc.get("failures", -1)) == 0, "post singleton coverage/render regression")
        req(int(sc.get("over_20", -1)) == 0 and int(sc.get("dense_no_spacing_17plus", -1)) == 0, "post singleton spacing regression")
        req(int(sc.get("max_word_cells", 999)) <= 20, "post singleton word-width regression")

        run(str(ROOT / "tools/audit_battle_false_lead_recurrence.py"), "--target", str(TIP), "--out", str(POST_FALSE_LEAD))
        false_lead = load(POST_FALSE_LEAD)
        flc = false_lead.get("counts") or {}
        req(false_lead.get("ok") is True and int(flc.get("reintroduced", -1)) == 0, "post false-lead regression")

        run(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_TERM))
        term = load(POST_TERM)
        tc = term.get("counts") or {}
        req(int(tc.get("current_still_expanded", -1)) == 0, "post expanded terminators remain")
        req(int(tc.get("separator_nul_lost", -1)) == 0, "post separator NUL loss remains")
        req(int(tc.get("runtime_risk", -1)) == 0, "post terminator runtime risk remains")

        run(str(ROOT / "tools/audit_speaker_dictlead_nul_collisions.py"), "--target", str(TIP), "--out", str(POST_COLL))
        coll = load(POST_COLL)
        cc = coll.get("counts") or {}
        req(coll.get("ok") is True, "post speaker collision audit failed")
        req(int(cc.get("japanese_or_mixed_remaining", -1)) == 0, "post hidden Japanese remains")
        req(int(cc.get("over_20", -1)) == 0, "post speaker collision width regression")

        run(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE_SEGPTR))
        seg = load(POST_FALSE_SEGPTR)
        req(seg.get("ok") is True and int(seg.get("sites_found", -1)) == 0, "post false segmented-pointer regression")

        promoted = TIP.read_bytes()
        sb = len(promoted) - 0x800000
        req(promoted[sb + 0x5D01F4:sb + 0x5D01F4 + 2] == b"\xE5\x18", "post 5D01F4 false lead regression")
        req(promoted[sb + 0x5D7084:sb + 0x5D7084 + 3] == bytes.fromhex("35E518"), "post 5D7084 portrait metadata regression")
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
        "status": "promoted_dialogue_readability",
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
        "post_singleton_audit": ident(POST_SINGLETON),
        "post_false_lead_audit": ident(POST_FALSE_LEAD),
        "post_terminator_audit": ident(POST_TERM),
        "post_speaker_collision_audit": ident(POST_COLL),
        "post_false_segptr_audit": ident(POST_FALSE_SEGPTR),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "post_width_offenders_zero": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_width_max_le_20": int((load(POST_WIDTH).get("population") or {}).get("max_line_cells", 999)) <= 20,
            "post_singleton_failures_zero": int((load(POST_SINGLETON).get("counts") or {}).get("failures", -1)) == 0,
            "post_singleton_dense_zero": int((load(POST_SINGLETON).get("counts") or {}).get("dense_no_spacing_17plus", -1)) == 0,
            "post_false_lead_reintroduced_zero": int((load(POST_FALSE_LEAD).get("counts") or {}).get("reintroduced", -1)) == 0,
            "post_p2_runtime_risk_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_speaker_hidden_japanese_zero": int((load(POST_COLL).get("counts") or {}).get("japanese_or_mixed_remaining", -1)) == 0,
            "post_false_segmented_pointer_zero": int(load(POST_FALSE_SEGPTR).get("sites_found", -1)) == 0,
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "postpromotion checks failed")
    atomic_json(REPORT, report)

    cleanup = []
    reclaimed = 0
    for path in (CAND, CAND_SAVE):
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
