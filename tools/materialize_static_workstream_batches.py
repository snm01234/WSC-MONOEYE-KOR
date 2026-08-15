#!/usr/bin/env python3
"""Materialize the bounded static queue into dispatch-only batch CSVs."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
OUT = ROOT / "out/script/translation_workstreams_static_batches"
INDEX = ROOT / "out/script/translation_workstreams_static_batch_index.csv"

csv.field_size_limit(100_000_000)


def main() -> None:
    with QUEUE.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["batch_id"]].append(row)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.csv"):
        old.unlink()
    fields = list(rows[0]) if rows else ["workstream", "batch_id", "batch_order", "source_batch_id", "address_or_slot", "source", "status", "reason"]
    index_rows = []
    for batch_id in sorted(groups):
        batch_rows = sorted(groups[batch_id], key=lambda row: int(row["batch_order"]))
        path = OUT / f"{batch_id}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(batch_rows)
        index_rows.append({
            "batch_id": batch_id,
            "workstream": batch_rows[0]["workstream"],
            "rows": len(batch_rows),
            "first_address": batch_rows[0]["address_or_slot"],
            "last_address": batch_rows[-1]["address_or_slot"],
            "status_counts": json.dumps({k: sum(r["status"] == k for r in batch_rows) for k in sorted({r["status"] for r in batch_rows})}, ensure_ascii=False),
            "source_batch_ids": " | ".join(sorted({r["source_batch_id"] for r in batch_rows if r["source_batch_id"]})),
        })
    index_fields = ["batch_id", "workstream", "rows", "first_address", "last_address", "status_counts", "source_batch_ids"]
    with INDEX.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=index_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(index_rows)
    print(json.dumps({"batches": len(index_rows), "rows": len(rows), "directory": str(OUT), "index": str(INDEX)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
