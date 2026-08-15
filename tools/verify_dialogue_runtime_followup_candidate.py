#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "out/patch"
MAIN = P / "monoeye_ko_expanded.wsc"
CAND = P / "dialogue_runtime_followup_candidate.wsc"
SAVE = P / "dialogue_runtime_followup_candidate.sav"
BUILD = P / "dialogue_runtime_followup_report.json"
WIDTH = P / "dialogue_runtime_followup_width_audit.json"
TERM = P / "dialogue_runtime_followup_terminator_audit.json"
FALSE = P / "dialogue_runtime_followup_false_segptr.json"
COLL = P / "dialogue_runtime_followup_collision_candidate.json"
OUT = P / "dialogue_runtime_followup_final_status.json"
EXPECTED_MAIN = "8e80bc7e722652b9c6b31282c272966ae92f9d3c82975344c577556bf5b9145a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def ident(path: Path) -> dict:
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "size": path.stat().st_size, "sha256": sha(path)}


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    for p in (MAIN, CAND, SAVE, BUILD, WIDTH, TERM, FALSE, COLL):
        req(p.is_file(), f"missing artifact: {p}")
    req(MAIN.stat().st_size == ROM_SIZE and sha(MAIN) == EXPECTED_MAIN, "main TIP changed")
    req(CAND.stat().st_size == ROM_SIZE, "candidate ROM size wrong")
    req(SAVE.stat().st_size == SAVE_SIZE, "candidate SaveRAM size wrong")
    req(checksum_ok(CAND.read_bytes()), "candidate checksum invalid")

    build = load(BUILD)
    bc = build.get("counts") or {}
    req(build.get("ok") is True, "build report failed")
    req(str(((build.get("candidate") or {}).get("sha256") or "")).lower() == sha(CAND), "build hash mismatch")
    req(int(bc.get("targets", -1)) == 16, "target count drifted")
    req(int(bc.get("hidden_collision_targets", -1)) == 5, "hidden collision target count drifted")
    req(int(bc.get("legacy_제장_remaining_after", -1)) == 0, "제장 residual remains")
    req(int(bc.get("terminator_changes", -1)) == 0, "terminator changed")
    req(int(bc.get("unexpected_diff_offsets", -1)) == 0, "unexpected diff exists")
    req(int(bc.get("max_after_cells", 999)) <= 20, "target text exceeds 20 cells")

    width = load(WIDTH)
    wp = width.get("population") or {}
    req(width.get("ok") is True, "20-cell audit failed")
    req(str(((width.get("rom") or {}).get("sha256") or "")).lower() == sha(CAND), "width audit hash mismatch")
    req(int(wp.get("offender_records", -1)) == 0 and int(wp.get("max_line_cells", 999)) <= 20, "20-cell audit regression")

    term = load(TERM)
    tc = term.get("counts") or {}
    req(int(tc.get("current_still_expanded", -1)) == 0, "P2 expanded terminator remains")
    req(int(tc.get("separator_nul_lost", -1)) == 0, "separator NUL regression")
    req(int(tc.get("runtime_risk", -1)) == 0, "P2 runtime risk")

    false = load(FALSE)
    req(false.get("ok") is True and int(false.get("sites_found", -1)) == 0, "false segmented pointer regression")

    coll = load(COLL)
    cc = coll.get("counts") or {}
    req(coll.get("ok") is True, "speaker dictlead collision audit failed")
    req(int(cc.get("immediate_hidden_dialogues", -1)) == 19, "hidden dialogue population drifted")
    req(int(cc.get("japanese_or_mixed_remaining", -1)) == 0, "hidden Japanese/mixed residual remains")
    req(int(cc.get("over_20", -1)) == 0, "hidden dialogue >20 remains")

    cand = CAND.read_bytes()
    status = {
        "schema_version": 1,
        "ok": True,
        "status": "candidate_ready_for_runtime_validation",
        "main_unchanged": ident(MAIN),
        "candidate": ident(CAND),
        "candidate_ws_checksum": f"{int.from_bytes(cand[-2:], 'little'):04X}",
        "candidate_save": {"path": str(SAVE.relative_to(ROOT)).replace("\\", "/"), "size": SAVE.stat().st_size},
        "counts": {
            "targets": int(bc["targets"]),
            "private_ext3_inplace": int(bc["private_ext3_inplace"]),
            "alias_ext3_retargets": int(bc["alias_ext3_retargets"]),
            "dedicated_ext_2byte_retarget": int(bc["dedicated_ext_2byte_retarget"]),
            "legacy_제장_remaining_after": int(bc["legacy_제장_remaining_after"]),
            "hidden_dialogues_audited": int(cc["immediate_hidden_dialogues"]),
            "hidden_japanese_or_mixed_remaining": int(cc["japanese_or_mixed_remaining"]),
            "width_offenders": int(wp["offender_records"]),
            "max_line_cells": int(wp["max_line_cells"]),
        },
        "checks": {
            "main_tip_unchanged": True,
            "candidate_checksum_valid": True,
            "target_texts_exact_and_le_20": True,
            "record_extents_prefixes_terminators_preserved": True,
            "legacy_제장_zero": True,
            "speaker_dictlead_hidden_japanese_zero": True,
            "p2_terminator_risk_zero": True,
            "false_segmented_pointer_zero": True,
        },
        "reports": {
            "build": ident(BUILD),
            "width": ident(WIDTH),
            "terminator": ident(TERM),
            "false_segptr": ident(FALSE),
            "speaker_collision": ident(COLL),
        },
    }
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
