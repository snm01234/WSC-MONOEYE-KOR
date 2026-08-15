#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "dialogue_20cell_candidate.wsc"
CAND_SAVE = ROOT / "sram/dialogue_20cell_candidate.sav"
BATCH = ROOT / "out/script/dialogue_20cell_batch_validation.json"
BUILD = PATCH / "dialogue_20cell_report.json"
WIDTH = PATCH / "dialogue_20cell_width_audit.json"
TERM = PATCH / "dialogue_20cell_terminator_audit.json"
FALSE = PATCH / "dialogue_20cell_false_segptr.json"
OUT = PATCH / "dialogue_20cell_final_status.json"
EXPECTED_MAIN = "bbd14e0792264787985462c14d75cc77af168b90efc45b3a01d58b9a1de3d1ec"
EXPECTED_TARGET_RECORDS = 7923
EXPECTED_CONTROLLED_RETARGETS = 1128
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ident(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "size": path.stat().st_size, "sha256": sha(path)}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    for p in (MAIN, SAVE, CAND, CAND_SAVE, BATCH, BUILD, WIDTH, TERM, FALSE):
        req(p.is_file(), f"missing required artifact: {p}")
    req(MAIN.stat().st_size == ROM_SIZE and sha(MAIN) == EXPECTED_MAIN, "main TIP changed during candidate work")
    req(CAND.stat().st_size == ROM_SIZE, "candidate ROM size wrong")
    req(SAVE.stat().st_size == SAVE_SIZE and CAND_SAVE.stat().st_size == SAVE_SIZE, "SaveRAM size wrong")
    req(CAND_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM differs from current live main SaveRAM")
    req(checksum_ok(CAND.read_bytes()), "candidate WonderSwan checksum invalid")

    batch = load(BATCH)
    req(batch.get("ok") is True, "translation batch validation failed")
    bc = batch.get("counts") or {}
    req(int(bc.get("missing", -1)) == 0 and int(bc.get("extras", -1)) == 0, "translation coverage not exact")
    req(int(bc.get("conflicts", -1)) == 0 and int(bc.get("invalid", -1)) == 0, "translation batches conflict/invalid")

    build = load(BUILD)
    req(build.get("ok") is True, "candidate build report not clean")
    cc = build.get("counts") or {}
    req(int(cc.get("target_records", -1)) == EXPECTED_TARGET_RECORDS, "target population drifted")
    retargets = int(cc.get("record_retarget_ext3_records", -1))
    payload_changes = int(cc.get("record_payload_changes", -1))
    req(retargets == EXPECTED_CONTROLLED_RETARGETS, "controlled retarget population drifted")
    req(payload_changes == retargets, "record payload changes are not fully accounted retargets")
    req(int(cc.get("terminator_changes", -1)) == 0, "dialogue terminator changed")
    req(int(cc.get("unexpected_diff_offsets", -1)) == 0, "unexpected ROM diff exists")
    req(int(cc.get("max_after_cells", 999)) <= 20, "build report has over-20 target")
    req(str(((build.get("candidate") or {}).get("sha256") or "")).lower() == sha(CAND), "build report candidate hash mismatch")

    width = load(WIDTH)
    req(width.get("ok") is True, "20-cell width audit failed")
    pop = width.get("population") or {}
    req(int(pop.get("offender_records", -1)) == 0, "20-cell audit still has offenders")
    req(int(pop.get("max_line_cells", 999)) <= 20, "candidate-wide max line >20")
    cam = width.get("camille_6117ca") or {}
    req(cam.get("abs") == "6117CA" and int(cam.get("max_line_cells", -1)) == 20, "Camille screenshot calibration row is not exactly 20")
    req(str((((width.get("rom") or {}).get("sha256")) or "")).lower() == sha(CAND), "width audit candidate hash mismatch")

    term = load(TERM)
    tc = term.get("counts") or {}
    req(int(tc.get("current_still_expanded", -1)) == 0, "P2 terminator move regression")
    req(int(tc.get("separator_nul_lost", -1)) == 0, "separator NUL regression")
    req(int(tc.get("runtime_risk", -1)) == 0, "terminator runtime risk regression")

    false = load(FALSE)
    req(false.get("ok") is True and int(false.get("sites_found", -1)) == 0, "false segmented pointer write detected")

    cand = CAND.read_bytes()
    status = {
        "ok": True,
        "status": "candidate_ready_for_runtime_validation",
        "line_limit": 20,
        "main_unchanged": ident(MAIN),
        "candidate": ident(CAND),
        "candidate_ws_checksum": f"{int.from_bytes(cand[-2:], 'little'):04X}",
        "candidate_save": ident(CAND_SAVE),
        "counts": {
            "target_records": int(cc["target_records"]),
            "space_only_reflow_records": int(cc["space_only_reflow_records"]),
            "llm_retranslation_records": int(cc["llm_retranslation_records"]),
            "target_ext3_slots": int(cc["target_ext3_slots"]),
            "inplace_slots": int(cc["inplace_slots"]),
            "repoint_append_slots": int(cc["repoint_append_slots"]),
            "record_retarget_ext3_records": retargets,
            "redirect_ext3_slots": int(cc["redirect_ext3_slots"]),
            "kept_short_nonext_records": int(cc["kept_short_nonext_records"]),
            "short_partner_source_retranslation_records": int(cc["short_partner_source_retranslation_records"]),
            "max_after_cells": int(cc["max_after_cells"]),
            "candidate_audit_records": int(pop["records"]),
            "candidate_audit_lines": int(pop["lines"]),
            "candidate_audit_offenders": int(pop["offender_records"]),
            "candidate_audit_max_line_cells": int(pop["max_line_cells"]),
        },
        "checks": {
            "translation_batch_exact_coverage": True,
            "translation_batch_no_conflicts": True,
            "translation_batch_no_japanese_or_unencodable": True,
            "record_payload_byte_identical_except_controlled_retargets": True,
            "controlled_record_retargets_fixed_extent": True,
            "terminators_byte_identical": True,
            "all_audited_lines_le_20": True,
            "camille_6117ca_exactly_20": True,
            "p2_terminator_risk_zero": True,
            "false_segmented_pointer_zero": True,
            "main_tip_unchanged": True,
            "candidate_saveram_exact_current_main": True,
        },
        "reports": {
            "batch_validation": ident(BATCH),
            "build": ident(BUILD),
            "width": ident(WIDTH),
            "terminator": ident(TERM),
            "false_segptr": ident(FALSE),
        },
    }
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
