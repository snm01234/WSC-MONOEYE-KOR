#!/usr/bin/env python3
"""Classify scenario rows that still lack a contract-bound review result.

This is a read-only structural audit.  It does not infer Korean, create a
translation result, or write any ROM/sheet data.  Its purpose is to make the
remaining gap explicit: rows absent from the structural preclear inventory are
different from rows present there but intentionally left without a runtime
contract binding.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
PRECLEAR = ROOT / "out/script/main_translation_llm_review/structural_preclear.csv"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
SHEET = ROOT / "out/script/translation_sheet.csv"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
OUT = ROOT / "out/script/scenario_gap_structural_manifest.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(10**9)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_json_array(value: str) -> list[dict[str, object]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def main() -> int:
    queue = read_csv(QUEUE)
    preclear = {
        str(row.get("abs") or "").upper(): row
        for row in read_csv(PRECLEAR)
    }
    canonical = {
        str(row.get("abs") or "").upper(): row
        for row in read_csv(SHEET)
    }
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))
    active = {
        str(row.get("address") or "").upper()
        for row in contracts.get("contracts") or []
        if str(row.get("status") or "") == "active"
    }
    contract_by_address = {
        str(row.get("address") or "").upper(): row
        for row in contracts.get("contracts") or []
    }

    target_statuses = {
        "scenario_gap_structural_preclear",
        "scenario_structural_quarantine",
    }
    targets = [row for row in queue if row.get("status") in target_statuses]
    details: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for row in targets:
        address = str(row.get("address_or_slot") or "").upper()
        pre = preclear.get(address, {})
        status = str(row.get("status") or "")
        if status == "scenario_gap_structural_preclear":
            if not pre:
                category = "missing_structural_preclear_record"
                evidence = "canonical scenario row is absent from structural_preclear.csv"
            elif not str(pre.get("bundle_id") or ""):
                category = "preclear_unbound_contract"
                evidence = str(pre.get("reason") or "no bundle/contract binding")
            else:
                category = "preclear_contract_binding_unresolved"
                evidence = str(pre.get("reason") or "contract binding unresolved")
        else:
            category = "semantic_quarantine_result"
            evidence = str(row.get("reason") or "semantic result is quarantined")
        counts[category] += 1
        bundle_context = parse_json_array(str(row.get("context_bundle_json") or ""))
        neighbor_context = parse_json_array(str(row.get("context_neighbors_json") or ""))
        contract = contract_by_address.get(address, {})
        details.append({
            "abs": address,
            "bank": address[:2],
            "queue_batch_id": str(row.get("batch_id") or ""),
            "queue_batch_order": str(row.get("batch_order") or ""),
            "status": status,
            "category": category,
            "evidence": evidence,
            "source": str(row.get("source") or ""),
            "jp": str(row.get("source_jp") or canonical.get(address, {}).get("jp") or ""),
            "current_ko": str(row.get("current_ko") or canonical.get(address, {}).get("ko") or ""),
            "prefix_hex": str(row.get("prefix_hex") or canonical.get(address, {}).get("prefix_hex") or ""),
            "body_hex": str(row.get("body_hex") or canonical.get(address, {}).get("body_hex") or ""),
            "preclear_bundle_id": str(pre.get("bundle_id") or ""),
            "preclear_reason": str(pre.get("reason") or ""),
            "preclear_main_tip_sha256": str(pre.get("main_tip_sha256") or ""),
            "active_contract_present": address in active,
            "contract_status": str(contract.get("status") or "none"),
            "contract_route": str(contract.get("route") or ""),
            "contract_confidence": str(contract.get("confidence") or ""),
            "contract_conflict": str(contract.get("conflict") or ""),
            "context_required": str(row.get("context_required") or "") == "yes",
            "context_bundle": bundle_context,
            "context_neighbors": neighbor_context,
            "translation_action": (
                "bind_or_quarantine_contract_before_semantic_review"
                if status == "scenario_gap_structural_preclear"
                else "preserve_quarantine_until_semantic_and_structural_review_complete"
            ),
            "application_allowed": False,
        })

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_gap_static.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "scenario gap has no complete contract-bound semantic result; no application performed",
        "inputs": {
            "queue": str(QUEUE.relative_to(ROOT)).replace("\\", "/"),
            "preclear": str(PRECLEAR.relative_to(ROOT)).replace("\\", "/"),
            "contract": str(CONTRACT.relative_to(ROOT)).replace("\\", "/"),
            "sheet_sha256": sha(SHEET),
            "main_rom_sha256": sha(ROM),
            "contract_sha256": sha(CONTRACT),
        },
        "counts": {
            "rows": len(details),
            "status": dict(sorted(Counter(row["status"] for row in details).items())),
            "category": dict(sorted(counts.items())),
            "active_contract_present": sum(bool(row["active_contract_present"]) for row in details),
            "application_allowed": 0,
        },
        "next_actions": [
            "For missing_structural_preclear_record, regenerate a current-TIP structural record before any LLM semantic pass.",
            "For preclear_unbound_contract, resolve caller/role/prefix using maintained static evidence or keep the row quarantined.",
            "For semantic_quarantine_result, do not promote or overwrite the main sheet until the result has complete provenance and structural proof.",
            "Keep runtime/BizHawk fields stopped_by_user; no runtime evidence is required for this inventory step.",
        ],
        "records": details,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": report["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
