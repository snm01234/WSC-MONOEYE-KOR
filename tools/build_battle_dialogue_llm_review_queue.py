#!/usr/bin/env python3
"""Build a non-destructive review queue for battle dialogue sheets.

The ambiguous/placeholder battle sheets intentionally contain rows whose
runtime visibility or speaker-prefix boundary is not established.  This tool
does not translate or modify those sheets; it records which rows can receive
independent short-line semantic review and which rows must remain quarantined.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


DEFAULT_INPUTS = (
    "out/script/battle_voice_ambiguous_translation_sheet.csv",
    "out/script/battle_voice_placeholder_translation_sheet.csv",
)
APPROVED_INVENTORY = (
    "out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv",
    "out/script/battle_voice_inline_control_translation_sheet.csv",
    "out/script/uncovered_translation_sheet_llm_reviewed.csv",
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def classify(path: Path, row: dict[str, str]) -> tuple[str, str, str]:
    """Return queue class, blocking reason, and next static action."""

    if row.get("classification") == "placeholder_or_template" or "placeholder" in path.name:
        return (
            "template_or_stub_quarantine",
            "runtime_visibility_unproven",
            "retain_original; do_not_translate_or_apply",
        )
    if row.get("boundary_review_required", "").lower() == "yes":
        return (
            "leading_fragment_quarantine",
            "speaker_prefix_boundary_unproven",
            "retain_original; require_static_caller_contract",
        )
    if row.get("stub_class") == "mass_stub":
        return (
            "template_or_stub_quarantine",
            "mass_stub_runtime_visibility_unproven",
            "retain_original; do_not_translate_or_apply",
        )
    return (
        "semantic_llm_review_ready",
        "none",
        "short_line_llm_review_without_neighbor_context",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-csv", default="out/script/battle_dialogue_llm_review_queue.csv")
    parser.add_argument("--out-json", default="out/script/battle_dialogue_llm_review_queue.json")
    parser.add_argument("--inputs", nargs="*", default=list(DEFAULT_INPUTS))
    args = parser.parse_args()

    queue: list[dict[str, str]] = []
    source_counts: dict[str, dict[str, int]] = {}
    for input_name in args.inputs:
        path = Path(input_name)
        rows = read_rows(path)
        counts: Counter[str] = Counter()
        for row in rows:
            queue_class, reason, action = classify(path, row)
            counts[queue_class] += 1
            queue.append(
                {
                    "source_sheet": str(path).replace("\\", "/"),
                    "batch_id": row.get("batch_id", ""),
                    "batch_order": row.get("batch_order", ""),
                    "abs": row.get("abs", ""),
                    "original_jp": row.get("original_jp", ""),
                    "current_text": row.get("current_text", ""),
                    "body_capacity": row.get("body_capacity", ""),
                    "stub_class": row.get("stub_class", ""),
                    "boundary_review_required": row.get("boundary_review_required", ""),
                    "notes": row.get("notes", ""),
                    "queue_class": queue_class,
                    "blocking_reason": reason,
                    "next_static_action": action,
                    "translation_source": "",
                    "review_status": "pending",
                    "apply_status": "not_applied",
                }
            )
        source_counts[str(path).replace("\\", "/")] = dict(sorted(counts.items()))

    queue.sort(key=lambda row: (row["source_sheet"], row["batch_id"], int(row["batch_order"] or 0)))
    overall = Counter(row["queue_class"] for row in queue)
    batch_counts = Counter((row["source_sheet"], row["batch_id"]) for row in queue)

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(queue[0].keys()))
        writer.writeheader()
        writer.writerows(queue)

    approved_inventory = []
    for input_name in APPROVED_INVENTORY:
        path = Path(input_name)
        if not path.exists():
            continue
        rows = read_rows(path)
        approved_inventory.append(
            {
                "sheet": str(path).replace("\\", "/"),
                "rows": len(rows),
                "translation_source_counts": dict(Counter(r.get("translation_source", "") for r in rows)),
                "review_status_counts": dict(Counter(r.get("review_status", "") for r in rows)),
                "workflow_status_counts": dict(Counter(r.get("workflow_status", "") for r in rows)),
            }
        )

    report = {
        "schema": "battle-dialogue-review-queue/v1",
        "inputs": [str(Path(p)).replace("\\", "/") for p in args.inputs],
        "rows": len(queue),
        "batches": len(batch_counts),
        "queue_class_counts": dict(sorted(overall.items())),
        "source_counts": source_counts,
        "approved_inventory": approved_inventory,
        "runtime_trace": "stopped_by_user",
        "promotion": "not_ready; queue is planning-only and never writes canonical sheets or ROM",
    }
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
