#!/usr/bin/env python3
"""Mark normalization-only review outputs as still pending semantic review.

The first implementation of the later review waves normalized existing Korean
but did not perform a source-grounded semantic retranslation.  This tool makes
that provenance explicit without touching the canonical sheet, ROM, or
SaveRAM.  Batches with an authored manual map are left untouched.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "out/script/main_translation_llm_review"
RESULT = BASE / "results"
MAPS = BASE / "manual_maps"


def main() -> None:
    changed = []
    for manifest_path in sorted(RESULT.glob("MR*_result_manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bid = str(manifest["batch_id"]).upper()
        if int(bid[2:]) < 46 or (MAPS / f"{bid}_manual.json").is_file():
            continue
        result_path = ROOT / manifest["result"]
        with result_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
            fields = list(rows[0]) if rows else []
        for row in rows:
            row["new_translation_source"] = "existing_sheet_normalization"
            row["new_review_status"] = "normalization_only_pending_llm"
            row["reviewer_notes"] = (
                "기존 한국어의 제어 잔재·공백만 정규화한 후보이며, 일본어 원문 기반 의미 재검수 전에는 "
                "LLM 번역 완료로 간주하지 않음. runtime contract quarantine은 해제하지 않음."
            )
            row["apply_status"] = "not_applied_semantic_pending"
            if "source_model" in row:
                row["source_model"] = "deterministic normalization (semantic LLM review pending)"
        with result_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        manifest["semantic_review"] = "normalization_only_pending_llm"
        manifest["review_status"] = "normalization_only_pending_llm"
        manifest["translation_source"] = "existing_sheet_normalization"
        manifest["source_model"] = "deterministic normalization (semantic LLM review pending)"
        manifest["apply_status"] = "not_applied_semantic_pending"
        manifest["reason"] = (
            "existing Korean was normalized only; source-grounded semantic LLM retranslation is still required"
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(bid)
    print(json.dumps({"changed": len(changed), "first": changed[:3], "last": changed[-3:]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
