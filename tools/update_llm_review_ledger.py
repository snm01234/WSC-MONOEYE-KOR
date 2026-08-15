#!/usr/bin/env python3
"""Build a deterministic ledger for sequential LLM review dispatch."""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "out/script/main_translation_llm_review/batch_index.csv"
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
OUT = RESULT_DIR / "dispatch_ledger.csv"


def main() -> None:
    batches = list(csv.DictReader(INDEX.open(encoding="utf-8-sig", newline="")))
    fields = [
        "batch_id", "wave", "batch_order", "rows", "bundles", "first_abs", "last_abs",
        "source_status", "semantic_status", "structural_status", "apply_status", "result_csv",
    ]
    out_rows = []
    for order, batch in enumerate(batches, start=1):
        bid = batch["batch_id"]
        manifest_path = RESULT_DIR / f"{bid}_result_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            semantic = manifest.get("semantic_review", "complete")
            structural = manifest.get("structural_status", "hold")
            apply_status = manifest.get("apply_status", "not_applied")
            result_csv = manifest.get("result", f"out/script/main_translation_llm_review/results/{bid}_reviewed.csv")
        else:
            semantic = "queued"
            structural = "pending_structural_preclear" if batch["status"] != "ready_for_llm_review" else "ready"
            apply_status = "not_started"
            result_csv = ""
        out_rows.append({
            "batch_id": bid,
            "wave": batch["wave"],
            "batch_order": str(order),
            "rows": batch["rows"],
            "bundles": batch["bundles"],
            "first_abs": batch["first_abs"],
            "last_abs": batch["last_abs"],
            "source_status": batch["status"],
            "semantic_status": semantic,
            "structural_status": structural,
            "apply_status": apply_status,
            "result_csv": result_csv,
        })
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    print(json.dumps({
        "ledger": str(OUT),
        "batches": len(out_rows),
        "semantic_complete": sum(r["semantic_status"] == "complete" for r in out_rows),
        "structural_hold": sum(r["structural_status"] == "hold" for r in out_rows),
        "pending": sum(r["semantic_status"] != "complete" for r in out_rows),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
