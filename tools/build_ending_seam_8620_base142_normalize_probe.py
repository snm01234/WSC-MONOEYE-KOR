#!/usr/bin/env python3
"""Diagnostic: normalize resource-8620 object tile base 0x0142 -> 0x0140.

The supplied ending states show the band-overlapping resource 3000:8620 object
with +0A=0140 in Original and +0A=0142 in current Main.  Earlier frame-sync
probes at D3AA/D51C/D5C0 are too late if the wrong base is already fixed when
7C:0F65 creates the object.

This probe changes no dialogue, dictionary, ending overlay, resource data, or
renderer code.  It hooks the common 7C:0F65 setup immediately before 91C9 and
only changes CX when the requested tile base is exactly 0142.  All other bases
and behavior are byte-equivalent to current Main.

Diagnostic only.  Never promote this conditional compensation as the final fix;
if it cures the seam, trace and synchronize the upstream caller that produced
0142 instead of 0140.
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

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT = ROOT / "out/patch/ending_seam_current_8620_base142_to140_probe.wsc"
OUT_SAVE = ROOT / "sram/ending_seam_current_8620_base142_to140_probe.sav"
REPORT = ROOT / "out/patch/ending_seam_current_8620_base142_to140_probe_report.json"

SITE_LOGICAL = 0x7C1060
SITE_EXPECT = bytes.fromhex("8B4EFABB0100")  # mov cx,[bp-6] ; mov bx,1
CAVE_LOGICAL = 0x7CFF77
CAVE_SEG = 0xC000
# mov cx,[bp-6]
# cmp cx,0142
# jne keep
# mov cx,0140
# keep: mov bx,1
# retf
WRAPPER = bytes.fromhex("8B4EFA81F942017503B94001BB0100CB")
PATCH = bytes([0x9A, CAVE_LOGICAL & 0xFF, (CAVE_LOGICAL >> 8) & 0xFF,
               CAVE_SEG & 0xFF, (CAVE_SEG >> 8) & 0xFF, 0x90])


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def main() -> int:
    parent = bytes(load_rom(PARENT))
    save_before = sha(LIVE_SAVE.read_bytes())
    main_before = sha(parent)
    sb = stock_base(parent)
    site = sb + SITE_LOGICAL
    cave = sb + CAVE_LOGICAL
    if parent[site:site + len(SITE_EXPECT)] != SITE_EXPECT:
        raise RuntimeError(f"site drift: {parent[site:site+len(SITE_EXPECT)].hex().upper()}")
    if parent[cave:cave + len(WRAPPER)] != b"\xFF" * len(WRAPPER):
        raise RuntimeError("diagnostic cave occupied")

    out = bytearray(parent)
    out[site:site + len(PATCH)] = PATCH
    out[cave:cave + len(WRAPPER)] = WRAPPER
    update_ws_checksum(out)

    diffs = [i for i,(a,b) in enumerate(zip(parent,out)) if a != b]
    noncs = [i for i in diffs if i not in (len(out)-2, len(out)-1)]
    allowed = set(range(site, site+len(PATCH))) | set(range(cave, cave+len(WRAPPER)))
    unexpected = sorted(set(noncs) - allowed)
    if unexpected:
        raise RuntimeError(f"unexpected diff scope: {unexpected[:8]}")

    OUT.write_bytes(out)
    shutil.copyfile(LIVE_SAVE, OUT_SAVE)
    rep = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_seam_8620_base142_normalize_probe.py",
        "ok": True,
        "parent": str(PARENT.relative_to(ROOT)).replace("\\","/"),
        "parent_sha256": main_before,
        "rom": str(OUT.relative_to(ROOT)).replace("\\","/"),
        "sha256": sha(out),
        "checksum": f"{out[-2] | (out[-1] << 8):04X}",
        "saveram": str(OUT_SAVE.relative_to(ROOT)).replace("\\","/"),
        "saveram_sha256": sha(OUT_SAVE.read_bytes()),
        "hook": {
            "logical_site": "7C:1060",
            "before": SITE_EXPECT.hex().upper(),
            "after": PATCH.hex().upper(),
            "cave": "7C:FF77",
            "wrapper": WRAPPER.hex().upper(),
            "condition": "requested resource-8620 tile base CX == 0142",
            "action": "replace with 0140 before 91C9; all other bases unchanged",
        },
        "dialogue_changes": 0,
        "dictionary_changes": 0,
        "ending_overlay_changes": 0,
        "resource_8620_changes": 0,
        "main_tip_unchanged": sha(PARENT.read_bytes()) == main_before,
        "live_saveram_unchanged": sha(LIVE_SAVE.read_bytes()) == save_before,
        "promotion": "blocked_diagnostic_only",
        "interpretation": {
            "clean": "0142 initial tile base is causal; trace upstream producer of 0142 and fix there",
            "bad": "0142 is correlated state only; continue tracing actual writer of BG rows 9-11",
        },
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
