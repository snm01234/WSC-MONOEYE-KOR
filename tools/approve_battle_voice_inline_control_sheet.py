#!/usr/bin/env python3
"""Approve battle-voice E62F inline-control translation sheet for ROM apply.

Marks every non-empty ko row as translation_source=llm / review_status=approved /
workflow_status=approved on the master sheet and IC001-IC006 batches.
Does not modify ROM or SaveRAM.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "out/script/battle_voice_inline_control_translation_sheet.csv"
BATCH_DIR = ROOT / "out/script/battle_voice_inline_control_batches"
REPORT = ROOT / "out/patch/battle_voice_inline_control_approve_report.json"


def approve_rows(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"approved": 0, "empty": 0}
    for row in rows:
        ko = (row.get("ko") or "").strip()
        if not ko:
            counts["empty"] += 1
            continue
        row["ko"] = ko
        row["translation_source"] = "llm"
        row["review_status"] = "approved"
        row["workflow_status"] = "approved"
        counts["approved"] += 1
    return counts


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    with MASTER.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        master_rows = list(reader)
    master_counts = approve_rows(master_rows)
    write_csv(MASTER, master_rows, fieldnames)

    batch_counts: dict[str, dict[str, int]] = {}
    for batch_path in sorted(BATCH_DIR.glob("IC*.csv")):
        with batch_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        batch_counts[batch_path.name] = approve_rows(rows)
        write_csv(batch_path, rows, fieldnames)

    report = {
        "schema_version": 1,
        "generated_by": "tools/approve_battle_voice_inline_control_sheet.py",
        "ok": master_counts["empty"] == 0 and master_counts["approved"] == len(master_rows),
        "master_counts": master_counts,
        "batch_counts": batch_counts,
        "records": len(master_rows),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
