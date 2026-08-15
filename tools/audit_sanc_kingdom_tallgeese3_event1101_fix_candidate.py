#!/usr/bin/env python3
"""Independent static audit for the STG15T Event Error 3000:1101 fix candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, ws_header  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CANDIDATE = ROOT / "out/patch/sanc_kingdom_tallgeese3_event1101_fix_candidate.wsc"
REPORT = ROOT / "out/patch/sanc_kingdom_tallgeese3_event1101_fix_audit.json"

STAGE_LO = 0x660000
STAGE_HI = 0x6613B2
RESTORE_SITES = (0x6609DF, 0x6609F7, 0x6609FC)
POINTER_STARTS = (0x6609DE, 0x6609F6, 0x6609FB)
EXPECTED_PTR = bytes.fromhex("FF09E600")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def logical(data: bytes, lo: int, hi: int) -> bytes:
    sb = stock_base(data)
    return data[sb + lo : sb + hi]


def diff_positions(left: bytes, right: bytes, base: int = 0) -> list[int]:
    return [base + i for i, (a, b) in enumerate(zip(left, right)) if a != b]


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    candidate = bytes(load_rom(CANDIDATE))

    sb_main = stock_base(main_rom)
    main_vs_candidate = diff_positions(main_rom, candidate)
    expected_file_sites = [sb_main + x for x in RESTORE_SITES]
    non_checksum = [x for x in main_vs_candidate if x < len(candidate) - 2]

    stage_main_diffs = diff_positions(
        logical(original, STAGE_LO, STAGE_HI), logical(main_rom, STAGE_LO, STAGE_HI), STAGE_LO
    )
    stage_candidate_diffs = diff_positions(
        logical(original, STAGE_LO, STAGE_HI), logical(candidate, STAGE_LO, STAGE_HI), STAGE_LO
    )

    checks = {
        "main_stage15t_has_only_three_known_corruptions": stage_main_diffs == list(RESTORE_SITES),
        "candidate_stage15t_matches_original_byte_for_byte": stage_candidate_diffs == [],
        "candidate_nonchecksum_delta_is_exactly_three_bytes": non_checksum == expected_file_sites,
        "candidate_restored_original_values": all(
            candidate[sb_main + x] == original[stock_base(original) + x] == 0x09 for x in RESTORE_SITES
        ),
        "all_three_far_pointers_are_66_09ff": all(
            logical(candidate, x, x + 4) == EXPECTED_PTR for x in POINTER_STARTS
        ),
        "wrong_66_10ff_target_is_tile_like": logical(candidate, 0x6610FF, 0x661107)
        == bytes.fromhex("1A1A1A1B1B1B1B1B"),
        "reported_error_1101_is_wrong_target_plus_two": 0x1101 == 0x10FF + 2,
        "checksum_valid": checksum_valid(candidate),
        "rom_size_unchanged": len(candidate) == len(main_rom),
    }

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_sanc_kingdom_tallgeese3_event1101_fix_candidate.py",
        "ok": all(checks.values()),
        "checks": checks,
        "sha256": {
            "main": sha(main_rom),
            "original": sha(original),
            "candidate": sha(candidate),
        },
        "stage15t": {
            "range": "66:0000-13B1",
            "main_vs_original_diffs": [f"{x:06X}" for x in stage_main_diffs],
            "candidate_vs_original_diffs": [f"{x:06X}" for x in stage_candidate_diffs],
        },
        "candidate_vs_main_nonchecksum_file_offsets": [f"{x:08X}" for x in non_checksum],
        "event_error_relation": {
            "reported": "3000:1101",
            "false_pointer_target": "66:10FF",
            "correct_pointer_target": "66:09FF",
            "offset_delta": 2,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
