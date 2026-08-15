#!/usr/bin/env python3
"""Audit the read-only LLM review execution packets against the current TIP."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "out/script/translation_review_execution_plan.json"
INDEX = ROOT / "out/script/translation_review_execution_batch_index.csv"
QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
COVERAGE = ROOT / "out/script/translation_workstream_coverage_audit.json"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
OUT = ROOT / "out/script/translation_review_execution_plan_audit.json"

csv.field_size_limit(100_000_000)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    index = read_csv(INDEX)
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    queue = read_csv(QUEUE)
    expected_tip = digest(TIP)
    files_exist = True
    file_counts_match = True
    index_rows: list[dict[str, str]] = []
    scenario_rows: list[dict[str, str]] = []
    static_rows: list[dict[str, str]] = []
    for entry in index:
        path = ROOT / str(entry["path"])
        exists = path.is_file()
        files_exist = files_exist and exists
        rows = read_csv(path) if exists else []
        file_counts_match = file_counts_match and len(rows) == int(entry["rows"])
        index_rows.append(entry)
        if entry["batch_kind"] == "scenario_bundle_rereview":
            scenario_rows.extend(rows)
        else:
            static_rows.extend(rows)

    scenario_by_bundle: dict[str, set[str]] = defaultdict(set)
    for row in scenario_rows:
        scenario_by_bundle[row.get("bundle_id") or row.get("address_or_slot")].add(row["execution_batch_id"])
    duplicate_scenario_abs = [address for address, count in Counter(row["address_or_slot"] for row in scenario_rows).items() if count > 1]
    split_bundles = [bundle for bundle, batches in scenario_by_bundle.items() if len(batches) != 1]
    scenario_context_missing = [
        row["address_or_slot"]
        for row in scenario_rows
        if row.get("context_bundle_json") in {None, "", "[]"} or row.get("context_neighbors_json") in {None, "", "[]"}
    ]
    static_duplicate_abs = [address for address, count in Counter(row["address_or_slot"] for row in static_rows).items() if count > 1]
    known_duplicate_abs = set((coverage.get("duplicate_layer_addresses") or {}).keys())
    unexpected_static_duplicate_abs = sorted(set(static_duplicate_abs) - known_duplicate_abs)
    stale_tip_entries = [entry["execution_batch_id"] for entry in index if entry.get("source_tip_sha256") != expected_tip]
    not_promotable = all(entry.get("promotion_allowed") == "no" for entry in index)
    runtime_not_run = plan.get("runtime_trace") == "stopped_by_user" and plan.get("runtime_validation_performed") is False
    report = {
        "schema_version": 1,
        "artifact": "translation-review-execution-plan-audit/v1",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "inputs": {
            "plan": str(PLAN.relative_to(ROOT).as_posix()),
            "index": str(INDEX.relative_to(ROOT).as_posix()),
            "tip_sha256": expected_tip,
        },
        "counts": {
            "index_batches": len(index),
            "scenario_batches": sum(1 for entry in index if entry["batch_kind"] == "scenario_bundle_rereview"),
            "static_batches": sum(1 for entry in index if entry["batch_kind"] == "static_queue_hold_or_exclusion"),
            "scenario_rows": len(scenario_rows),
            "static_rows": len(static_rows),
            "scenario_bundles": len(scenario_by_bundle),
            "scenario_context_missing": len(scenario_context_missing),
            "scenario_duplicate_abs": len(duplicate_scenario_abs),
            "split_bundle_count": len(split_bundles),
            "static_duplicate_abs": len(static_duplicate_abs),
            "unexpected_static_duplicate_abs": len(unexpected_static_duplicate_abs),
            "stale_tip_entries": len(stale_tip_entries),
        },
        "checks": {
            "all_index_files_exist": files_exist,
            "all_index_row_counts_match": file_counts_match,
            "scenario_bundles_not_split": not split_bundles,
            "scenario_addresses_unique": not duplicate_scenario_abs,
            "all_scenario_rows_have_bundle_and_neighbor_context": not scenario_context_missing,
            "static_duplicate_layers_are_known_overlap": not unexpected_static_duplicate_abs,
            "all_batches_current_tip_bound": not stale_tip_entries,
            "all_batches_not_promotable": not_promotable,
            "runtime_not_run": runtime_not_run,
            "queue_row_count_matches_plan": len(static_rows) == len(queue),
        },
        "details": {
            "split_bundles": split_bundles,
            "duplicate_scenario_abs": duplicate_scenario_abs,
            "scenario_context_missing": scenario_context_missing,
            "unexpected_static_duplicate_abs": unexpected_static_duplicate_abs,
            "stale_tip_entries": stale_tip_entries,
            "static_duplicate_abs": static_duplicate_abs,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = all(report["checks"].values())
    print(json.dumps({"ok": ok, "counts": report["counts"], "output": str(OUT.relative_to(ROOT))}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
