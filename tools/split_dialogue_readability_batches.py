#!/usr/bin/env python3
"""Split semantic readability rewrites into compact review batches."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "out/script/dialogue_readability_worklist.json"
OUT_DIR = ROOT / "data/dialogue_readability_batches"
MANIFEST = OUT_DIR / "manifest.json"
BATCH_SIZE = 50


def main() -> int:
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    groups = doc.get("semantic_rewrite_groups") or []
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("input_*.json"):
        old.unlink()
    manifest = []
    for start in range(0, len(groups), BATCH_SIZE):
        chunk = groups[start:start + BATCH_SIZE]
        batch_no = start // BATCH_SIZE + 1
        path = OUT_DIR / f"input_{batch_no:03d}.json"
        compact = {
            "schema_version": 1,
            "purpose": "source-grounded Korean rewrite for 2x20-cell dialogue readability",
            "rules": [
                "translate the combined two Japanese source rows as one coherent utterance",
                "return exactly two Korean rows, each <=20 display cells including spaces",
                "use readable spacing; do not solve capacity by deleting 3+ spaces",
                "preserve meaning, names, technical terms, tone, and important punctuation",
                "prefer natural concise Korean over literal machine-translation wording",
            ],
            "groups": [
                {
                    "group_id": g["group_id"],
                    "addresses": [r["abs"] for r in g["records"]],
                    "jp": [r["source_jp"] for r in g["records"]],
                    "pre20cell_ko": g["pre20cell_rows"],
                    "legacy_dense_ko": g["legacy_after_rows"],
                    "legacy_spaces_removed": g["legacy_spaces_removed"],
                }
                for g in chunk
            ],
        }
        path.write_text(json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest.append({
            "batch": batch_no,
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "groups": len(chunk),
            "first_group": chunk[0]["group_id"] if chunk else None,
            "last_group": chunk[-1]["group_id"] if chunk else None,
        })
    MANIFEST.write_text(json.dumps({
        "schema_version": 1,
        "batch_size": BATCH_SIZE,
        "total_groups": len(groups),
        "batches": manifest,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total_groups": len(groups), "batches": len(manifest)}, ensure_ascii=False))
    print(MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
