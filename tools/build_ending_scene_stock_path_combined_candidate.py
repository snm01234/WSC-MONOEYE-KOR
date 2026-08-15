#!/usr/bin/env python3
"""Build a diagnostic with both ending-credit subsystem and renderer hooks stock.

This combines two previously independent negative tests:
1) C2: revert every finalized ending-credit Korean atlas/hook/lifecycle byte to
   the 2026-08-14 15:23 pre-ending baseline;
2) B: restore the three global renderer sites used by the intermission patch.

The purpose is to test an interaction between those two change sets.  This is
strictly diagnostic and must never be promoted directly.
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

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PRE_ENDING = ROOT / "out/patch/backup/20260814_152320_pre_ending_credits_all_prepared/monoeye_ko_expanded.wsc"
FINAL_ENDING = ROOT / "out/patch/ending_credits_galmuri11_bitmap_page21_end_restore_candidate/monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page21_end_restore_test.wsc"
OUT = ROOT / "out/patch/ending_scene_stock_path_combined_candidate.wsc"
OUT_SAVE = ROOT / "sram/ending_scene_stock_path_combined_candidate.sav"
REPORT = ROOT / "out/patch/ending_scene_stock_path_combined_candidate_report.json"

EXPECTED_MAIN_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
EXPECTED_PRE_SHA = "3012695f01cab7a12f022efe897a8fca90a244648570dd6fd2d05f036d8f807f"
EXPECTED_FINAL_SHA = "6ca50bb617b290619ebb47696aec4446fd1b7c59407e20e36726a54a122d1e0e"
ROM_SIZE = 0x1000000
CHECKSUM = {ROM_SIZE - 2, ROM_SIZE - 1}

# Expanded-ROM physical locations for logical bank 78 sites.
STOCK_RENDER_SITES = {
    0xF89C4D: bytes.fromhex("9A B5 DE 00 80"),       # lcall 8000:DEB5
    0xF8A06E: bytes.fromhex("26 89 97 00 38"),       # es:[bx+3800] = dx
    0xF8A0EB: bytes.fromhex("26 89 B7 00 38"),       # es:[bx+3800] = si
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def changed(before: bytes, after: bytes) -> set[int]:
    return {i for i, (a, b) in enumerate(zip(before, after)) if a != b}


def main() -> int:
    parent = MAIN.read_bytes()
    pre = PRE_ENDING.read_bytes()
    final = FINAL_ENDING.read_bytes()
    save = LIVE_SAVE.read_bytes()
    if (len(parent), len(pre), len(final)) != (ROM_SIZE, ROM_SIZE, ROM_SIZE):
        raise SystemExit("unexpected ROM size")
    if sha(parent) != EXPECTED_MAIN_SHA:
        raise SystemExit(f"main drifted: {sha(parent)}")
    if sha(pre) != EXPECTED_PRE_SHA or sha(final) != EXPECTED_FINAL_SHA:
        raise SystemExit("historical ending source identity drifted")

    result = bytearray(parent)
    ending_sites = changed(pre, final) - CHECKSUM
    # Current Main was proven to retain the finalized ending payload at all
    # these sites.  Restore the pre-ending value only at those exact bytes.
    bad = [i for i in ending_sites if parent[i] != final[i]]
    if bad:
        raise SystemExit(f"current main no longer matches finalized ending at {bad[0]:08X}")
    for i in ending_sites:
        result[i] = pre[i]

    # Also restore the three independent global renderer hooks to stock.
    for off, payload in STOCK_RENDER_SITES.items():
        result[off:off + len(payload)] = payload

    checksum = update_ws_checksum(result)
    out = bytes(result)
    if int.from_bytes(out[-2:], "little") != (sum(out[:-2]) & 0xFFFF):
        raise SystemExit("checksum invalid")

    OUT.write_bytes(out)
    shutil.copyfile(LIVE_SAVE, OUT_SAVE)

    diffs = changed(parent, out)
    allowed = set(ending_sites) | CHECKSUM
    for off, payload in STOCK_RENDER_SITES.items():
        allowed.update(range(off, off + len(payload)))
    leaked = sorted(diffs - allowed)
    if leaked:
        raise SystemExit(f"diff leaked: {leaked[0]:08X}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_scene_stock_path_combined_candidate.py",
        "ok": True,
        "purpose": "Test interaction: complete pre-ending subsystem + stock global renderer sites in one ROM.",
        "parent_sha256": sha(parent),
        "candidate": {
            "path": str(OUT.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(out),
            "checksum": f"{checksum:04X}",
        },
        "paired_saveram": {
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(OUT_SAVE.read_bytes()),
            "byte_exact_live": OUT_SAVE.read_bytes() == save,
        },
        "restored_ending_nonchecksum_bytes": len(ending_sites),
        "renderer_sites": {f"{off:08X}": payload.hex().upper() for off, payload in STOCK_RENDER_SITES.items()},
        "changed_bytes_vs_main_including_checksum": len(diffs),
        "main_unchanged": MAIN.read_bytes() == parent,
        "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save,
        "runtime_validation": "cold reset/replay required; Korean ending credits and intermission graphics may regress by design",
        "promotion": "blocked_diagnostic_only",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
