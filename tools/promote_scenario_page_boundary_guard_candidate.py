#!/usr/bin/env python3
"""Promote the user-validated scenario page-boundary guard candidate.

ROM-only transaction.  The live SaveRAM is preserved byte-exactly.  The
promotion is bound to the exact marker-corrected candidate SHA and requires the
63-risk build report, pre-promotion regression reports, and explicit user
runtime approval.  A verified timestamped rollback copy is created before the
atomic TIP replacement; every standard regression gate is then rerun on main.
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "scenario_page_boundary_guard_candidate.wsc"
CAND_SAVE = ROOT / "sram/scenario_page_boundary_guard_candidate.sav"
BUILD = PATCH / "scenario_page_boundary_guard_candidate_report.json"
APPROVAL = PATCH / "scenario_page_boundary_guard_user_validation.json"
PRE_WIDTH = PATCH / "scenario_page_boundary_guard_candidate_20cell.json"
PRE_LEADS = PATCH / "scenario_page_boundary_guard_candidate_false_lead.json"
PRE_P2 = PATCH / "scenario_page_boundary_guard_candidate_p2.json"
PRE_FALSE = PATCH / "scenario_page_boundary_guard_candidate_false_segptr.json"
PRE_RUNTIME = PATCH / "scenario_page_boundary_guard_candidate_runtime_dialogue.json"
PRE_TERM = PATCH / "scenario_page_boundary_guard_candidate_terminology.json"

POST_WIDTH = PATCH / "scenario_page_boundary_guard_postpromotion_20cell.json"
POST_WIDTH_CSV = SCRIPT / "scenario_page_boundary_guard_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "scenario_page_boundary_guard_postpromotion_false_lead.json"
POST_P2 = PATCH / "scenario_page_boundary_guard_postpromotion_p2.json"
POST_FALSE = PATCH / "scenario_page_boundary_guard_postpromotion_false_segptr.json"
POST_RUNTIME = PATCH / "scenario_page_boundary_guard_postpromotion_runtime_dialogue.json"
POST_TERM = PATCH / "scenario_page_boundary_guard_postpromotion_terminology.json"
PROMOTION = PATCH / "scenario_page_boundary_guard_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "6136fe7294f186952cfb1366bb4a38179484f4d86fe6f85af23beb3cb35e0ae0"
EXPECTED_CAND = "35c56e0f8d1aaec9b4687490ddc7b9e999f100ce2987666612931178d0ca44c2"
EXPECTED_SAVE = "8954611a8870bc5456accbeed0bb525ca2372bd5425ec274a75baf34d3bd5a01"
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


def run_checked(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def validate_reports(width: Path, leads: Path, p2: Path, false: Path, runtime: Path, term: Path, expected_sha: str) -> dict[str, Any]:
    w = load(width)
    wp = w.get("population") or {}
    req(w.get("ok") is True and w.get("width_ok") is True, "20-cell audit failed")
    req(str((w.get("rom") or {}).get("sha256") or "").lower() == expected_sha, "20-cell SHA mismatch")
    req(int(wp.get("records", -1)) == 24047 and int(wp.get("offender_records", -1)) == 0, "20-cell population/offender drift")
    req(int(wp.get("max_line_cells", -1)) <= 20, "20-cell maximum drift")

    l = load(leads)
    lc = l.get("counts") or {}
    req(l.get("ok") is True and not l.get("failures"), "visible-lead audit failed")
    req(str((l.get("target") or {}).get("sha256") or "").lower() == expected_sha, "visible-lead SHA mismatch")
    req(int(lc.get("total_guarded_leads", -1)) == 340 and int(lc.get("reintroduced", -1)) == 0, "visible-lead regression")

    p = load(p2)
    pc = p.get("counts") or {}
    req(p.get("ok") is True, "P2 terminator audit failed")
    req(int(pc.get("separator_nul_lost", -1)) == 0 and int(pc.get("runtime_risk", -1)) == 0, "P2 separator/runtime risk")

    f = load(false)
    req(f.get("ok") is True and int(f.get("sites_found", -1)) == 0, "false segmented-pointer audit failed")
    fsha = str((((f.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower()
    req(fsha == expected_sha, "false-segptr SHA mismatch")

    r = load(runtime)
    rc = r.get("counts") or {}
    req(r.get("ok") is True and not r.get("failures"), "runtime dialogue regression failed")
    req(str((r.get("target") or {}).get("sha256") or "").lower() == expected_sha, "runtime dialogue SHA mismatch")
    req(int(rc.get("targets", -1)) == 245 and int(rc.get("failures", -1)) == 0, "runtime target regression")
    req(int(rc.get("bank5f_canonical", -1)) == 75, "bank5F population drift")

    t = load(term)
    tc = t.get("counts") or {}
    req(t.get("status") == "clean", "terminology audit not clean")
    req(str((t.get("tip") or {}).get("sha256") or "").lower() == expected_sha, "terminology SHA mismatch")
    req(int(tc.get("active_source_hits", -1)) == 0 and int(tc.get("dictionary_hits", -1)) == 0 and int(tc.get("rendered_record_hits", -1)) == 0, "terminology residual")

    return {
        "width_records": int(wp["records"]),
        "width_offenders": int(wp["offender_records"]),
        "guarded_leads": int(lc["total_guarded_leads"]),
        "visible_lead_reintroduced": int(lc["reintroduced"]),
        "p2_runtime_risk": int(pc["runtime_risk"]),
        "false_segptr_sites": int(f["sites_found"]),
        "runtime_targets": int(rc["targets"]),
        "bank5f": int(rc["bank5f_canonical"]),
        "terminology_hits": [int(tc["active_source_hits"]), int(tc["dictionary_hits"]), int(tc["rendered_record_hits"])],
    }


def direct_page_proof(path: Path) -> dict[str, Any]:
    rom = path.read_bytes()
    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))

    got = read_encoded_z_safe(rom, sb + 0x61E234, max_len=64)
    req(got is not None, "61E234 unreadable")
    payload, term_file = got
    payload = bytes(payload)
    term = int(term_file) - sb
    req(payload.hex().upper() == "173418F184F191", f"61E234 payload drift: {payload.hex()}")
    req(term == 0x61E23B, f"61E234 terminator drift: {term:06X}")
    req(rom[sb + 0x61E23B] == 0 and rom[sb + 0x61E23C] == 0, "Garrod double-NUL boundary lost")
    req(rom[sb + 0x61E23D] == 0x18, "Garrod next-page 18 lead lost")

    raw184 = bytes(dictionary.raw_entry(0x0184))
    req(raw184.startswith(bytes.fromhex("EC8D")), "stock 0184 lacks EC8D Hangul run marker")
    req(dictionary.expand_index(0x0184, tbl) == "앞으로　어쩌냐니", "stock 0184 semantic drift")
    req(dictionary.expand_index(0x0191, tbl) == "……", "stock 0191 semantic drift")
    req(dictionary.expand_index(0x02B8, tbl) == "……음、　우선　티파를", "Garrod second-page first row drift")
    req(dictionary.expand_index(0x02C5, tbl) == "안전한　곳에　데려가야겠지？", "Garrod continuation drift")
    return {
        "61E234_payload": payload.hex().upper(),
        "61E234_terminator": f"{term:06X}",
        "double_nul": [f"{rom[sb + 0x61E23B]:02X}", f"{rom[sb + 0x61E23C]:02X}"],
        "slot_0184_raw": raw184.hex().upper(),
        "slot_0184": dictionary.expand_index(0x0184, tbl),
        "slot_0191": dictionary.expand_index(0x0191, tbl),
        "slot_02B8": dictionary.expand_index(0x02B8, tbl),
        "slot_02C5": dictionary.expand_index(0x02C5, tbl),
    }


def main() -> int:
    required = (TIP, SAVE, CAND, CAND_SAVE, BUILD, APPROVAL, PRE_WIDTH, PRE_LEADS, PRE_P2, PRE_FALSE, PRE_RUNTIME, PRE_TERM)
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE and sha(SAVE) == EXPECTED_SAVE, "live SaveRAM identity drifted")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE and sha(CAND_SAVE) == EXPECTED_SAVE, "candidate SaveRAM identity drifted")
    req(checksum_ok(CAND), "candidate checksum invalid")

    build = load(BUILD)
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    risk = build.get("risk_inventory") or {}
    req(int(risk.get("current_shape_total", -1)) == 63, "risk population drift")
    req(int(risk.get("original_exact_dict2_repaired", -1)) == 45, "exact repair population drift")
    req(int(risk.get("original_mixed_observe_only", -1)) == 18, "mixed observe population drift")
    req(int(risk.get("candidate_exact_dict2_residual", -1)) == 0, "exact dict2 residual remains")
    req(int(risk.get("candidate_mixed_residual", -1)) == 18, "mixed residual population drift")
    guards = build.get("guards") or {}
    boolean_guards = {
        key: value for key, value in guards.items()
        if key != "diff_outside_accounted_extents"
    }
    req(boolean_guards and all(value is True for value in boolean_guards.values()), "build boolean guard failed")
    req(int(guards.get("diff_outside_accounted_extents", -1)) == 0, "build has unaccounted diff extents")
    alloc = build.get("allocation") or {}
    req(int(alloc.get("selected_unreachable_slots", -1)) == 22 and int(alloc.get("pointer_table_changes", -1)) == 0, "allocation proof drift")
    req(all("EC8D" in str(row.get("encoded_hex", "")) or not any('가' <= ch <= '힣' for ch in str(row.get("fragment", ""))) for row in alloc.get("slots") or []), "Hangul stock fragment missing EC8D marker")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    pre_checks = validate_reports(PRE_WIDTH, PRE_LEADS, PRE_P2, PRE_FALSE, PRE_RUNTIME, PRE_TERM, EXPECTED_CAND)
    pre_direct = direct_page_proof(CAND)

    save_before = ident(SAVE)
    candidate_id = ident(CAND)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_scenario_page_boundary_guard"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "generated_by": "tools/promote_scenario_page_boundary_guard_candidate.py",
        "reason": "pre_scenario_page_boundary_guard",
        "main_tip": ident(backup),
        "candidate_sha256": EXPECTED_CAND,
        "user_validation": ident(APPROVAL),
    })

    staged = TIP.with_name(f".{TIP.name}.scenario_page_boundary_guard.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(sha(TIP) == EXPECTED_CAND and checksum_ok(TIP), "promoted TIP identity/checksum failure")
        req(ident(SAVE) == save_before, "live SaveRAM changed")
        post_direct = direct_page_proof(TIP)
        req(post_direct == pre_direct, "direct page proof changed after promotion")

        run_checked(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV))
        run_checked(str(ROOT / "tools/audit_battle_false_lead_recurrence.py"), "--target", str(TIP), "--out", str(POST_LEADS))
        run_checked(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_P2))
        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--out", str(POST_FALSE))
        run_checked(str(ROOT / "tools/audit_runtime_dialogue_regression_followup.py"), "--target", str(TIP), "--out", str(POST_RUNTIME))
        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--out", str(POST_TERM))
        post_checks = validate_reports(POST_WIDTH, POST_LEADS, POST_P2, POST_FALSE, POST_RUNTIME, POST_TERM, EXPECTED_CAND)
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_scenario_page_boundary_guard_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_runtime_verified_scenario_page_boundary_guard",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": ident(TIP),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": ident(SAVE),
        "source_candidate_before_cleanup": candidate_id,
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "direct_page_proof": post_direct,
        "pre_checks": pre_checks,
        "post_checks": post_checks,
        "risk_inventory": risk,
        "allocation": {
            "selected_unreachable_slots": alloc.get("selected_unreachable_slots"),
            "novel_fragments": alloc.get("novel_fragments"),
            "pointer_table_changes": alloc.get("pointer_table_changes"),
            "fragment_storage_bytes_including_nuls": alloc.get("fragment_storage_bytes_including_nuls"),
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(report["live_saveram_after"] == save_before, "SaveRAM post identity drift")
    atomic_json(PROMOTION, report)

    cleanup = []
    reclaimed = 0
    for path in (CAND, CAND_SAVE, SCRIPT / "scenario_page_boundary_guard_candidate_20cell_offenders.csv", POST_WIDTH_CSV):
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            cleanup.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(PROMOTION, report)

    print(json.dumps({
        "ok": True,
        "main_sha256": sha(TIP),
        "checksum": report["checksum"],
        "save_sha256": sha(SAVE),
        "backup": report["backup"]["path"],
        "risk_repaired": risk["original_exact_dict2_repaired"],
        "mixed_observe_only": risk["original_mixed_observe_only"],
        "post_checks": post_checks,
        "cleanup_reclaimed_bytes": reclaimed,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
