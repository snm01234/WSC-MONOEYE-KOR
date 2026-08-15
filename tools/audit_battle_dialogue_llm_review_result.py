#!/usr/bin/env python3
"""Fail-closed static audit for the non-promotable battle review result."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def has_japanese(text: str) -> bool:
    return any(0x3041 <= ord(c) <= 0x3096 or 0x30A1 <= ord(c) <= 0x30FA for c in text)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="out/script/battle_dialogue_llm_review/results/battle_voice_ambiguous_nonstub_ready_reviewed.csv")
    ap.add_argument("--queue", default="out/script/battle_dialogue_llm_review_queue.csv")
    ap.add_argument("--source", default="out/script/battle_voice_ambiguous_translation_sheet.csv")
    ap.add_argument("--out", default="out/script/battle_dialogue_llm_review/battle_review_static_audit.json")
    args = ap.parse_args()
    result_path = ROOT / args.result
    queue_path = ROOT / args.queue
    source_path = ROOT / args.source
    with result_path.open(encoding="utf-8-sig", newline="") as fh:
        results = list(csv.DictReader(fh))
    with queue_path.open(encoding="utf-8-sig", newline="") as fh:
        queue = [r for r in csv.DictReader(fh) if r.get("queue_class") == "semantic_llm_review_ready"]
    result_abs = {r.get("abs", "") for r in results}
    queue_abs = {r.get("abs", "") for r in queue}
    errors = []
    if result_abs != queue_abs:
        errors.append({"kind": "population_mismatch", "missing": sorted(queue_abs - result_abs), "extra": sorted(result_abs - queue_abs)})
    for row in results:
        text = row.get("proposed_ko", "")
        if not text or len(text) > 20:
            errors.append({"kind": "length_or_empty", "abs": row.get("abs"), "length": len(text)})
        if has_japanese(text):
            errors.append({"kind": "japanese_residual", "abs": row.get("abs")})
        if "\x00" in text:
            errors.append({"kind": "embedded_nul", "abs": row.get("abs")})
        if row.get("apply_status") != "not_applied_structural_preclear":
            errors.append({"kind": "apply_status", "abs": row.get("abs"), "value": row.get("apply_status")})
    report = {
        "schema": "battle-dialogue-review-static-audit/v1",
        "ok": not errors,
        "result_rows": len(results),
        "queue_rows": len(queue),
        "batches": sorted({r.get("batch_id") for r in results}),
        "errors": errors,
        "result_sha256": sha256(result_path),
        "source_sheet_sha256": sha256(source_path),
        "canonical_sheet_changed": False,
        "rom_changed": False,
        "runtime_trace": "stopped_by_user",
        "promotion": "blocked_until_battle_route_visibility_boundary_and_encoding_gates",
    }
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
