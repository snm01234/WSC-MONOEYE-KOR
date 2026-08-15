#!/usr/bin/env python3
"""Promote user-validated dialogue_legacy_mt_literal batch018 candidate (ROM-only)."""
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
CAND = PATCH / "dialogue_legacy_mt_literal_candidate.wsc"
CAND_SAVE = SRAM / "dialogue_legacy_mt_literal_candidate.sav"
BUILD = PATCH / "dialogue_legacy_mt_literal_candidate_report.json"
BATCH = ROOT / "data/dialogue_legacy_mt_literal_batch018.json"
APPROVAL = PATCH / "dialogue_legacy_mt_literal_batch018_user_validation.json"

POST_WIDTH = PATCH / "dialogue_legacy_mt_batch018_postpromotion_20cell.json"
POST_WIDTH_CSV = SCRIPT / "dialogue_legacy_mt_batch018_postpromotion_20cell_offenders.csv"
POST_LEADS = PATCH / "dialogue_legacy_mt_batch018_postpromotion_false_lead.json"
POST_FALSE = PATCH / "dialogue_legacy_mt_batch018_postpromotion_false_segptr.json"
POST_TERM = PATCH / "dialogue_legacy_mt_batch018_postpromotion_terminology.json"
PROMOTION = PATCH / "dialogue_legacy_mt_literal_batch018_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}

EXPECTED_MAIN = "93de328215eec7d4162279e5956e6cf110741b0ad3a311e9f499019ce6c5f81e"
EXPECTED_CAND = "cef2d40d7a0568e3add4025d8ebc6f5e6340f0a2b545a5f88decc6d28e3375f5"
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


def direct_proof(path: Path, targets: dict[str, str]) -> dict[str, Any]:
    rom = path.read_bytes()
    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(rom, EXT, EXT3)
    rows: dict[str, Any] = {}
    for abs_hex, expected in sorted(targets.items(), key=lambda item: int(item[0], 16)):
        logical = int(abs_hex, 16)
        got = read_encoded_z_safe(rom, sb + logical, max_len=256)
        req(got is not None, f"unreadable {abs_hex}")
        payload, term = got
        _prefix, body, kind = split_prefix_body(payload)
        req(kind == "dialogue", f"not dialogue at {abs_hex}")
        req(rom[term] == 0, f"terminator lost at {abs_hex}")
        rendered = d.expand(body, tbl).rstrip("　 \t")
        want = expected.replace(" ", "　")
        req(rendered == want, f"render drift at {abs_hex}: {rendered!r}")
        rows[abs_hex] = {"payload": payload.hex().upper(), "terminator": f"{term - sb:06X}", "render": rendered}
    return rows


def validate_post(expected_sha: str, parent_offender_abs: set[str]) -> dict[str, Any]:
    w = load(POST_WIDTH)
    wp = w.get("population") or {}
    req(str((w.get("rom") or {}).get("sha256") or "").lower() == expected_sha, "20-cell SHA mismatch")
    cand_offenders = {
        str(row.get("abs") or row.get("address") or "").upper()
        for row in (w.get("offenders") or [])
    }
    cand_offenders.discard("")
    new_offenders = sorted(cand_offenders - parent_offender_abs)
    req(not new_offenders, f"new 20-cell offenders introduced: {new_offenders}")
    req(int(wp.get("records", -1)) == 24047, "20-cell record count drift")

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
        "width_offenders_total": int(wp.get("offender_records", len(cand_offenders))),
        "width_offenders_preexisting": sorted(parent_offender_abs),
        "width_offenders_new": new_offenders,
        "visible_lead_reintroduced": int(lc["reintroduced"]),
        "false_segptr_sites": int(f["sites_found"]),
        "terminology_hits": [
            int(tc["active_source_hits"]),
            int(tc["dictionary_hits"]),
            int(tc["rendered_record_hits"]),
        ],
    }


