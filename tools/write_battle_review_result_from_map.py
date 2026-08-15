#!/usr/bin/env python3
"""Emit a non-promotable semantic review result for battle short lines."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "out/script/battle_dialogue_llm_review_queue.csv"
RESULT_DIR = ROOT / "out/script/battle_dialogue_llm_review/results"


def has_japanese_syllabary(text: str) -> bool:
    return any(0x3041 <= ord(c) <= 0x3096 or 0x30A1 <= ord(c) <= 0x30FA for c in text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mapping", type=Path)
    args = ap.parse_args()
    mapping_path = args.mapping if args.mapping.is_absolute() else ROOT / args.mapping
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    with QUEUE.open(encoding="utf-8-sig", newline="") as fh:
        queue_rows = list(csv.DictReader(fh))
    targets = [r for r in queue_rows if r["queue_class"] == "semantic_llm_review_ready"]
    missing = sorted(set(r["abs"] for r in targets) - set(mapping))
    extra = sorted(set(mapping) - set(r["abs"] for r in targets))
    if missing or extra:
        raise SystemExit(f"mapping mismatch: missing={missing} extra={extra}")

    fields = list(targets[0])
    for field in ["proposed_ko", "source_model", "reviewed_at", "new_translation_source", "new_review_status", "apply_status"]:
        if field not in fields:
            fields.append(field)
    out_rows = []
    for row in targets:
        text = mapping[row["abs"]]
        if not text or len(text) > 20 or has_japanese_syllabary(text) or "\x00" in text:
            raise SystemExit(f"invalid translation at {row['abs']}: {text!r}")
        item = dict(row)
        item.update({
            "proposed_ko": text,
            "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
            "reviewed_at": date.today().isoformat(),
            "new_translation_source": "llm",
            "new_review_status": "llm_retranslated_structural_hold",
            "apply_status": "not_applied_structural_preclear",
        })
        out_rows.append(item)

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / "battle_voice_ambiguous_nonstub_ready_reviewed.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    manifest = {
        "schema_version": 1,
        "source_queue": str(QUEUE.relative_to(ROOT)).replace("\\", "/"),
        "result": str(out.relative_to(ROOT)).replace("\\", "/"),
        "rows": len(out_rows),
        "batches": sorted({r["batch_id"] for r in out_rows}),
        "semantic_review": "complete",
        "structural_status": "hold",
        "apply_status": "not_applied",
        "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
        "translation_source": "llm",
        "review_status": "llm_retranslated_structural_hold",
        "reason": "battle short-line review completed without neighboring scenario context; runtime visibility/boundary and encoding gates remain",
        "canonical_sheet_changed": False,
        "rom_changed": False,
        "saveram_changed": False,
        "runtime_trace": "stopped_by_user",
    }
    manifest_path = RESULT_DIR / "battle_voice_ambiguous_nonstub_ready_result_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(out), "manifest": str(manifest_path), "rows": len(out_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
