#!/usr/bin/env python3
"""Build a minimal STG15T (Sanc Kingdom) Event Error 3000:1101 fix.

Runtime report:
- current main TIP
- STG15T / ガラスの王国 (Sanc Kingdom)
- entering battle with Tallgeese III can show Event Error 12288 / 4353
  (= 0x3000 : 0x1101)

Static root cause:
The current main differs from the Japanese original in the complete STG15T event
block (66:0000-13B1) at exactly three bytes: 66:09DF, 66:09F7, 66:09FC.
They are the high byte of three far pointers that should be 66:09FF but were
changed by the historical FF09 -> FF10 false replacement:

    66:09DE  FF 09 E6 00  -> current FF 10 E6 00
    66:09F6  FF 09 E6 00  -> current FF 10 E6 00
    66:09FB  FF 09 E6 00  -> current FF 10 E6 00

66:09FF is an event/control stream. 66:10FF is tile-like data. The reported
failure offset 0x1101 is exactly two bytes after the false 0x10FF target, which
matches the interpreter entering non-event data and failing immediately.

This builder restores only those three bytes from the original ROM, updates the
WonderSwan checksum, and emits a candidate plus a cloned SaveRAM. The main TIP
and live SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT_ROM = PATCH / "sanc_kingdom_tallgeese3_event1101_fix_candidate.wsc"
OUT_SAVE = ROOT / "sram/sanc_kingdom_tallgeese3_event1101_fix_candidate.sav"
OUT_REPORT = PATCH / "sanc_kingdom_tallgeese3_event1101_fix_report.json"

ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768
MAIN_SHA256 = "d7543ad4a62d9e7a9687583e85005dc4ca137e6fa62238eb70e58492248985c9"
ORIGINAL_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

STAGE15T_START = 0x660000
STAGE15T_END = 0x6613B2  # exclusive; name table/header begins at 66:13B2
RESTORE_SITES = (0x6609DF, 0x6609F7, 0x6609FC)
POINTER_STARTS = (0x6609DE, 0x6609F6, 0x6609FB)
GOOD_TARGET = 0x6609FF
BAD_TARGET = 0x6610FF
ERROR_SEGMENT = 0x3000
ERROR_OFFSET = 0x1101


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data).upper()}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def logical_slice(data: bytes | bytearray, lo: int, hi: int) -> bytes:
    sb = stock_base(data)
    return bytes(data[sb + lo : sb + hi])


def diff_positions(left: bytes, right: bytes, base: int = 0) -> list[int]:
    if len(left) != len(right):
        raise BuildError("diff input length mismatch")
    return [base + i for i, (a, b) in enumerate(zip(left, right)) if a != b]


def diff_runs(left: bytes, right: bytes) -> list[tuple[int, int]]:
    if len(left) != len(right):
        raise BuildError("ROM size changed")
    rows: list[tuple[int, int]] = []
    start: int | None = None
    for i, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = i
        elif a == b and start is not None:
            rows.append((start, i))
            start = None
    if start is not None:
        rows.append((start, len(left)))
    return rows


def covered(run: tuple[int, int], allowed: Iterable[tuple[int, int]]) -> bool:
    lo, hi = run
    return any(a <= lo and hi <= b for a, b in allowed)


def main() -> int:
    main_before = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = MAIN_SAVE.read_bytes()

    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA256:
        raise BuildError("current main TIP identity drifted")
    if len(original) != ORIGINAL_SIZE or sha(original) != ORIGINAL_SHA256:
        raise BuildError("original ROM identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    stage_original = logical_slice(original, STAGE15T_START, STAGE15T_END)
    stage_main = logical_slice(main_before, STAGE15T_START, STAGE15T_END)
    stage_diffs_before = diff_positions(stage_original, stage_main, STAGE15T_START)
    if stage_diffs_before != list(RESTORE_SITES):
        raise BuildError(
            "STG15T diff set drifted: " + ", ".join(f"{x:06X}" for x in stage_diffs_before)
        )

    sb_main = stock_base(main_before)
    sb_original = stock_base(original)
    candidate = bytearray(main_before)
    records: list[dict[str, Any]] = []

    for logical in RESTORE_SITES:
        src = sb_original + logical
        dst = sb_main + logical
        original_byte = original[src]
        current_byte = candidate[dst]
        if original_byte != 0x09 or current_byte != 0x10:
            raise BuildError(
                f"{logical:06X}: expected original/main 09/10, got {original_byte:02X}/{current_byte:02X}"
            )
        candidate[dst] = original_byte
        records.append(
            {
                "logical": f"{logical:06X}",
                "before": f"{current_byte:02X}",
                "after": f"{original_byte:02X}",
                "role": "high byte of STG15T far pointer 66:10FF -> 66:09FF",
            }
        )

    checksum = update_ws_checksum(candidate)
    out = bytes(candidate)

    expected_pointer = bytes.fromhex("FF09E600")
    pointer_checks = {
        f"{logical:06X}": logical_slice(out, logical, logical + 4).hex().upper()
        for logical in POINTER_STARTS
    }
    stage_after = logical_slice(out, STAGE15T_START, STAGE15T_END)
    stage_diffs_after = diff_positions(stage_original, stage_after, STAGE15T_START)

    good_target = logical_slice(out, GOOD_TARGET, GOOD_TARGET + 24)
    bad_target = logical_slice(out, BAD_TARGET, BAD_TARGET + 24)
    error_probe = logical_slice(out, 0x660000 + ERROR_OFFSET, 0x660000 + ERROR_OFFSET + 8)

    runs = diff_runs(main_before, out)
    allowed = [(sb_main + logical, sb_main + logical + 1) for logical in RESTORE_SITES]
    allowed.append((len(out) - 2, len(out)))
    outside = [run for run in runs if not covered(run, allowed)]

    checks = {
        "stage15t_before_diff_exactly_three_false_pointer_bytes": stage_diffs_before == list(RESTORE_SITES),
        "three_bytes_restored_to_original_09": all(
            out[sb_main + logical] == 0x09 for logical in RESTORE_SITES
        ),
        "three_far_pointers_now_target_66_09ff": all(
            logical_slice(out, logical, logical + 4) == expected_pointer
            for logical in POINTER_STARTS
        ),
        "stage15t_event_block_byte_identical_to_original_after_fix": not stage_diffs_after,
        "good_target_is_event_like_not_tile_run": good_target[:9] == bytes.fromhex("08000015190D0AE600"),
        "bad_target_is_tile_like": bad_target[:8] == bytes.fromhex("1A1A1A1B1B1B1B1B"),
        "reported_1101_lands_two_bytes_after_bad_10ff": ERROR_OFFSET == (BAD_TARGET & 0xFFFF) + 2,
        "candidate_diffs_bounded_to_three_bytes_plus_checksum": not outside,
        "checksum_valid": checksum_valid(out),
        "main_tip_unchanged": bytes(load_rom(MAIN)) == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "outside": outside}, ensure_ascii=False))

    atomic_bytes(OUT_ROM, out)
    atomic_bytes(OUT_SAVE, save_before)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_sanc_kingdom_tallgeese3_event1101_fix_candidate.py",
        "ok": True,
        "published": False,
        "status": "static_root_cause_proven_candidate_ready_for_runtime_test",
        "runtime_observation": {
            "stage": "STG15T ガラスの王国 / Sanc Kingdom",
            "trigger": "Tallgeese III battle",
            "event_error_decimal": [12288, 4353],
            "event_error_hex_segment_offset": [f"{ERROR_SEGMENT:04X}", f"{ERROR_OFFSET:04X}"],
        },
        "diagnosis": {
            "stage15t_original_vs_main_diff_count": len(stage_diffs_before),
            "stage15t_original_vs_main_diffs": [f"{x:06X}" for x in stage_diffs_before],
            "cause": "historical FF09 -> FF10 false replacement corrupted three live STG15T far pointers",
            "wrong_target": "66:10FF",
            "correct_target": "66:09FF",
            "decisive_relation": "reported offset 0x1101 == wrong target 0x10FF + 2",
            "good_target_hex": good_target.hex().upper(),
            "bad_target_hex": bad_target.hex().upper(),
            "error_probe_66_1101_hex": error_probe.hex().upper(),
        },
        "input": {
            "main_tip": identity(MAIN, main_before),
            "original_rom": identity(ORIGINAL, original),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "output": {
            "rom": identity(OUT_ROM, out),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{checksum:04X}",
            "changed_bytes_vs_main_including_checksum": sum(hi - lo for lo, hi in runs),
            "diff_runs_vs_main": [
                {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}", "length": hi - lo}
                for lo, hi in runs
            ],
        },
        "records": records,
        "pointer_checks": pointer_checks,
        "checks": checks,
        "promotion": "blocked_pending_user_runtime_verification",
        "test_protocol": [
            "Load sanc_kingdom_tallgeese3_event1101_fix_candidate.wsc with the paired SaveRAM.",
            "Reproduce the same STG15T/Sanc Kingdom battle using Tallgeese III.",
            "Confirm Event Error 12288 / 4353 no longer appears and battle/event progression continues.",
            "If confirmed, promote these same three byte restores to the main TIP; no other event-bank bytes are required for this fix.",
        ],
    }
    atomic_json(OUT_REPORT, report)

    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["output"]["rom"],
                "save": report["output"]["save"],
                "checksum": report["output"]["checksum"],
                "stage15t_diffs_before": report["diagnosis"]["stage15t_original_vs_main_diffs"],
                "stage15t_diffs_after": [f"{x:06X}" for x in stage_diffs_after],
                "report": rel(OUT_REPORT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
