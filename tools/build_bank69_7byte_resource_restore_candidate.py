#!/usr/bin/env python3
"""Build a diagnostic candidate restoring seven isolated bank69 bytes to stock.

The seven sites are inside the structurally excluded bank64-69 data/resource
region and were previously classified as unintended EXT3 single-byte writes.
This builder starts only from the current v1.0.1 main TIP, restores exactly the
seven stock bytes, refreshes the WonderSwan checksum, and pairs the candidate
with a copy of the current live SaveRAM.

Diagnostic only: promotion remains blocked pending user runtime validation.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum  # noqa: E402

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT = ROOT / "out/patch/bank69_7byte_resource_restore_candidate.wsc"
OUT_SAVE = ROOT / "sram/bank69_7byte_resource_restore_candidate.sav"
REPORT = ROOT / "out/patch/bank69_7byte_resource_restore_candidate_report.json"

EXPECTED_ORIGINAL_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_MAIN_SHA256 = "c8ee51be9c5e33dfd88e7565453ff031a931aaf4948d9cd4aee35a7ec6892e86"
EXPECTED_MAIN_SIZE = 16_777_216
EXPECTED_SAVE_SIZE = 32_768

# logical address -> (current-main byte, original-stock byte)
RESTORES = {
    0x696A7E: (0xF8, 0x7F),
    0x696B8E: (0xF7, 0x27),
    0x696C6F: (0xF8, 0x7F),
    0x696C73: (0xF8, 0x7F),
    0x696C77: (0xF8, 0x7F),
    0x696C7B: (0xF8, 0x7F),
    0x696C7F: (0xF8, 0x7F),
}


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def checksum(data: bytes | bytearray) -> str:
    return f"{int.from_bytes(bytes(data)[-2:], 'little'):04X}"


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    parent = bytes(load_rom(MAIN))
    live_save = LIVE_SAVE.read_bytes()

    if sha256(original) != EXPECTED_ORIGINAL_SHA256:
        raise RuntimeError("original ROM identity drift")
    if len(parent) != EXPECTED_MAIN_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise RuntimeError("current v1.0.1 main TIP identity drift")
    if len(live_save) != EXPECTED_SAVE_SIZE:
        raise RuntimeError("live SaveRAM missing or wrong size")

    main_before = sha256(parent)
    save_before = sha256(live_save)
    sb = stock_base(parent)
    if sb != 0x800000:
        raise RuntimeError(f"unexpected stock base: {sb:#x}")

    out = bytearray(parent)
    changes: list[dict[str, object]] = []
    for logical, (expected_current, stock_value) in RESTORES.items():
        physical = sb + logical
        original_value = original[logical]
        current_value = parent[physical]
        if original_value != stock_value:
            raise RuntimeError(
                f"original drift at {logical:06X}: {original_value:02X} != {stock_value:02X}"
            )
        if current_value != expected_current:
            raise RuntimeError(
                f"main drift at {logical:06X}: {current_value:02X} != {expected_current:02X}"
            )
        out[physical] = stock_value
        changes.append(
            {
                "logical": f"{logical >> 16:02X}:{logical & 0xFFFF:04X}",
                "physical": f"{physical:08X}",
                "before": f"{current_value:02X}",
                "after": f"{stock_value:02X}",
            }
        )

    update_ws_checksum(out)

    # Exact-delta guard: only the seven requested bytes plus the checksum bytes
    # may differ from the current main TIP.
    changed_offsets = [i for i, (a, b) in enumerate(zip(parent, out)) if a != b]
    allowed = {sb + logical for logical in RESTORES} | {len(out) - 2, len(out) - 1}
    unexpected = [i for i in changed_offsets if i not in allowed]
    if unexpected:
        raise RuntimeError(
            "unexpected candidate delta: " + ", ".join(f"{x:08X}" for x in unexpected[:20])
        )
    requested_changed = [sb + logical for logical in RESTORES if parent[sb + logical] != out[sb + logical]]
    if len(requested_changed) != len(RESTORES):
        raise RuntimeError("not all seven requested bytes changed")

    OUT.write_bytes(out)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    if sha256(MAIN.read_bytes()) != main_before:
        raise RuntimeError("main TIP changed during candidate build")
    if sha256(LIVE_SAVE.read_bytes()) != save_before:
        raise RuntimeError("live SaveRAM changed during candidate build")
    if OUT_SAVE.read_bytes() != live_save:
        raise RuntimeError("paired SaveRAM is not byte-exact to current live SaveRAM")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_bank69_7byte_resource_restore_candidate.py",
        "status": "static_verified_candidate_pending_user_runtime",
        "promotion": "blocked_pending_user_runtime_validation",
        "parent": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "sha256": main_before,
        },
        "candidate": {
            "path": "out/patch/bank69_7byte_resource_restore_candidate.wsc",
            "sha256": sha256(out),
            "size": len(out),
            "checksum": checksum(out),
        },
        "paired_saveram": {
            "path": "sram/bank69_7byte_resource_restore_candidate.sav",
            "sha256": sha256(OUT_SAVE.read_bytes()),
            "size": OUT_SAVE.stat().st_size,
            "copied_from_current_live_saveram": True,
        },
        "restores": changes,
        "delta_guard": {
            "requested_nonchecksum_byte_count": len(RESTORES),
            "actual_requested_byte_count": len(requested_changed),
            "checksum_byte_offsets": [f"{len(out)-2:08X}", f"{len(out)-1:08X}"],
            "unexpected_byte_count": len(unexpected),
        },
        "runtime_test_focus": [
            "ending/cinematic moving graphics and transitions",
            "late-game battle animations and large sprite/effect sequences",
            "look for transient tile/sprite corruption, wrong colors, seams, or progression regressions",
        ],
        "main_tip_unchanged": True,
        "live_saveram_unchanged": True,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
