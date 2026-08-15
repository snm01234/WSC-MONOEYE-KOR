#!/usr/bin/env python3
"""Analyze why New Game softlocks on primary-hook PoC."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIG = (ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc").read_bytes()
BASE = 0x820
COUNT = 96


def glyph_record(rom: bytes, idx: int) -> bytes:
    return bytes(rom[0x400440 + idx * 16 : 0x400440 + idx * 16 + 16])


def main() -> None:
    nonempty = []
    empty = []
    for i in range(COUNT):
        idx = BASE + i
        g = glyph_record(ORIG, idx)
        (nonempty if any(g) else empty).append(idx)

    print(f"E740 window indices {BASE:04X}..{BASE + COUNT - 1:04X}")
    print(f"  nonempty stock glyphs: {len(nonempty)} / {COUNT}")
    print(f"  empty stock glyphs:    {len(empty)} / {COUNT}")
    print(f"  empty idxs: {[f'{x:04X}' for x in empty]}")

    specials = [0x816, 0x7A5, 0x7A9, 0x7FF]
    print("\nNearby special/sentinel indices:")
    for idx in specials:
        g = glyph_record(ORIG, idx)
        print(f"  {idx:04X} nonempty={any(g)} head={g[:8].hex()}")

    variants = {
        "07_script_only": ROOT / "out/patch/bisect/07_script_only_stage1_ok.wsc",
        "08_secondary_hook": ROOT / "out/patch/bisect/08_hook_pad_poc.wsc",
        "font_only": ROOT / "out/patch/rom_font_only.wsc",
        "09_primary_hook": ROOT / "out/patch/bisect/09_primary_hook_pad_poc.wsc",
        "seed": ROOT / "out/patch/monoeye_ko_seed.wsc",
    }
    print("\n=== Bisect matrix ===")
    print(f"{'name':22} {'40':>6} {'5F':>6} {'60':>6} {'7A':>6} primary secondary NG?")
    for name, path in variants.items():
        if not path.exists():
            print(f"{name:22} MISSING")
            continue
        rom = path.read_bytes()
        d40 = sum(a != b for a, b in zip(ORIG[0x400000:0x410000], rom[0x400000:0x410000]))
        d5f = sum(a != b for a, b in zip(ORIG[0x5F0000:0x600000], rom[0x5F0000:0x600000]))
        d60 = sum(a != b for a, b in zip(ORIG[0x600000:0x610000], rom[0x600000:0x610000]))
        d7a = sum(a != b for a, b in zip(ORIG[0x7A0000:0x7B0000], rom[0x7A0000:0x7B0000]))
        prim = rom[0x7A0521] == 0xE9
        sec = rom[0x7A0618] == 0xE9
        # Known from prior BizHawk notes
        ng = {
            "07_script_only": "OK(stage1)",
            "08_secondary_hook": "OK(prog, no Hangul)",
            "font_only": "likely OK",
            "09_primary_hook": "FAIL(user)",
            "seed": "FAIL(user)",
        }.get(name, "?")
        print(f"{name:22} {d40:6d} {d5f:6d} {d60:6d} {d7a:6d} {str(prim):7} {str(sec):9} {ng}")

    print(
        """
=== Root cause ===
Primary display path 7A:0521 loads glyphs via RAM 1A6E indices.
Our hook remaps indices 0x820-0x87F (codes E740+) to bank40 padding.

Stock ROM already has nearly-full nonempty glyphs in that window (UI/shared).
Previous overwrite experiments proved: touching those pixels shows Hangul in
dialogue then breaks progression. Remapping *reads* of the same indices is
the same collision for UI: New Game transition still asks for those indices,
gets Hangul padding instead of UI tiles, and softlocks.

08 hooked only secondary 7A:0618 (not the 1A6E blitter) -> progression OK,
Hangul invisible. 09 hooked the real blitter -> Hangul path armed, New Game fails.
"""
    )


if __name__ == "__main__":
    main()
