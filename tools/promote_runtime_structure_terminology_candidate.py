#!/usr/bin/env python3
"""Promote the user-validated runtime structure / terminology candidate to main TIP.

ROM-only transaction.  The current live SaveRAM is preserved byte-exactly.
The script validates the candidate chain and pre-promotion audits, creates a
verified rollback backup, atomically replaces the main TIP, reruns the standard
regression gates against the promoted main, verifies the screen-bound structural
targets directly, and rolls back automatically on any post-promotion failure.
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
CAND = PATCH / "runtime_structure_terminology_candidate.wsc"
CAND_SAVE = ROOT / "sram/runtime_structure_terminology_candidate.sav"
BUILD = PATCH / "runtime_structure_terminology_candidate_report.json"
CAND_AUDIT = PATCH / "runtime_structure_terminology_candidate_audit.json"
APPROVAL = PATCH / "runtime_structure_terminology_user_validation.json"
PRE_TERM = PATCH / "runtime_structure_terminology_standard_audit.json"
PRE_WIDTH = PATCH / "runtime_structure_terminology_20cell_audit.json"
PRE_LEADS = PATCH / "runtime_structure_terminology_false_lead_audit.json"
PRE_P2 = PATCH / "runtime_structure_terminology_terminator_audit.json"
PRE_FALSE = PATCH / "runtime_structure_terminology_false_segptr.json"
PRE_RUNTIME = PATCH / "runtime_structure_terminology_runtime_regression_audit.json"

POST_TERM = PATCH / "runtime_structure_terminology_postpromotion_standard_audit.json"
POST_WIDTH = PATCH / "runtime_structure_terminology_postpromotion_20cell_audit.json"
POST_WIDTH_CSV = SCRIPT / "runtime_structure_terminology_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "runtime_structure_terminology_postpromotion_false_lead_audit.json"
POST_P2 = PATCH / "runtime_structure_terminology_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "runtime_structure_terminology_postpromotion_false_segptr.json"
POST_RUNTIME = PATCH / "runtime_structure_terminology_postpromotion_runtime_regression_audit.json"
PROMOTION = PATCH / "runtime_structure_terminology_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "f4f0ee2c0546e0794dae262b6246a190525763b6174d3423bec3ca20d8d2f212"
EXPECTED_CAND = "6136fe7294f186952cfb1366bb4a38179484f4d86fe6f85af23beb3cb35e0ae0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_WIDTH_RECORDS = 24_047
EXPECTED_WIDTH_LINES = 24_459
EXPECTED_GUARDED_LEADS = 340
EXPECTED_RUNTIME_TARGETS = 245
EXPECTED_BANK5F = 75


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


def req(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


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


def validate_term(doc: dict[str, Any], expected_sha: str) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("status") == "clean", "terminology audit not clean")
    req(str((doc.get("tip") or {}).get("sha256") or "").lower() == expected_sha, "terminology SHA mismatch")
    req(int(counts.get("active_source_hits", -1)) == 0, "active-source terminology residual")
    req(int(counts.get("dictionary_hits", -1)) == 0, "dictionary terminology residual")
    req(int(counts.get("rendered_record_hits", -1)) == 0, "rendered terminology residual")


def validate_width(doc: dict[str, Any], expected_sha: str) -> None:
    pop = doc.get("population") or {}
    req(doc.get("ok") is True and doc.get("width_ok") is True, "20-cell audit failed")
    req(str((doc.get("rom") or {}).get("sha256") or "").lower() == expected_sha, "20-cell SHA mismatch")
    req(int(pop.get("records", -1)) == EXPECTED_WIDTH_RECORDS, "20-cell record population drifted")
    req(int(pop.get("lines", -1)) == EXPECTED_WIDTH_LINES, "20-cell line population drifted")
    req(int(pop.get("offender_records", -1)) == 0, "20-cell offenders remain")
    req(int(pop.get("max_line_cells", -1)) <= 20, "20-cell maximum drifted")


def validate_leads(doc: dict[str, Any], expected_sha: str) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "visible-lead audit failed")
    req(str((doc.get("target") or {}).get("sha256") or "").lower() == expected_sha, "visible-lead SHA mismatch")
    req(int(counts.get("total_guarded_leads", -1)) == EXPECTED_GUARDED_LEADS, "guarded lead population drifted")
    req(int(counts.get("reintroduced", -1)) == 0, "visible lead reintroduced")


def validate_p2(doc: dict[str, Any]) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True, "P2 terminator audit failed")
    req(int(counts.get("current_still_expanded", -1)) == 0, "expanded P2 terminator remains")
    req(int(counts.get("separator_nul_lost", -1)) == 0, "P2 separator NUL lost")
    req(int(counts.get("runtime_risk", -1)) == 0, "P2 runtime risk remains")


def validate_false(doc: dict[str, Any], expected_sha: str) -> None:
    req(doc.get("ok") is True and int(doc.get("sites_found", -1)) == 0, "false segmented-pointer audit failed")
    target = (((doc.get("inputs") or {}).get("target") or {}).get("sha256") or "")
    req(str(target).lower() == expected_sha, "false-segptr SHA mismatch")


def validate_runtime(doc: dict[str, Any], expected_sha: str) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "runtime regression audit failed")
    req(str((doc.get("target") or {}).get("sha256") or "").lower() == expected_sha, "runtime regression SHA mismatch")
    req(int(counts.get("targets", -1)) == EXPECTED_RUNTIME_TARGETS, "runtime target population drifted")
    req(int(counts.get("failures", -1)) == 0, "runtime regression failure")
    req(int(counts.get("bank5f_discovered_active", -1)) == EXPECTED_BANK5F, "bank5F discovered population drift")
    req(int(counts.get("bank5f_canonical", -1)) == EXPECTED_BANK5F, "bank5F canonical population drift")
    req(doc.get("bank5f_coverage_ok") is True, "bank5F coverage failed")


def direct_runtime_proof(path: Path) -> dict[str, Any]:
    rom = path.read_bytes()
    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))

    def payload(logical: int, max_len: int = 128) -> tuple[bytes, int]:
        got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
        req(got is not None, f"unreadable zstring {logical:06X}")
        body, term = got
        return bytes(body), int(term - sb)

    exact_payloads = {
        0x61E23D: ("18F2B801010101010101010101", 0x61E24A),
        0x61E24B: ("F2C5010101010101010101010101", 0x61E259),
        0x59971D: ("173418F2B701010101010101010101010101010101", 0x599732),
        0x5D5982: ("E518433201010101010101010101010101010101", 0x5D5996),
        0x5D5B1F: ("E518433201010101010101010101010101010101", 0x5D5B33),
    }
    exact_rows: dict[str, Any] = {}
    for logical, (expected_hex, expected_term) in exact_payloads.items():
        raw, term = payload(logical)
        req(raw.hex().upper() == expected_hex and term == expected_term, f"runtime structure drift at {logical:06X}")
        exact_rows[f"{logical:06X}"] = {"payload": raw.hex().upper(), "terminator": f"{term:06X}"}

    phrase_expect = {
        0x032D1: "킹・오브・하트의　이름을　걸고！！",
        0x0189D: "대차병！！",
        0x0FEFB: "십이왕방패대차병",
        0x102BD: "십이왕방패！　대차병！！",
    }
    phrases = {}
    for index, expected in phrase_expect.items():
        actual = dictionary.expand_index(index, tbl).rstrip("　 \t")
        req(actual == expected, f"phrase drift {index:05X}: {actual!r}")
        phrases[f"{index:05X}"] = actual

    ple_residuals = []
    for index in list(range(int(dictionary.count))) + list(range(0x1000, 0x1000 + int(dictionary.ext3_count))):
        try:
            text = dictionary.expand_index(index, tbl).rstrip("　 \t")
        except Exception:
            continue
        if "풀투" in text:
            ple_residuals.append({"index": f"{index:05X}", "text": text})
    req(not ple_residuals, f"풀투 dictionary residuals remain: {ple_residuals[:5]}")

    weapon_expected = {
        0x75C9E6: ("금봉형　빔　라이플", 0),
        0x75CA18: ("십이왕방패대차병", 1),
        0x75C3C7: ("대형　미사일　런처", 1),
        0x75CB03: ("트리플　메가소닉　포", 0),
    }
    weapons = {}
    for logical, (expected, max_pad) in weapon_expected.items():
        raw, term = payload(logical)
        text = dictionary.expand(raw, tbl)
        trimmed = text.rstrip("　 \t")
        trailing = len(text) - len(trimmed)
        req(trimmed == expected and trailing <= max_pad, f"weapon display drift at {logical:06X}: {text!r}")
        weapons[f"{logical:06X}"] = {"rendered": trimmed, "trailing_visible_pad": trailing, "terminator": f"{term:06X}"}

    return {
        "exact_runtime_records": exact_rows,
        "semantic_phrases": phrases,
        "ple_two_residuals": ple_residuals,
        "weapon_examples": weapons,
    }


def main() -> int:
    required = (TIP, SAVE, CAND, CAND_SAVE, BUILD, CAND_AUDIT, APPROVAL, PRE_TERM, PRE_WIDTH, PRE_LEADS, PRE_P2, PRE_FALSE, PRE_RUNTIME)
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM missing/wrong size")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE, "candidate SaveRAM missing/wrong size")
    req(checksum_ok(CAND), "candidate WonderSwan checksum invalid")

    build = load(BUILD)
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req((build.get("verification") or {}).get("unaccounted_diff_runs", 0) == 0, "build has unaccounted diff runs")

    cand_audit = load(CAND_AUDIT)
    req(cand_audit.get("ok") is True and not cand_audit.get("failures"), "candidate dedicated audit failed")
    req(str((cand_audit.get("target") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "candidate audit SHA mismatch")
    req(len(cand_audit.get("checks") or []) == 17, "candidate audit check population drifted")
    req(all(bool(row.get("ok")) for row in cand_audit.get("checks") or []), "candidate dedicated check failed")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main SHA mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate SHA mismatch")

    validate_term(load(PRE_TERM), EXPECTED_CAND)
    validate_width(load(PRE_WIDTH), EXPECTED_CAND)
    validate_leads(load(PRE_LEADS), EXPECTED_CAND)
    validate_p2(load(PRE_P2))
    validate_false(load(PRE_FALSE), EXPECTED_CAND)
    validate_runtime(load(PRE_RUNTIME), EXPECTED_CAND)
    pre_direct = direct_runtime_proof(CAND)

    save_before = ident(SAVE)
    candidate_id = ident(CAND)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_runtime_structure_terminology"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_structure_terminology_candidate.py",
        "reason": "pre_runtime_structure_terminology",
        "main_tip": ident(backup),
        "candidate_sha256": EXPECTED_CAND,
        "user_validation": ident(APPROVAL),
    })

    staged = TIP.with_name(f".{TIP.name}.runtime_structure_terminology.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP), "promoted TIP checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")
        post_direct = direct_runtime_proof(TIP)
        req(post_direct == pre_direct, "promoted direct runtime proof differs from candidate")

        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--out", str(POST_TERM))
        validate_term(load(POST_TERM), EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV))
        validate_width(load(POST_WIDTH), EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_battle_false_lead_recurrence.py"), "--target", str(TIP), "--out", str(POST_LEADS))
        validate_leads(load(POST_LEADS), EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_P2))
        validate_p2(load(POST_P2))
        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE))
        validate_false(load(POST_FALSE), EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_runtime_dialogue_regression_followup.py"), "--target", str(TIP), "--out", str(POST_RUNTIME))
        validate_runtime(load(POST_RUNTIME), EXPECTED_CAND)
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    tip_after = ident(TIP)
    save_after = ident(SAVE)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_structure_terminology_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_runtime_verified_runtime_structure_terminology",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": tip_after,
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": save_after,
        "source_candidate_before_cleanup": candidate_id,
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "candidate_audit": ident(CAND_AUDIT),
        "direct_runtime_targets": post_direct,
        "post_terminology_audit": ident(POST_TERM),
        "post_20cell_audit": ident(POST_WIDTH),
        "post_visible_lead_audit": ident(POST_LEADS),
        "post_terminator_audit": ident(POST_P2),
        "post_false_segptr_audit": ident(POST_FALSE),
        "post_runtime_regression_audit": ident(POST_RUNTIME),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": tip_after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "terminology_clean": load(POST_TERM).get("status") == "clean",
            "post_20cell_zero": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_visible_lead_zero": int((load(POST_LEADS).get("counts") or {}).get("reintroduced", -1)) == 0,
            "post_guarded_leads_340": int((load(POST_LEADS).get("counts") or {}).get("total_guarded_leads", -1)) == EXPECTED_GUARDED_LEADS,
            "post_p2_risk_zero": int((load(POST_P2).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_false_segptr_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "runtime_dialogue_245_exact": int((load(POST_RUNTIME).get("counts") or {}).get("targets", -1)) == EXPECTED_RUNTIME_TARGETS,
            "bank5f_75_exact": int((load(POST_RUNTIME).get("counts") or {}).get("bank5f_canonical", -1)) == EXPECTED_BANK5F,
            "ple_two_residual_zero": not post_direct["ple_two_residuals"],
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "promotion report checks failed")
    atomic_json(PROMOTION, report)

    cleanup_paths = (
        CAND,
        CAND_SAVE,
        ROOT / "sram/runtime_structure_terminology_candidate.sav",
        SCRIPT / "runtime_structure_terminology_20cell_offenders.csv",
        POST_WIDTH_CSV,
    )
    cleanup: list[dict[str, Any]] = []
    reclaimed = 0
    for path in cleanup_paths:
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            cleanup.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(PROMOTION, report)

    print(json.dumps({
        "ok": True,
        "main_sha256": tip_after["sha256"],
        "checksum": report["checksum"],
        "save_unchanged": report["checks"]["live_saveram_unchanged"],
        "guarded_leads": (load(POST_LEADS).get("counts") or {}).get("total_guarded_leads"),
        "runtime_dialogue_targets": (load(POST_RUNTIME).get("counts") or {}).get("targets"),
        "bank5f": (load(POST_RUNTIME).get("counts") or {}).get("bank5f_canonical"),
        "backup": report["backup"]["path"],
        "cleanup_reclaimed_bytes": reclaimed,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
