#!/usr/bin/env python3
"""
Apply top-frequency sequential-scan (no far-pointer) KO lines via expansion
dictionary size-preserving tokens onto the tip ROM.

Does NOT force-format bank10. Pointer classification uses the 8MiB backup so
already-relocated far-pointer lines stay out of the pool.

Prefer `tools/build_script_ko.py` for the full hybrid pipeline.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    tip = ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc"
    ptr = ROOT / "out" / "patch" / "monoeye_ko_expanded_8mb.wsc"
    report = ROOT / "out" / "patch" / "seq_ext_dict_report.json"
    if not tip.exists():
        print(f"missing tip ROM: {tip}", file=sys.stderr)
        return 1
    if not ptr.exists():
        print(f"missing pointer-ref ROM: {ptr}", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "apply_ext_dict_unit.py"),
        "--rom",
        str(tip),
        "--out-rom",
        str(tip),
        "--pointer-ref-rom",
        str(ptr),
        "--only-no-pointer",
        "--rank",
        "early-abs",
        "--stock-reclaim",
        "--out-report",
        str(report),
        "--tbl",
        str(ROOT / "out" / "patch" / "hangul_patch_pad3.tbl"),
        "--meta",
        str(ROOT / "out" / "patch" / "exp_dictionary_meta.json"),
        "--base-rom",
        str(ROOT / "out" / "patch" / "monoeye_ko_marked.wsc"),
        "--slots",
        "265",
    ]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
