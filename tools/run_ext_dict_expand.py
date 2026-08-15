#!/usr/bin/env python3
"""Grow extended dictionary slots on the current expanded ROM (force-format).

Usage:
  python tools/run_ext_dict_expand.py 128
  python tools/run_ext_dict_expand.py 256

Does not rebuild safe/spill/seq. Backs up the previous ROM first.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TOOLS = ROOT / "tools"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: run_ext_dict_expand.py <slots>")
    slots = int(sys.argv[1])
    if slots < 1 or slots > 265:
        raise SystemExit("slots must be 1..265 (token space hard cap)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = ROOT / f"out/patch/monoeye_ko_expanded_pre_ext{slots}_{ts}.wsc"
    shutil.copy2(ROM, bak)
    print(f"Backup → {bak}")

    cmd = [
        sys.executable,
        str(TOOLS / "apply_ext_dict_unit.py"),
        "--rom",
        str(ROM),
        "--out-rom",
        str(ROM),
        "--tbl",
        str(ROOT / "out/patch/hangul_patch.tbl"),
        "--sheet",
        str(ROOT / "out/script/translations_full.json"),
        "--seed",
        str(ROOT / "data/translations_seed_hook96.json"),
        "--meta",
        str(ROOT / "out/patch/ext_dictionary_meta.json"),
        "--slots",
        str(slots),
        "--out-report",
        str(ROOT / "out/patch/ext_dict_apply_report.json"),
    ]
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        raise SystemExit(rc)
    rc = subprocess.call(
        [
            sys.executable,
            str(TOOLS / "verify_marked_hangul_hook.py"),
            "--rom",
            str(ROM),
        ],
        cwd=ROOT,
    )
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
