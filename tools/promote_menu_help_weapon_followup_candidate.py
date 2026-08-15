#!/usr/bin/env python3
"""Promote the user-validated menu-help/weapon follow-up candidate to main TIP.

ROM-only transaction. The live SaveRAM is preserved. This promotion validates
both candidate stages (menu-help/padding base + follow-up), creates a verified
rollback backup, atomically replaces the main TIP, reruns the standard static
regression gates, verifies all 134 menu-help translations plus the user-screen
follow-up targets, and rolls back automatically on any post-promotion failure.
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
BASE_CAND = PATCH / "menu_help_weapon_padding_candidate.wsc"
BASE_SAVE = ROOT / "sram/menu_help_weapon_padding_candidate.sav"
CAND = PATCH / "menu_help_weapon_followup_candidate.wsc"
CAND_SAVE = ROOT / "sram/menu_help_weapon_followup_candidate.sav"
BASE_REPORT = PATCH / "menu_help_weapon_padding_candidate_report.json"
BUILD_REPORT = PATCH / "menu_help_weapon_followup_report.json"
APPROVAL = PATCH / "menu_help_weapon_followup_user_validation.json"

PRE_TERM = PATCH / "menu_help_weapon_followup_terminology_audit.json"
PRE_WIDTH = PATCH / "menu_help_weapon_followup_20cell_audit.json"
PRE_LEADS = PATCH / "menu_help_weapon_followup_false_lead_audit.json"
PRE_P2 = PATCH / "menu_help_weapon_followup_terminator_audit.json"
PRE_FALSE = PATCH / "menu_help_weapon_followup_false_segptr.json"
PRE_RUNTIME = PATCH / "menu_help_weapon_followup_runtime_dialogue_audit.json"

POST_TERM = PATCH / "menu_help_weapon_followup_postpromotion_terminology_audit.json"
POST_WIDTH = PATCH / "menu_help_weapon_followup_postpromotion_20cell_audit.json"
POST_WIDTH_CSV = SCRIPT / "menu_help_weapon_followup_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "menu_help_weapon_followup_postpromotion_false_lead_audit.json"
POST_P2 = PATCH / "menu_help_weapon_followup_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "menu_help_weapon_followup_postpromotion_false_segptr.json"
POST_RUNTIME = PATCH / "menu_help_weapon_followup_postpromotion_runtime_dialogue_audit.json"
PROMOTION = PATCH / "menu_help_weapon_followup_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "cfb1905aa8f19eb94b92bd23cb96b2657d05b7d18e7b3426b435ef41cb345f5f"
EXPECTED_BASE = "4b08d0a94d6082881c7663a73a56963b33597c2ad3ed168f38ea96cd4a12c6bc"
EXPECTED_CAND = "f4f0ee2c0546e0794dae262b6246a190525763b6174d3423bec3ca20d8d2f212"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_WIDTH_RECORDS = 24_047
EXPECTED_WIDTH_LINES = 24_459
EXPECTED_GUARDED_LEADS = 338
EXPECTED_RUNTIME_TARGETS = 245
EXPECTED_BANK5F = 75

ASSIGN_TITLES = (0x5F2AEF, 0x5F2B58, 0x5F2B7B, 0x5F2BA0, 0x5F2BD5, 0x5F2BE4, 0x5F2C16, 0x5F2E6B)
LOWER_LABEL = 0x75B49E
RELEASE_LABEL = 0x5F445F
TRIPLE_WEAPON = 0x75CB03
DESPADA_WEAPON = 0x75C3C7


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
    req(int(pop.get("offender_records", -1)) == 0 and int(pop.get("max_line_cells", -1)) <= 20, "20-cell overflow")


def validate_leads(doc: dict[str, Any], expected_sha: str) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "false-lead audit failed")
    req(str((doc.get("target") or {}).get("sha256") or "").lower() == expected_sha, "false-lead SHA mismatch")
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
    req(str((((doc.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower() == expected_sha, "false-segptr SHA mismatch")


def validate_runtime(doc: dict[str, Any], expected_sha: str) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "runtime dialogue audit failed")
    req(str((doc.get("target") or {}).get("sha256") or "").lower() == expected_sha, "runtime dialogue SHA mismatch")
    req(int(counts.get("targets", -1)) == EXPECTED_RUNTIME_TARGETS and int(counts.get("failures", -1)) == 0, "runtime dialogue population/failure drift")
    req(int(counts.get("bank5f_discovered_active", -1)) == EXPECTED_BANK5F, "bank5F discovered population drift")
    req(int(counts.get("bank5f_canonical", -1)) == EXPECTED_BANK5F and doc.get("bank5f_coverage_ok") is True, "bank5F coverage drift")


def runtime_texts(path: Path, base_report: dict[str, Any]) -> dict[str, Any]:
    rom = path.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)

    def render(logical: int, max_len: int = 128) -> tuple[bytes, str, int]:
        got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
        req(got is not None, f"unreadable zstring at {logical:06X}")
        payload, term = got
        return bytes(payload), dictionary.expand(bytes(payload), tbl), int(term - sb)

    # Re-verify all 134 menu/help translations from the first candidate stage.
    mismatches: list[dict[str, str]] = []
    applied = base_report.get("applied") or []
    req(len(applied) == 134, f"base menu/help applied population drifted: {len(applied)}")
    for row in applied:
        logical = int(str(row["abs"]), 16)
        _payload, text, _term = render(logical)
        actual = text.rstrip("　 \t")
        # E6 2F is a line/control prefix preserved byte-exact by the candidate;
        # the reviewed `ko` field intentionally describes only the visible body.
        if str(row.get("prefix_hex") or "").upper() == "E62F" and actual.startswith("<E62F>"):
            actual = actual[len("<E62F>"):]
        expected = str(row["ko"]).rstrip("　 \t")
        if actual != expected:
            mismatches.append({"abs": f"{logical:06X}", "expected": expected, "actual": actual})
    req(not mismatches, f"menu/help render mismatch: {mismatches[:5]}")

    assign = {}
    for logical in ASSIGN_TITLES:
        payload, text, term = render(logical)
        cleaned = text.rstrip("　 \t")
        req(cleaned == "배속", f"assignment title mismatch at {logical:06X}: {cleaned!r}")
        req("티탄즈가" not in cleaned, f"assignment title still leaked 티탄즈가 at {logical:06X}")
        assign[f"{logical:06X}"] = {"payload": payload.hex().upper(), "rendered": cleaned, "terminator": f"{term:06X}"}

    lower_payload, lower_text, lower_term = render(LOWER_LABEL)
    req(lower_text.rstrip("　 \t") == "내림", f"내림 label mismatch: {lower_text!r}")

    release_payload, release_text, release_term = render(RELEASE_LABEL)
    req(release_text.rstrip("　 \t") == "해제", f"해제 label mismatch: {release_text!r}")

    triple_payload, triple_text, triple_term = render(TRIPLE_WEAPON)
    req(triple_text == "트리플　메가소닉　포", f"triple weapon mismatch: {triple_text!r}")

    despada_payload, despada_text, despada_term = render(DESPADA_WEAPON)
    despada_trim = despada_text.rstrip("　 \t")
    despada_trailing = len(despada_text) - len(despada_trim)
    req(despada_trim == "대형　미사일　런처", f"Despada full name mismatch: {despada_text!r}")
    req(despada_trailing <= 1 and len(despada_text) <= 10, f"Despada visible padding regressed: {despada_text!r}")

    return {
        "menu_help_exact": len(applied),
        "assignment_titles": assign,
        "lower": {"payload": lower_payload.hex().upper(), "rendered": lower_text.rstrip("　 \t"), "terminator": f"{lower_term:06X}"},
        "release": {"payload": release_payload.hex().upper(), "rendered": release_text.rstrip("　 \t"), "terminator": f"{release_term:06X}"},
        "triple_weapon": {"payload": triple_payload.hex().upper(), "rendered": triple_text, "trailing_visible_spaces": len(triple_text) - len(triple_text.rstrip("　 \t")), "terminator": f"{triple_term:06X}"},
        "despada_weapon": {"payload": despada_payload.hex().upper(), "rendered": despada_text, "trimmed": despada_trim, "trailing_visible_spaces": despada_trailing, "visual_cells": len(despada_text), "terminator": f"{despada_term:06X}"},
    }


def main() -> int:
    required = (TIP, SAVE, BASE_CAND, CAND, BASE_REPORT, BUILD_REPORT, APPROVAL, PRE_TERM, PRE_WIDTH, PRE_LEADS, PRE_P2, PRE_FALSE, PRE_RUNTIME)
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(BASE_CAND.stat().st_size == ROM_SIZE and sha(BASE_CAND) == EXPECTED_BASE, "base candidate identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "follow-up candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM missing/wrong size")
    req(checksum_ok(CAND), "candidate WonderSwan checksum invalid")

    base_report = load(BASE_REPORT)
    build = load(BUILD_REPORT)
    approval = load(APPROVAL)
    req(base_report.get("ok") is True and str((base_report.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "base candidate chain parent mismatch")
    req(str((base_report.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_BASE, "base candidate report hash mismatch")
    req(int((base_report.get("counts") or {}).get("ui_targets", -1)) == 134, "base UI target population drifted")
    req(int((base_report.get("counts") or {}).get("target_failures", -1)) == 0, "base candidate target failures")
    req((base_report.get("verification") or {}).get("weapon_full_name_preserved") is True, "triple weapon base verification missing")

    req(build.get("ok") is True and str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_BASE, "follow-up parent chain mismatch")
    req(str((build.get("main_tip_unchanged") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "follow-up main baseline mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "follow-up report candidate mismatch")
    req(int((build.get("assignment_titles") or {}).get("count", -1)) == 8, "assignment title target count drifted")
    req((build.get("lower_label") or {}).get("rendered") == "내림", "follow-up 내림 verification missing")
    req((build.get("despada") or {}).get("trimmed_render") == "대형　미사일　런처", "Despada full-name verification missing")
    req(int((build.get("verification") or {}).get("target_failures", -1)) == 0, "follow-up target failures")
    req(int((build.get("verification") or {}).get("unaccounted_diff_runs", -1)) == 0, "follow-up unaccounted diff")

    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user promotion approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main hash mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate hash mismatch")

    validate_term(load(PRE_TERM), EXPECTED_CAND)
    validate_width(load(PRE_WIDTH), EXPECTED_CAND)
    validate_leads(load(PRE_LEADS), EXPECTED_CAND)
    validate_p2(load(PRE_P2))
    validate_false(load(PRE_FALSE), EXPECTED_CAND)
    validate_runtime(load(PRE_RUNTIME), EXPECTED_CAND)
    static_pre = runtime_texts(CAND, base_report)

    save_before = ident(SAVE)
    candidate_id = ident(CAND)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_menu_help_weapon_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "generated_by": "tools/promote_menu_help_weapon_followup_candidate.py",
        "reason": "pre_menu_help_weapon_followup",
        "main_tip": ident(backup),
        "candidate_sha256": EXPECTED_CAND,
        "user_validation": ident(APPROVAL),
    })

    staged = TIP.with_name(f".{TIP.name}.menu_help_weapon_followup.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP), "promoted TIP checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")
        static_post = runtime_texts(TIP, base_report)
        req(static_post == static_pre, "promoted runtime target proof differs from candidate")

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
        "generated_by": "tools/promote_menu_help_weapon_followup_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_runtime_verified_menu_help_weapon_followup",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": tip_after,
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": save_after,
        "source_candidate_before_cleanup": candidate_id,
        "user_validation": ident(APPROVAL),
        "base_candidate_report": ident(BASE_REPORT),
        "followup_build_report": ident(BUILD_REPORT),
        "runtime_targets": static_post,
        "post_terminology_audit": ident(POST_TERM),
        "post_20cell_audit": ident(POST_WIDTH),
        "post_visible_lead_audit": ident(POST_LEADS),
        "post_terminator_audit": ident(POST_P2),
        "post_false_segptr_audit": ident(POST_FALSE),
        "post_runtime_dialogue_audit": ident(POST_RUNTIME),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": tip_after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "menu_help_134_exact": int(static_post.get("menu_help_exact", -1)) == 134,
            "assignment_titles_8_exact": len(static_post.get("assignment_titles") or {}) == 8,
            "lower_exact": (static_post.get("lower") or {}).get("rendered") == "내림",
            "release_exact": (static_post.get("release") or {}).get("rendered") == "해제",
            "triple_weapon_exact": (static_post.get("triple_weapon") or {}).get("rendered") == "트리플　메가소닉　포",
            "despada_full_exact": (static_post.get("despada_weapon") or {}).get("trimmed") == "대형　미사일　런처",
            "despada_padding_bounded": int((static_post.get("despada_weapon") or {}).get("trailing_visible_spaces", 99)) <= 1,
            "terminology_clean": (load(POST_TERM).get("status") == "clean"),
            "post_20cell_zero": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_visible_lead_zero": int((load(POST_LEADS).get("counts") or {}).get("reintroduced", -1)) == 0,
            "post_p2_risk_zero": int((load(POST_P2).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_false_segptr_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "runtime_dialogue_245_exact": int((load(POST_RUNTIME).get("counts") or {}).get("targets", -1)) == EXPECTED_RUNTIME_TARGETS,
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "promotion report checks failed")
    atomic_json(PROMOTION, report)

    cleanup_paths = (
        CAND, CAND_SAVE,
        BASE_CAND, BASE_SAVE,
        ROOT / "sram/menu_help_weapon_followup_candidate.sav",
        ROOT / "sram/menu_help_weapon_padding_candidate.sav",
        PATCH / "menu_help_weapon_followup_20cell_audit.csv",
        PATCH / "menu_help_weapon_padding_20cell_audit.csv",
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
        "menu_help_exact": static_post["menu_help_exact"],
        "assignment_titles": len(static_post["assignment_titles"]),
        "lower": static_post["lower"]["rendered"],
        "triple_weapon": static_post["triple_weapon"]["rendered"],
        "despada_weapon": static_post["despada_weapon"]["trimmed"],
        "backup": report["backup"]["path"],
        "cleanup_reclaimed_bytes": reclaimed,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
