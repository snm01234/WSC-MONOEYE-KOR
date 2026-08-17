#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import load_rom, stock_base  # noqa: E402

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SHEET = ROOT / "out/script/translation_sheet.csv"
OUT = ROOT / "out/patch/stage22t_uso_katejina_event_control_audit.json"

ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
MAIN_SHA = "f68b3261beecc32047d17952e36bc2b891cd5d66410f9fc9293487571a0fc8e2"

ERROR_SEGMENT = 0x3000
ERROR_OFFSET = 36067  # 0x8CE3
CONVERSATION_LO = 0x638C6B
CONVERSATION_HI = 0x638FE3  # exclusive; Frost-brothers dialogue starts after this scene
SUSPECT_RECORD = 0x638CD5
SUSPECT_ORIGINAL = bytes.fromhex("173418F1912B1D")
SUSPECT_MAIN = bytes.fromhex("173418E51852F1")
SUSPECT_TERMINATOR = 0x638CDC
ERROR_LOGICAL = 0x630000 | ERROR_OFFSET

# Historical fail-closed allowlist for banks 64-67. These are fixed event-name
# or label strings, not executable event body bytes. Banks 68-69 must be exact.
INTENTIONAL_EVENT_DATA_RANGES: dict[tuple[int, int], str] = {
    (0x643200, 0x643202): "覚醒 name token",
    (0x64500E, 0x645010): "覚醒 control-name token",
    (0x645019, 0x64501B): "覚醒 name token",
    (0x64501D, 0x64501F): "覚醒 turn-name token",
    (0x64B2B9, 0x64B2BB): "覚醒 name token",
    (0x651F16, 0x651F18): "激突戦宙域 fixed label token",
    (0x6649C4, 0x6649C6): "覚醒 name token",
    (0x66A145, 0x66A147): "ポゥ fixed event-name token",
    (0x66BB3B, 0x66BB3D): "ポゥ fixed event-name token",
    (0x66E004, 0x66E006): "ポゥ fixed event-name token",
    (0x66F18A, 0x66F18C): "覚醒 name token",
    (0x673E06, 0x673E08): "覚醒 name token",
    (0x673EA0, 0x673EA2): "防御 fixed event-name token",
    (0x67AF01, 0x67AF09): "ゲ－ムオ－バ－ event-name string",
    (0x67C0EC, 0x67C0F4): "ゲ－ムオ－バ－ event-name string",
    (0x67EBFB, 0x67EBFD): "防御 fixed event-name token",
    (0x67EC02, 0x67EC04): "防御 fixed event-name token",
    (0x67EC83, 0x67EC85): "防御 fixed event-name token",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def logical(data: bytes, sb: int, lo: int, hi: int) -> bytes:
    return data[sb + lo : sb + hi]


def diff_runs(a: bytes, b: bytes, base: int) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise RuntimeError("diff length mismatch")
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            out.append((base + start, base + i))
            start = None
    if start is not None:
        out.append((base + start, base + len(a)))
    return out


def sheet_rows() -> list[dict[str, Any]]:
    csv.field_size_limit(10_000_000)
    rows: list[dict[str, Any]] = []
    with SHEET.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                address = int(row["abs"], 16)
            except (KeyError, ValueError):
                continue
            if not (CONVERSATION_LO <= address < CONVERSATION_HI):
                continue
            if row.get("kind") != "dialogue":
                continue
            prefix = bytes.fromhex(row.get("prefix_hex", ""))
            body = bytes.fromhex(row.get("body_hex", ""))
            rows.append({
                "address": address,
                "prefix": prefix,
                "body": body,
                "body_start": address + len(prefix),
                "body_end": address + len(prefix) + len(body),
                "jp": row.get("jp", ""),
                "ko_catalog": row.get("ko", ""),
            })
    return rows


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    main_rom = bytes(load_rom(MAIN))
    if sha(original) != ORIGINAL_SHA:
        raise SystemExit("original ROM SHA drifted")
    if sha(main_rom) != MAIN_SHA:
        raise SystemExit(f"main TIP SHA drifted: {sha(main_rom)}")
    sbo = stock_base(original)
    sbm = stock_base(main_rom)

    # Full executable/event-data bank comparison.
    bank_summary: dict[str, Any] = {}
    unknown_event_runs: list[tuple[int, int]] = []
    observed_allowlisted: list[dict[str, Any]] = []
    for bank in range(0x64, 0x6A):
        lo, hi = bank << 16, (bank + 1) << 16
        runs = diff_runs(logical(original, sbo, lo, hi), logical(main_rom, sbm, lo, hi), lo)
        for run in runs:
            reason = INTENTIONAL_EVENT_DATA_RANGES.get(run)
            if reason is None:
                unknown_event_runs.append(run)
            else:
                observed_allowlisted.append({
                    "start": f"{run[0]:06X}",
                    "end_exclusive": f"{run[1]:06X}",
                    "length": run[1] - run[0],
                    "reason": reason,
                })
        bank_summary[f"{bank:02X}"] = {
            "diff_runs": len(runs),
            "diff_bytes": sum(b - a for a, b in runs),
            "runs": [{"start": f"{a:06X}", "end_exclusive": f"{b:06X}"} for a, b in runs],
        }

    # Conversation window: only dialogue bodies may differ. Prefixes, NULs and
    # all inter-record event/control bytes must remain byte-exact to Original.
    rows = sheet_rows()
    mask = bytearray(CONVERSATION_HI - CONVERSATION_LO)
    terminator_failures: list[dict[str, Any]] = []
    prefix_failures: list[dict[str, Any]] = []
    for row in rows:
        a = int(row["address"])
        prefix = bytes(row["prefix"])
        bs, be = int(row["body_start"]), int(row["body_end"])
        if prefix and logical(original, sbo, a, bs) != logical(main_rom, sbm, a, bs):
            prefix_failures.append({"address": f"{a:06X}"})
        for x in range(max(bs, CONVERSATION_LO), min(be, CONVERSATION_HI)):
            mask[x - CONVERSATION_LO] = 1
        if be < CONVERSATION_HI:
            if original[sbo + be] != 0 or main_rom[sbm + be] != 0:
                terminator_failures.append({
                    "address": f"{a:06X}",
                    "terminator": f"{be:06X}",
                    "original": f"{original[sbo + be]:02X}",
                    "main": f"{main_rom[sbm + be]:02X}",
                })

    nonbody_diff_positions = [
        x for x in range(CONVERSATION_LO, CONVERSATION_HI)
        if original[sbo + x] != main_rom[sbm + x] and not mask[x - CONVERSATION_LO]
    ]
    nonbody_runs: list[tuple[int, int]] = []
    if nonbody_diff_positions:
        start = prev = nonbody_diff_positions[0]
        for x in nonbody_diff_positions[1:]:
            if x == prev + 1:
                prev = x
            else:
                nonbody_runs.append((start, prev + 1))
                start = prev = x
        nonbody_runs.append((start, prev + 1))

    error_control = logical(original, sbo, ERROR_LOGICAL, ERROR_LOGICAL + 13)
    error_control_main = logical(main_rom, sbm, ERROR_LOGICAL, ERROR_LOGICAL + 13)
    suspect_orig = logical(original, sbo, SUSPECT_RECORD, SUSPECT_RECORD + len(SUSPECT_ORIGINAL))
    suspect_main = logical(main_rom, sbm, SUSPECT_RECORD, SUSPECT_RECORD + len(SUSPECT_MAIN))

    checks = {
        "event_banks_64_69_no_unknown_diffs": not unknown_event_runs,
        "event_banks_68_69_byte_exact": bank_summary["68"]["diff_bytes"] == 0 and bank_summary["69"]["diff_bytes"] == 0,
        "conversation_41_dialogue_rows_found": len(rows) == 41,
        "conversation_non_dialogue_bytes_byte_exact": not nonbody_diff_positions,
        "conversation_prefixes_byte_exact": not prefix_failures,
        "conversation_terminators_preserved": not terminator_failures,
        "reported_8ce3_control_bytes_byte_exact": error_control == error_control_main,
        "reported_8ce3_begins_17_28": error_control.startswith(bytes.fromhex("1728")),
        "suspect_638cd5_original_shape": suspect_orig == SUSPECT_ORIGINAL,
        "suspect_638cd5_current_shape": suspect_main == SUSPECT_MAIN,
        "suspect_638cd5_terminator_preserved": original[sbo + SUSPECT_TERMINATOR] == 0 and main_rom[sbm + SUSPECT_TERMINATOR] == 0,
        "suspect_638cd5_current_is_direct_e518": suspect_main[3:5] == bytes.fromhex("E518"),
    }
    ok = all(checks.values())

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_stage22t_uso_katejina_event_control.py",
        "ok": ok,
        "status": "control_bytes_clean_probable_preceding_ext3_parser_hazard" if ok else "audit_failed",
        "input": {
            "original_sha256": sha(original),
            "main_sha256": sha(main_rom),
        },
        "runtime_observation": {
            "stage": "STAGE22t 終末への序曲",
            "scene": "웃소-카테지나 대화 이벤트",
            "event_error_decimal": [ERROR_SEGMENT, ERROR_OFFSET],
            "event_error_hex": [f"{ERROR_SEGMENT:04X}", f"{ERROR_OFFSET:04X}"],
            "low_offset_correlated_logical": f"{ERROR_LOGICAL:06X}",
            "correlation_note": "project precedent interprets Event Error as 3000:offset; 0x8CE3 coincides exactly with the live 63:8CE3 control row in this scene",
        },
        "event_data_banks_64_69": {
            "banks": bank_summary,
            "allowlisted_diff_runs": observed_allowlisted,
            "unknown_diff_runs": [{"start": f"{a:06X}", "end_exclusive": f"{b:06X}"} for a, b in unknown_event_runs],
            "conclusion": "no executable/event-body byte change found outside the historical fixed-name/label allowlist",
        },
        "conversation_window": {
            "start": f"{CONVERSATION_LO:06X}",
            "end_exclusive": f"{CONVERSATION_HI:06X}",
            "dialogue_rows": len(rows),
            "non_dialogue_diff_count": len(nonbody_diff_positions),
            "non_dialogue_diff_runs": [{"start": f"{a:06X}", "end_exclusive": f"{b:06X}"} for a, b in nonbody_runs],
            "prefix_failures": prefix_failures,
            "terminator_failures": terminator_failures,
        },
        "error_site": {
            "logical": f"{ERROR_LOGICAL:06X}",
            "original_hex_13": error_control.hex().upper(),
            "main_hex_13": error_control_main.hex().upper(),
            "byte_exact": error_control == error_control_main,
        },
        "strongest_static_hypothesis": {
            "record": f"{SUSPECT_RECORD:06X}",
            "original_payload": suspect_orig.hex().upper(),
            "main_payload": suspect_main.hex().upper(),
            "original_grammar": "17 34 18 + native stock body F1 91 2B 1D",
            "main_grammar": "17 34 18 + direct E5 18 ext3 portal 52 F1",
            "rendered_intent": "……어？",
            "terminator": f"{SUSPECT_TERMINATOR:06X}",
            "reason": "direct E5 18 scenario-first record immediately precedes the unchanged 08/17 event-control chain containing 63:8CE3; this matches prior runtime-proven ext3/control-boundary failure families",
            "confidence": "strong static hypothesis; runtime fix not yet tested",
        },
        "checks": checks,
        "conclusion": "The reported event-control bytes are byte-identical to Original. The leading structural suspect is parser state from the translated 63:8CD5 direct-E518 dialogue body, not a modified 63:8CE3 control opcode.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": ok,
        "main_sha256": sha(main_rom),
        "event_unknown_runs": len(unknown_event_runs),
        "conversation_rows": len(rows),
        "conversation_nonbody_diffs": len(nonbody_diff_positions),
        "terminator_failures": len(terminator_failures),
        "error_site": f"{ERROR_LOGICAL:06X}",
        "error_site_byte_exact": error_control == error_control_main,
        "suspect": f"{SUSPECT_RECORD:06X}",
        "report": str(OUT.relative_to(ROOT)).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
