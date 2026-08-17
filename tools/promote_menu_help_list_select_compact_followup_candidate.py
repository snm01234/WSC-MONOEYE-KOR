#!/usr/bin/env python3
"""Promote the user-validated menu help/list compact follow-up to main TIP.

ROM-only transaction. The live main SaveRAM is preserved. The script validates
both cumulative candidate stages, records the user's runtime approval, creates a
verified rollback backup, atomically replaces the main TIP, re-verifies the 60
new menu routes plus the prior assignment/status routes, runs the standard
static regression tools, and rolls back automatically on any failure.
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

import build_menu_help_list_select_compact_followup_candidate as build
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_menu_help_supply_status_spill_followup_candidate import (
    ASSIGN_TITLE_POINTERS,
    TARGETS as PREV_HELP_TARGETS,
    TARGET_POINTERS as PREV_HELP_POINTERS,
    payload_at,
    read_le16_logical,
    render_payload,
)
from monoeye_rom import Dictionary, Tbl, stock_base

TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = PATCH / "menu_help_supply_status_spill_followup_candidate.wsc"
PARENT_REPORT = PATCH / "menu_help_supply_status_spill_followup_candidate_report.json"
CAND = PATCH / "menu_help_list_select_compact_followup_candidate.wsc"
BUILD_REPORT = PATCH / "menu_help_list_select_compact_followup_candidate_report.json"
APPROVAL = PATCH / "menu_help_list_select_compact_followup_user_validation.json"
PROMOTION = PATCH / "menu_help_list_select_compact_followup_promotion_report.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

POST_TERM = PATCH / "menu_help_list_select_compact_postpromotion_terminology_audit.json"
POST_DIALOGUE = PATCH / "menu_help_list_select_compact_postpromotion_dialogue_runtime_safety.json"
POST_DIALOGUE_MANIFEST = PATCH / "menu_help_list_select_compact_postpromotion_dialogue_runtime_contracts.json"
POST_LEADS = PATCH / "menu_help_list_select_compact_postpromotion_false_lead_audit.json"
POST_P2 = PATCH / "menu_help_list_select_compact_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "menu_help_list_select_compact_postpromotion_false_segptr.json"
POST_RUNTIME = PATCH / "menu_help_list_select_compact_postpromotion_runtime_dialogue_audit.json"

EXPECTED_MAIN = "f62f14d15f3d76ad2eb33e2b55531ab4781d230cb63bcc2348bbd703d8c39be3"
EXPECTED_PARENT = "462e5f7e812546d49ee21f5b979c7dec8d3f8deada5e738d8026db5778cc4a3c"
EXPECTED_CAND = "dbc0e567fb3d7d3cd207a7e1dc6a737fbca5d131c299e8b0efc977daee546458"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def req(ok: bool, message: str) -> None:
    if not ok:
        raise PromotionError(message)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def checksum_ok(path: Path) -> bool:
    data = path.read_bytes()
    return len(data) == ROM_SIZE and (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def run_checked(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def runtime_proof(path: Path) -> dict[str, Any]:
    rom = path.read_bytes()
    parent = PARENT.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    parent_dictionary = Dictionary(parent)
    stock = Dictionary(rom)
    parent_stock = Dictionary(parent)
    sb = stock_base(rom)

    scope = build.load_scope()
    list_rows = [row for row in scope if row[2] == "목록"]
    compact_rows = [row for row in scope if row[2] != "목록"]
    req(len(scope) == build.EXPECTED_TARGETS, f"scope count drifted: {len(scope)}")
    req(len(list_rows) == build.EXPECTED_LIST_TITLES, "list route count drifted")
    req(len(compact_rows) == build.EXPECTED_NONLIST, "compact route count drifted")

    compact_verified: dict[str, Any] = {}
    for logical, _jp, ko in compact_rows:
        raw, term = payload_at(rom, logical, max_len=96)
        actual = dictionary.expand(raw, tbl)
        req(len(raw) == 4, f"compact payload length drift at {logical:06X}: {len(raw)}")
        req(raw.count(0x01) == 0, f"visible padding remains at {logical:06X}")
        req(actual == ko, f"compact render mismatch at {logical:06X}: {actual!r} != {ko!r}")
        req(term - sb == logical + 4, f"compact terminator drift at {logical:06X}")
        compact_verified[f"{logical:06X}"] = actual

    list_verified: dict[str, Any] = {}
    for logical, _jp, ko in list_rows:
        hits = build.pointer_hits_in_table(parent, logical)
        req(len(hits) == 1, f"list parent pointer ownership drift at {logical:06X}: {hits}")
        pointer = hits[0]
        off16 = read_le16_logical(rom, pointer)
        active = 0x5F0000 | off16
        raw, term = payload_at(rom, active, max_len=32)
        actual = dictionary.expand(raw, tbl)
        req(active == build.LIST_SPILL_LOGICAL, f"list route not private spill at {logical:06X}: {active:06X}")
        req(raw.count(0x01) == 0 and len(raw) == 4, f"list spill shape drift at {logical:06X}")
        req(actual == ko, f"list render mismatch at {logical:06X}: {actual!r}")
        list_verified[f"{logical:06X}"] = {
            "pointer": f"{pointer:06X}",
            "active": f"{active:06X}",
            "rendered": actual,
            "terminator": f"{term - sb:06X}",
        }

    # The menu no longer depends on the reused stock slot 005E; it must remain
    # byte-exact to the cumulative parent instead of being rewritten again.
    req(bytes(stock.raw_entry(build.LIST_STOCK_SLOT)) == bytes(parent_stock.raw_entry(build.LIST_STOCK_SLOT)), "stock 005E changed")
    req(parent_dictionary.expand(build.token_from_dict_index(build.LIST_STOCK_SLOT), tbl).rstrip("　 \t") == "그건", "parent 005E diagnosis drifted")

    assignment: dict[str, str] = {}
    for _source, pointer in ASSIGN_TITLE_POINTERS.items():
        off16 = read_le16_logical(rom, pointer)
        raw, _term = payload_at(rom, 0x5F0000 | off16, max_len=32)
        actual = render_payload(dictionary, tbl, raw)
        req(actual == "배속", f"assignment regression at pointer {pointer:06X}: {actual!r}")
        assignment[f"{pointer:06X}"] = actual

    previous_help: dict[str, str] = {}
    for logical, _jp, ko, _group in PREV_HELP_TARGETS:
        pointer = PREV_HELP_POINTERS[logical]
        off16 = read_le16_logical(rom, pointer)
        raw, _term = payload_at(rom, 0x5F0000 | off16, max_len=96)
        actual = render_payload(dictionary, tbl, raw)
        req(actual == ko, f"previous help regression at {logical:06X}: {actual!r} != {ko!r}")
        previous_help[f"{logical:06X}"] = actual

    return {
        "scope_targets": len(scope),
        "compact_help_exact": len(compact_verified),
        "list_routes_exact": len(list_verified),
        "visible_0x01_active": 0,
        "list_stock_005E_untouched": True,
        "assignment_routes_exact": len(assignment),
        "previous_help_routes_exact": len(previous_help),
        "list_routes": list_verified,
    }


def validate_standard_reports() -> dict[str, Any]:
    term = load_json(POST_TERM)
    dialogue = load_json(POST_DIALOGUE)

    req(term.get("status") == "clean", "terminology audit not clean")
    req(dialogue.get("ok") is True, "authoritative dialogue runtime safety gate failed")
    req(int((dialogue.get("counts") or {}).get("hard_failures", -1)) == 0, "dialogue runtime safety hard failures remain")
    req(int((dialogue.get("counts") or {}).get("review_items", -1)) == 0, "dialogue runtime safety review items remain")
    return {
        "terminology_clean": True,
        "dialogue_runtime_safety_clean": True,
    }


def main() -> int:
    for path in (TIP, SAVE, PARENT, PARENT_REPORT, CAND, BUILD_REPORT, APPROVAL):
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM missing/wrong size")
    req(PARENT.stat().st_size == ROM_SIZE and sha(PARENT) == EXPECTED_PARENT, "cumulative parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "final candidate identity drifted")
    req(checksum_ok(CAND), "candidate WonderSwan checksum invalid")

    parent_report = load_json(PARENT_REPORT)
    build_report = load_json(BUILD_REPORT)
    approval = load_json(APPROVAL)
    req(parent_report.get("ok") is True, "parent report not successful")
    req(str((parent_report.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_PARENT, "parent report candidate hash mismatch")
    req((parent_report.get("verification") or {}).get("assignment_fix_preserved") is not False, "parent assignment verification failed")
    req(build_report.get("ok") is True, "final build report not successful")
    req(str((build_report.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_PARENT, "final build parent mismatch")
    req(str((build_report.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "final report candidate hash mismatch")
    checks = build_report.get("verification") or {}
    for key in (
        "all_51_help_records_token_plus_nul",
        "all_9_list_routes_render_exact",
        "active_visible_0x01_zero",
        "stock_005E_untouched",
        "assignment_fix_preserved",
        "previous_30_help_routes_preserved",
        "diffs_bounded",
        "main_tip_unchanged",
        "main_saveram_untouched",
    ):
        req(checks.get(key) is True, f"final build verification missing/failed: {key}")
    req(int((build_report.get("counts") or {}).get("targets", -1)) == 60, "final target count drifted")
    req(int((build_report.get("counts") or {}).get("visible_0x01_after_active_routes", -1)) == 0, "final visible padding count nonzero")

    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main hash mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate hash mismatch")

    pre_runtime = runtime_proof(CAND)
    save_before = ident(SAVE)
    tip_before = ident(TIP)
    candidate_id = ident(CAND)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_menu_help_list_select_compact_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "generated_by": "tools/promote_menu_help_list_select_compact_followup_candidate.py",
        "reason": "pre_menu_help_list_select_compact_followup",
        "main_tip": ident(backup),
        "candidate": candidate_id,
        "user_validation": ident(APPROVAL),
    })

    staged = TIP.with_name(f".{TIP.name}.menu_help_list_select_compact.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP), "promoted TIP checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed during promotion")
        post_runtime = runtime_proof(TIP)
        req(post_runtime == pre_runtime, "promoted runtime proof differs from candidate")

        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--out", str(POST_TERM))
        run_checked(str(ROOT / "tools/audit_dialogue_runtime_safety_gate.py"), "--target", str(TIP), "--out", str(POST_DIALOGUE), "--manifest", str(POST_DIALOGUE_MANIFEST))
        standard = validate_standard_reports()
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    tip_after = ident(TIP)
    save_after = ident(SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_menu_help_list_select_compact_followup_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_runtime_verified_menu_help_list_select_compact_followup",
        "before": tip_before,
        "after": tip_after,
        "backup": ident(backup),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": save_before,
        "live_saveram_after": save_after,
        "source_candidate": candidate_id,
        "user_validation": ident(APPROVAL),
        "parent_build_report": ident(PARENT_REPORT),
        "final_build_report": ident(BUILD_REPORT),
        "runtime_proof": post_runtime,
        "standard_regression": standard,
        "post_terminology_audit": ident(POST_TERM),
        "post_dialogue_runtime_safety": ident(POST_DIALOGUE),
        "post_dialogue_runtime_contracts": ident(POST_DIALOGUE_MANIFEST),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": tip_after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "60_menu_routes_verified": int(post_runtime.get("scope_targets", -1)) == 60,
            "51_compact_help_exact": int(post_runtime.get("compact_help_exact", -1)) == 51,
            "9_list_routes_exact": int(post_runtime.get("list_routes_exact", -1)) == 9,
            "active_visible_0x01_zero": int(post_runtime.get("visible_0x01_active", -1)) == 0,
            "stock_005E_untouched": post_runtime.get("list_stock_005E_untouched") is True,
            "assignment_fix_preserved": int(post_runtime.get("assignment_routes_exact", -1)) == 8,
            "previous_30_help_routes_preserved": int(post_runtime.get("previous_help_routes_exact", -1)) == 30,
            "standard_regression_all_pass": all(standard.values()),
        },
        "cleanup": {"performed": False, "note": "candidate/report artifacts retained"},
    }
    req(all(report["checks"].values()), "promotion checks failed")
    atomic_json(PROMOTION, report)

    print(json.dumps({
        "ok": True,
        "main_sha256": tip_after["sha256"],
        "checksum": report["checksum"],
        "save_unchanged": report["checks"]["live_saveram_unchanged"],
        "menu_routes": post_runtime["scope_targets"],
        "compact_help": post_runtime["compact_help_exact"],
        "list_routes": post_runtime["list_routes_exact"],
        "active_visible_0x01": post_runtime["visible_0x01_active"],
        "assignment_routes": post_runtime["assignment_routes_exact"],
        "previous_help_routes": post_runtime["previous_help_routes_exact"],
        "backup": report["backup"]["path"],
        "standard_regression": standard,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
