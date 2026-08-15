#!/usr/bin/env python3
"""Promote the user-validated terminology round-2 candidate to the main TIP.

ROM-only transaction. The live main SaveRAM is preserved. Promotion is bound to
exact parent/candidate hashes and the explicit user-validation artifact. A
verified rollback backup is created first; the terminology, 20-cell,
visible-lead, P2-terminator, false-segmented-pointer, and prior runtime-dialogue
audits are rerun against the promoted TIP. Any failure after replacement restores
the verified backup.
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
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base

TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "terminology_round2_candidate.wsc"
CAND_SAVE = ROOT / "sram/terminology_round2_candidate.sav"
SRAM_MIRROR = ROOT / "sram/terminology_round2_candidate.sav"
BUILD = PATCH / "terminology_round2_candidate_report.json"
PRE_TERM = PATCH / "terminology_round2_audit.json"
PRE_WIDTH = PATCH / "terminology_round2_20cell_audit.json"
PRE_LEADS = PATCH / "terminology_round2_false_lead_audit.json"
PRE_P2 = PATCH / "terminology_round2_terminator_audit.json"
PRE_FALSE = PATCH / "terminology_round2_false_segptr.json"
PRE_RUNTIME = PATCH / "terminology_round2_runtime_dialogue_audit.json"
APPROVAL = PATCH / "terminology_round2_user_validation.json"

POST_TERM = PATCH / "terminology_round2_postpromotion_audit.json"
POST_WIDTH = PATCH / "terminology_round2_postpromotion_20cell_audit.json"
POST_WIDTH_CSV = SCRIPT / "terminology_round2_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "terminology_round2_postpromotion_false_lead_audit.json"
POST_P2 = PATCH / "terminology_round2_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "terminology_round2_postpromotion_false_segptr.json"
POST_RUNTIME = PATCH / "terminology_round2_postpromotion_runtime_dialogue_audit.json"
REPORT = PATCH / "terminology_round2_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_PARENT = "5c2d4620809274338bda6d46eb6229fa810e6a3ad9b1c58d41ccb5a503abd67f"
EXPECTED_CAND = "cfb1905aa8f19eb94b92bd23cb96b2657d05b7d18e7b3426b435ef41cb345f5f"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_WIDTH_RECORDS = 24_047
EXPECTED_WIDTH_LINES = 24_459
EXPECTED_GUARDED_LEADS = 338
EXPECTED_RUNTIME_TARGETS = 245
EXPECTED_BANK5F = 75
OLBA_RECORD = 0x5D2A4F
OLBA_EXT3_INDEX = 0x5865
OLBA_EXPECTED = "죽여　버리기엔　아깝지만……"
OLBA_PREFIX_PORTAL = bytes.fromhex("17E5184865")


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


def validate_terminology(doc: dict, expected_sha: str) -> None:
    tip = doc.get("tip") or {}
    counts = doc.get("counts") or {}
    req(doc.get("status") == "clean", "terminology audit not clean")
    req(str(tip.get("sha256") or "").lower() == expected_sha, "terminology audit target SHA mismatch")
    req(int(counts.get("active_source_hits", -1)) == 0, "active-source terminology residuals remain")
    req(int(counts.get("dictionary_hits", -1)) == 0, "dictionary terminology residuals remain")
    req(int(counts.get("rendered_record_hits", -1)) == 0, "rendered terminology residuals remain")


def validate_width(doc: dict, expected_sha: str) -> None:
    rom = doc.get("rom") or {}
    pop = doc.get("population") or {}
    req(doc.get("ok") is True and doc.get("width_ok") is True and doc.get("terminology_ok") is True, "20-cell audit failed")
    req(str(rom.get("sha256") or "").lower() == expected_sha, "20-cell audit target SHA mismatch")
    req(int(pop.get("records", -1)) == EXPECTED_WIDTH_RECORDS, "20-cell record population drifted")
    req(int(pop.get("lines", -1)) == EXPECTED_WIDTH_LINES, "20-cell line population drifted")
    req(int(pop.get("offender_records", -1)) == 0, "20-cell offenders remain")
    req(int(pop.get("max_line_cells", -1)) <= 20, "20-cell maximum exceeded")
    req(not doc.get("offenders"), "20-cell offender rows remain")
    req(not doc.get("terminology_residuals"), "20-cell terminology residual rows remain")


def validate_leads(doc: dict, expected_sha: str) -> None:
    target = doc.get("target") or {}
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "visible-lead audit failed")
    req(str(target.get("sha256") or "").lower() == expected_sha, "visible-lead audit target SHA mismatch")
    req(int(counts.get("total_guarded_leads", -1)) == EXPECTED_GUARDED_LEADS, "guarded-lead population drifted")
    req(int(counts.get("reintroduced", -1)) == 0, "visible lead reintroduced")


def validate_p2(doc: dict) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True, "P2 terminator audit failed")
    req(int(counts.get("current_still_expanded", -1)) == 0, "expanded P2 terminator remains")
    req(int(counts.get("separator_nul_lost", -1)) == 0, "separator NUL loss remains")
    req(int(counts.get("runtime_risk", -1)) == 0, "P2 runtime risk remains")


def validate_false_segptr(doc: dict, expected_sha: str) -> None:
    target = ((doc.get("inputs") or {}).get("target") or {})
    req(doc.get("ok") is True, "false segmented-pointer audit failed")
    req(int(doc.get("sites_found", -1)) == 0, "false segmented-pointer writes remain")
    req(str(target.get("sha256") or "").lower() == expected_sha, "false-segptr audit target SHA mismatch")


def validate_runtime(doc: dict, expected_sha: str) -> None:
    target = doc.get("target") or {}
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True and not doc.get("failures"), "runtime dialogue regression audit failed")
    req(str(target.get("sha256") or "").lower() == expected_sha, "runtime audit target SHA mismatch")
    req(int(counts.get("targets", -1)) == EXPECTED_RUNTIME_TARGETS, "runtime target population drifted")
    req(int(counts.get("failures", -1)) == 0, "runtime dialogue failure remains")
    req(int(counts.get("bank5f_discovered_active", -1)) == EXPECTED_BANK5F, "bank5F discovery drifted")
    req(int(counts.get("bank5f_canonical", -1)) == EXPECTED_BANK5F, "bank5F canonical population drifted")
    req(doc.get("bank5f_coverage_ok") is True, "bank5F coverage incomplete")


def validate_olba(path: Path) -> dict:
    rom = bytes(load_rom(path))
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    phrase = dictionary.expand_index(OLBA_EXT3_INDEX, tbl).rstrip("\u3000 \t")
    req(phrase == OLBA_EXPECTED, f"Olba phrase mismatch: {phrase!r}")
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + OLBA_RECORD, max_len=64)
    req(got is not None, "Olba record no longer has a safe terminator")
    raw, term = got
    req(bytes(raw).startswith(OLBA_PREFIX_PORTAL), f"Olba prefix/portal drifted: {bytes(raw)[:5].hex()}")
    return {
        "record": f"{OLBA_RECORD:06X}",
        "ext3_index": f"{OLBA_EXT3_INDEX:05X}",
        "phrase": phrase,
        "record_prefix_portal": bytes(raw)[:5].hex().upper(),
        "terminator": f"{term - sb:06X}",
    }


def main() -> int:
    required = (TIP, SAVE, CAND, BUILD, PRE_TERM, PRE_WIDTH, PRE_LEADS, PRE_P2, PRE_FALSE, PRE_RUNTIME, APPROVAL)
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.is_file() and SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM missing or wrong size")
    req(checksum_ok(CAND), "candidate WonderSwan checksum invalid")

    build = load(BUILD)
    counts = build.get("counts") or {}
    req(build.get("ok") is True, "candidate build report not clean")
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_PARENT, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req(int(counts.get("stock_entries_patched", -1)) == 6, "stock patch count drifted")
    req(int(counts.get("ext3_physical_phrases_patched", -1)) == 50, "ext3 patch count drifted")
    req(int(counts.get("semantic_runtime_corrections", -1)) == 1, "semantic correction count drifted")
    req(int(counts.get("after_dictionary_hits", -1)) == 0, "build dictionary residual count nonzero")
    req(int(counts.get("after_rendered_record_hits", -1)) == 0, "build rendered residual count nonzero")
    req(int(counts.get("unexpected_diff_offsets", -1)) == 0, "unexpected build diff detected")

    validate_terminology(load(PRE_TERM), EXPECTED_CAND)
    validate_width(load(PRE_WIDTH), EXPECTED_CAND)
    validate_leads(load(PRE_LEADS), EXPECTED_CAND)
    validate_p2(load(PRE_P2))
    validate_false_segptr(load(PRE_FALSE), EXPECTED_CAND)
    validate_runtime(load(PRE_RUNTIME), EXPECTED_CAND)
    olba_pre = validate_olba(CAND)

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user promotion approval missing")
    req(str(approval.get("parent_tip_sha256") or "").lower() == EXPECTED_PARENT, "approval parent mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    save_before = ident(SAVE)
    candidate_ident = ident(CAND)
    candidate_save_ident = ident(CAND_SAVE) if CAND_SAVE.is_file() else None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_terminology_round2"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "rollback backup verification failed")
    backup_manifest = {
        "schema_version": 1,
        "generated_by": "tools/promote_terminology_round2_candidate.py",
        "reason": "pre_terminology_round2",
        "main_tip": ident(backup),
        "candidate_sha256": EXPECTED_CAND,
    }
    atomic_json(backup_dir / "backup_manifest.json", backup_manifest)

    staged = TIP.with_name(f".{TIP.name}.terminology_round2.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP), "promoted TIP checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")
        olba_post = validate_olba(TIP)

        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--out", str(POST_TERM))
        validate_terminology(load(POST_TERM), EXPECTED_CAND)

        run_checked(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV))
        validate_width(load(POST_WIDTH), EXPECTED_CAND)

        run_checked(str(ROOT / "tools/audit_battle_false_lead_recurrence.py"), "--target", str(TIP), "--out", str(POST_LEADS))
        validate_leads(load(POST_LEADS), EXPECTED_CAND)

        run_checked(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_P2))
        validate_p2(load(POST_P2))

        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE))
        validate_false_segptr(load(POST_FALSE), EXPECTED_CAND)

        run_checked(str(ROOT / "tools/audit_runtime_dialogue_regression_followup.py"), "--target", str(TIP), "--out", str(POST_RUNTIME))
        validate_runtime(load(POST_RUNTIME), EXPECTED_CAND)
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_terminology_round2_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_terminology_round2_user_verified",
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
        "olba_pre": olba_pre,
        "olba_post": olba_post,
        "post_terminology_audit": ident(POST_TERM),
        "post_20cell_audit": ident(POST_WIDTH),
        "post_visible_lead_audit": ident(POST_LEADS),
        "post_terminator_audit": ident(POST_P2),
        "post_false_segptr_audit": ident(POST_FALSE),
        "post_runtime_dialogue_audit": ident(POST_RUNTIME),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "terminology_residual_zero": int((load(POST_TERM).get("counts") or {}).get("dictionary_hits", -1)) == 0 and int((load(POST_TERM).get("counts") or {}).get("rendered_record_hits", -1)) == 0,
            "post_20cell_zero_offenders": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_visible_lead_reintroduced_zero": int((load(POST_LEADS).get("counts") or {}).get("reintroduced", -1)) == 0,
            "post_p2_runtime_risk_zero": int((load(POST_P2).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "runtime_dialogue_245_exact": int((load(POST_RUNTIME).get("counts") or {}).get("targets", -1)) == EXPECTED_RUNTIME_TARGETS,
            "olba_semantic_fix_exact": olba_post["phrase"] == OLBA_EXPECTED,
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
        "olba": olba_post["phrase"],
        "backup": report["backup"]["path"],
        "cleanup_reclaimed_bytes": reclaimed,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
