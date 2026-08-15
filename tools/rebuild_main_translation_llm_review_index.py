#!/usr/bin/env python3
"""Rebuild the review result index from result manifests only."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "out/script/main_translation_llm_review/results"
rows = []
for p in sorted(RESULT.glob("MR*_result_manifest.json")):
    m = json.loads(p.read_text(encoding="utf-8"))
    bid = m["batch_id"]
    rows.append({
        "batch_id": bid,
        "result_csv": f"{bid}_reviewed.csv",
        "manifest": p.name,
        "rows": m["rows"],
        "bundles": m["bundles"],
        "semantic_review": m["semantic_review"],
        "status": f"structural_{m['structural_status']}" if m["structural_status"] in {"hold", "ready"} else m["structural_status"],
        "apply_status": m["apply_status"],
    })
rows.sort(key=lambda r: int(r["batch_id"][2:]))
tip = rows[0]["batch_id"] if rows else ""
main_tip = json.loads((RESULT / f"{tip}_result_manifest.json").read_text(encoding="utf-8"))["main_tip_sha256"] if rows else ""
out = {"schema_version": 1, "main_tip_sha256": main_tip, "batches": rows}
(RESULT / "index.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"batches": len(rows), "semantic_complete": sum(r["semantic_review"] == "complete" for r in rows)}, ensure_ascii=False))
