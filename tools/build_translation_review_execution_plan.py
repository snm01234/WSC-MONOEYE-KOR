#!/usr/bin/env python3
"""Prepare current-TIP LLM re-review batches without translating or applying them.

Scenario records are grouped by their existing one-/two-line bundle so a future
LLM pass receives both lines together.  Battle/ID/structural rows retain their
existing static batch IDs and are tagged with the next required decision.  The
outputs are staging inputs only; no Korean text is changed and no ROM is built.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "out/script/main_translation_llm_review/results"
STATIC_QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
CANONICAL_SHEET = ROOT / "out/script/translation_sheet.csv"
OUT_DIR = ROOT / "out/script/translation_review_execution_batches"
OUT_INDEX = ROOT / "out/script/translation_review_execution_batch_index.csv"
OUT_REPORT = ROOT / "out/script/translation_review_execution_plan.json"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"

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


def compact_record(row: dict[str, str]) -> dict[str, str]:
    """Keep context packets auditable while avoiding unrelated columns."""
    return {
        key: str(row.get(key) or "")
        for key in ("abs", "kind", "jp", "ko", "prefix_hex", "body_hex", "notes")
    }


def hex_key(value: str) -> tuple[int, str]:
    try:
        return int(str(value or "0"), 16), str(value or "")
    except ValueError:
        return 1 << 60, str(value or "")


def scenario_records(canonical: list[dict[str, str]]) -> list[dict[str, str]]:
    canonical_by_abs = {str(row.get("abs") or "").upper(): row for row in canonical}
    canonical_by_bank: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in canonical:
        abs_addr = str(row.get("abs") or "").upper()
        if len(abs_addr) >= 2:
            canonical_by_bank[abs_addr[:2]].append(row)
    canonical_index = {
        str(row.get("abs") or "").upper(): index
        for rows in canonical_by_bank.values()
        for index, row in enumerate(rows)
    }
    records: list[dict[str, str]] = []
    for path in sorted(RESULTS.glob("MR*_reviewed.csv")):
        for row in read_csv(path):
            status = str(row.get("review_status") or "")
            semantic = status == "llm_retranslated_structural_hold"
            provenance_ok = row.get("explicit_llm_provenance") == "yes" and row.get("completed_review_evidence") == "yes"
            if semantic:
                decision = "llm_rereview_required" if not provenance_ok else "provenance_sufficient_structural_hold"
                reason = "missing_explicit_llm_provenance_or_completed_review_evidence" if not provenance_ok else "provenance_present_but_structural_gate_pending"
            else:
                decision = "manual_contract_before_llm"
                reason = "scenario_structural_quarantine"
            records.append(
                {
                    "record_type": "scenario_result",
                    "workstream": "scenario",
                    "source_file": str(path.relative_to(ROOT).as_posix()),
                    "source_batch_id": str(row.get("batch_id") or ""),
                    "address_or_slot": str(row.get("abs") or "").upper(),
                    "bundle_id": str(row.get("bundle_id") or ""),
                    "bundle_order": str(row.get("bundle_order") or ""),
                    "line_role": str(row.get("line_role") or ""),
                    "priority": str(row.get("priority") or "P3"),
                    "route": str(row.get("route") or "scenario"),
                    "context_required": "yes",
                    "source_jp": str(row.get("source_jp") or ""),
                    "current_ko": str(row.get("current_ko") or ""),
                    "prior_proposed_ko_reference": str(row.get("proposed_ko") or ""),
                    "prior_proposed_authoritative": "no",
                    "explicit_llm_provenance": str(row.get("explicit_llm_provenance") or "no"),
                    "completed_review_evidence": str(row.get("completed_review_evidence") or "no"),
                    "quality_flags": str(row.get("quality_flags") or ""),
                    "decision": decision,
                    "decision_reason": reason,
                    "promotion_allowed": "no",
                    "context_bundle_json": "[]",
                    "context_neighbors_json": "[]",
                    "context_source": "translation_sheet.csv;canonical-address-order",
                }
            )
    by_bundle: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in records:
        by_bundle[row["bundle_id"] or f"single_{row['address_or_slot']}"] .append(row)
    for bundle_rows in by_bundle.values():
        bundle_rows.sort(key=lambda item: (int(item["bundle_order"] or 0), hex_key(item["address_or_slot"])))
        bundle_abs = {item["address_or_slot"] for item in bundle_rows}
        # The bundle payload is authoritative source + current sheet data,
        # not the previous proposed Korean text.
        bundle_payload = []
        for item in bundle_rows:
            source = canonical_by_abs.get(item["address_or_slot"], {})
            bundle_payload.append(
                {
                    "abs": item["address_or_slot"],
                    "line_role": item["line_role"],
                    "source_jp": item["source_jp"],
                    "current_ko": item["current_ko"],
                    "prefix_hex": str(source.get("prefix_hex") or ""),
                    "body_hex": str(source.get("body_hex") or ""),
                }
            )
        first_abs = bundle_rows[0]["address_or_slot"]
        bank = first_abs[:2]
        ordered_bank = canonical_by_bank.get(bank, [])
        positions = [canonical_index.get(item["address_or_slot"]) for item in bundle_rows]
        valid_positions = [position for position in positions if position is not None]
        if valid_positions:
            start = min(valid_positions)
            end = max(valid_positions)
            neighbor_rows = ordered_bank[max(0, start - 2):start] + ordered_bank[end + 1:end + 3]
            neighbor_rows = [row for row in neighbor_rows if str(row.get("abs") or "").upper() not in bundle_abs]
        else:
            neighbor_rows = []
        neighbors_payload = [compact_record(row) for row in neighbor_rows]
        for item in bundle_rows:
            item["context_bundle_json"] = json.dumps(bundle_payload, ensure_ascii=False, separators=(",", ":"))
            item["context_neighbors_json"] = json.dumps(neighbors_payload, ensure_ascii=False, separators=(",", ":"))
    return records


def static_records(canonical: list[dict[str, str]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for row in read_csv(STATIC_QUEUE):
        status = str(row.get("status") or "")
        workstream = str(row.get("workstream") or "")
        if status == "fixed_data_structural_excluded_non_dialogue":
            decision = "excluded_non_dialogue"
        elif status in {"scenario_gap_structural_preclear", "scenario_structural_quarantine", "leading_fragment_quarantine", "template_or_stub_quarantine"}:
            decision = "manual_contract_before_llm"
        elif status == "stale_parent_tip_rebase":
            decision = "rebase_from_current_tip"
        elif status.endswith("native_stock_reuse_ready"):
            decision = "native_stock_static_review_pending_approval"
        else:
            decision = "storage_and_encoding_review"
        context = "yes" if workstream == "scenario" else "no"
        records.append(
            {
                "record_type": "static_queue",
                "workstream": workstream,
                "source_file": str(STATIC_QUEUE.relative_to(ROOT).as_posix()),
                "source_batch_id": str(row.get("batch_id") or ""),
                "address_or_slot": str(row.get("address_or_slot") or "").upper(),
                "bundle_id": "",
                "bundle_order": str(row.get("batch_order") or ""),
                "line_role": "",
                "priority": "P3",
                "route": workstream,
                "context_required": context,
                "source_jp": str(row.get("source_jp") or ""),
                "current_ko": str(row.get("current_ko") or ""),
                "prior_proposed_ko_reference": "",
                "prior_proposed_authoritative": "no",
                "explicit_llm_provenance": "not_applicable",
                "completed_review_evidence": "not_applicable",
                "quality_flags": str(row.get("reason") or ""),
                "decision": decision,
                "decision_reason": str(row.get("reason") or ""),
                "promotion_allowed": "no",
                "status": status,
                "prefix_hex": str(row.get("prefix_hex") or ""),
                "body_hex": str(row.get("body_hex") or ""),
                "context_bundle_json": str(row.get("context_bundle_json") or "[]"),
                "context_neighbors_json": str(row.get("context_neighbors_json") or "[]"),
                "context_source": "translation_sheet.csv;static_queue_context" if workstream == "scenario" else "not_required",
            }
        )
    return records


def write_batch(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    canonical = read_csv(CANONICAL_SHEET)
    scenario = scenario_records(canonical)
    static = static_records(canonical)
    # Existing static queue batches are retained.  Scenario semantic results
    # are rebuilt into chronological bundle-preserving SR batches.
    scenario.sort(key=lambda row: (hex_key(row["address_or_slot"]), row["bundle_id"], int(row["bundle_order"] or 0)))
    grouped: list[list[dict[str, str]]] = []
    by_bundle: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in scenario:
        by_bundle[row["bundle_id"] or f"single_{row['address_or_slot']}"] .append(row)
    bundles = sorted(by_bundle.values(), key=lambda bundle: hex_key(bundle[0]["address_or_slot"]))
    current: list[dict[str, str]] = []
    for bundle in bundles:
        if current and len(current) + len(bundle) > 60:
            grouped.append(current)
            current = []
        current.extend(bundle)
    if current:
        grouped.append(current)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Do not delete any existing files; overwrite only the deterministic SR
    # batch names that this plan owns.
    batch_rows: list[dict[str, str]] = []
    index_rows: list[dict[str, str]] = []
    for i, rows in enumerate(grouped, 1):
        batch_id = f"SR{i:04d}"
        for order, row in enumerate(rows, 1):
            row = dict(row)
            row["execution_batch_id"] = batch_id
            row["execution_batch_order"] = str(order)
            batch_rows.append(row)
        file_path = OUT_DIR / f"{batch_id}.csv"
        write_batch(file_path, [dict(r, execution_batch_id=batch_id, execution_batch_order=str(n)) for n, r in enumerate(rows, 1)])
        index_rows.append(
            {
                "execution_batch_id": batch_id,
                "batch_kind": "scenario_bundle_rereview",
                "rows": str(len(rows)),
                "bundles": str(len({r["bundle_id"] for r in rows})),
                "context_required": "yes",
                "llm_execution": "not_run",
                "promotion_allowed": "no",
                "source_tip_sha256": digest(TIP),
                "path": str(file_path.relative_to(ROOT).as_posix()),
            }
        )

    # Preserve static batch IDs and create an index entry for every static
    # batch, including explicit non-dialogue exclusions.
    by_static: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in static:
        by_static[row["source_batch_id"] or "UNBATCHED"].append(row)
    for batch_id in sorted(by_static):
        rows = by_static[batch_id]
        path = OUT_DIR / f"{batch_id}.csv"
        write_batch(path, [dict(r, execution_batch_id=batch_id, execution_batch_order=str(i)) for i, r in enumerate(sorted(rows, key=lambda r: hex_key(r["address_or_slot"])), 1)])
        index_rows.append(
            {
                "execution_batch_id": batch_id,
                "batch_kind": "static_queue_hold_or_exclusion",
                "rows": str(len(rows)),
                "bundles": "0",
                "context_required": "yes" if any(r["context_required"] == "yes" for r in rows) else "no",
                "llm_execution": "not_run",
                "promotion_allowed": "no",
                "source_tip_sha256": digest(TIP),
                "path": str(path.relative_to(ROOT).as_posix()),
            }
        )

    index_rows.sort(key=lambda row: row["execution_batch_id"])
    with OUT_INDEX.open("w", encoding="utf-8-sig", newline="") as fh:
        fields = list(index_rows[0].keys()) if index_rows else []
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(index_rows)

    decision_counts = Counter(row["decision"] for row in scenario + static)
    scenario_status_counts = Counter(
        "semantic" if row["decision"] in {"llm_rereview_required", "provenance_sufficient_structural_hold"} else "structural_quarantine"
        for row in scenario
    )
    scenario_provenance_counts = Counter(
        (row["explicit_llm_provenance"], row["completed_review_evidence"])
        for row in scenario
    )
    scenario_context_counts = Counter(
        "has_neighbors" if row.get("context_neighbors_json") not in {"", "[]"} else "no_neighbors_at_bank_edge"
        for row in scenario
    )
    report = {
        "schema_version": 1,
        "artifact": "translation-review-execution-plan/v1",
        "read_only_staging": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "requested_model": "GPT-5.6 Luna",
        "executed_model": None,
        "model_note": "Luna is unavailable in this runtime; no LLM translation call was executed",
        "main_tip_sha256": digest(TIP),
        "counts": {
            "scenario_result_rows": len(scenario),
            "scenario_bundle_batches": len(grouped),
            "scenario_bundles": len(bundles),
            "static_queue_rows": len(static),
            "static_batches": len(by_static),
            "execution_batches": len(index_rows),
            "decision_rows": len(scenario) + len(static),
        },
        "decision_counts": dict(sorted(decision_counts.items())),
        "scenario_provenance": {
            "status_counts": dict(sorted(scenario_status_counts.items())),
            "evidence_pair_counts": {
                f"explicit={explicit};completed={completed}": count
                for (explicit, completed), count in sorted(scenario_provenance_counts.items())
            },
            "mandatory_rereview_rule": "explicit_llm_provenance != yes OR completed_review_evidence != yes",
        },
        "scenario_context": {
            "rows_with_bundle_context": sum(1 for row in scenario if row.get("context_bundle_json") not in {"", "[]"}),
            "rows_with_neighbor_context": scenario_context_counts.get("has_neighbors", 0),
            "rows_at_bank_edge_without_neighbors": scenario_context_counts.get("no_neighbors_at_bank_edge", 0),
            "neighbor_policy": "up to two preceding and two following canonical records in the same bank, excluding the current bundle",
            "bundle_policy": "all rows sharing bundle_id are kept in one execution batch",
        },
        "policies": {
            "scenario_context": "first/continuation rows stay in the same bundle; no global wrap is inserted",
            "battle_context": "short battle lines do not require scenario-neighbor context",
            "proposal_authority": "prior proposed Korean is reference only; new result must carry explicit model/provenance and review evidence",
            "fixed_data": "structural non-dialogue exclusions remain no-translation rows",
            "promotion": "all batches are not promotable until the authoritative contract/safety gate passes",
        },
        "outputs": {
            "batch_directory": str(OUT_DIR.relative_to(ROOT).as_posix()),
            "batch_index": str(OUT_INDEX.relative_to(ROOT).as_posix()),
            "report": str(OUT_REPORT.relative_to(ROOT).as_posix()),
        },
        "checks": {
            "scenario_bundle_not_split": all(int(row["rows"]) <= 60 for row in index_rows if row["batch_kind"] == "scenario_bundle_rereview"),
            "all_scenario_rows_planned": len(scenario) == sum(int(row["rows"]) for row in index_rows if row["batch_kind"] == "scenario_bundle_rereview"),
            "all_static_rows_planned": len(static) == sum(int(row["rows"]) for row in index_rows if row["batch_kind"] == "static_queue_hold_or_exclusion"),
            "no_translation_executed": True,
            "no_promotion_allowed": all(row["promotion_allowed"] == "no" for row in index_rows),
            "all_scenario_rows_have_bundle_context": all(row.get("context_bundle_json") not in {"", "[]"} for row in scenario),
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = all(report["checks"].values())
    print(json.dumps({"ok": ok, "counts": report["counts"], "decision_counts": report["decision_counts"], "output": str(OUT_REPORT.relative_to(ROOT))}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
