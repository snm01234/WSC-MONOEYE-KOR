#!/usr/bin/env python3
"""Freeze the static source rows referenced by a staged scenario result."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
SOURCE_DIR = ROOT / "out/script/main_translation_llm_review/batches"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("result_batch_id")
    args = ap.parse_args()
    bid = args.result_batch_id.upper()
    result = RESULT_DIR / f"{bid}_reviewed.csv"
    rows = list(csv.DictReader(result.open(encoding="utf-8-sig", newline="")))
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))["contracts"]
    by_abs = {str(x["address"]).upper(): x for x in contracts}
    fields = [
        "workstream", "batch_id", "batch_order", "source_batch_id", "address_or_slot",
        "source", "status", "reason", "source_jp", "current_ko", "prefix_hex", "body_hex",
    ]
    out_rows = []
    for row in rows:
        addr = str(row["abs"]).upper()
        c = by_abs.get(addr, {})
        out_rows.append({
            "workstream": "scenario",
            "batch_id": str(row.get("batch_id") or bid),
            "batch_order": str(row.get("batch_order") or ""),
            "source_batch_id": "SG0001",
            "address_or_slot": addr,
            "source": "out/script/translation_sheet.csv",
            "status": "scenario_gap_structural_preclear",
            "reason": "frozen source for staged static-gap result",
            "source_jp": str(row.get("source_jp") or ""),
            "current_ko": str(row.get("current_ko") or ""),
            "prefix_hex": " ".join(str(c.get("source_prefix_hex") or c.get("control_prefix_hex") or "")[i:i+2] for i in range(0, len(str(c.get("source_prefix_hex") or c.get("control_prefix_hex") or "")), 2)),
            "body_hex": " ".join(str(c.get("source_body_hex") or "")[i:i+2] for i in range(0, len(str(c.get("source_body_hex") or "")), 2)),
        })
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    out = SOURCE_DIR / f"{bid}_source.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    manifest_path = RESULT_DIR / f"{bid}_result_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_batch"] = str(out.relative_to(ROOT)).replace("\\", "/")
    manifest["source_snapshot"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source": str(out), "manifest": str(manifest_path), "rows": len(out_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
