#!/usr/bin/env python3
"""Convert translation_sheet.csv into apply/verify JSON ({lines:[{abs,jp,ko}]})."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from script_translation_scope import script_graphics_reason
from translation_source_policy import assert_translation_source_allowed

csv.field_size_limit(10_000_000)


def normalize_ko(text: str) -> str:
    text = text.replace("...", "……").replace("!", "！").replace("?", "？")
    text = text.replace("！　！", "！！").replace("？　？", "？？")
    return text.replace(" ", "　")


def sheet_to_lines(path: Path) -> tuple[list[dict], dict]:
    lines: list[dict] = []
    skipped = {
        "no_abs": 0,
        "no_ko": 0,
        "bad_abs": 0,
        "excluded_script_graphics_block": 0,
    }
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            abs_raw = (row.get("abs") or "").strip()
            ko = (row.get("ko") or "").strip()
            jp = row.get("jp") or ""
            if not abs_raw:
                skipped["no_abs"] += 1
                continue
            if not ko:
                skipped["no_ko"] += 1
                continue
            try:
                abs_off = int(abs_raw, 16)
            except ValueError:
                skipped["bad_abs"] += 1
                continue
            exclusion = script_graphics_reason(abs_off)
            if exclusion:
                skipped[exclusion] += 1
                continue
            lines.append(
                {
                    "abs": f"{abs_off:06X}",
                    "jp": jp,
                    "ko": normalize_ko(ko),
                    "id": row.get("id") or "",
                    "kind": row.get("kind") or "",
                }
            )
    return lines, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out" / "script" / "translation_sheet.csv",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "script" / "translations_full.json",
    )
    args = ap.parse_args()

    assert_translation_source_allowed(
        args.sheet,
        role="sheet-to-translations conversion",
    )
    lines, skipped = sheet_to_lines(args.sheet)
    # Prefer first occurrence per abs (stable sheet order).
    by_abs: dict[str, dict] = {}
    dupes = 0
    for line in lines:
        if line["abs"] in by_abs:
            dupes += 1
            continue
        by_abs[line["abs"]] = line
    unique = list(by_abs.values())

    payload = {
        "description": "Full-sheet Korean translations for expanded reinsertion",
        "source": str(args.sheet),
        "line_count": len(unique),
        "skipped": skipped,
        "duplicate_abs_skipped": dupes,
        "lines": unique,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        f"Wrote {args.out} | lines={len(unique)} "
        f"skipped={skipped} duplicate_abs={dupes}"
    )


if __name__ == "__main__":
    main()
