#!/usr/bin/env python3
"""Build and statically verify marked PoC ROM (no emulator launch)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
OUT = ROOT / "out" / "patch"
POC = OUT / "bisect" / "10_marked_ui_isolation_poc.wsc"
SAFE = OUT / "bisect" / "07_script_only_stage1_ok.wsc"
BIZHAWK_OUT = ROOT / "out" / "bizhawk" / "marked10"


def run(cmd: list[str]) -> None:
    print("=" * 60)
    print(" ".join(cmd))
    print("=" * 60)
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        sys.exit(rc)


def bank_diff_report(original: bytes, patched: bytes) -> dict:
    banks = [0x40, 0x5F, 0x60, 0x7A]
    return {
        f"{b:02X}": sum(
            a != b
            for a, b in zip(
                original[b * 0x10000 : (b + 1) * 0x10000],
                patched[b * 0x10000 : (b + 1) * 0x10000],
            )
        )
        for b in banks
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild", action="store_true", help="Rebuild PoC via run_hook_pad_poc.py")
    ap.add_argument(
        "--runtime",
        action="store_true",
        help="(deprecated) Do not use from automation; run BizHawk manually instead.",
    )
    args = ap.parse_args()

    if args.rebuild:
        run([sys.executable, str(TOOLS / "run_hook_pad_poc.py")])

    if not POC.exists():
        raise SystemExit(f"Missing PoC ROM: {POC}")

    run(
        [
            sys.executable,
            str(TOOLS / "verify_marked_hangul_hook.py"),
            "--rom",
            str(POC),
        ]
    )

    orig = (ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc").read_bytes()
    poc = POC.read_bytes()
    safe = SAFE.read_bytes() if SAFE.exists() else b""
    report = {
        "poc": str(POC),
        "vs_original": bank_diff_report(orig, poc),
        "vs_safe_07": bank_diff_report(safe, poc) if safe else {},
        "safe_seed_restored": (OUT / "monoeye_ko_seed.wsc").read_bytes() == safe if safe else None,
    }
    report_path = POC.with_suffix(".phase_report.json")
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Wrote {report_path}")

    if args.runtime:
        raise SystemExit(
            "Automatic BizHawk launch is disabled. "
            "See docs/HANGUL_DISPLAY_STRATEGY.md §8 for manual test steps."
        )


if __name__ == "__main__":
    main()
