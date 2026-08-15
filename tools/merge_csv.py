#!/usr/bin/env python3
"""Merge out/script/splits/split_*.csv back into translation_sheet.csv."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

csv.field_size_limit(10_000_000)

ROOT = Path(__file__).resolve().parents[1]
from translation_source_policy import assert_translation_source_allowed


def merge_csv(splits_dir: Path, output_file: Path) -> int:
    split_files = sorted(splits_dir.glob("split_*.csv"))
    if not split_files:
        print("No split files found.")
        return 1

    assert_translation_source_allowed(
        split_files[0],
        role="split-sheet merge",
    )

    with open(split_files[0], encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
    if not fieldnames:
        print("No CSV headers in first split.")
        return 1

    all_rows: list[dict] = []
    for filepath in split_files:
        with open(filepath, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                all_rows.append(row)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Merged {len(split_files)} files ({len(all_rows)} rows) into {output_file}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--splits-dir",
        type=Path,
        default=ROOT / "out" / "script" / "splits",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "script" / "translation_sheet.csv",
    )
    args = ap.parse_args()
    return merge_csv(args.splits_dir, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
