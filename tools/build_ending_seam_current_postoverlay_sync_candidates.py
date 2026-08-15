#!/usr/bin/env python3
"""Build current-main ending seam candidates after entry-sync alone proved insufficient.

Runtime evidence:
- Historical native-two-token bad bundle becomes clean with a frame-boundary sync at 7E:D3AA.
- Current main remains bad with the same D3AA sync.
- Between historical bad and current main, the only changed bytes in the relevant
  D3AA..D523 pre-update window are the page20 overlay call at 7E:D4F1-D4F6.

This builder creates:
1) a diagnostic combination: D3AA entry sync + D4F1 overlay bypass;
2) the preferred structural candidate: keep the overlay, but synchronize at
   7E:D51C immediately before the D523 animation-update loop, after all page20
   CPU work and event setup;
3) the same late sync on the historical bad bundle as a cross-generation check.

No dialogue/dictionary bytes are changed. Diagnostic only until runtime-tested.
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

CURRENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
HIST_BAD = ROOT / "out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = ROOT / "out/patch/ending_seam_current_postoverlay_sync_report.json"

CURRENT_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
HIST_BAD_SHA = "1e2b9b23c8f8d82e50c0f11c142e5ee655e090d18178107960661fa94d52e31b"

ENTRY_SITE = 0x7ED3AA
ENTRY_EXPECT = bytes.fromhex("9A83910080")
OVERLAY_SITE = 0x7ED4F1
OVERLAY_MAIN = bytes.fromhex("9A24FF00F090")
OVERLAY_STOCK = bytes.fromhex("B90000BB0000")
LATE_SITE = 0x7ED51C
LATE_EXPECT = bytes.fromhex("26810F2000")  # or word ptr es:[bx],0020
CAVE = 0x7EFD83
CAVE_SEG = 0xE000

# D3AA wrapper from the already runtime-tested historical sync candidate.
ENTRY_WRAPPER = bytes.fromhex("9C50E4023C9072FAE4023C9073FA589D9A83910080CB")
ENTRY_CALL = bytes([0x9A, CAVE & 0xFF, (CAVE >> 8) & 0xFF, CAVE_SEG & 0xFF, (CAVE_SEG >> 8) & 0xFF])

# D51C late wrapper:
#   push ax
#   or word ptr es:[bx],0020       ; original D51C operation
# wait_vblank: in al,02 / cmp al,90 / jb wait_vblank
# wait_frame:  in al,02 / cmp al,90 / jae wait_frame
#   pop ax
#   retf
# Flags are intentionally not preserved: the stock OR flags are not consumed;
# D521 jumps to D528 where TEST overwrites them before the conditional branch.
LATE_WRAPPER = bytes.fromhex("5026810F2000E4023C9072FAE4023C9073FA58CB")
LATE_CALL = bytes([0x9A, CAVE & 0xFF, (CAVE >> 8) & 0xFF, CAVE_SEG & 0xFF, (CAVE_SEG >> 8) & 0xFF])


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def emit(parent_path: Path, out_stem: str, *, mode: str, expected_sha: str) -> dict:
    parent = bytes(load_rom(parent_path))
    if sha(parent) != expected_sha:
        raise RuntimeError(f"parent SHA drift: {parent_path} {sha(parent)}")
    sb = stock_base(parent)
    out = bytearray(parent)

    cave = sb + CAVE
    if parent[cave:cave+max(len(ENTRY_WRAPPER), len(LATE_WRAPPER))] != b"\xFF" * max(len(ENTRY_WRAPPER), len(LATE_WRAPPER)):
        raise RuntimeError(f"cave occupied in {parent_path}")

    allowed: set[int] = set()
    details: dict[str, object] = {"mode": mode}

    if mode == "entry_sync_plus_overlay_bypass":
        es = sb + ENTRY_SITE
        os = sb + OVERLAY_SITE
        if parent[es:es+5] != ENTRY_EXPECT:
            raise RuntimeError(f"entry site drift: {parent[es:es+5].hex().upper()}")
        if parent[os:os+6] != OVERLAY_MAIN:
            raise RuntimeError(f"overlay site drift: {parent[os:os+6].hex().upper()}")
        out[es:es+5] = ENTRY_CALL
        out[os:os+6] = OVERLAY_STOCK
        out[cave:cave+len(ENTRY_WRAPPER)] = ENTRY_WRAPPER
        allowed |= set(range(es, es+5)) | set(range(os, os+6)) | set(range(cave, cave+len(ENTRY_WRAPPER)))
        details.update({
            "entry_site": "7E:D3AA",
            "overlay_site": "7E:D4F1",
            "overlay_note": "page20 Korean lower overlay intentionally absent in this diagnostic",
            "sync_point": "scene entry before current-only page20 CPU blit",
        })
    elif mode == "late_preupdate_sync":
        ls = sb + LATE_SITE
        if parent[ls:ls+5] != LATE_EXPECT:
            raise RuntimeError(f"late site drift: {parent[ls:ls+5].hex().upper()}")
        out[ls:ls+5] = LATE_CALL
        out[cave:cave+len(LATE_WRAPPER)] = LATE_WRAPPER
        allowed |= set(range(ls, ls+5)) | set(range(cave, cave+len(LATE_WRAPPER)))
        details.update({
            "hook_site": "7E:D51C",
            "hook_before": LATE_EXPECT.hex().upper(),
            "hook_after": LATE_CALL.hex().upper(),
            "sync_point": "after page20 overlay/event setup, immediately before D523 animation-update loop",
            "page20_overlay_preserved": parent[sb+OVERLAY_SITE:sb+OVERLAY_SITE+6] == OVERLAY_MAIN,
        })
    else:
        raise RuntimeError(mode)

    update_ws_checksum(out)
    out_rom = ROOT / "out/patch" / f"{out_stem}.wsc"
    out_save = ROOT / "sram" / f"{out_stem}.sav"
    out_rom.write_bytes(out)
    shutil.copyfile(LIVE_SAVE, out_save)

    diffs = [i for i,(a,b) in enumerate(zip(parent,out)) if a != b]
    noncs = [i for i in diffs if i not in (len(out)-2, len(out)-1)]
    unexpected = sorted(set(noncs) - allowed)
    if unexpected:
        raise RuntimeError(f"unexpected diff scope: {unexpected[:16]}")

    return {
        **details,
        "parent": str(parent_path.relative_to(ROOT)).replace("\\", "/"),
        "parent_sha256": sha(parent),
        "rom": str(out_rom.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha(out),
        "checksum": f"{out[-2] | (out[-1] << 8):04X}",
        "saveram": str(out_save.relative_to(ROOT)).replace("\\", "/"),
        "saveram_sha256": sha(out_save.read_bytes()),
        "cave": "7E:FD83",
        "wrapper": (ENTRY_WRAPPER if mode == "entry_sync_plus_overlay_bypass" else LATE_WRAPPER).hex().upper(),
        "nonchecksum_changed_bytes": len(noncs),
    }


def main() -> int:
    main_before = sha(CURRENT.read_bytes())
    save_before = sha(LIVE_SAVE.read_bytes())
    rows = [
        emit(CURRENT, "ending_seam_current_entry_sync_plus_overlay_bypass_probe",
             mode="entry_sync_plus_overlay_bypass", expected_sha=CURRENT_SHA),
        emit(CURRENT, "ending_seam_current_preupdate_frame_sync_candidate",
             mode="late_preupdate_sync", expected_sha=CURRENT_SHA),
        emit(HIST_BAD, "ending_seam_hist_bad_preupdate_frame_sync_probe",
             mode="late_preupdate_sync", expected_sha=HIST_BAD_SHA),
    ]
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_seam_current_postoverlay_sync_candidates.py",
        "ok": True,
        "evidence": {
            "hist_bad_entry_sync_user_result": "clean",
            "current_main_entry_sync_user_result": "bad",
            "pre_D523_diff_hist_vs_current": [
                {"logical": "7E:D4F1-D4F6", "hist": OVERLAY_STOCK.hex().upper(), "current": OVERLAY_MAIN.hex().upper(), "meaning": "current-only page20 Korean CPU overlay"}
            ],
            "interpretation": "entry timing and current-only post-entry overlay timing are additive; late sync is placed after both and before first animation update",
        },
        "candidates": rows,
        "recommended_order": [
            "ending_seam_current_preupdate_frame_sync_candidate.wsc",
            "ending_seam_current_entry_sync_plus_overlay_bypass_probe.wsc only if causal decomposition is desired",
            "ending_seam_hist_bad_preupdate_frame_sync_probe.wsc optional cross-generation robustness check",
        ],
        "main_tip_unchanged": sha(CURRENT.read_bytes()) == main_before,
        "live_saveram_unchanged": sha(LIVE_SAVE.read_bytes()) == save_before,
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
