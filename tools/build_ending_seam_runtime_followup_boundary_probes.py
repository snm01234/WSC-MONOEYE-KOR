#!/usr/bin/env python3
"""Reconstruct exact 2026-08-09 runtime-followup intermediate ROMs for ending-seam diagnosis.

Historical facts:
- FB6629... pre-runtime-followup main is user-confirmed ending-seam clean.
- F11B11... promoted final is user-confirmed ending-seam affected.
- 3C1CFA... focused candidate and AC146F... duplicate-followup candidate can be
  reconstructed byte-exact from the historical parent/final pair.

This builder never writes the live main TIP or live SaveRAM.  It creates diagnostic
ROMs in out/patch plus byte-exact copies of the current live SaveRAM for cold replay.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import update_ws_checksum  # noqa: E402

GOOD = Path(r"D:\legacy_260814\out\patch\backup\20260809_224743_pre_runtime_measured_followup_structural\monoeye_ko_expanded.wsc")
BAD = Path(r"D:\legacy_260814\out\patch\backup\20260810_002641_pre_runtime_measured_round2_20260809\monoeye_ko_expanded.wsc")
LIVE_MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT = ROOT / "out/patch"
SRAM = ROOT / "sram"
REPORT = OUT / "ending_seam_runtime_followup_boundary_probes_report.json"

GOOD_SHA = "fb6629d89cfd0dd8f48a621af5c0175d4602f113abec5d781b54b32b14efa86d"
BAD_SHA = "f11b11c05e94e6b3007061e00586bbf74424be1e36c07571f43cb6a4584bafaf"
STAGE1_SHA = "3c1cfaf9a2e091718268489185efcfacc5735987115e98b94ec6dd3468e1b15d"
STAGE2_SHA = "ac146f120b3656caf150480428d3ee118b4e471433bc071dbfcd6d11029fd9c3"
SB = 0x800000

# logical -> payload length (terminator itself is unchanged)
RECORD_LEN = {
    0x59976D: 8,
    0x59984F: 8,
    0x59987B: 9,
    0x599977: 11,
    0x5999A6: 9,
    0x6226BE: 17,
    0x622832: 12,
    0x622848: 7,
    0x622850: 13,
    0x67AF01: 8,
    0x67C0EC: 8,
    0x693D54: 3,
    0x63E6E4: 7,
    0x63EB4A: 7,
    0x63F0BD: 7,
    0x63F483: 7,
    0x63F67C: 7,
}

# selected retired stock dictionary slot -> (logical storage address, old payload length)
SLOTS = {
    0x00B9: (0x5FCD90, 14),
    0x0173: (0x5FCEEF, 19),
    0x020A: (0x5FCF81, 23),
    0x0274: (0x5FD017, 10),
    0x0338: (0x5FE361, 14),
    0x0494: (0x5FE423, 8),
    0x0591: (0x5FE47A, 17),
    0x0714: (0x5FF03C, 24),
    0x073E: (0x5FE509, 14),
    0x08DB: (0x5FF4CE, 17),
    0x09A6: (0x5FF8B8, 14),
    0x09C9: (0x5FE690, 14),
}

FOCUSED_RECORDS = [
    0x59984F, 0x59987B, 0x599977,
    0x6226BE, 0x622832, 0x622848, 0x622850,
]
FOCUSED_SLOTS = [0x00B9, 0x0173, 0x020A, 0x0274, 0x0338, 0x0591, 0x0714, 0x073E, 0x08DB]
DUPLICATE_FOLLOWUP_RECORDS = [0x59976D, 0x5999A6]
DUPLICATE_FOLLOWUP_SLOTS = [0x0494]
STATIC_DUPLICATE_RECORDS = [0x693D54, 0x67AF01, 0x67C0EC]
STATIC_DUPLICATE_SLOTS = [0x09A6]
NATIVE_TWO_TOKEN_RECORDS = [0x63E6E4, 0x63EB4A, 0x63F0BD, 0x63F483, 0x63F67C]
NATIVE_TWO_TOKEN_SLOTS = [0x09C9]


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def ws_checksum(data: bytes | bytearray) -> str:
    return f"{int.from_bytes(data[-2:], 'little'):04X}"


def build(good: bytes, bad: bytes, records: list[int], slots: list[int]) -> bytearray:
    out = bytearray(good)
    for logical in records:
        n = RECORD_LEN[logical]
        off = SB + logical
        out[off:off + n] = bad[off:off + n]
    for index in slots:
        logical, old_len = SLOTS[index]
        off = SB + logical
        out[off:off + old_len + 1] = bad[off:off + old_len + 1]
    update_ws_checksum(out)
    return out


def emit(name: str, rom: bytearray, expected_sha: str | None, tag: str) -> dict:
    path = OUT / name
    path.write_bytes(rom)
    got = sha(rom)
    if expected_sha and got != expected_sha:
        raise RuntimeError(f"{name}: SHA mismatch {got} != {expected_sha}")
    save_path = SRAM / (path.stem + ".sav")
    shutil.copy2(LIVE_SAVE, save_path)
    return {
        "tag": tag,
        "rom": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": got,
        "checksum": ws_checksum(rom),
        "paired_saveram": str(save_path.relative_to(ROOT)).replace("\\", "/"),
        "paired_saveram_sha256": sha(save_path.read_bytes()),
    }


def main() -> int:
    good = GOOD.read_bytes()
    bad = BAD.read_bytes()
    if sha(good) != GOOD_SHA or sha(bad) != BAD_SHA:
        raise RuntimeError("historical boundary ROM identity drift")
    main_before = sha(LIVE_MAIN.read_bytes())
    save_before = sha(LIVE_SAVE.read_bytes())

    stage1 = build(good, bad, FOCUSED_RECORDS, FOCUSED_SLOTS)
    stage2 = build(
        good,
        bad,
        FOCUSED_RECORDS + DUPLICATE_FOLLOWUP_RECORDS,
        FOCUSED_SLOTS + DUPLICATE_FOLLOWUP_SLOTS,
    )
    # Diagnostic-only splits after the exact AC146 stage.  These do not claim to
    # reproduce the overwritten E3E2 intermediate; they isolate the two static
    # structural-addition families using the final promoted bytes.
    stage2_staticdup = build(
        good,
        bad,
        FOCUSED_RECORDS + DUPLICATE_FOLLOWUP_RECORDS + STATIC_DUPLICATE_RECORDS,
        FOCUSED_SLOTS + DUPLICATE_FOLLOWUP_SLOTS + STATIC_DUPLICATE_SLOTS,
    )
    stage2_native = build(
        good,
        bad,
        FOCUSED_RECORDS + DUPLICATE_FOLLOWUP_RECORDS + NATIVE_TWO_TOKEN_RECORDS,
        FOCUSED_SLOTS + DUPLICATE_FOLLOWUP_SLOTS + NATIVE_TWO_TOKEN_SLOTS,
    )
    final_reconstructed = build(
        good,
        bad,
        FOCUSED_RECORDS + DUPLICATE_FOLLOWUP_RECORDS + STATIC_DUPLICATE_RECORDS + NATIVE_TWO_TOKEN_RECORDS,
        FOCUSED_SLOTS + DUPLICATE_FOLLOWUP_SLOTS + STATIC_DUPLICATE_SLOTS + NATIVE_TWO_TOKEN_SLOTS,
    )
    if sha(final_reconstructed) != BAD_SHA or bytes(final_reconstructed) != bad:
        raise RuntimeError("full reconstruction is not byte-exact to historical bad boundary")

    rows = [
        emit("ending_seam_stage1_focused_3c1_probe.wsc", stage1, STAGE1_SHA, "exact historical focused candidate"),
        emit("ending_seam_stage2_duplicate_ac146_probe.wsc", stage2, STAGE2_SHA, "exact historical duplicate-followup candidate"),
        emit("ending_seam_stage2_plus_static_duplicates_probe.wsc", stage2_staticdup, None, "diagnostic: AC146 + 693D54/67AF01/67C0EC + slot09A6"),
        emit("ending_seam_stage2_plus_native_two_token_probe.wsc", stage2_native, None, "diagnostic: AC146 + five native-two-token repairs + slot09C9"),
    ]

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_seam_runtime_followup_boundary_probes.py",
        "ok": True,
        "historical_boundary": {
            "good": {"path": str(GOOD), "sha256": GOOD_SHA},
            "bad": {"path": str(BAD), "sha256": BAD_SHA},
            "full_reconstruction_byte_exact": True,
        },
        "chronology": [
            {"sha256": GOOD_SHA, "meaning": "pre-runtime-followup main; user reports ending seam absent"},
            {"sha256": STAGE1_SHA, "meaning": "focused 7-record candidate; exact reconstruction"},
            {"sha256": STAGE2_SHA, "meaning": "focused + 59976D/5999A6 duplicate follow-up; exact reconstruction"},
            {"sha256": BAD_SHA, "meaning": "final 17-record structural promotion; user reports ending seam present"},
        ],
        "probes": rows,
        "test_order": [
            "ending_seam_stage1_focused_3c1_probe.wsc",
            "ending_seam_stage2_duplicate_ac146_probe.wsc",
            "If AC146 is clean: ending_seam_stage2_plus_static_duplicates_probe.wsc and ending_seam_stage2_plus_native_two_token_probe.wsc",
        ],
        "notes": [
            "The overwritten E3E2 intermediate is not claimed to be reconstructed by the two category-split probes.",
            "All probes are diagnostic only and promotion is blocked.",
            "Use cold reset/replay with the paired SaveRAM; do not reuse a savestate containing old VRAM/runtime state.",
        ],
        "main_tip_unchanged": sha(LIVE_MAIN.read_bytes()) == main_before,
        "live_saveram_unchanged": sha(LIVE_SAVE.read_bytes()) == save_before,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
