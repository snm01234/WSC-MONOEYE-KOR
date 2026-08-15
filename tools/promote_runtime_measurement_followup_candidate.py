#!/usr/bin/env python3
"""Promote the user-validated runtime measurement follow-up candidate.

ROM-only transaction. The current live SaveRAM is never replaced. The script
binds the exact parent/candidate identities and user approval, creates a verified
rollback backup, atomically replaces the main TIP, reruns independent width,
visible-lead, terminator, Gundam terminology and false-segmented-pointer gates,
checks the GP03 weapon/name-table fixes directly, and rolls back on any failure.
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
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "runtime_measurement_followup_candidate.wsc"
CAND_SAVE = ROOT / "sram/runtime_measurement_followup_candidate.sav"
SRAM_MIRROR = ROOT / "sram/runtime_measurement_followup_candidate.sav"
BUILD = PATCH / "runtime_measurement_followup_report.json"
ACCEPT = PATCH / "runtime_measurement_followup_acceptance_audit.json"
PRE_WIDTH = PATCH / "runtime_measurement_followup_width_audit.json"
PRE_LEADS = PATCH / "runtime_measurement_followup_false_lead_audit.json"
PRE_FALSE = PATCH / "runtime_measurement_followup_false_segptr.json"
PRE_TERMS = PATCH / "runtime_measurement_followup_gundam_terminology_audit.json"
PRE_TERM = PATCH / "runtime_measurement_followup_prepromotion_terminator_audit.json"
APPROVAL = PATCH / "runtime_measurement_followup_user_validation.json"
POST_WIDTH = PATCH / "runtime_measurement_followup_postpromotion_width_audit.json"
POST_WIDTH_CSV = SCRIPT / "runtime_measurement_followup_postpromotion_width_offenders.csv"
POST_LEADS = PATCH / "runtime_measurement_followup_postpromotion_false_lead_audit.json"
POST_FALSE = PATCH / "runtime_measurement_followup_postpromotion_false_segptr.json"
POST_TERMS = PATCH / "runtime_measurement_followup_postpromotion_gundam_terminology_audit.json"
POST_TERM = PATCH / "runtime_measurement_followup_postpromotion_terminator_audit.json"
REPORT = PATCH / "runtime_measurement_followup_promotion_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_PARENT = "48320a9336346bf6c6b230b7199426197a7a6321a16d4caed9989aa29c6d9c13"
EXPECTED_CAND = "8a53737d209ff695fdcd78c0f46f9e61eff9a15d8c4f01b0f387e8dd05488af2"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_RECORDS = 253
EXPECTED_WIDTH_RECORDS = 24_047
EXPECTED_WIDTH_LINES = 24_459
EXPECTED_BATTLE_RECORDS = 9_783
EXPECTED_GUARDED_LEADS = 335


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


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_checked(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def verify_direct_runtime_fixes(path: Path) -> dict:
    rom = path.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    weapon = dictionary.expand_index(0x006F, tbl)
    req(weapon == "돌격", f"GP03 weapon 006F drifted: {weapon!r}")
    sb = stock_base(rom)
    expected = {"75D882": "캐라・슨", "75D8AC": "캐라・슨이다！"}
    rendered: dict[str, str] = {}
    for address, wanted in expected.items():
        got = read_encoded_z_safe(rom, sb + int(address, 16), max_len=64)
        req(got is not None, f"unreadable name75 record {address}")
        text = dictionary.expand(bytes(got[0]), tbl).rstrip("\u3000 \t")
        rendered[address] = text
        req(text == wanted, f"name75 drift {address}: {text!r} != {wanted!r}")
    return {"weapon_006F": weapon, "name75": rendered}


def validate_width(doc: dict) -> None:
    req(doc.get("ok") is True and doc.get("width_ok") is True and doc.get("terminology_ok") is True, "width audit not clean")
    req(not doc.get("offenders"), "width offenders remain")
    req(not doc.get("terminology_residuals"), "width terminology residuals remain")
    pop = doc.get("population") or {}
    req(int(pop.get("records", -1)) == EXPECTED_WIDTH_RECORDS, "width record population drifted")
    req(int(pop.get("lines", -1)) == EXPECTED_WIDTH_LINES, "width line population drifted")
    req(int(pop.get("offender_records", -1)) == 0, "width offender count nonzero")
    req(int(pop.get("max_line_cells", -1)) == 20, "width max cell drifted")
    battle = ((pop.get("by_scope") or {}).get("battle_voice") or {})
    req(int(battle.get("records", -1)) == EXPECTED_BATTLE_RECORDS, "battle record population drifted")
    req(int(battle.get("over_20_records", -1)) == 0, "battle width regression")


def validate_leads(doc: dict) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("ok") is True, "visible-lead audit failed")
    req(int(counts.get("total_guarded_leads", -1)) == EXPECTED_GUARDED_LEADS, "visible-lead population drifted")
    req(int(counts.get("reintroduced", -1)) == 0, "visible lead recurrence remains")


def validate_terms(doc: dict) -> None:
    counts = doc.get("counts") or {}
    req(doc.get("status") == "clean", "Gundam terminology audit failed")
    for key in ("active_source_hits", "dictionary_hits", "rendered_record_hits"):
        req(int(counts.get(key, -1)) == 0, f"Gundam terminology {key} remains")


def validate_false_segptr(doc: dict) -> None:
    req(doc.get("ok") is True and int(doc.get("sites_found", -1)) == 0, "false segmented pointer regression")


def validate_terminator(doc: dict) -> None:
    counts = doc.get("counts") or {}
    req(int(counts.get("current_still_expanded", -1)) == 0, "expanded P2 terminator remains")
    req(int(counts.get("separator_nul_lost", -1)) == 0, "separator NUL loss remains")
    req(int(counts.get("runtime_risk", -1)) == 0, "P2 runtime terminator risk remains")


def main() -> int:
    required = (
        TIP, SAVE, CAND, CAND_SAVE, BUILD, ACCEPT, PRE_WIDTH, PRE_LEADS,
        PRE_FALSE, PRE_TERMS, PRE_TERM, APPROVAL, TBL_PATH, EXT_META, EXT3_META,
    )
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size wrong")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE, "candidate SaveRAM size wrong")
    req(CAND_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM does not match current live SaveRAM")
    req(checksum_ok(CAND.read_bytes()), "candidate WonderSwan checksum invalid")

    build = load(BUILD)
    counts = build.get("counts") or {}
    req(build.get("ok") is True, "build report is not clean")
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_PARENT, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req(int(counts.get("records", -1)) == EXPECTED_RECORDS, "build target count drifted")
    req(int(counts.get("terminator_changes", -1)) == 0, "build terminator change detected")
    req(int(counts.get("unexpected_diff_offsets", -1)) == 0, "build unexpected diff detected")

    accept = load(ACCEPT)
    req(accept.get("ok") is True and not accept.get("failures"), "acceptance audit failed")
    req(str(accept.get("main_sha256") or "").lower() == EXPECTED_PARENT, "acceptance parent mismatch")
    req(str(accept.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "acceptance candidate mismatch")

    validate_width(load(PRE_WIDTH))
    validate_leads(load(PRE_LEADS))
    validate_false_segptr(load(PRE_FALSE))
    validate_terms(load(PRE_TERMS))
    validate_terminator(load(PRE_TERM))
    direct_before = verify_direct_runtime_fixes(CAND)

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(approval.get("runtime_validation_status") == "all_reported_cases_verified", "runtime validation incomplete")
    req(str(approval.get("parent_tip_sha256") or "").lower() == EXPECTED_PARENT, "approval parent mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    save_before = ident(SAVE)
    candidate_ident = ident(CAND)
    candidate_save_ident = ident(CAND_SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_runtime_measurement_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "rollback backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.runtime_measurement_followup.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP.read_bytes()), "promoted TIP checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        run_checked(
            str(ROOT / "tools/audit_dialogue_20cell_candidate.py"),
            "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV),
        )
        validate_width(load(POST_WIDTH))

        run_checked(
            str(ROOT / "tools/audit_battle_false_lead_recurrence.py"),
            "--target", str(TIP), "--out", str(POST_LEADS),
        )
        validate_leads(load(POST_LEADS))

        run_checked(
            str(ROOT / "tools/audit_p2_local_terminator_moves.py"),
            "--target", str(TIP), "--out", str(POST_TERM),
        )
        validate_terminator(load(POST_TERM))

        run_checked(
            str(ROOT / "tools/audit_gundam_terminology_standard.py"),
            "--tip", str(TIP), "--tbl", str(TBL_PATH), "--out", str(POST_TERMS),
        )
        validate_terms(load(POST_TERMS))

        run_checked(
            str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE),
        )
        validate_false_segptr(load(POST_FALSE))

        direct_after = verify_direct_runtime_fixes(TIP)
        req(direct_after == direct_before, "direct runtime fix verification changed after promotion")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        marker = subprocess.run(
            [sys.executable, str(ROOT / "tools/hangul_marker.py")],
            cwd=ROOT, env=env, check=True, capture_output=True, text=True,
        ).stdout.strip().upper()
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
        "generated_by": "tools/promote_runtime_measurement_followup_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_runtime_measurement_followup_user_verified",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup),
        "live_saveram": save_after,
        "source_candidate_before_cleanup": candidate_ident,
        "source_candidate_saveram_before_cleanup": candidate_save_ident,
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "pre_acceptance_audit": ident(ACCEPT),
        "post_width_audit": ident(POST_WIDTH),
        "post_visible_lead_audit": ident(POST_LEADS),
        "post_terminator_audit": ident(POST_TERM),
        "post_terminology_audit": ident(POST_TERMS),
        "post_false_segptr_audit": ident(POST_FALSE),
        "direct_runtime_fixes": direct_after,
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "post_width_zero_offenders": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_visible_lead_reintroduced_zero": int((load(POST_LEADS).get("counts") or {}).get("reintroduced", -1)) == 0,
            "post_p2_runtime_risk_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_terminology_clean": load(POST_TERMS).get("status") == "clean",
            "post_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "weapon_and_name_checks_exact": direct_after == direct_before,
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
    # Keep the final console summary safe on Windows CP949 terminals. The
    # authoritative UTF-8 report above retains the original Korean/Japanese.
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
