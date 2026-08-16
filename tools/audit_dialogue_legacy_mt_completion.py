#!/usr/bin/env python3
"""Final classification audit for the legacy-MT dialogue sweep.

This does not claim every untouched Korean line was manually retranslated.  It
proves that the entire forensic population has been passed through the static
review pipeline and classifies every non-batched record into an explicit
reason for leaving it unchanged/deferred.
"""
from __future__ import annotations

import glob
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
CANDIDATE_REPORT = ROOT / "out/patch/dialogue_legacy_mt_literal_candidate_report.json"
ACCEPTANCE = ROOT / "out/patch/dialogue_legacy_mt_literal_acceptance_audit.json"
REMAINING_AUDIT = ROOT / "out/script/dialogue_legacy_mt_remaining_audit.json"
LANGUAGE_AUDIT = ROOT / "out/script/dialogue_legacy_mt_language_anomalies.json"
OUT = ROOT / "out/script/dialogue_legacy_mt_completion_audit.json"

FACE_START = 0x61E400
FACE_END = 0x61F500
CORRUPT_SPECIAL = {"603F33", "603F3D", "603F45", "603F57", "603F72", "603F7C", "603F84", "603F91"}
KNOWN_SHORT_DEFERRED = {"6116F3", "613317", "6192C6", "631E7B"}
JP_CONTENT_RE = re.compile(r"[ぁ-んァ-ン一-龥A-Za-zＡ-Ｚ０-９0-9]")
PUNCT_ONLY_RE = re.compile(r"[………。！？？「」『』（）\s　]+")


def load_done() -> dict[str, str]:
    done: dict[str, str] = {}
    for raw in glob.glob(str(ROOT / "data/dialogue_legacy_mt_literal_batch*.json")):
        doc = json.loads(Path(raw).read_text(encoding="utf-8"))
        for address, ko in (doc.get("targets") or {}).items():
            done[str(address).upper()] = str(ko)
    return done


def is_meaningful_retarget(row: dict) -> bool:
    jp = str(row.get("jp") or "")
    return bool(JP_CONTENT_RE.search(jp)) and not bool(PUNCT_ONLY_RE.fullmatch(jp))


def main() -> int:
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    done = load_done()
    rows = work.get("records") or []
    remaining = [r for r in rows if str(r["abs"]).upper() not in done]

    categories: Counter[str] = Counter()
    samples: dict[str, list[dict]] = {}
    classified: list[dict] = []
    for row in remaining:
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        route = str(row.get("route") or "")
        if FACE_START <= logical < FACE_END:
            category = "face_name_data_table_not_prose"
        elif address in CORRUPT_SPECIAL:
            category = "603f_special_source_not_coherent_japanese"
        elif address in KNOWN_SHORT_DEFERRED:
            category = "short_body_known_text_fix_deferred_no_safe_token"
        elif route == "short_body_requires_stock_route":
            category = "short_body_normal_or_noncritical_no_safe_token"
        elif route == "retarget_body_to_ext3" and is_meaningful_retarget(row):
            category = "retarget_meaningful_current_render_acceptable"
        elif route == "retarget_body_to_ext3":
            category = "retarget_punctuation_or_nonprose_no_change"
        elif route == "existing_ext3_portal":
            category = "portal_static_scanned_no_high_confidence_change"
        else:
            category = "unclassified"
        categories[category] += 1
        item = {
            "abs": address,
            "route": route,
            "jp": row.get("jp"),
            "current": row.get("current_render"),
            "category": category,
        }
        classified.append(item)
        samples.setdefault(category, [])
        if len(samples[category]) < 8:
            samples[category].append(item)

    build = json.loads(CANDIDATE_REPORT.read_text(encoding="utf-8"))
    acceptance = json.loads(ACCEPTANCE.read_text(encoding="utf-8"))
    remaining_audit = json.loads(REMAINING_AUDIT.read_text(encoding="utf-8"))
    language_audit = json.loads(LANGUAGE_AUDIT.read_text(encoding="utf-8"))

    total = len(rows)
    accounted = len(done) + len(remaining)
    gates = {
        "entire_forensic_population_accounted": accounted == total,
        "all_remaining_classified": categories.get("unclassified", 0) == 0,
        "candidate_target_count_matches_batches": int(build.get("targets", -1)) == len(done),
        "candidate_acceptance_overall_ok": bool(acceptance.get("overall_ok")),
        "remaining_audit_covers_current_remaining": int(remaining_audit.get("remaining", -1)) == len(remaining),
        "language_anomaly_audit_covers_current_remaining": int(language_audit.get("remaining", -1)) == len(remaining),
    }
    report = {
        "schema_version": 1,
        "status": "static_full_population_scan_complete" if all(gates.values()) else "incomplete",
        "scope_note": "Static full-population scan is complete. This is not a claim that every unchanged row received a new manual translation; unchanged rows were retained when no high-confidence defect was established.",
        "worklist_total": total,
        "batched_retranslations": len(done),
        "remaining_classified": len(remaining),
        "remaining_categories": dict(categories),
        "known_deferred_addresses": sorted(KNOWN_SHORT_DEFERRED),
        "special_source_addresses": sorted(CORRUPT_SPECIAL),
        "candidate_sha256": (build.get("candidate") or {}).get("sha256"),
        "gates": gates,
        "overall_ok": all(gates.values()),
        "samples": samples,
        "rows": classified,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "worklist_total", "batched_retranslations", "remaining_classified", "remaining_categories", "candidate_sha256", "gates", "overall_ok")}, ensure_ascii=False, indent=2))
    print(OUT)
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
