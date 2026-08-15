#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from measure_aux_prefix_rule import code_units
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
FOLLOWUP = PATCH / "dialogue_runtime_followup_candidate.wsc"
CAND = PATCH / "dialogue_runtime_followup_portrait_candidate.wsc"
CAND_SAVE = ROOT / "sram/dialogue_runtime_followup_portrait_candidate.sav"
SPEC = ROOT / "data/dialogue_runtime_followup_ko.json"
PORTRAIT_REPORT = PATCH / "dialogue_runtime_followup_portrait_report.json"
WIDTH = PATCH / "dialogue_runtime_followup_portrait_width_audit.json"
TERM = PATCH / "dialogue_runtime_followup_portrait_terminator_audit.json"
FALSE = PATCH / "dialogue_runtime_followup_portrait_false_segptr.json"
COLL = PATCH / "dialogue_runtime_followup_portrait_collision.json"
SHORT = ROOT / "out/script/battle_dialogue_short_fixed_metadata_targets.csv"
VOICE = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
SAFE = PATCH / "backup/20260807_123035_pre_residual_voice_ko/runtime_text_id_scenario_voice_proven_candidate.wsc"
FALSE_A = ROOT / "data/aux_false_prefix_cleanup_ko.json"
FALSE_B = ROOT / "data/battle_dialogue_prefix_cleanup_ko.json"
TBL = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT = PATCH / "dialogue_runtime_followup_portrait_final_status.json"
EXPECTED_MAIN = "8e80bc7e722652b9c6b31282c272966ae92f9d3c82975344c577556bf5b9145a"
EXPECTED_FOLLOWUP = "a7b6e622a767b2e894ad6e8b683319a8a6d2089052b3551b8561c7510369d03e"
EXPECTED_CAND = "4e4cdcabdf88ddfa1c14f792ebf97e796e0e7cfa9a72f712599aa38cc955e49d"
EXPECTED_SAFE = "5919fbf7bb25d692ca0593a63961fe34148a69ba9c0ef7b5a04b153b1c7414c4"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
SCREEN_PREFIXES = {
    0x5D014E: bytes.fromhex("02F191"),
    0x5D0211: bytes.fromhex("02F191"),
    0x5D03ED: bytes.fromhex("02F191"),
}
BATTLE_FOLLOWUP_PREFIX = {"5D84F4": bytes.fromhex("40")}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def first_unit(payload: bytes) -> bytes:
    units = code_units(payload)
    if not units:
        return b""
    off, size = units[0]
    return payload[:size] if off == 0 and size > 0 else b""


def false_prefixes() -> set[int]:
    out: set[int] = set()
    for path in (FALSE_A, FALSE_B):
        doc = load(path)
        rows = doc.get("targets") or ([doc.get("record")] if doc.get("record") else [])
        for row in rows:
            if row and row.get("abs"):
                out.add(int(str(row["abs"]), 16))
    return out


