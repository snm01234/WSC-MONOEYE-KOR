#!/usr/bin/env python3
"""Run the Korean seed-patch pipeline in order."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
from translation_source_policy import assert_translation_source_allowed

STEPS = [
    (["extract_script.py"], "1/5 Extract dialogue DB"),
    (["build_hangul_font.py", "--padding-store", "--padding-marker-code", "E3DB", "--padding-max", "1027", "--by-frequency", "--translations", "out/script/translations_full.json", "--seed-priority", "data/translations_seed_hook96.json"], "2/5 Build Hangul font + TBL"),
    (["patch_font_hangul_hook.py", "--rom", "out/patch/rom_font_only.wsc", "--out", "out/patch/rom_font_hooked.wsc", "--map", "out/patch/hangul_char_map.json"], "2.5/5 Patch Font Hook"),
    (["apply_translations.py", "--rom", "out/patch/rom_font_hooked.wsc", "--tbl", "out/patch/hangul_patch.tbl", "--translations", "data/translations_seed_hook96.json", "--hangul-marker", "E3DB", "--out", "out/patch"], "3-4/5 Encode & reinsert seed lines"),
    (["verify_marked_hangul_hook.py", "--rom", "out/patch/monoeye_ko_seed.wsc"], "5/5 Verify round-trip"),
]


def main() -> None:
    assert_translation_source_allowed(
        ROOT / "out/script/translations_full.json",
        role="legacy seed-pipeline font input",
    )
    for args_list, label in STEPS:
        print("=" * 60)
        print(label)
        print("=" * 60)
        cmd = [sys.executable, str(TOOLS / args_list[0]), *args_list[1:]]
        rc = subprocess.call(cmd, cwd=ROOT)
        if rc != 0:
            sys.exit(rc)
    print("\nPipeline OK → out/patch/monoeye_ko_seed.wsc")


if __name__ == "__main__":
    main()
