#!/usr/bin/env python3
"""Copy verified marked PoC to the development baseline ROM."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / "out" / "patch" / "bisect" / "10_marked_ui_isolation_poc.wsc"
VERIFY = POC.with_suffix(".marked_verify.json")
DEFAULT_OUT = ROOT / "out" / "patch" / "monoeye_ko_marked.wsc"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--poc", type=Path, default=POC)
    ap.add_argument("--verify", type=Path, default=VERIFY)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--require-runtime-log",
        type=Path,
        default=ROOT / "out" / "bizhawk" / "marked10" / "runtime.log",
        help="Optional runtime log that must contain DONE",
    )
    ap.add_argument("--skip-runtime-check", action="store_true")
    args = ap.parse_args()

    if not args.poc.exists():
        raise SystemExit(f"Missing PoC: {args.poc}")
    if not args.verify.exists():
        raise SystemExit(f"Missing static verify report: {args.verify}")

    report = json.loads(args.verify.read_text(encoding="utf-8"))
    if report.get("status") != "PASS":
        raise SystemExit(f"Static verify not PASS: {args.verify}")

    if not args.skip_runtime_check:
        if not args.require_runtime_log.exists():
            raise SystemExit(
                f"Runtime log missing: {args.require_runtime_log}. "
                "Complete manual BizHawk test (see docs/HANGUL_DISPLAY_STRATEGY.md §8.1), "
                "or pass --skip-runtime-check."
            )
        log = args.require_runtime_log.read_text(encoding="utf-8")
        if "DONE frame=" not in log:
            raise SystemExit("Runtime log incomplete (no DONE line)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.poc, args.out)
    meta = {
        "source": str(args.poc),
        "verify": str(args.verify),
        "runtime_log": str(args.require_runtime_log) if args.require_runtime_log.exists() else None,
        "note": "Development baseline with marked Hangul hook; safe seed remains 07_script_only.",
    }
    meta_path = args.out.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Promoted {args.poc.name} -> {args.out}")
    print(f"Wrote {meta_path}")


if __name__ == "__main__":
    main()
