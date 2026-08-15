#!/usr/bin/env python3
"""Promote the user-validated Haman/Colony Laser MT context retranslation.

ROM-only. Live SaveRAM untouched. Backs up main TIP, atomically replaces it with
the candidate, proves key renders, reruns standard regression gates (allowing the
seven pre-existing 20-cell offenders that already exist on the parent), and
removes the candidate ROM/SaveRAM pair after success.
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
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = SRAM / "monoeye_ko_expanded.sav"
CAND = PATCH / "haman_colony_laser_mt_context_retranslation_candidate.wsc"
CAND_SAVE = SRAM / "haman_colony_laser_mt_context_retranslation_candidate.sav"
BUILD = PATCH / "haman_colony_laser_mt_context_retranslation_candidate_report.json"
APPROVAL = PATCH / "haman_colony_laser_mt_context_retranslation_user_validation.json"
SPEC = ROOT / "data/haman_colony_laser_mt_context_retranslation_ko.json"

POST_WIDTH = PATCH / "haman_colony_laser_mt_postpromotion_20cell.json"
POST_WIDTH_CSV = SCRIPT / "haman_colony_laser_mt_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "haman_colony_laser_mt_postpromotion_false_lead.json"
POST_FALSE = PATCH / "haman_colony_laser_mt_postpromotion_false_segptr.json"
POST_TERM = PATCH / "haman_colony_laser_mt_postpromotion_terminology.json"
PROMOTION = PATCH / "haman_colony_laser_mt_context_retranslation_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}

EXPECTED_MAIN = "cef2d40d7a0568e3add4025d8ebc6f5e6340f0a2b545a5f88decc6d28e3375f5"
EXPECTED_CAND = "d479f3ec861dc1da9b5da83dbf6900711367b6e09b3b975c4c72adbc3e633316"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

KNOWN_20CELL_OFFENDERS = {
    "63CF8A",
    "63CFF8",
    "63D00A",
    "63D321",
    "63E226",
    "63E55C",
    "63F64C",
}

PROOF_RENDERS = {
    0x629D9B: "……하고　있구나、　하만　녀석。",
    0x629D24: "이　작전은　콜로니　레이저를",
    0x629D33: "봉쇄할　수　있느냐에　달려　있다！！",
    0x6335D0: "네오・지온　함대는　콜로니　레이저의",
    0x6335E2: "주위에　부대를　전개하고　있습니다。",
    0x63366A: "조준이　고정되기　전에　콜로니　레이저에",
    0x63367D: "달라붙으면　승산이　있다。",
    0x633620: "역습당할　위험이　크다。",
    0x629C90: "네오　지온도　정위치　대기　중입니다！！",
}


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


def run_audit(*args: str, allow_nonzero: bool = False) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=False)
    if completed.returncode != 0 and not allow_nonzero:
        raise RuntimeError(f"audit failed ({completed.returncode}): {' '.join(args)}")


def direct_proof(path: Path) -> dict[str, Any]:
    rom = path.read_bytes()
    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(rom, EXT, EXT3)
    rows: dict[str, Any] = {}
    for logical, expected in PROOF_RENDERS.items():
        got = read_encoded_z_safe(rom, sb + logical, max_len=256)
        req(got is not None, f"unreadable {logical:06X}")
        payload, term = got
        prefix, body, kind = split_prefix_body(payload)
        req(kind == "dialogue", f"not dialogue at {logical:06X}")
        req(rom[term] == 0, f"terminator lost at {logical:06X}")
        rendered = d.expand(body, tbl).rstrip("　 \t")
        req(rendered == expected, f"render drift at {logical:06X}: {rendered!r}")
        rows[f"{logical:06X}"] = {
            "payload": payload.hex().upper(),
            "terminator": f"{term - sb:06X}",
            "prefix": prefix.hex().upper(),
            "render": rendered,
        }
    return rows


def validate_post(expected_sha: str) -> dict[str, Any]:
    w = load(POST_WIDTH)
    wp = w.get("population") or {}
    req(str((w.get("rom") or {}).get("sha256") or "").lower() == expected_sha, "20-cell SHA mismatch")
    req(int(wp.get("records", -1)) == 24047, "20-cell record count drifted")
    offender_abs = {str(row.get("abs") or "").upper() for row in (w.get("offenders") or [])}
    req(offender_abs == KNOWN_20CELL_OFFENDERS, f"20-cell offender set drifted: {sorted(offender_abs)}")
    req(int(wp.get("offender_records", -1)) == len(KNOWN_20CELL_OFFENDERS), "20-cell offender count")
    req(w.get("terminology_ok") is True, "20-cell terminology residual")

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
    req(
        int(tc.get("active_source_hits", -1)) == 0
        and int(tc.get("dictionary_hits", -1)) == 0
        and int(tc.get("rendered_record_hits", -1)) == 0,
        "terminology residual",
    )

    return {
        "width_records": int(wp["records"]),
        "width_offenders": int(wp["offender_records"]),
        "width_offender_abs": sorted(offender_abs),
        "known_preexisting_20cell_offenders_preserved": True,
        "guarded_leads": int(lc["total_guarded_leads"]),
        "visible_lead_reintroduced": int(lc["reintroduced"]),
        "false_segptr_sites": int(f["sites_found"]),
        "terminology_hits": [
            int(tc["active_source_hits"]),
            int(tc["dictionary_hits"]),
            int(tc["rendered_record_hits"]),
        ],
    }


def cleanup() -> dict[str, Any]:
    removed: list[str] = []
    reclaimed = 0
    for path in (CAND, CAND_SAVE):
        if path.is_file():
            reclaimed += path.stat().st_size
            path.unlink()
            removed.append(str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"))
    return {"files": removed, "reclaimed_bytes": reclaimed}


def main() -> int:
    for path in (TIP, SAVE, CAND, CAND_SAVE, BUILD, APPROVAL, SPEC):
        req(path.is_file(), f"missing {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drift")
    req(checksum_ok(CAND), "candidate checksum invalid")

    build = load(BUILD)
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    checks = build.get("checks") or {}
    req(checks and all(value is True for value in checks.values()), f"build checks not all true: {checks}")
    req(int((build.get("summary") or {}).get("targets", 0)) == 15, "target population drifted")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")
    req(
        {str(x).upper() for x in (approval.get("known_preexisting_20cell_offenders") or [])} == KNOWN_20CELL_OFFENDERS,
        "approval known offender set mismatch",
    )

    save_before = ident(SAVE)
    candidate_id = ident(CAND)
    pre_direct = direct_proof(CAND)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_haman_colony_laser_mt_context_retranslation"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "generated_by": "tools/promote_haman_colony_laser_mt_context_retranslation_candidate.py",
            "reason": "pre_haman_colony_laser_mt_context_retranslation",
            "main_tip": ident(backup),
            "candidate_sha256": EXPECTED_CAND,
            "user_validation": ident(APPROVAL),
        },
    )

    staged = TIP.with_name(f".{TIP.name}.haman_colony.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(sha(TIP) == EXPECTED_CAND and checksum_ok(TIP), "promoted TIP identity/checksum failure")
        req(ident(SAVE) == save_before, "live SaveRAM changed")
        post_direct = direct_proof(TIP)
        req(post_direct == pre_direct, "direct proof changed after promotion")

        run_audit(
            str(ROOT / "tools/audit_dialogue_20cell_candidate.py"),
            "--rom",
            str(TIP),
            "--out",
            str(POST_WIDTH),
            "--out-csv",
            str(POST_WIDTH_CSV),
            allow_nonzero=True,
        )
        run_audit(
            str(ROOT / "tools/audit_battle_false_lead_recurrence.py"),
            "--target",
            str(TIP),
            "--out",
            str(POST_LEADS),
        )
        run_audit(
            str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target",
            str(TIP),
            "--out",
            str(POST_FALSE),
        )
        run_audit(
            str(ROOT / "tools/audit_gundam_terminology_standard.py"),
            "--tip",
            str(TIP),
            "--out",
            str(POST_TERM),
        )
        post_checks = validate_post(EXPECTED_CAND)
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    cleaned = cleanup()
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_haman_colony_laser_mt_context_retranslation_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_authorized_haman_colony_laser_mt_context_retranslation",
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
        "spec": ident(SPEC),
        "direct_proof": post_direct,
        "post_checks": post_checks,
        "cleanup": cleaned,
    }
    req(report["live_saveram_after"] == save_before, "SaveRAM post identity drift")
    atomic_json(PROMOTION, report)
    print(
        json.dumps(
            {
                "promoted": True,
                "tip_sha256": EXPECTED_CAND,
                "checksum": report["checksum"],
                "backup": report["backup"]["path"],
                "cleanup_files": len(cleaned["files"]),
                "reclaimed_bytes": cleaned["reclaimed_bytes"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
