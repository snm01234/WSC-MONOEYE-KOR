#!/usr/bin/env python3
"""Emit one semantic review result batch from a checked source-to-Korean map.

The emitted result is never promoted automatically. Runtime-contract quarantine
and all apply guards remain explicit in the manifest.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from write_mr0001_review_result import glossary_ids

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "out/script/main_translation_llm_review"
RESULT_DIR = BASE / "results"


def has_japanese_syllabary(text: str) -> bool:
    """Reject Hiragana/Katakana letters but allow the name separator ・."""
    return any(
        0x3041 <= ord(c) <= 0x3096 or 0x30A1 <= ord(c) <= 0x30FA
        for c in text
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_id")
    ap.add_argument("mapping", type=Path)
    args = ap.parse_args()
    bid = args.batch_id.upper()
    source = BASE / "batches" / f"{bid}.csv"
    mapping_path = args.mapping if args.mapping.is_absolute() else ROOT / args.mapping
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig", newline="")))
    missing = sorted(set(r["abs"] for r in rows) - set(mapping))
    extra = sorted(set(mapping) - set(r["abs"] for r in rows))
    if missing or extra:
        raise SystemExit(f"mapping mismatch: missing={missing} extra={extra}")
    out = RESULT_DIR / f"{bid}_reviewed.csv"
    manifest_path = RESULT_DIR / f"{bid}_result_manifest.json"
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    # The batch CSV already carries blank proposal/review columns.  Extend only
    # truly new fields so the result remains a valid, uniquely keyed CSV.
    fields = list(rows[0])
    for field in [
        "source_model", "reviewed_at", "glossary_ids", "apply_status",
        "translation_source", "review_status", "review_count",
    ]:
        if field not in fields:
            fields.append(field)
    out_rows = []
    for row in rows:
        text = mapping[row["abs"]]
        if not text or len(text) > 20 or has_japanese_syllabary(text) or "\x00" in text:
            raise SystemExit(f"invalid translation at {row['abs']}: {text!r}")
        item = dict(row)
        item.update({
            "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
            "reviewed_at": date.today().isoformat(),
            "glossary_ids": glossary_ids(row["source_jp"]),
            "proposed_ko": text,
            "reviewer_notes": "일본어 원문과 실제 bundle 문맥 기준 재번역. runtime contract quarantine은 해제하지 않음.",
            "new_translation_source": "llm",
            "new_review_status": "llm_retranslated_structural_hold",
            "apply_status": "not_applied_structural_preclear",
            "translation_source": "llm",
            "review_status": "llm_retranslated_structural_hold",
            "review_count": "1",
        })
        out_rows.append(item)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    manifest = {
        "schema_version": 1,
        "batch_id": bid,
        "source_batch": str(source.relative_to(ROOT)).replace("\\", "/"),
        "result": str(out.relative_to(ROOT)).replace("\\", "/"),
        "rows": len(out_rows),
        "bundles": len({r["bundle_id"] for r in out_rows}),
        "semantic_review": "complete",
        "structural_status": "hold",
        "apply_status": "not_applied",
        "main_tip_sha256": rows[0]["main_tip_sha256"],
        "source_body_sha256_set": sorted({r["source_body_sha256"] for r in rows}),
        "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
        "translation_source": "llm",
        "review_status": "llm_retranslated_structural_hold",
        "reason": "continuation rows remain runtime-contract quarantine until structural preclear",
        "canonical_sheet_changed": False,
        "rom_changed": False,
        "saveram_changed": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(out), "manifest": str(manifest_path), "rows": len(out_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
