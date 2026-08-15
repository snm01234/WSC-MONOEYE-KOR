#!/usr/bin/env python3
"""Run capacity analysis + expanded Korean reinsertion pipeline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent

STEPS = [
    ("extract_script.py", "1/6 Extract dialogue DB"),
    ("build_translation_batch.py --count 80", "2/6 Build capacity-test translation batch"),
    (
        "build_hangul_font.py --translations data/translations_batch.json",
        "3/6 Build Hangul font from batch chars",
    ),
    ("analyze_capacity.py", "4/6 Analyze dictionary/script capacity"),
    (
        "apply_translations_expanded.py --translations data/translations_batch.json",
        "5/6 Apply expanded dictionary/script reinsertion",
    ),
    (
        "verify_patch.py --rom out/patch/monoeye_ko_expanded.wsc "
        "--translations data/translations_batch.json "
        "--out out/patch/verify_expanded_report.json",
        "6/6 Verify expanded patch",
    ),
]


def main() -> None:
    for step, label in STEPS:
        print("=" * 60)
        print(label)
        print("=" * 60)
        parts = step.split()
        script = parts[0]
        args = parts[1:]
        rc = subprocess.call(
            [sys.executable, str(TOOLS / script), *args],
            cwd=ROOT,
        )
        if rc != 0:
            sys.exit(rc)
    print("\nExpanded pipeline OK → out/patch/monoeye_ko_expanded.wsc")


if __name__ == "__main__":
    main()
