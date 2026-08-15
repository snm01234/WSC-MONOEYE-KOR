#!/usr/bin/env python3
"""Fail-closed final gate for dialogue_readability_candidate.wsc.

This gate combines the independent readability, singleton-spacing, battle false-
lead, terminator, speaker-collision and false-segmented-pointer audits.  It does
not promote the ROM; it only emits a status file suitable for later user-approved
promotion.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CAND = PATCH / "dialogue_readability_candidate.wsc"
BUILD = PATCH / "dialogue_readability_report.json"
WIDTH = PATCH / "dialogue_readability_width_audit.json"
SINGLETON = PATCH / "dialogue_readability_singleton_audit.json"
FALSE_LEAD = PATCH / "dialogue_readability_false_lead_audit.json"
TERM = PATCH / "dialogue_readability_terminator_audit.json"
COLL = PATCH / "dialogue_readability_speaker_collision_audit.json"
FALSE_SEGPTR = PATCH / "dialogue_readability_false_segptr_audit.json"
OUT = PATCH / "dialogue_readability_final_status.json"
EXPECTED_MAIN = "8287c930a2193d5842783a5f49167aa77550e16139bdc76674c61e2602f2cff1"
ROM_SIZE = 16_777_216


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(cond: bool, message: str) -> None:
    if not cond:
        raise RuntimeError(message)


def main() -> int:
    for path in (MAIN, CAND, BUILD, WIDTH, SINGLETON, FALSE_LEAD, TERM, COLL, FALSE_SEGPTR):
        req(path.is_file(), f"missing artifact: {path}")
    req(MAIN.stat().st_size == ROM_SIZE and sha(MAIN) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE, "candidate size drifted")
    candidate_hash = sha(CAND)
    candidate = CAND.read_bytes()
    req((sum(candidate[:-2]) & 0xFFFF) == int.from_bytes(candidate[-2:], "little"), "WonderSwan checksum invalid")

    build = load(BUILD)
    bc = build.get("counts") or {}
    req(build.get("ok") is True, "build report failed")
    req(str(((build.get("candidate") or {}).get("sha256") or "")).lower() == candidate_hash, "build hash mismatch")
    req(int(bc.get("targets", -1)) == 1979, "combined rewrite population drifted")
    req(int(bc.get("two_row_readability_records", -1)) == 1412, "two-row readability population drifted")
    req(int(bc.get("singleton_source_rewrite_records", -1)) == 567, "singleton population drifted")
    req(int(bc.get("false_lead_cleanup_records", -1)) == 264, "false-lead population drifted")
    req(int(bc.get("compact3_records", -1)) == 0, "compact3 unexpectedly used")
    req(int(bc.get("terminator_changes", -1)) == 0, "build terminator drift")
    req(int(bc.get("record_extent_changes", -1)) == 0, "build record extent drift")
    req(int(bc.get("unexpected_diff_offsets", -1)) == 0, "unexpected build diff")
    req(int(bc.get("max_after_cells", 999)) <= 20, "build target width regression")

    width = load(WIDTH)
    wp = width.get("population") or {}
    req(width.get("ok") is True, "20-cell audit failed")
    req(str(((width.get("rom") or {}).get("sha256") or "")).lower() == candidate_hash, "width audit hash mismatch")
    req(int(wp.get("records", -1)) == 15405, "width population drifted")
    req(int(wp.get("offender_records", -1)) == 0 and int(wp.get("max_line_cells", 999)) <= 20, "width offender remains")

    singleton = load(SINGLETON)
    sc = singleton.get("counts") or {}
    req(singleton.get("ok") is True, "singleton audit failed")
    req(str(((singleton.get("rom") or {}).get("sha256") or "")).lower() == candidate_hash, "singleton audit hash mismatch")
    req(int(sc.get("expected", -1)) == 567 and int(sc.get("decoded", -1)) == 567, "singleton coverage drifted")
    req(int(sc.get("failures", -1)) == 0 and int(sc.get("over_20", -1)) == 0, "singleton failure remains")
    req(int(sc.get("dense_no_spacing_17plus", -1)) == 0, "dense singleton remains")
    req(int(sc.get("max_word_cells", 999)) <= 20, "singleton word exceeds row limit")

    false_lead = load(FALSE_LEAD)
    fc = false_lead.get("counts") or {}
    req(false_lead.get("ok") is True, "false-lead recurrence audit failed")
    req(str(((false_lead.get("target") or {}).get("sha256") or "")).lower() == candidate_hash, "false-lead audit hash mismatch")
    req(int(fc.get("proven_visible_text_leads", -1)) == 264, "false-lead proof population drifted")
    req(int(fc.get("reintroduced", -1)) == 0, "visible Japanese sentence lead reintroduced")

    term = load(TERM)
    tc = term.get("counts") or {}
    req(int(tc.get("runtime_risk", -1)) == 0 and int(tc.get("separator_nul_lost", -1)) == 0, "terminator runtime risk remains")

    coll = load(COLL)
    cc = coll.get("counts") or {}
    req(coll.get("ok") is True, "speaker collision audit failed")
    req(int(cc.get("japanese_or_mixed_remaining", -1)) == 0 and int(cc.get("over_20", -1)) == 0, "speaker collision residue remains")

    seg = load(FALSE_SEGPTR)
    req(seg.get("ok") is True and int(seg.get("sites_found", -1)) == 0, "false segmented-pointer write remains")

    # Raw runtime anchors: false sentence lead gone, genuine portrait metadata retained.
    stock_base = len(candidate) - 0x800000
    a_5d01f4 = stock_base + 0x5D01F4
    a_5d7084 = stock_base + 0x5D7084
    req(candidate[a_5d01f4:a_5d01f4 + 2] == b"\xE5\x18", "5D01F4 false lead not removed")
    req(candidate[a_5d7084:a_5d7084 + 3] == bytes.fromhex("35E518"), "5D7084 genuine portrait metadata lost")

    status = {
        "schema_version": 1,
        "generated_by": "tools/verify_dialogue_readability_candidate.py",
        "ok": True,
        "status": "candidate_ready_for_runtime_validation",
        "main_unchanged": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": EXPECTED_MAIN, "size": ROM_SIZE},
        "candidate": {
            "path": "out/patch/dialogue_readability_candidate.wsc",
            "sha256": candidate_hash,
            "size": len(candidate),
            "ws_checksum": f"{int.from_bytes(candidate[-2:], 'little'):04X}",
        },
        "counts": {
            "two_row_readability_records": 1412,
            "singleton_source_rewrite_records": 567,
            "false_lead_cleanup_records": 264,
            "runtime_width_records": int(wp.get("records", 0)),
            "runtime_width_offenders": int(wp.get("offender_records", 0)),
            "singleton_dense_no_spacing_17plus": int(sc.get("dense_no_spacing_17plus", 0)),
            "singleton_max_word_cells": int(sc.get("max_word_cells", 0)),
            "false_lead_reintroduced": int(fc.get("reintroduced", 0)),
            "terminator_runtime_risk": int(tc.get("runtime_risk", 0)),
            "speaker_hidden_japanese": int(cc.get("japanese_or_mixed_remaining", 0)),
            "false_segmented_pointer_sites": int(seg.get("sites_found", 0)),
        },
        "anchors": {
            "5D01F4_payload_lead": candidate[a_5d01f4:a_5d01f4 + 4].hex().upper(),
            "5D7084_payload_lead": candidate[a_5d7084:a_5d7084 + 5].hex().upper(),
            **(singleton.get("anchors") or {}),
        },
    }
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
