#!/usr/bin/env python3
"""Build scene-local frame-boundary synchronization probes for the ending seam.

Evidence before this builder:
- Historical AC146 Stage2 is seam-clean.
- Historical native-two-token full bundle is seam-bad.
- Every proper subset of the five native changes is seam-clean.
- Adding one invisible marker OR one empty dictionary recursion to the known-bad
  bundle makes it seam-clean while visible text and dialogue payloads stay the same.
- Cross-loading a clean ending savestate into the known-bad ROM keeps the ending
  band byte-identical for subsequent frames, so the bad state is accumulated before
  entering the ending scene rather than generated from corrupt ending assets.

This candidate therefore does not touch dialogue/dictionary data.  It replaces the
existing scene-entry object reset call at 7E:D3AA with a far call to a private
same-bank wrapper.  The wrapper waits for LCD_LINE (port 02h) to enter VBlank
(line >= 144) and then wrap back into the visible frame (line < 144), restores AX
and FLAGS, executes the original 8000:9183 object reset, and RETF's.

Diagnostic only; never promote without runtime validation.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import load_rom, stock_base, update_ws_checksum

HIST_BAD = ROOT / "out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc"
CURRENT_MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = ROOT / "out/patch/ending_seam_frame_boundary_sync_candidates_report.json"

HIST_BAD_SHA = "1e2b9b23c8f8d82e50c0f11c142e5ee655e090d18178107960661fa94d52e31b"
SITE_LOGICAL = 0x7ED3AA
SITE_EXPECT = bytes.fromhex("9A83910080")  # lcall 8000:9183
CAVE_LOGICAL = 0x7EFD83
CAVE_SEG = 0xE000

# pushf; push ax
# wait_vblank: in al,02; cmp al,90; jb wait_vblank
# wait_frame:  in al,02; cmp al,90; jae wait_frame
# pop ax; popf; lcall 8000:9183; retf
WRAPPER = bytes.fromhex("9C50E4023C9072FAE4023C9073FA589D9A83910080CB")
PATCH_CALL = bytes([0x9A, CAVE_LOGICAL & 0xFF, (CAVE_LOGICAL >> 8) & 0xFF, CAVE_SEG & 0xFF, (CAVE_SEG >> 8) & 0xFF])


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def build(parent_path: Path, out_stem: str, expected_sha: str | None = None) -> dict:
    parent = bytes(load_rom(parent_path))
    if expected_sha and sha(parent) != expected_sha:
        raise RuntimeError(f"parent SHA drift {parent_path}: {sha(parent)}")
    sb = stock_base(parent)
    site = sb + SITE_LOGICAL
    cave = sb + CAVE_LOGICAL
    if parent[site:site+len(SITE_EXPECT)] != SITE_EXPECT:
        raise RuntimeError(f"scene entry site drift in {parent_path}: {parent[site:site+5].hex().upper()}")
    if parent[cave:cave+len(WRAPPER)] != b"\xFF" * len(WRAPPER):
        raise RuntimeError(f"sync cave occupied in {parent_path}")

    out = bytearray(parent)
    out[site:site+5] = PATCH_CALL
    out[cave:cave+len(WRAPPER)] = WRAPPER
    update_ws_checksum(out)

    out_rom = ROOT / "out/patch" / f"{out_stem}.wsc"
    out_save = ROOT / "sram" / f"{out_stem}.sav"
    out_rom.write_bytes(out)
    shutil.copyfile(LIVE_SAVE, out_save)

    diffs = [i for i,(a,b) in enumerate(zip(parent,out)) if a != b]
    noncs = [i for i in diffs if i not in (len(out)-2,len(out)-1)]
    allowed = set(range(site,site+5)) | set(range(cave,cave+len(WRAPPER)))
    if set(noncs) - allowed:
        raise RuntimeError(f"unexpected diff scope: {sorted(set(noncs)-allowed)[:8]}")

    return {
        "parent": str(parent_path.relative_to(ROOT)).replace("\\","/"),
        "parent_sha256": sha(parent),
        "rom": str(out_rom.relative_to(ROOT)).replace("\\","/"),
        "sha256": sha(out),
        "checksum": f"{out[-2] | (out[-1]<<8):04X}",
        "saveram": str(out_save.relative_to(ROOT)).replace("\\","/"),
        "saveram_sha256": sha(out_save.read_bytes()),
        "site": "7E:D3AA",
        "site_before": SITE_EXPECT.hex().upper(),
        "site_after": PATCH_CALL.hex().upper(),
        "cave": "7E:FD83",
        "wrapper": WRAPPER.hex().upper(),
        "wrapper_bytes": len(WRAPPER),
        "nonchecksum_changed_bytes": len(noncs),
    }


def main() -> int:
    main_before = sha(CURRENT_MAIN.read_bytes())
    save_before = sha(LIVE_SAVE.read_bytes())
    rows = [
        build(HIST_BAD, "ending_seam_hist_bad_frame_sync_entry_probe", HIST_BAD_SHA),
        build(CURRENT_MAIN, "ending_seam_current_main_frame_sync_entry_candidate"),
    ]
    rep = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_seam_frame_boundary_sync_candidates.py",
        "ok": True,
        "method": {
            "scene_entry_hook": "7E:D3AA original lcall 8000:9183 -> E000:FD83 wrapper",
            "sync": "poll LCD_LINE port 02h until >=144 (VBlank), then until <144 (new visible frame), then execute original object reset",
            "dialogue_dictionary_changes": 0,
            "intent": "remove dependence of ending scene start on parser/main-loop timing accumulated before entry",
        },
        "candidates": rows,
        "main_tip_unchanged": sha(CURRENT_MAIN.read_bytes()) == main_before,
        "live_saveram_unchanged": sha(LIVE_SAVE.read_bytes()) == save_before,
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
