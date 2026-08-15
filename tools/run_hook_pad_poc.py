#!/usr/bin/env python3
"""Build provenance-marked Hangul font hook + seed translation PoC (dual pad)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
OUT = ROOT / "out" / "patch"
SEED = ROOT / "data" / "translations_seed_hook96.json"
FULL = ROOT / "out" / "script" / "translations_full.json"
MARKER = "E3DB"
# Physical dual-pad capacity (bank40 96 + bank3F 931).
PADDING_MAX = 1027


def run(args: list[str]) -> None:
    print("=" * 60)
    print(" ".join(args))
    print("=" * 60)
    rc = subprocess.call([sys.executable, *args], cwd=ROOT)
    if rc != 0:
        sys.exit(rc)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    translations = str(FULL if FULL.exists() else SEED)
    run(
        [
            str(TOOLS / "build_hangul_font.py"),
            "--padding-store",
            "--padding-marker-code",
            MARKER,
            "--padding-max",
            str(PADDING_MAX),
            "--by-frequency",
            "--seed-priority",
            str(SEED),
            "--translations",
            translations,
        ]
    )
    mapping = json.loads((OUT / "hangul_char_map.json").read_text(encoding="utf-8"))
    pad = mapping.get("padding_store") or {}
    print(
        f"Font map: count={pad.get('count')} "
        f"pad1={pad.get('pad1_slots')} pad2={pad.get('pad2_slots')}"
    )
    run(
        [
            str(TOOLS / "patch_font_hangul_hook.py"),
            "--rom",
            str(OUT / "rom_font_only.wsc"),
            "--out",
            str(OUT / "rom_font_hooked.wsc"),
            "--map",
            str(OUT / "hangul_char_map.json"),
        ]
    )
    run(
        [
            str(TOOLS / "apply_translations.py"),
            "--rom",
            str(OUT / "rom_font_hooked.wsc"),
            "--tbl",
            str(OUT / "hangul_patch.tbl"),
            "--translations",
            str(SEED),
            "--hangul-marker",
            MARKER,
            "--out",
            str(OUT),
        ]
    )
    src = OUT / "monoeye_ko_seed.wsc"
    dst = OUT / "bisect" / "10_marked_ui_isolation_poc.wsc"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    # Promote dual-pad baseline for safe-unit expansion.
    marked = OUT / "monoeye_ko_marked.wsc"
    marked.write_bytes(src.read_bytes())
    run(
        [
            str(TOOLS / "verify_marked_hangul_hook.py"),
            "--rom",
            str(dst),
        ]
    )
    print(f"\nPoC snapshot: {dst}")
    print(f"Marked baseline: {marked}")
    print(
        "Dual-pad: slots0-95 @40:F9F8, slots96+ @3F:C5CE (CX=2F00 hypothesis). "
        "Emulator check deferred."
    )


if __name__ == "__main__":
    main()
