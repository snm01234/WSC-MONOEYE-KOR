#!/usr/bin/env python3
"""Add explicit provenance columns to scenario staging results only."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"

csv.field_size_limit(100_000_000)


def main() -> None:
    files = sorted(RESULT_DIR.glob("MR*_reviewed.csv"))
    changed = 0
    total = 0
    for path in files:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
            fields = list(rows[0]) if rows else []
        if not rows:
            continue
        for field in ["translation_source", "review_status", "review_count"]:
            if field not in fields:
                fields.append(field)
        for row in rows:
            total += 1
            source = str(row.get("new_translation_source") or "")
            status = str(row.get("new_review_status") or "")
            if source == "llm":
                row["translation_source"] = "llm"
                row["review_status"] = status or "llm_retranslated_structural_hold"
                row["review_count"] = "1"
            else:
                row["translation_source"] = ""
                row["review_status"] = status or "structural_quarantine_pending_llm"
                row["review_count"] = "0"
            row.setdefault("reviewed_at", date.today().isoformat())
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        changed += 1
    print({"files": changed, "rows": total})


if __name__ == "__main__":
    main()
