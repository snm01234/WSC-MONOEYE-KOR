#!/usr/bin/env python3
"""Build a narrow diagnostic that bypasses only the page-20 Korean credit overlay.

The supplied Original/main RetroArch states prove the reported middle-band issue is
not FG scroll: both states have FGXScroll=0, while BG map 0x3000 rows 9-11 in main
contain a one-entry-advanced stream.  The stock D4xx loader is byte-exact between
Original and main except for the page-20 Korean overlay call planted at 7E:D4F1.

This diagnostic restores only the six stock bytes at D4F1:
    mov cx,0 ; mov bx,0
instead of:
    lcall F000:FF24 ; nop

That temporarily removes the Korean page-20 bottom bar but leaves every other
translation, atlas record, cinematic hook, graphics blob and SaveRAM untouched.
It is intentionally a runtime-cause probe, not a promotion candidate.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/ending_scene_page20_overlay_bypass_candidate.wsc"
OUT_SAVE = ROOT / "sram/ending_scene_page20_overlay_bypass_candidate.sav"
REPORT = ROOT / "out/patch/ending_scene_page20_overlay_bypass_candidate_report.json"

EXPECTED_MAIN_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
SITE = 0x7ED4F1
MAIN_BYTES = bytes.fromhex("9A24FF00F090")
STOCK_BYTES = bytes.fromhex("B90000BB0000")
SHARED_OVERLAY = 0x7FFF18
SHARED_OVERLAY_END = 0x7FFFCC
PAGE21_SITE = 0x7ED5C0
PAGE21_LEN = 4


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(a):
        if a[i] == b[i]:
            i += 1
            continue
        s = i
        while i < len(a) and a[i] != b[i]:
            i += 1
        out.append((s, i))
    return out


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = SAVE.read_bytes()
    if sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if sha(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("Original identity drifted")
    if len(save) != 32768:
        raise BuildError("SaveRAM size drifted")

    sb = stock_base(parent)
    osb = stock_base(original)
    if parent[sb + SITE : sb + SITE + 6] != MAIN_BYTES:
        raise BuildError("current page20 overlay site drifted")
    if original[osb + SITE : osb + SITE + 6] != STOCK_BYTES:
        raise BuildError("Original D4F1 bytes drifted")

    shared_before = parent[sb + SHARED_OVERLAY : sb + SHARED_OVERLAY_END]
    page21_before = parent[sb + PAGE21_SITE : sb + PAGE21_SITE + PAGE21_LEN]

    candidate = bytearray(parent)
    candidate[sb + SITE : sb + SITE + 6] = STOCK_BYTES
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)

    if result[sb + SITE : sb + SITE + 6] != STOCK_BYTES:
        raise BuildError("D4F1 restore failed")
    if result[sb + SHARED_OVERLAY : sb + SHARED_OVERLAY_END] != shared_before:
        raise BuildError("shared overlay changed")
    if result[sb + PAGE21_SITE : sb + PAGE21_SITE + PAGE21_LEN] != page21_before:
        raise BuildError("page21 hook changed")
    if int.from_bytes(result[-2:], "little") != (sum(result[:-2]) & 0xFFFF):
        raise BuildError("checksum invalid")

    runs = diff_runs(parent, result)
    allowed_logic = (sb + SITE, sb + SITE + 6)
    for lo, hi in runs:
        if lo >= len(result) - 2:
            continue
        if not (allowed_logic[0] <= lo and hi <= allowed_logic[1]):
            raise BuildError(f"unexpected diff {lo:08X}-{hi:08X}")

    OUT.write_bytes(result)
    shutil.copy2(SAVE, OUT_SAVE)
    if MAIN.read_bytes() != parent or SAVE.read_bytes() != save:
        raise BuildError("live main/SaveRAM mutated")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_scene_page20_overlay_bypass_candidate.py",
        "ok": True,
        "status": "diagnostic_runtime_validation_required",
        "parent_main_sha256": sha(parent),
        "candidate": {
            "path": "out/patch/ending_scene_page20_overlay_bypass_candidate.wsc",
            "sha256": sha(result),
            "checksum": f"{checksum:04X}",
        },
        "paired_saveram": {
            "path": "sram/ending_scene_page20_overlay_bypass_candidate.sav",
            "sha256": sha(save),
            "byte_exact_live": True,
        },
        "state_evidence": {
            "original_and_main_FGXScroll": "00 / 00",
            "bg_map_region": "0x3000 rows 9-11",
            "alignment": "77 of 79 comparable cells satisfy main[i] == original[i+1]",
            "sprite_phase": "Original tiles 085/086 vs main 087/088",
            "interpretation": "runtime resource/animation phase difference, not an X-scroll register error",
        },
        "change": {
            "logical": "7E:D4F1-D4F6",
            "before_hex": MAIN_BYTES.hex().upper(),
            "after_hex": STOCK_BYTES.hex().upper(),
            "before": "lcall F000:FF24 page20 Korean CPU overlay; nop",
            "after": "stock mov cx,0; mov bx,0",
            "temporary_visual_tradeoff": "page20 Korean bottom credit bar is intentionally absent in this diagnostic",
        },
        "preserved": {
            "all_other_translation_changes": True,
            "shared_overlay_code": True,
            "page21_hook": True,
            "graphics_blobs": True,
            "tilemap_assets": True,
            "dialogue": True,
            "live_main": True,
            "live_saveram": True,
        },
        "diff": {
            "changed_runs": [
                {"start": f"{a:08X}", "end_exclusive": f"{b:08X}", "length": b-a}
                for a, b in runs
            ]
        },
        "runtime_gate": (
            "Cold-reset with the paired SaveRAM, replay the same ending scene, and check only whether "
            "the middle cinematic band aligns with Original. Ignore the intentionally missing Korean page20 bar."
        ),
        "promotion": "blocked_diagnostic_only",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
