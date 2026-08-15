#!/usr/bin/env python3
"""Classify the final fixed-scope dialogue context residuals.

The context review is considered semantically complete when every residual is
one of:
  * same-JP wording/style variation (not evidence of mistranslation),
  * an explicitly reviewed lexical/ratio false positive, or
  * a separately tracked structural record pathology.

This tool does not mutate ROMs.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "out/script/dialogue_context_neighborhood_worklist.json"
RESIDUAL = ROOT / "out/script/dialogue_context_candidate_residual.json"
ACCEPTANCE = ROOT / "out/patch/dialogue_legacy_mt_literal_acceptance_audit.json"
BUILD = ROOT / "out/patch/dialogue_legacy_mt_literal_candidate_report.json"
OUT = ROOT / "out/script/dialogue_context_review_completion.json"

# Manually re-read against Japanese source/context.  These are intentionally
# not rewritten: the heuristic fired on a substring, expressive repetition,
# an attack-call elongation, or a clause whose meaning is completed by the
# adjacent record.
ACCEPTABLE_FALSE_POSITIVES = {
    "6012F3",  # 消えろよぉっ！！ -> 사라져버려！！
    "6096A3",  # 震えて -> 부들부들 ... (natural repetition)
    "61224D",  # カミーユ・ビダンです -> full name, not '비단' mistranslation
    "62069A",  # 次々に -> 차례차례로
    "62133A",  # clause completed by 621349
    "621349",  # ばりばりの新型機 -> 따끈따끈한 신형기
    "623E24",  # やってくれたようだな -> 해냈군, prior clause supplies subject
    "625DCA",  # 消えちまっている -> 사라져버렸어
    "626379",  # ゴォォッド…フィンガー attack-call elongation
    "632D68",  # チョロチョロ -> 쫄랑쫄랑
    "636BD6",  # 消える -> 사라져 (lexical substring false hit)
    "63AAD3",  # ミアーーーン shout elongation
}
STRUCTURAL_DEFERRED = {
    "630695",  # pathological record walk; ~1280-cell repeat, not semantic MT
}
STYLE_ONLY_REASON = "same_jp_as_corrected_target_differs"


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    residual = json.loads(RESIDUAL.read_text(encoding="utf-8"))
    acceptance = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
    build = json.loads(BUILD.read_text(encoding="utf-8"))

    classified = []
    counts = Counter()
    unresolved = []
    for row in residual.get("rows") or []:
        address = str(row.get("abs") or "").upper()
        reasons = set(row.get("reasons") or [])
        if address in STRUCTURAL_DEFERRED:
            classification = "structural_deferred_not_translation"
        elif address in ACCEPTABLE_FALSE_POSITIVES:
            classification = "reviewed_acceptable_or_heuristic_false_positive"
        elif reasons and reasons <= {STYLE_ONLY_REASON}:
            classification = "same_jp_contextual_style_variant"
        else:
            classification = "unresolved_semantic_residual"
            unresolved.append(row)
        counts[classification] += 1
        classified.append({**row, "classification": classification})

    summary = {
        "status": "context_semantic_review_complete_with_structural_defer" if not unresolved else "context_review_incomplete",
        "fixed_context_radius_records_each_side": int(ledger.get("radius_records_each_side") or 0),
        "fixed_context_clusters": int((ledger.get("summary") or {}).get("context_clusters") or 0),
        "fixed_context_records": int((ledger.get("summary") or {}).get("neighborhood_records") or 0),
        "direct_retranslation_targets": int(build.get("targets") or 0),
        "candidate_sha256": str((build.get("candidate") or {}).get("sha256") or ""),
        "residual_flags_total": len(classified),
        "classification_counts": dict(counts),
        "unresolved_semantic_residuals": len(unresolved),
        "structural_deferred": sorted(STRUCTURAL_DEFERRED),
        "candidate_acceptance_overall_ok": bool(acceptance.get("overall_ok")),
    }
    gates = {
        "fixed_scope_present": summary["fixed_context_records"] == 7194,
        "candidate_target_count_expected": summary["direct_retranslation_targets"] == 1512,
        "candidate_acceptance_ok": summary["candidate_acceptance_overall_ok"],
        "no_unresolved_semantic_residual": not unresolved,
        "structural_defer_is_known_only": set(summary["structural_deferred"]) == {"630695"},
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_dialogue_context_review_completion.py",
        "policy": {
            "translation_source": "Japanese original only",
            "same_jp_variation": "do not homogenize blindly; speaker/context can justify wording/honorific differences",
            "structural_record": "do not repair with generic translation portal writer",
        },
        "summary": summary,
        "gates": gates,
        "overall_ok": all(gates.values()),
        "unresolved": unresolved,
        "classified_residuals": classified,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": summary, "gates": gates, "overall_ok": report["overall_ok"]}, ensure_ascii=False, indent=2))
    print(OUT)
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
