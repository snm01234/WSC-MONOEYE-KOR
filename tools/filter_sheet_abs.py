#!/usr/bin/env python3
"""Filter a translation sheet to an absolute-address window (logical 8MiB abs)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_quality.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out/script/translations_ep3_window.json",
    )
    ap.add_argument(
        "--min-abs",
        type=lambda s: int(s, 16),
        default=0x6040A5,
        help="Inclusive low abs (hex)",
    )
    ap.add_argument(
        "--max-abs",
        type=lambda s: int(s, 16),
        default=0x62FFFF,
        help="Inclusive high abs (hex)",
    )
    ap.add_argument(
        "--description",
        default="ep3 test window bank60-62 (6040A5-62FFFF)",
    )
    args = ap.parse_args()

    if not args.sheet.exists():
        print(f"missing sheet: {args.sheet}", file=sys.stderr)
        return 1

    data = json.loads(args.sheet.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "lines" in data:
        lines = list(data["lines"])
        meta = {k: v for k, v in data.items() if k != "lines"}
    elif isinstance(data, list):
        lines = data
        meta = {}
    else:
        print(f"unrecognized sheet: {args.sheet}", file=sys.stderr)
        return 1

    kept = []
    for row in lines:
        abs_s = row.get("abs")
        if not abs_s:
            continue
        abs_off = int(abs_s, 16)
        if args.min_abs <= abs_off <= args.max_abs:
            kept.append(row)

    out = {
        **meta,
        "description": args.description,
        "min_abs": f"{args.min_abs:06X}",
        "max_abs": f"{args.max_abs:06X}",
        "source_sheet": str(args.sheet),
        "lines": kept,
        "line_count": len(kept),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {args.out} | kept={len(kept)} / {len(lines)} "
        f"abs={args.min_abs:06X}-{args.max_abs:06X}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