def main() -> int:
    for path in (TIP, SAVE, CAND, CAND_SAVE, BUILD, BATCH, APPROVAL):
        req(path.is_file(), f"missing {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drift")
    req(checksum_ok(CAND), "candidate checksum invalid")

    build = load(BUILD)
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req(int(build.get("targets", -1)) == 14, "target count drifted")
    req(int(build.get("unexpected_diff_bytes", -1)) == 0, "unexpected diffs")

    batch = load(BATCH)
    targets = {str(k).upper(): str(v) for k, v in (batch.get("targets") or {}).items()}
    req(len(targets) == 14, "batch target count drifted")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    save_before = ident(SAVE)
    pre_direct = direct_proof(CAND, targets)

    # Parent tip already has 7 over-20 scenario lines outside batch018; gate on no NEW offenders.
    parent_width = load(PATCH / "dialogue_legacy_mt_batch018_main_width_baseline.json")
    parent_offender_abs = {
        str(row.get("abs") or row.get("address") or "").upper()
        for row in (parent_width.get("offenders") or [])
    }
    parent_offender_abs.discard("")
    req(parent_offender_abs == {
        "63CF8A", "63CFF8", "63D00A", "63D321", "63E226", "63E55C", "63F64C"
    }, "parent 20-cell baseline drifted")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_dialogue_legacy_mt_literal_batch018"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "generated_by": "tools/promote_dialogue_legacy_mt_literal_batch018_candidate.py",
            "reason": "pre_dialogue_legacy_mt_literal_batch018",
            "main_tip": ident(backup),
            "candidate_sha256": EXPECTED_CAND,
            "user_validation": ident(APPROVAL),
        },
    )

    staged = TIP.with_name(f".{TIP.name}.batch018.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(sha(TIP) == EXPECTED_CAND and checksum_ok(TIP), "promoted TIP identity/checksum failure")
        req(ident(SAVE) == save_before, "live SaveRAM changed")
        post_direct = direct_proof(TIP, targets)
        req(post_direct == pre_direct, "direct proof changed after promotion")

        # Width audit exits non-zero when any over-20 lines exist. Parent tip already
        # has 7 such lines; still write the report, then gate on no NEW offenders.
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        width_proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/audit_dialogue_20cell_candidate.py"),
                "--rom",
                str(TIP),
                "--out",
                str(POST_WIDTH),
                "--out-csv",
                str(POST_WIDTH_CSV),
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
        req(POST_WIDTH.is_file(), "20-cell audit report missing")
        req(width_proc.returncode in (0, 1), f"20-cell audit crashed: rc={width_proc.returncode}")
        run_checked(
            str(ROOT / "tools/audit_battle_false_lead_recurrence.py"),
            "--target",
            str(TIP),
            "--out",
            str(POST_LEADS),
        )
        run_checked(
            str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target",
            str(TIP),
            "--out",
            str(POST_FALSE),
        )
        run_checked(
            str(ROOT / "tools/audit_gundam_terminology_standard.py"),
            "--tip",
            str(TIP),
            "--out",
            str(POST_TERM),
        )
        post_checks = validate_post(EXPECTED_CAND, parent_offender_abs)
        post_checks["p2_audit"] = "skipped_missing_p2_local_ext3_expansion_approval"
        post_checks["width_gate"] = "no_new_offenders_vs_parent_baseline"
        post_checks["width_audit_returncode"] = width_proc.returncode
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_dialogue_legacy_mt_literal_batch018_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_runtime_verified_dialogue_legacy_mt_literal_batch018",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": ident(TIP),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": ident(SAVE),
        "source_candidate": ident(CAND),
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "batch": ident(BATCH),
        "direct_proof": post_direct,
        "post_checks": post_checks,
        "scope_note": {
            "targets": 14,
            "not_a_full_scattered_mt_unification": True,
            "remaining_blocked_source_after_prior_refresh": 522,
            "remaining_bing_mt_after_prior_refresh": 429,
            "user_feedback": "scattered translation unification not yet perceptible; expected for 14-line chronicle slice",
        },
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
                "targets": 14,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
