#!/usr/bin/env python3
"""Classify the 896 broad Japanese-residual records into actionable routes.

The classification is bound to three read-only audits:

* baseline before stage 1 (896 records),
* after the 15 shared dictionary replacements,
* after the cumulative stage-1 record candidate.

It distinguishes proven dialogue/voice text, complete bank-75 UI labels, mixed
Hangul/Japanese composition repairs, short fixed-table fragments, and likely
data.  It never writes a ROM or SaveRAM.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "out/patch/broad_japanese_residual_audit.json"
AFTER_SHARED = ROOT / "out/patch/broad_japanese_residual_after_shared_audit.json"
AFTER_STAGE1 = ROOT / "out/patch/broad_japanese_residual_after_stage1_audit.json"
STAGE1_REPORT = ROOT / "out/patch/broad_residual_stage1_report.json"
OUT = ROOT / "out/patch/broad_japanese_residual_classification.json"


class ClassificationError(RuntimeError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": digest(path)}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ClassificationError(f"invalid JSON root: {path}")
    return value


def rows(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for bucket in ((document.get("records") or {}).values()):
        found.extend(dict(row) for row in (bucket or []))
    return found


def category_for(row: Mapping[str, Any]) -> tuple[str, str, str, bool]:
    region = str(row.get("region") or "")
    tier = str(row.get("tier") or "")
    body = int(row.get("body_capacity") or 0)
    core = int(row.get("core_count") or 0)
    shape = str(row.get("shape") or "")

    if region == "aux":
        return (
            "proven_dialogue_voice_translation_needed",
            "reviewed Korean sentence/fragment catalog, then private ext3 or guarded short-token patch",
            "high",
            True,
        )
    if region == "script" and tier == "B":
        return (
            "proven_script_text_translation_needed",
            "review source context, then private ext3 or guarded short-token patch",
            "high",
            True,
        )
    if region == "script":
        return (
            "script_single_kana_or_data",
            "exclude until event/table consumer or screen evidence proves it is visible text",
            "low",
            False,
        )
    if region == "name75":
        return (
            "legacy_name_table_data_or_fragment",
            "do not patch automatically; verify table boundary and screen consumer first",
            "low",
            False,
        )
    if region == "name75_ui":
        if body < 2 or core < 2:
            return (
                "ui_short_fragment_screen_evidence",
                "screen/table-specific proof; one-byte entries require table relocation or renderer mapping",
                "medium",
                False,
            )
        if shape == "mixed_ko_jp":
            return (
                "ui_mixed_composition_repair",
                "trace every contributing dictionary token and repair the complete composed label",
                "high",
                True,
            )
        return (
            "ui_complete_label_translation_needed",
            "build a reviewed UI label catalog; body>=4 uses ext3, body 2-3 uses guarded stock routing",
            "high",
            True,
        )
    return (
        "unclassified_structural_evidence_needed",
        "manual structural review",
        "low",
        False,
    )


def storage_route(row: Mapping[str, Any]) -> str:
    body = int(row.get("body_capacity") or 0)
    if body >= 4:
        return "private_ext3_candidate"
    if body >= 2:
        return "two_byte_stock_exact_or_guarded_slot"
    if body == 1:
        return "table_or_renderer_specific_method"
    return "not_patchable_without_structure_recovery"


def main() -> int:
    baseline = load(BASELINE)
    after_shared = load(AFTER_SHARED)
    after_stage1 = load(AFTER_STAGE1)
    stage1_report = load(STAGE1_REPORT)
    if baseline.get("ok") is not True or after_shared.get("ok") is not True or after_stage1.get("ok") is not True:
        raise ClassificationError("one or more source audits are not successful")
    if stage1_report.get("ok") is not True:
        raise ClassificationError("stage-1 build report is not successful")

    base_rows = rows(baseline)
    shared_rows = rows(after_shared)
    final_rows = rows(after_stage1)
    if len(base_rows) != 896 or len(shared_rows) != 887 or len(final_rows) != 853:
        raise ClassificationError(
            f"population drift: baseline={len(base_rows)} shared={len(shared_rows)} final={len(final_rows)}"
        )
    shared_ids = {str(row["record_id"]) for row in shared_rows}
    final_by_id = {str(row["record_id"]): row for row in final_rows}
    final_ids = set(final_by_id)

    classified: list[dict[str, Any]] = []
    counts = collections.Counter()
    storage_counts = collections.Counter()
    auto_eligible = 0
    for source in sorted(base_rows, key=lambda row: int(row["logical_address"])):
        record_id = str(source["record_id"])
        row = dict(source)
        if record_id not in shared_ids:
            category = "resolved_stage1_shared_dictionary"
            action = "already included in shared_dictionary_cleanup_candidate"
            confidence = "verified"
            eligible = False
        elif record_id not in final_ids:
            category = "resolved_stage1_record_patch"
            action = "already included in broad_residual_stage1_candidate"
            confidence = "verified"
            eligible = False
        else:
            # Classify the current post-stage-1 rendering rather than the stale
            # baseline shape; shared dictionary replacements can turn a pure
            # Japanese label into a mixed Hangul/Japanese composition.
            row = dict(final_by_id[record_id])
            category, action, confidence, eligible = category_for(row)
        route = storage_route(row)
        row["classification"] = category
        row["recommended_action"] = action
        row["classification_confidence"] = confidence
        row["candidate_after_review"] = eligible
        row["storage_route"] = route
        classified.append(row)
        counts[category] += 1
        storage_counts[route] += 1
        auto_eligible += int(eligible)

    if sum(counts.values()) != 896:
        raise ClassificationError("classification total mismatch")
    if counts["resolved_stage1_shared_dictionary"] != 9:
        raise ClassificationError("shared-dictionary resolution count drifted")
    if counts["resolved_stage1_record_patch"] != 34:
        raise ClassificationError("record-patch resolution count drifted")

    priority = [
        {
            "order": 1,
            "category": "proven_dialogue_voice_translation_needed",
            "count": counts["proven_dialogue_voice_translation_needed"],
            "reason": "proven text banks and trusted record bodies; highest-value next translation catalog",
        },
        {
            "order": 2,
            "category": "proven_script_text_translation_needed",
            "count": counts["proven_script_text_translation_needed"],
            "reason": "proven script records outside the earlier narrow target set",
        },
        {
            "order": 3,
            "category": "ui_complete_label_translation_needed",
            "count": counts["ui_complete_label_translation_needed"],
            "reason": "complete fixed UI labels, but terminology and width require UI review",
        },
        {
            "order": 4,
            "category": "ui_mixed_composition_repair",
            "count": counts["ui_mixed_composition_repair"],
            "reason": "already mixed Hangul/Japanese; repair must follow composing dictionary consumers",
        },
        {
            "order": 5,
            "category": "ui_short_fragment_screen_evidence",
            "count": counts["ui_short_fragment_screen_evidence"],
            "reason": "short fixed entries cannot be safely translated from decoded fragments alone",
        },
    ]

    document = {
        "schema_version": 1,
        "generated_by": "tools/classify_broad_japanese_residuals.py",
        "read_only": True,
        "ok": True,
        "inputs": {
            "baseline_audit": identity(BASELINE),
            "after_shared_audit": identity(AFTER_SHARED),
            "after_stage1_audit": identity(AFTER_STAGE1),
            "stage1_build_report": identity(STAGE1_REPORT),
        },
        "population": {
            "baseline_records": 896,
            "resolved_by_shared_dictionary": 9,
            "resolved_by_record_patch": 34,
            "remaining_after_stage1": 853,
            "remaining_candidate_after_translation_or_composition_review": auto_eligible,
        },
        "counts_by_classification": dict(sorted(counts.items())),
        "counts_by_storage_route": dict(sorted(storage_counts.items())),
        "priority_order": priority,
        "policy": {
            "ext3": "use for complete record bodies of at least four bytes; deduplicate identical Korean phrases",
            "short_2_3_bytes": "use existing exact stock phrase or guarded retired/shared semantic slot",
            "one_byte": "never force ext3/stock token; requires table relocation or renderer-specific mapping",
            "mixed_composition": "repair the full composed label and audit every shared dictionary consumer",
            "data_exclusion": "name-table tail and isolated script glyphs remain excluded without pointer/screen proof",
        },
        "records": classified,
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "population": document["population"], "counts": document["counts_by_classification"], "storage": document["counts_by_storage_route"], "out": str(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
