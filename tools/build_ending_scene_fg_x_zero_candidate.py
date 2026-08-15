#!/usr/bin/env python3
"""Build a narrowly-scoped ending-scene foreground X-scroll reset candidate.

The user-provided Original/main screenshots are pixel-identical for y=0..71,
while y=80..95 in current main is exactly the Original image shifted left by
8 pixels (one 8x8 tile).  The affected ending renderer at 7E:D4A5 resets only
BG X/Y (ports 10h/11h); it does not make FG X deterministic.  Preserve the
existing BG resets and use their already-zero AL to additionally reset FG X
(port 12h) without changing code size.

Current:
    xor al,al ; out 10h,al ; xor al,al ; out 11h,al
Candidate:
    xor al,al ; out 10h,al ; out 11h,al ; out 12h,al

No graphics blob, dialogue, tilemap, credit atlas, SaveRAM, or other ending
hook is modified.  Main TIP is never overwritten by this builder.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/ending_scene_fg_x_zero_candidate.wsc"
OUT_SAVE = ROOT / "sram/ending_scene_fg_x_zero_candidate.sav"
REPORT = ROOT / "out/patch/ending_scene_fg_x_zero_candidate_report.json"

EXPECTED_MAIN_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

SCROLL_BLOCK = 0x7ED4A5
PATCH_SITE = 0x7ED4A9
CURRENT_BLOCK = bytes.fromhex("32C0E61032C0E611")
ORIGINAL_BLOCK = CURRENT_BLOCK
PATCH_BEFORE = bytes.fromhex("32C0E611")
PATCH_AFTER = bytes.fromhex("E611E612")
# Preserve the page-20 Korean ending overlay hook already present immediately later.
PAGE20_HOOK = 0x7ED4F1
EXPECTED_PAGE20_HOOK = bytes.fromhex("9A24FF00F090")
# A separate ending entry already demonstrates the engine's full zero-scroll
# initialization; keep it byte-exact as a structural reference.
FULL_RESET = 0x7ED7D0
EXPECTED_FULL_RESET = bytes.fromhex("32C0E61032C0E61132C0E61232C0E613")


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def checksum_info(rom: bytes) -> dict[str, Any]:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {
        "stored": f"{stored:04X}",
        "computed": f"{computed:04X}",
        "valid": stored == computed,
    }


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise BuildError("ROM length changed")
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(a):
        if a[i] == b[i]:
            i += 1
            continue
        start = i
        while i < len(a) and a[i] != b[i]:
            i += 1
        out.append((start, i))
    return out


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    lo, hi = run
    cursor = lo
    for a, b in sorted(allowed):
        if b <= cursor:
            continue
        if a > cursor:
            return False
        cursor = max(cursor, b)
        if cursor >= hi:
            return True
    return cursor >= hi


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"current main identity drifted: {sha(parent)}")
    if sha(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("Original ROM identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")

    sb = stock_base(parent)
    osb = stock_base(original)
    if bytes(parent[sb + SCROLL_BLOCK : sb + SCROLL_BLOCK + len(CURRENT_BLOCK)]) != CURRENT_BLOCK:
        raise BuildError("current 7E:D4A5 scroll block drifted")
    if bytes(original[osb + SCROLL_BLOCK : osb + SCROLL_BLOCK + len(ORIGINAL_BLOCK)]) != ORIGINAL_BLOCK:
        raise BuildError("Original 7E:D4A5 scroll block drifted")
    if bytes(parent[sb + PATCH_SITE : sb + PATCH_SITE + len(PATCH_BEFORE)]) != PATCH_BEFORE:
        raise BuildError("patch site drifted")
    if bytes(parent[sb + PAGE20_HOOK : sb + PAGE20_HOOK + len(EXPECTED_PAGE20_HOOK)]) != EXPECTED_PAGE20_HOOK:
        raise BuildError("existing page20 hook drifted")
    if bytes(parent[sb + FULL_RESET : sb + FULL_RESET + len(EXPECTED_FULL_RESET)]) != EXPECTED_FULL_RESET:
        raise BuildError("full four-register reset reference drifted")

    candidate = bytearray(parent)
    file_site = sb + PATCH_SITE
    candidate[file_site : file_site + len(PATCH_AFTER)] = PATCH_AFTER
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)

    expected_block = bytes.fromhex("32C0E610E611E612")
    if bytes(result[sb + SCROLL_BLOCK : sb + SCROLL_BLOCK + len(expected_block)]) != expected_block:
        raise BuildError("candidate scroll block rewrite failed")
    if bytes(result[sb + PAGE20_HOOK : sb + PAGE20_HOOK + len(EXPECTED_PAGE20_HOOK)]) != EXPECTED_PAGE20_HOOK:
        raise BuildError("page20 hook changed unexpectedly")
    if bytes(result[sb + FULL_RESET : sb + FULL_RESET + len(EXPECTED_FULL_RESET)]) != EXPECTED_FULL_RESET:
        raise BuildError("full-reset reference changed unexpectedly")

    allowed = [
        (file_site, file_site + len(PATCH_AFTER)),
        (len(result) - 2, len(result)),
    ]
    runs = diff_runs(parent, result)
    unexpected = [r for r in runs if not covered(r, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected}")
    if not checksum_info(result)["valid"]:
        raise BuildError("candidate checksum invalid")

    OUT.write_bytes(result)
    shutil.copy2(SAVE, OUT_SAVE)
    if MAIN.read_bytes() != parent:
        raise BuildError("builder mutated live main")
    if SAVE.read_bytes() != save:
        raise BuildError("builder mutated live SaveRAM")
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("paired SaveRAM copy mismatch")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_scene_fg_x_zero_candidate.py",
        "ok": True,
        "status": "static_verified_pending_user_runtime_validation",
        "parent": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "sha256": EXPECTED_MAIN_SHA,
        },
        "candidate": {
            "path": "out/patch/ending_scene_fg_x_zero_candidate.wsc",
            "sha256": sha(result),
            "checksum": f"{checksum:04X}",
        },
        "paired_saveram": {
            "path": "sram/ending_scene_fg_x_zero_candidate.sav",
            "sha256": sha(save),
            "byte_exact_live": True,
        },
        "screenshot_evidence": {
            "original_vs_main_y_0_71": "pixel-exact",
            "main_y_80_95_relation": "main[x] == original[x+8] for all comparable pixels",
            "measured_horizontal_error_px": -8,
            "tile_width_px": 8,
            "interpretation": "lower/middle foreground band is one tile left while upper/background band is aligned",
        },
        "change": {
            "logical": f"{PATCH_SITE:06X}",
            "before_hex": PATCH_BEFORE.hex().upper(),
            "after_hex": PATCH_AFTER.hex().upper(),
            "full_block_before": CURRENT_BLOCK.hex().upper(),
            "full_block_after": expected_block.hex().upper(),
            "effect": "preserve BG X/Y zero and additionally set foreground X scroll port 12h to zero",
            "code_size_changed": False,
        },
        "guards": {
            "original_same_bg_reset_sequence": True,
            "page20_overlay_hook_byte_exact": True,
            "full_reset_reference_byte_exact": True,
            "graphics_assets_unchanged": True,
            "dialogue_unchanged": True,
            "tilemaps_unchanged": True,
            "main_unchanged": True,
            "live_saveram_unchanged": True,
        },
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(b - a for a, b in runs),
            "unexpected_runs": 0,
            "runs_detail": [
                {
                    "start": f"{a:08X}",
                    "end_exclusive": f"{b:08X}",
                    "length": b - a,
                }
                for a, b in runs
            ],
        },
        "runtime_gate": "re-enter the ending scene from paired SaveRAM/cold reset; confirm the y=72..95 middle graphic is no longer shifted left by one tile",
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
