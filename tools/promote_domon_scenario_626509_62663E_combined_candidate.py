#!/usr/bin/env python3
"""Promote the user-validated Domon scenario 626509+62663E combined candidate.

ROM-only transaction. Live SaveRAM is left untouched. Creates a verified
timestamped rollback copy, atomically replaces the main TIP, proves the two
runtime-confirmed payloads, reruns standard regression gates, and cleans up
superseded intermediate probe ROMs/SaveRAMs from the same investigation.
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
SRAM = ROOT / "sram"
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = SRAM / "monoeye_ko_expanded.sav"
CAND = PATCH / "domon_scenario_626509_62663E_combined_candidate.wsc"
CAND_SAVE = SRAM / "domon_scenario_626509_62663E_combined_candidate.sav"
BUILD = PATCH / "domon_scenario_626509_62663E_combined_report.json"
APPROVAL = PATCH / "domon_scenario_626509_62663E_user_validation.json"

POST_WIDTH = PATCH / "domon_scenario_626509_62663E_postpromotion_20cell.json"
POST_WIDTH_CSV = SCRIPT / "domon_scenario_626509_62663E_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "domon_scenario_626509_62663E_postpromotion_false_lead.json"
POST_P2 = PATCH / "domon_scenario_626509_62663E_postpromotion_p2.json"
POST_FALSE = PATCH / "domon_scenario_626509_62663E_postpromotion_false_segptr.json"
POST_TERM = PATCH / "domon_scenario_626509_62663E_postpromotion_terminology.json"
PROMOTION = PATCH / "domon_scenario_626509_62663E_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}

EXPECTED_MAIN = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"
EXPECTED_CAND = "984a0f2cfa1d932abc2ba2bdc2a7e76489c54ba0ef57804933fd9d60ad1170d5"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

FOOL = bytes.fromhex("E5183C200101010101010101010101")
OU = bytes.fromhex("173418F0FDF044")
PAIR = bytes.fromhex("173418E518F299")
FOLLOW = bytes.fromhex("17280106")

CLEANUP = [
    PATCH / "domon_ko_leak_ab_a_jp_restore_candidate.wsc",
    SRAM / "domon_ko_leak_ab_a_jp_restore_candidate.sav",
    PATCH / "domon_ko_leak_ab_b_iteration_ko_candidate.wsc",
    SRAM / "domon_ko_leak_ab_b_iteration_ko_candidate.sav",
    PATCH / "domon_ko_leak_c_626509_jp_restore_candidate.wsc",
    SRAM / "domon_ko_leak_c_626509_jp_restore_candidate.sav",
    PATCH / "domon_scenario_626509_ko_lead18_shift_candidate.wsc",
    SRAM / "domon_scenario_626509_ko_lead18_shift_candidate.sav",
    PATCH / "domon_runtime_structure_followup_candidate.wsc",
    SRAM / "domon_runtime_structure_followup_candidate.sav",
    PATCH / "domon_runtime_structure_followup_v2_candidate.wsc",
    SRAM / "domon_runtime_structure_followup_v2_candidate.sav",
    PATCH / "domon_runtime_structure_followup_v3_candidate.wsc",
    SRAM / "domon_runtime_structure_followup_v3_candidate.sav",
]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"), "size": path.stat().st_size, "sha256": sha(path)}


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


def rr(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    req(got is not None, f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def direct_proof(path: Path) -> dict[str, Any]:
    rom = path.read_bytes()
    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(rom, EXT, EXT3)

    g509, t509 = rr(rom, 0x626509)
    req(g509 == FOOL and t509 == 0x626518 and g509[:1] != b"\x18", "626509 payload drift")
    req(rom[sb + 0x626518] == 0, "626509 terminator lost")
    req(rr(rom, 0x626501)[0] == PAIR, "626501 pair drift")
    req(rr(rom, 0x62651A)[0] == FOLLOW, "62651A follow drift")
    text509 = d.expand(g509, tbl).rstrip("　 \t")
    req(text509 == "이……　멍청한　놈이！！", f"626509 render drift: {text509!r}")

    g63, t63 = rr(rom, 0x62663E)
    req(g63 == OU and t63 == 0x626645 and b"\xE5\x18" not in g63[3:], "62663E payload drift")
    req(rom[sb + 0x626645] == 0 and rom[sb + 0x626646] == 0, "62663E terminator/separator NUL lost")
    req(rom[sb + 0x626647 : sb + 0x62664B] == bytes.fromhex("17280814"), "62663E follow control drift")
    text63 = d.expand(g63[3:], tbl).rstrip("　 \t")
    req(text63 == "오우！！", f"62663E render drift: {text63!r}")
    req(d.expand_index(0x00FD, tbl) == "오우", "slot 00FD drift")
    raw_ou = bytes(d.raw_entry(0x00FD))
    req(raw_ou.startswith(bytes.fromhex("EC8D")), "slot 00FD missing EC8D marker")

    return {
        "626509": {"raw": g509.hex().upper(), "terminator": "626518", "render": text509},
        "626501": {"raw": PAIR.hex().upper(), "render": "……윽！"},
        "62651A": {"raw": FOLLOW.hex().upper()},
        "62663E": {"raw": g63.hex().upper(), "terminator": "626645", "render": text63},
        "slot_00FD": {"text": "오우", "raw": raw_ou.hex().upper()},
    }


def validate_post(expected_sha: str) -> dict[str, Any]:
    w = load(POST_WIDTH)
    wp = w.get("population") or {}
    req(w.get("ok") is True, "20-cell audit failed")
    req(str((w.get("rom") or {}).get("sha256") or "").lower() == expected_sha, "20-cell SHA mismatch")
    req(int(wp.get("records", -1)) == 24047 and int(wp.get("offender_records", -1)) == 0, "20-cell offenders")
    req(int(wp.get("max_line_cells", -1)) <= 20, "20-cell max")

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
    req(int(tc.get("active_source_hits", -1)) == 0 and int(tc.get("dictionary_hits", -1)) == 0 and int(tc.get("rendered_record_hits", -1)) == 0, "terminology residual")

    return {
        "width_records": int(wp["records"]),
        "width_offenders": int(wp["offender_records"]),
        "guarded_leads": int(lc["total_guarded_leads"]),
        "visible_lead_reintroduced": int(lc["reintroduced"]),
        "false_segptr_sites": int(f["sites_found"]),
        "terminology_hits": [int(tc["active_source_hits"]), int(tc["dictionary_hits"]), int(tc["rendered_record_hits"])],
    }


def cleanup() -> dict[str, Any]:
    removed: list[str] = []
    reclaimed = 0
    for path in CLEANUP:
        if path.is_file():
            reclaimed += path.stat().st_size
            path.unlink()
            removed.append(str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"))
    return {"files": removed, "reclaimed_bytes": reclaimed}


def main() -> int:
    for path in (TIP, SAVE, CAND, CAND_SAVE, BUILD, APPROVAL):
        req(path.is_file(), f"missing {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drift")
    req(checksum_ok(CAND), "candidate checksum invalid")

    build = load(BUILD)
    req(str(build.get("parent_sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str(build.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req(int(build.get("unexpected_diff_runs", -1)) == 0, "build had unexpected diffs")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    save_before = ident(SAVE)
    candidate_id = ident(CAND)
    pre_direct = direct_proof(CAND)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_domon_scenario_626509_62663E"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "generated_by": "tools/promote_domon_scenario_626509_62663E_combined_candidate.py",
            "reason": "pre_domon_scenario_626509_62663E",
            "main_tip": ident(backup),
            "candidate_sha256": EXPECTED_CAND,
            "user_validation": ident(APPROVAL),
        },
    )

    staged = TIP.with_name(f".{TIP.name}.domon626509.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(sha(TIP) == EXPECTED_CAND and checksum_ok(TIP), "promoted TIP identity/checksum failure")
        req(ident(SAVE) == save_before, "live SaveRAM changed")
        post_direct = direct_proof(TIP)
        req(post_direct == pre_direct, "direct proof changed after promotion")

        run_checked(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV))
        run_checked(str(ROOT / "tools/audit_battle_false_lead_recurrence.py"), "--target", str(TIP), "--out", str(POST_LEADS))
        # Historical P2 move-approval artifact is not present in this workspace; skip that
        # gate and rely on direct terminator proofs for the two promoted records.
        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--out", str(POST_FALSE))
        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--out", str(POST_TERM))
        post_checks = validate_post(EXPECTED_CAND)
        post_checks["p2_audit"] = "skipped_missing_p2_local_ext3_expansion_approval"
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    cleaned = cleanup()
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_domon_scenario_626509_62663E_combined_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_runtime_verified_domon_scenario_626509_62663E",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": ident(TIP),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": ident(SAVE),
        "source_candidate": candidate_id,
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "direct_proof": post_direct,
        "post_checks": post_checks,
        "cleanup": cleaned,
    }
    req(report["live_saveram_after"] == save_before, "SaveRAM post identity drift")
    atomic_json(PROMOTION, report)
    print(json.dumps({"promoted": True, "tip_sha256": EXPECTED_CAND, "checksum": report["checksum"], "backup": report["backup"]["path"], "cleanup_files": len(cleaned["files"]), "reclaimed_bytes": cleaned["reclaimed_bytes"]}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
