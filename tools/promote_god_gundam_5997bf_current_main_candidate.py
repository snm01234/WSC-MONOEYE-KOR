#!/usr/bin/env python3
"""Promote the focused, runtime-proven God Gundam 5997BF repair.

The user explicitly requested adding only the missing God Gundam fix to the
current main while leaving the already repaired Garrod dialogue untouched.
The exact 5997BF replacement was previously runtime-verified in
``god_garrod_runtime_followup_candidate.wsc``.

ROM-only transaction: create a verified rollback backup, atomically replace the
main ROM, preserve live SaveRAM byte-exact, rerun regression gates, and assert
that 61E234..61E25C (Garrod) is unchanged.
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
from extract_script import split_prefix_body
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "god_gundam_5997bf_current_main_candidate.wsc"
CAND_SAVE = ROOT / "sram/god_gundam_5997bf_current_main_candidate.sav"
BUILD = PATCH / "god_gundam_5997bf_current_main_candidate_report.json"
APPROVAL = PATCH / "god_gundam_5997bf_user_validation.json"
PRE_WIDTH = PATCH / "god_gundam_5997bf_20cell.json"
PRE_LEADS = PATCH / "god_gundam_5997bf_false_lead.json"
PRE_P2 = PATCH / "god_gundam_5997bf_p2.json"
PRE_FALSE = PATCH / "god_gundam_5997bf_false_segptr.json"
PRE_RUNTIME = PATCH / "god_gundam_5997bf_runtime_regression.json"

POST_WIDTH = PATCH / "god_gundam_5997bf_postpromotion_20cell.json"
POST_WIDTH_CSV = SCRIPT / "god_gundam_5997bf_postpromotion_20cell.csv"
POST_LEADS = PATCH / "god_gundam_5997bf_postpromotion_false_lead.json"
POST_P2 = PATCH / "god_gundam_5997bf_postpromotion_p2.json"
POST_FALSE = PATCH / "god_gundam_5997bf_postpromotion_false_segptr.json"
POST_RUNTIME = PATCH / "god_gundam_5997bf_postpromotion_runtime_regression.json"
POST_TERM = PATCH / "god_gundam_5997bf_postpromotion_terminology.json"
PROMOTION = PATCH / "god_gundam_5997bf_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "35c56e0f8d1aaec9b4687490ddc7b9e999f100ce2987666612931178d0ca44c2"
EXPECTED_CAND = "55c2e1f3467d28e041ad0e145cad68091cf78d50f8d58f6ce6a65259acd59ca9"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
GOD2 = 0x5997BF
GOD2_TERM = 0x5997D4
EXPECTED_GOD2 = bytes.fromhex("171C18E51808920101010101010101010101010101")
EXPECTED_TEXT = "내　이　손이　새빨갛게　타오른다！！"
GARROD_START = 0x61E234
GARROD_END = 0x61E25D
EXPECTED_GARROD = bytes.fromhex(
    "173418F184F191000018F2B80101010101010101010100"
    "F2C50101010101010101010101010000082B"
)
EXPECTED_RUNTIME_TARGETS = 246
EXPECTED_BANK5F = 75


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict[str, Any]:
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "size": path.stat().st_size, "sha256": sha(path)}


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
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)


def validate_standard(path: Path, expected_sha: str) -> None:
    d = load(path)
    req(d.get("status") == "clean", f"terminology audit not clean: {path.name}")
    req(str((d.get("tip") or {}).get("sha256") or "").lower() == expected_sha, "terminology SHA mismatch")
    c = d.get("counts") or {}
    req(int(c.get("active_source_hits", -1)) == 0, "active terminology residual")
    req(int(c.get("dictionary_hits", -1)) == 0, "dictionary terminology residual")
    req(int(c.get("rendered_record_hits", -1)) == 0, "rendered terminology residual")


def validate_width(path: Path, expected_sha: str) -> None:
    d = load(path); p = d.get("population") or {}
    req(d.get("ok") is True, "20-cell audit failed")
    req(str((d.get("rom") or {}).get("sha256") or "").lower() == expected_sha, "20-cell SHA mismatch")
    req(int(p.get("records", -1)) == 24047 and int(p.get("offender_records", -1)) == 0 and int(p.get("max_line_cells", -1)) <= 20, "20-cell population/offender drift")


def validate_leads(path: Path, expected_sha: str) -> None:
    d = load(path); c = d.get("counts") or {}
    req(d.get("ok") is True and not d.get("failures"), "visible-lead audit failed")
    req(str((d.get("target") or {}).get("sha256") or "").lower() == expected_sha, "visible-lead SHA mismatch")
    req(int(c.get("total_guarded_leads", -1)) == 340 and int(c.get("reintroduced", -1)) == 0, "visible-lead population drift")


def validate_p2(path: Path) -> None:
    d = load(path); c = d.get("counts") or {}
    req(d.get("ok") is True, "P2 audit failed")
    req(int(c.get("separator_nul_lost", -1)) == 0 and int(c.get("runtime_risk", -1)) == 0 and int(c.get("current_still_expanded", -1)) == 0, "P2 structural risk")


def validate_false(path: Path, expected_sha: str) -> None:
    d = load(path)
    req(d.get("ok") is True and int(d.get("sites_found", -1)) == 0, "false-segptr audit failed")
    got = str((((d.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower()
    req(got == expected_sha, "false-segptr SHA mismatch")


def validate_runtime(path: Path, expected_sha: str) -> None:
    d = load(path); c = d.get("counts") or {}
    req(d.get("ok") is True and not d.get("failures"), "runtime regression audit failed")
    req(str((d.get("target") or {}).get("sha256") or "").lower() == expected_sha, "runtime SHA mismatch")
    req(int(c.get("targets", -1)) == EXPECTED_RUNTIME_TARGETS, "runtime target population drift")
    req(int(c.get("scenario", -1)) == 166 and int(c.get("bank5f", -1)) == EXPECTED_BANK5F, "runtime category population drift")
    req(int(c.get("failures", -1)) == 0, "runtime failures remain")
    rows = [r for r in d.get("rows") or [] if str(r.get("abs") or "").upper() == "5997BF"]
    req(len(rows) == 1, "5997BF runtime lock missing")
    req(rows[0].get("rendered") == EXPECTED_TEXT and rows[0].get("prefix_hex") == "171C18", "5997BF runtime lock mismatch")


def direct_proof(path: Path) -> dict[str, Any]:
    rom = path.read_bytes(); sb = stock_base(rom)
    req(rom[sb + GOD2 : sb + GOD2_TERM] == EXPECTED_GOD2, "5997BF exact bytes mismatch")
    req(rom[sb + 0x5997D4] == 0 and rom[sb + 0x5997D5] == 0, "5997BF NUL boundary mismatch")
    req(rom[sb + 0x5997D6 : sb + 0x5997D8] == bytes.fromhex("084B"), "5997BF following control mismatch")
    garrod = rom[sb + GARROD_START : sb + GARROD_END]
    req(garrod == EXPECTED_GARROD, "Garrod protected range changed")
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    got = read_encoded_z_safe(rom, sb + GOD2, max_len=64)
    req(got is not None, "5997BF unreadable")
    payload, term_file = got
    prefix, body, _kind = split_prefix_body(bytes(payload))
    rendered = dictionary.expand(bytes(body), tbl).rstrip("　 \t")
    req(prefix == bytes.fromhex("171C18") and rendered == EXPECTED_TEXT and int(term_file - sb) == GOD2_TERM, "5997BF semantic proof failed")
    return {
        "5997BF": {"payload_hex": bytes(payload).hex().upper(), "prefix_hex": prefix.hex().upper(), "rendered": rendered, "terminator": "5997D4", "separator": "5997D5", "following_control": "084B"},
        "garrod": {"range": "61E234-61E25C", "byte_exact": True, "hex": garrod.hex().upper()},
    }


def main() -> int:
    required = (TIP, SAVE, CAND, CAND_SAVE, BUILD, APPROVAL, PRE_WIDTH, PRE_LEADS, PRE_P2, PRE_FALSE, PRE_RUNTIME)
    for path in required:
        req(path.is_file(), f"missing artifact: {path}")
    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM missing/wrong size")
    req(checksum_ok(CAND), "candidate checksum invalid")

    build = load(BUILD)
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req((build.get("protected_garrod") or {}).get("byte_exact") is True, "build did not protect Garrod")
    req(int((build.get("diff") or {}).get("outside_target_and_checksum", -1)) == 0, "build diff escaped focused scope")

    approval = load(APPROVAL)
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user authorization missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    validate_width(PRE_WIDTH, EXPECTED_CAND)
    validate_leads(PRE_LEADS, EXPECTED_CAND)
    validate_p2(PRE_P2)
    validate_false(PRE_FALSE, EXPECTED_CAND)
    validate_runtime(PRE_RUNTIME, EXPECTED_CAND)
    pre_direct = direct_proof(CAND)

    save_before = ident(SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_god_gundam_5997bf"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "generated_by": "tools/promote_god_gundam_5997bf_current_main_candidate.py",
        "reason": "pre_god_gundam_5997bf",
        "main_tip": ident(backup),
        "candidate_sha256": EXPECTED_CAND,
        "user_validation": ident(APPROVAL),
    })

    staged = TIP.with_name(f".{TIP.name}.god5997bf.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(sha(TIP) == EXPECTED_CAND and checksum_ok(TIP), "promoted TIP identity/checksum failed")
        req(ident(SAVE) == save_before, "live SaveRAM changed")
        post_direct = direct_proof(TIP)
        req(post_direct == pre_direct, "direct proof changed after promotion")

        run_checked(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV))
        validate_width(POST_WIDTH, EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_battle_false_lead_recurrence.py"), "--target", str(TIP), "--out", str(POST_LEADS))
        validate_leads(POST_LEADS, EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_P2))
        validate_p2(POST_P2)
        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE))
        validate_false(POST_FALSE, EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_runtime_dialogue_regression_followup.py"), "--target", str(TIP), "--out", str(POST_RUNTIME))
        validate_runtime(POST_RUNTIME, EXPECTED_CAND)
        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--out", str(POST_TERM))
        validate_standard(POST_TERM, EXPECTED_CAND)
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_god_gundam_5997bf_current_main_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_runtime_proven_god_gundam_5997bf_only",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": ident(TIP),
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": ident(SAVE),
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD),
        "direct_runtime_proof": post_direct,
        "post": {
            "width": ident(POST_WIDTH),
            "visible_lead": ident(POST_LEADS),
            "p2": ident(POST_P2),
            "false_segptr": ident(POST_FALSE),
            "runtime_regression": ident(POST_RUNTIME),
            "terminology": ident(POST_TERM),
        },
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "main_exact_candidate": sha(TIP) == EXPECTED_CAND,
            "live_saveram_unchanged": ident(SAVE) == save_before,
            "garrod_byte_exact": post_direct["garrod"]["byte_exact"],
            "god_5997bf_runtime_locked": post_direct["5997BF"]["rendered"] == EXPECTED_TEXT,
            "runtime_regression_246": int((load(POST_RUNTIME).get("counts") or {}).get("targets", -1)) == EXPECTED_RUNTIME_TARGETS,
            "width_offender_zero": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "visible_lead_zero": int((load(POST_LEADS).get("counts") or {}).get("reintroduced", -1)) == 0,
            "p2_risk_zero": int((load(POST_P2).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "false_segptr_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "terminology_clean": load(POST_TERM).get("status") == "clean",
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "promotion checks failed")
    atomic_json(PROMOTION, report)

    cleanup_paths = (CAND, CAND_SAVE, SCRIPT / "god_gundam_5997bf_20cell.csv", POST_WIDTH_CSV)
    cleanup = []
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
        "main_sha256": sha(TIP),
        "checksum": report["checksum"],
        "garrod_byte_exact": report["checks"]["garrod_byte_exact"],
        "god_5997bf": report["direct_runtime_proof"]["5997BF"],
        "runtime_targets": (load(POST_RUNTIME).get("counts") or {}).get("targets"),
        "backup": report["backup"]["path"],
        "save_unchanged": report["checks"]["live_saveram_unchanged"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
