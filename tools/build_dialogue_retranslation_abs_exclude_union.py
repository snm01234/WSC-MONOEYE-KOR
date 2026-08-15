#!/usr/bin/env python3
"""Build curated-abs exclude union and residual report against refreshed worklists.

Inputs (forensic / curated only; never treats blocked KO as translation source):
  - data/*_ko.json and dialogue_legacy_mt_literal_batch001-017
  - recent promotion direct_proof / allocation abs keys
  - out/script/dialogue_legacy_source_retranslation_worklist.json
  - out/script/dialogue_legacy_mt_retranslation_worklist.json

Outputs:
  - out/script/dialogue_retranslation_abs_exclude_union.json
  - out/script/dialogue_legacy_retranslation_residual_report.json
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SOURCE_WL = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
MT_WL = ROOT / "out/script/dialogue_legacy_mt_retranslation_worklist.json"
OUT_EXCLUDE = ROOT / "out/script/dialogue_retranslation_abs_exclude_union.json"
OUT_REPORT = ROOT / "out/script/dialogue_legacy_retranslation_residual_report.json"

EXPECTED_TIP = "edb0b2502753a6682b63ea535f65fd3fa017923b21cdb8ed06d8a30f32edf248"
ABS_RE = re.compile(r"^[0-9A-Fa-f]{5,6}$")

PROMOTE_REPORTS = [
    ROOT / "out/patch/garrod_loran_guen_literal_retranslation_promotion_report.json",
    ROOT / "out/patch/domon_master_asia_mt_source_retranslation_promotion_report.json",
    ROOT / "out/patch/domon_scenario_626509_62663E_promotion_report.json",
    ROOT / "out/patch/domon_followup_retranslation_promotion_report.json",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def add_abs(bucket: set[str], value: Any) -> None:
    text = str(value or "").strip().upper().replace("0X", "")
    if ABS_RE.fullmatch(text):
        bucket.add(f"{int(text, 16):06X}")


def collect_from_ko_json(path: Path, into: set[str], by_source: Counter[str]) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    before = len(into)
    if isinstance(doc.get("targets"), dict):
        for key in doc["targets"]:
            add_abs(into, key)
    for row in doc.get("entries") or []:
        if isinstance(row, dict):
            add_abs(into, row.get("abs"))
    for key in ("addresses", "abs_list", "records"):
        val = doc.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    add_abs(into, item.get("abs"))
                else:
                    add_abs(into, item)
    gained = len(into) - before
    if gained:
        by_source[path.name] += gained
    return gained


def collect_promote_proofs(into: set[str], by_source: Counter[str]) -> None:
    for path in PROMOTE_REPORTS:
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        before = len(into)
        proof = doc.get("direct_proof") or {}
        if isinstance(proof, dict):
            for key in proof:
                add_abs(into, key)
        for row in doc.get("allocations") or []:
            if isinstance(row, dict):
                add_abs(into, row.get("abs"))
        for row in (doc.get("summary") or {}).get("addresses") or []:
            add_abs(into, row)
        gained = len(into) - before
        if gained:
            by_source[path.name] += gained


def record_abs(row: dict[str, Any]) -> str | None:
    for key in ("abs", "address", "logical"):
        if key in row:
            text = str(row[key]).strip().upper().replace("0X", "")
            if ABS_RE.fullmatch(text):
                return f"{int(text, 16):06X}"
    return None


def bank_of(abs_hex: str) -> str:
    return f"{int(abs_hex, 16) >> 16:02X}"


def main() -> int:
    tip_sha = sha256_file(MAIN)
    if tip_sha != EXPECTED_TIP:
        raise SystemExit(f"main TIP SHA drifted: {tip_sha}")

    source = json.loads(SOURCE_WL.read_text(encoding="utf-8"))
    mt = json.loads(MT_WL.read_text(encoding="utf-8"))
    if str(source.get("summary", {}).get("main_tip_sha256") or "").lower() != tip_sha:
        # some builders put sha at top-level when printing; file has summary
        pass
    src_summary = source.get("summary") or source
    mt_summary = mt.get("summary") or mt
    if str(src_summary.get("main_tip_sha256") or "").lower() != tip_sha:
        raise SystemExit("source worklist SHA mismatch")
    if str(mt_summary.get("main_tip_sha256") or "").lower() != tip_sha:
        raise SystemExit("MT worklist SHA mismatch")

    exclude: set[str] = set()
    by_source: Counter[str] = Counter()

    for path in sorted((ROOT / "data").glob("*_ko.json")):
        collect_from_ko_json(path, exclude, by_source)
    for path in sorted((ROOT / "data").glob("dialogue_legacy_mt_literal_batch*.json")):
        collect_from_ko_json(path, exclude, by_source)
    for path in sorted((ROOT / "data").glob("dialogue_singleton_rewrite_batch*.json")):
        collect_from_ko_json(path, exclude, by_source)
    collect_promote_proofs(exclude, by_source)

    source_records = list(source.get("records") or [])
    mt_records = list(mt.get("records") or [])
    source_abs = {a for row in source_records if (a := record_abs(row))}
    mt_abs = {a for row in mt_records if (a := record_abs(row))}

    source_remaining = sorted(source_abs - exclude)
    mt_remaining = sorted(mt_abs - exclude)
    both_remaining = sorted(set(source_remaining) & set(mt_remaining))

    def route_bank(rows: list[dict[str, Any]], remaining: set[str]) -> tuple[dict[str, int], dict[str, int]]:
        banks: Counter[str] = Counter()
        routes: Counter[str] = Counter()
        for row in rows:
            abs_hex = record_abs(row)
            if not abs_hex or abs_hex not in remaining:
                continue
            banks[bank_of(abs_hex)] += 1
            route = str(row.get("route") or row.get("repair_route_hint") or row.get("suggested_route") or "unknown")
            routes[route] += 1
        return dict(sorted(banks.items())), dict(sorted(routes.items()))

    rem_set_src = set(source_remaining)
    rem_set_mt = set(mt_remaining)
    src_banks, src_routes = route_bank(source_records, rem_set_src)
    mt_banks, mt_routes = route_bank(mt_records, rem_set_mt)

    # High-confidence seed: MT residual ∩ source residual (Bing exact still on tip).
    batch018_seed = both_remaining
    mt_by_abs = {a: row for row in mt_records if (a := record_abs(row))}
    retarget_seed = sorted(
        a
        for a in batch018_seed
        if str((mt_by_abs.get(a) or {}).get("route") or "") == "retarget_body_to_ext3"
    )
    short_seed = sorted(
        a
        for a in batch018_seed
        if str((mt_by_abs.get(a) or {}).get("route") or "") == "short_body_requires_stock_route"
    )
    # Prior literal batches were ~78-183 addresses; first slice stays in that band.
    batch018_first_slice = retarget_seed[:120]
    scope_decision = {
        "recommended_next": "dialogue_legacy_mt_literal_batch018",
        "rationale": (
            "After TIP edb0b250 refresh, blocked-source residuals fell from ~6031 to "
            f"{int(src_summary.get('proven_blocked_source_records', len(source_abs)))}; "
            f"Bing-exact MT residuals are {int(mt_summary.get('proven_legacy_mt_records', len(mt_abs)))}. "
            "Next pass should be high-confidence MT∩source remaining addresses only, "
            "not a full cold retranslation. Scene-scoped data/*_ko.json remains preferred "
            "for user-reported scenes."
        ),
        "batch018_seed_population": len(batch018_seed),
        "batch018_seed_retarget_ext3": len(retarget_seed),
        "batch018_seed_short_stock": len(short_seed),
        "batch018_first_slice_size": len(batch018_first_slice),
        "batch018_first_slice_policy": "first 120 of retarget_body_to_ext3 from MT∩source seed; JP from pristine ROM only",
        "batch018_first_slice_abs": batch018_first_slice,
        "later_slices": "batch019+ continue retarget remainder then short_stock_route; re-run worklists after each promote",
        "scene_ko_json_preferred_when": "user runtime screenshot / named scene with mixed JP or terminology",
        "do_not": [
            "reapply translations_quality* or translation_sheet.csv",
            "use current_main_unanimous_duplicate of tip KO as translation source",
            "full TIP rebuild via build_monoeye_ko_all",
            "dump entire 429-seed into a single batch without defect triage",
        ],
    }
    # Full seed list lives in the residual report under residual_after_exclude, not as the batch size.

    exclude_doc = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_retranslation_abs_exclude_union.py",
        "main_tip_sha256": tip_sha,
        "summary": {
            "exclude_abs_count": len(exclude),
            "sources_contributing": len(by_source),
            "top_sources": by_source.most_common(20),
        },
        "counts_by_source_file": dict(sorted(by_source.items())),
        "exclude_abs": sorted(exclude),
    }
    OUT_EXCLUDE.write_text(json.dumps(exclude_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_retranslation_abs_exclude_union.py",
        "main_tip_sha256": tip_sha,
        "worklists": {
            "source": {
                "path": str(SOURCE_WL.relative_to(ROOT)).replace("\\", "/"),
                "proven_blocked_source_records": int(src_summary.get("proven_blocked_source_records", len(source_abs))),
                "evidence_counts": src_summary.get("evidence_counts"),
                "bank_counts_worklist": src_summary.get("bank_counts"),
                "route_counts_worklist": src_summary.get("route_counts"),
            },
            "mt": {
                "path": str(MT_WL.relative_to(ROOT)).replace("\\", "/"),
                "proven_legacy_mt_records": int(mt_summary.get("proven_legacy_mt_records", len(mt_abs))),
                "bank_counts_worklist": mt_summary.get("bank_counts"),
                "route_counts_worklist": mt_summary.get("route_counts"),
            },
            "snapshot_note": "Previous inventory (~6031 blocked-source) was tip SHA 6425767b…; this report is tip edb0b250…",
        },
        "exclude_union": {
            "path": str(OUT_EXCLUDE.relative_to(ROOT)).replace("\\", "/"),
            "count": len(exclude),
        },
        "residual_after_exclude": {
            "blocked_source_remaining": len(source_remaining),
            "legacy_mt_remaining": len(mt_remaining),
            "intersection_mt_and_source": len(both_remaining),
            "blocked_source_only": len(set(source_remaining) - set(mt_remaining)),
            "legacy_mt_only": len(set(mt_remaining) - set(source_remaining)),
            "source_bank_counts": src_banks,
            "source_route_counts": src_routes,
            "mt_bank_counts": mt_banks,
            "mt_route_counts": mt_routes,
            "mt_and_source_seed_abs": both_remaining,
        },
        "scope_decision": scope_decision,
        "delta_vs_stale_inventory": {
            "stale_blocked_source": 6031,
            "current_blocked_source": int(src_summary.get("proven_blocked_source_records", len(source_abs))),
            "reduction": 6031 - int(src_summary.get("proven_blocked_source_records", len(source_abs))),
            "stale_bing_mt_approx": 1232,
            "current_bing_mt": int(mt_summary.get("proven_legacy_mt_records", len(mt_abs))),
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "tip_sha256": tip_sha,
                "exclude_abs": len(exclude),
                "blocked_source_worklist": report["worklists"]["source"]["proven_blocked_source_records"],
                "legacy_mt_worklist": report["worklists"]["mt"]["proven_legacy_mt_records"],
                "blocked_source_remaining_after_exclude": len(source_remaining),
                "legacy_mt_remaining_after_exclude": len(mt_remaining),
                "batch018_seed_population": len(batch018_seed),
                "batch018_first_slice": len(batch018_first_slice),
                "exclude_out": str(OUT_EXCLUDE.relative_to(ROOT)).replace("\\", "/"),
                "report_out": str(OUT_REPORT.relative_to(ROOT)).replace("\\", "/"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