def render_record(rom: bytes, dictionary, tbl: Tbl, address: str, forced_prefix: bytes | None = None) -> str:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + int(address, 16), max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable record {address}")
    payload = bytes(got[0])
    if forced_prefix is not None:
        req(payload.startswith(forced_prefix), f"forced prefix missing at {address}")
        body = payload[len(forced_prefix):]
    else:
        _, body, _ = split_prefix_body(payload)
    return dictionary.expand(body, tbl).rstrip("\u3000 \t")


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    for path in (MAIN, MAIN_SAVE, FOLLOWUP, CAND, CAND_SAVE, SPEC, PORTRAIT_REPORT, WIDTH, TERM, FALSE, COLL, SHORT, VOICE, SAFE):
        req(path.is_file(), f"missing artifact: {path}")
    req(MAIN.stat().st_size == ROM_SIZE and sha(MAIN) == EXPECTED_MAIN, "main TIP changed before promotion")
    req(FOLLOWUP.stat().st_size == ROM_SIZE and sha(FOLLOWUP) == EXPECTED_FOLLOWUP, "follow-up parent drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "portrait candidate drifted")
    req(SAFE.stat().st_size == ROM_SIZE and sha(SAFE) == EXPECTED_SAFE, "safe structure baseline drifted")
    req(MAIN_SAVE.stat().st_size == SAVE_SIZE and CAND_SAVE.stat().st_size == SAVE_SIZE, "SaveRAM size wrong")
    req(MAIN_SAVE.read_bytes() == CAND_SAVE.read_bytes(), "candidate SaveRAM differs from live main SaveRAM")
    cand = CAND.read_bytes()
    req(checksum_ok(cand), "candidate checksum invalid")

    portrait = load(PORTRAIT_REPORT)
    pc = portrait.get("counts") or {}
    req(portrait.get("ok") is True, "portrait build report failed")
    req(str(((portrait.get("candidate") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "portrait report hash mismatch")
    req(int(pc.get("targets", -1)) == 358, "portrait target count drifted")
    req(int(pc.get("non_token", -1)) == 0, "unhandled long token-backed portrait record remains")
    req(int(pc.get("unexpected_diff_offsets", -1)) == 0, "portrait unexpected diff exists")
    anchor = portrait.get("screenshot_anchor_5D7084") or {}
    req(anchor.get("broken_visible_if_first_byte_consumed") == "こやナ", "screenshot signature not reproduced")
    req(anchor.get("metadata_hex") == "35", "5D7084 metadata drifted")
    req(anchor.get("render") == "아직　무대가　안　갖춰졌다는　건가……", "5D7084 Korean body drifted")

    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(cand, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    spec = load(SPEC)
    targets = spec.get("targets") or []
    req(len(targets) == 16, "follow-up target count drifted")
    followup_failures = []
    for row in targets:
        address = str(row["abs"]).upper()
        text = render_record(cand, dictionary, tbl, address, BATTLE_FOLLOWUP_PREFIX.get(address))
        if text != str(row["after"]):
            followup_failures.append({"abs": address, "expected": row["after"], "got": text})
    req(not followup_failures, f"follow-up target drift: {followup_failures[:5]}")

    # Recompute the battle structure population independently. After this candidate,
    # there must be no safe-proven missing-prefix record with body capacity >=4
    # whose live body is a token-only translation. Short/unproven records remain quarantined.
    safe = SAFE.read_bytes()
    sb = stock_base(cand)
    false = false_prefixes()
    with VOICE.open(encoding="utf-8-sig", newline="") as handle:
        voice_rows = [r for r in csv.DictReader(handle) if r.get("bank") in {"5D", "5E"}]
    remaining_long = []
    short_quarantine = 0
    safe_unproven = 0
    exact = 0
    for row in voice_rows:
        logical = int(row["record_start"], 16)
        original = bytes.fromhex(row["original_payload_hex"])
        plen = len(original)
        at = sb + logical
        if logical in false:
            continue
        metadata = first_unit(original)
        prefix = SCREEN_PREFIXES.get(logical, metadata)
        safe_exact = safe[at:at + plen].startswith(prefix) and safe[at + plen] == 0
        if not safe_exact:
            safe_unproven += 1
            continue
        live = cand[at:at + plen]
        if live.startswith(prefix):
            exact += 1
            continue
        body_capacity = plen - len(prefix)
        if body_capacity < 4:
            short_quarantine += 1
            continue
        token_only = (
            (live[:2] == b"\xE5\x18" and len(live) >= 4 and all(b == 1 for b in live[4:]))
            or (len(live) >= 2 and 0xF0 <= live[0] <= 0xFF and all(b == 1 for b in live[2:]))
        )
        if token_only:
            remaining_long.append(row["record_start"])
    req(not remaining_long, f"safe long portrait losses remain: {remaining_long[:20]}")

    # Previously independently proven short/fixed metadata repairs must remain exact.
    with SHORT.open(encoding="utf-8-sig", newline="") as handle:
        short_rows = list(csv.DictReader(handle))
    req(len(short_rows) == 104, "proven short metadata population drifted")
    short_fail = []
    for row in short_rows:
        logical = int(row["abs"], 16)
        before_len = len(bytes.fromhex(row["current_payload_hex"]))
        got = cand[sb + logical:sb + logical + before_len]
        metadata = bytes.fromhex(row["metadata_hex"])
        if not got.startswith(metadata):
            short_fail.append(row["abs"])
    req(not short_fail, f"proven short portrait repair regressed: {short_fail[:20]}")

    width = load(WIDTH)
    wp = width.get("population") or {}
    req(width.get("ok") is True, "runtime-aware 20-cell audit failed")
    req(str(((width.get("rom") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "width hash mismatch")
    req(int(wp.get("records", -1)) == 15405 and int(wp.get("offender_records", -1)) == 0 and int(wp.get("max_line_cells", 999)) <= 20, "20-cell regression")

    term = load(TERM)
    tc = term.get("counts") or {}
    req(int(tc.get("runtime_risk", -1)) == 0 and int(tc.get("separator_nul_lost", -1)) == 0, "P2 terminator regression")
    false_report = load(FALSE)
    req(false_report.get("ok") is True and int(false_report.get("sites_found", -1)) == 0, "false segmented pointer regression")
    coll = load(COLL)
    cc = coll.get("counts") or {}
    req(coll.get("ok") is True and int(cc.get("japanese_or_mixed_remaining", -1)) == 0 and int(cc.get("over_20", -1)) == 0, "speaker collision regression")

    status = {
        "schema_version": 1,
        "ok": True,
        "status": "candidate_ready_for_direct_promotion",
        "main_before": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": EXPECTED_MAIN, "size": ROM_SIZE},
        "candidate": {"path": "out/patch/dialogue_runtime_followup_portrait_candidate.wsc", "sha256": EXPECTED_CAND, "size": ROM_SIZE, "ws_checksum": f"{int.from_bytes(cand[-2:], 'little'):04X}"},
        "counts": {
            "followup_quality_targets": 16,
            "portrait_structure_targets": 358,
            "safe_long_token_backed_missing_after": len(remaining_long),
            "proven_short_metadata_rows_preserved": len(short_rows),
            "short_or_fixed_unproven_quarantine": short_quarantine,
            "safe_structure_unproven_rows": safe_unproven,
            "runtime_width_records": int(wp.get("records", 0)),
            "runtime_width_offenders": int(wp.get("offender_records", 0)),
            "runtime_width_max_cells": int(wp.get("max_line_cells", 0)),
        },
        "anchor_5D7084": anchor,
        "checks": {
            "main_unchanged_before_promotion": True,
            "candidate_checksum_valid": True,
            "followup_16_exact": True,
            "portrait_358_exact": True,
            "screenshot_koyana_root_cause_closed": True,
            "safe_long_token_backed_missing_zero": True,
            "proven_short_metadata_104_preserved": True,
            "runtime_20cell_zero_offenders": True,
            "p2_runtime_risk_zero": True,
            "false_segmented_pointer_zero": True,
            "speaker_collision_hidden_japanese_zero": True,
            "live_saveram_unchanged": True,
        },
    }
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
