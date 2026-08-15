#!/usr/bin/env python3
"""Audit every active translation workstream without runtime execution.

This report is deliberately read-only.  It joins the canonical translation
sheet, scenario LLM-review results, battle-review queue/results, and uncovered
sheet results into one current-TIP-bound inventory.  It does not infer a
translation for a fixed-data row and it never treats a legacy sheet or a
heuristic audit as promotion authority.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SHEET = ROOT / "out/script/translation_sheet.csv"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SCENARIO_RESULTS = ROOT / "out/script/main_translation_llm_review/results"
BATTLE_QUEUE = ROOT / "out/script/battle_dialogue_llm_review_queue.csv"
BATTLE_RESULTS = ROOT / "out/script/battle_dialogue_llm_review/results"
BATTLE_CONTRACT_MAP_DIR = ROOT / "out/script/battle_dialogue_llm_review"
ID_CONTRACT_MAP_DIR = ROOT / "out/script/id_dialogue_llm_review"
STRUCTURAL_PREFLIGHT = ROOT / "out/script/main_translation_llm_review/structural_preclear.csv"
OUT_JSON = ROOT / "out/script/translation_workstreams_static_audit.json"
OUT_CSV = ROOT / "out/script/translation_workstreams_static_queue.csv"
BATCH_INDEX = ROOT / "out/script/translation_workstreams_static_batch_index.csv"
BATCH_DIR = ROOT / "out/script/translation_workstreams_static_batches"
REBASE_REPORT = ROOT / "out/script/rebased_llm_staging/rebase_report.json"
REBASE_HOLD = ROOT / "out/script/rebased_llm_staging/rebase_hold.csv"
REBOUND = ROOT / "out/script/rebased_llm_staging/rebound_exact.csv"

csv.field_size_limit(100_000_000)
JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
CONTROL_RE = re.compile(r"<(?!E62F>)[A-Fa-f0-9]{2,8}>")
SCENARIO_BANKS = {"60", "61", "62", "63"}
FIXED_BANKS = {"64", "65", "66", "67", "68", "69", "6A", "6B", "6C", "6D", "6E", "6F"}
FIXED_INVENTORY = ROOT / "out/patch/bank64_6f_structure_inventory.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def compact_reason(*values: str) -> str:
    return ";".join(v for v in values if v)


def main() -> int:
    before = {
        "rom": digest(ROM),
        "sheet": digest(SHEET),
        "contract": digest(CONTRACT),
        "saveram": digest(SAVE),
    }
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract_by_abs = {str(x["address"]).upper(): x for x in contracts.get("contracts", [])}
    fixed_inventory = {}
    if FIXED_INVENTORY.is_file():
        try:
            fixed_inventory = json.loads(FIXED_INVENTORY.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fixed_inventory = {}
    fixed_data_excluded = bool(
        fixed_inventory.get("ok")
        and (fixed_inventory.get("checks") or {}).get("zero_production_targets")
        and (fixed_inventory.get("checks") or {}).get("promoted_tip_exact")
    )

    canonical = read_csv(SHEET)
    canonical_by_abs = {str(row.get("abs") or "").upper(): row for row in canonical}
    scenario: dict[str, dict[str, str]] = {}
    scenario_manifests: list[dict[str, Any]] = []
    for manifest_path in sorted(SCENARIO_RESULTS.glob("MR*_result_manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scenario_manifests.append(manifest)
        result_path = ROOT / str(manifest["result"])
        for row in read_csv(result_path):
            scenario[str(row.get("abs") or "").upper()] = row
    structural_preclear = read_csv(STRUCTURAL_PREFLIGHT)
    structural_by_abs = {
        str(row.get("abs") or "").upper(): row for row in structural_preclear
    }

    battle_queue = read_csv(BATTLE_QUEUE)
    battle_result_files = sorted(BATTLE_RESULTS.glob("*_reviewed.csv"))
    battle_results: list[dict[str, str]] = []
    for path in battle_result_files:
        battle_results.extend(read_csv(path))
    semantic_ready_static: dict[str, dict[str, Any]] = {}
    semantic_ready_path = ROOT / "out/script/battle_dialogue_llm_review/semantic_ready_static_audit.json"
    if semantic_ready_path.is_file():
        try:
            semantic_ready_static = {
                str(row.get("abs") or "").upper(): row
                for row in (json.loads(semantic_ready_path.read_text(encoding="utf-8")).get("rows") or [])
            }
        except (OSError, json.JSONDecodeError):
            semantic_ready_static = {}
    battle_contract_map_rows: dict[str, dict[str, str]] = {}
    battle_contract_map_files: list[Path] = []
    battle_contract_map_counts: Counter[str] = Counter()
    for path in sorted(BATTLE_CONTRACT_MAP_DIR.glob("BC*_static_map_audit.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        battle_contract_map_files.append(path)
        battle_contract_map_counts.update(report.get("counts") or {})
        for row in report.get("rows") or []:
            if row.get("semantic_quality_ok"):
                item = dict(row)
                item["_source_report"] = str(path.relative_to(ROOT)).replace("\\", "/")
                battle_contract_map_rows[str(row.get("abs") or "").upper()] = item
    id_contract_map_rows: dict[str, dict[str, str]] = {}
    id_contract_map_files: list[Path] = []
    id_contract_map_counts: Counter[str] = Counter()
    for path in sorted(ID_CONTRACT_MAP_DIR.glob("ID*_static_map_audit.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        id_contract_map_files.append(path)
        id_contract_map_counts.update(report.get("counts") or {})
        for row in report.get("rows") or []:
            if row.get("semantic_quality_ok"):
                item = dict(row)
                item["_source_report"] = str(path.relative_to(ROOT)).replace("\\", "/")
                id_contract_map_rows[str(row.get("abs") or "").upper()] = item
    battle_approved_paths = [
        ROOT / "out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv",
        ROOT / "out/script/battle_voice_inline_control_translation_sheet.csv",
    ]
    battle_approved: list[dict[str, str]] = []
    for path in battle_approved_paths:
        battle_approved.extend(read_csv(path))
    uncovered = read_csv(ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv")
    voice_contract_source = read_csv(ROOT / "out/script/runtime_text_residual_voice_sheet.csv")
    id_contract_source = read_csv(ROOT / "out/script/runtime_text_residual_id_bundle_sheet.csv")
    name75_source = read_csv(ROOT / "out/script/name75_battle_id_duplicate_residual_sheet.csv")
    # The approved legacy staging sheets carry their own parent TIP.  The 94
    # result rows are a subset of the ambiguous sheet, so the source sheet is
    # the authoritative parent binding for the union.
    battle_source_rows = read_csv(ROOT / "out/script/battle_voice_ambiguous_translation_sheet.csv")
    parent_by_abs: dict[str, str] = {}
    source_by_abs: dict[str, str] = {}
    reviewed_union: dict[str, dict[str, str]] = {}
    for path, rows in [
        ("out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv", read_csv(battle_approved_paths[0])),
        ("out/script/battle_voice_inline_control_translation_sheet.csv", read_csv(battle_approved_paths[1])),
        ("out/script/uncovered_translation_sheet_llm_reviewed.csv", uncovered),
    ]:
        for row in rows:
            address = str(row.get("abs") or "").upper()
            if not address:
                continue
            reviewed_union[address] = row
            parent_by_abs[address] = str(row.get("parent_tip_sha256") or "").lower()
            source_by_abs[address] = path
    for row in battle_source_rows:
        address = str(row.get("abs") or "").upper()
        if address and address not in parent_by_abs:
            parent_by_abs[address] = str(row.get("parent_tip_sha256") or "").lower()
            source_by_abs[address] = "out/script/battle_voice_ambiguous_translation_sheet.csv"
    for row in battle_results:
        address = str(row.get("abs") or "").upper()
        if address and address not in parent_by_abs:
            parent_by_abs[address] = str(row.get("parent_tip_sha256") or "").lower()
            source_by_abs[address] = "out/script/battle_dialogue_llm_review/results"
    rebase_hold_rows = read_csv(REBASE_HOLD)
    rebase_hold_abs = {str(row.get("abs") or "").upper() for row in rebase_hold_rows}
    rebound_rows = read_csv(REBOUND)

    queue: list[dict[str, str]] = []
    canonical_counts: Counter[str] = Counter()
    bank_counts: Counter[str] = Counter()
    bank_jp: Counter[str] = Counter()
    bank_control: Counter[str] = Counter()
    for row in canonical:
        address = str(row.get("abs") or "").upper()
        bank = address[:2]
        bank_counts[bank] += 1
        ko = str(row.get("ko") or "")
        has_jp = bool(JP_RE.search(ko))
        has_control = bool(CONTROL_RE.search(ko) or "\x00" in ko)
        if has_jp:
            bank_jp[bank] += 1
        if has_control:
            bank_control[bank] += 1
        if bank in SCENARIO_BANKS:
            reviewed = scenario.get(address)
            if reviewed is None:
                status = "scenario_gap_structural_preclear"
                reason = "canonical scenario row has no MR result; retain until contract binding is established"
                queue.append({
                    "workstream": "scenario",
                    "address_or_slot": address,
                    "source": "out/script/translation_sheet.csv",
                    "status": status,
                    "reason": reason,
                    "batch_id": str(structural_by_abs.get(address, {}).get("structural_batch_id") or ""),
                    "source_batch_id": "",
                    "source_jp": str(canonical_by_abs.get(address, {}).get("jp") or ""),
                    "current_ko": str(canonical_by_abs.get(address, {}).get("ko") or ""),
                    "prefix_hex": str(canonical_by_abs.get(address, {}).get("prefix_hex") or ""),
                    "body_hex": str(canonical_by_abs.get(address, {}).get("body_hex") or ""),
                })
            elif str(reviewed.get("new_translation_source") or "") == "llm":
                status = "scenario_llm_staged_structural_hold"
            else:
                status = "scenario_structural_quarantine"
                queue.append({
                    "workstream": "scenario",
                    "address_or_slot": address,
                    "source": str(reviewed.get("result_csv") or "main_translation_llm_review"),
                    "status": status,
                    "reason": str(reviewed.get("reviewer_notes") or reviewed.get("new_review_status") or "semantic review incomplete"),
                    "batch_id": str(reviewed.get("batch_id") or ""),
                    "source_batch_id": "",
                    "source_jp": str(reviewed.get("source_jp") or ""),
                    "current_ko": str(reviewed.get("current_ko") or ""),
                    "prefix_hex": "",
                    "body_hex": str(reviewed.get("source_body_hex") or ""),
                })
        elif bank in FIXED_BANKS:
            # Fixed-stride banks are not safe to send through the scenario
            # decoder.  Japanese/control residue becomes a review queue item,
            # never an automatic translation candidate.
            if fixed_data_excluded:
                status = "fixed_data_structural_excluded_non_dialogue"
                queue.append({
                    "workstream": "canonical_fixed_data",
                    "address_or_slot": address,
                    "source": "out/patch/bank64_6f_structure_inventory.json",
                    "status": status,
                    "reason": "current-TIP Original-boundary inventory excludes banks64-6F from production localization; retain row as structural evidence only",
                    "batch_id": "",
                    "source_batch_id": "",
                    "source_jp": str(row.get("jp") or ""),
                    "current_ko": ko,
                    "prefix_hex": str(row.get("prefix_hex") or ""),
                    "body_hex": str(row.get("body_hex") or ""),
                })
            elif has_jp or has_control or not ko.strip():
                status = "fixed_data_quality_review_required"
                queue.append({
                    "workstream": "canonical_fixed_data",
                    "address_or_slot": address,
                    "source": "out/script/translation_sheet.csv",
                    "status": status,
                    "reason": compact_reason(
                        "japanese_residual" if has_jp else "",
                        "control_or_nul_residual" if has_control else "",
                        "empty_current_ko" if not ko.strip() else "",
                        "fixed_stride_bank_requires_dedicated_decoder",
                    ),
                    "batch_id": "",
                    "source_batch_id": "",
                    "source_jp": str(row.get("jp") or ""),
                    "current_ko": ko,
                    "prefix_hex": str(row.get("prefix_hex") or ""),
                    "body_hex": str(row.get("body_hex") or ""),
                })
            else:
                status = "fixed_data_existing_text_not_reviewed_in_this_pipeline"
                # The canonical sheet has no per-row LLM provenance or review
                # counter.  Under the current quality policy, an otherwise
                # readable fixed-stride value is still a mandatory review
                # target until that evidence is attached; do not silently
                # treat legacy text as approved.
                queue.append({
                    "workstream": "canonical_fixed_data",
                    "address_or_slot": address,
                    "source": "out/script/translation_sheet.csv",
                    "status": "fixed_data_unreviewed_policy_required",
                    "reason": "no_explicit_llm_provenance_or_review_evidence;fixed_stride_bank_requires_dedicated_decoder",
                    "batch_id": "",
                    "source_batch_id": "",
                    "source_jp": str(row.get("jp") or ""),
                    "current_ko": ko,
                    "prefix_hex": str(row.get("prefix_hex") or ""),
                    "body_hex": str(row.get("body_hex") or ""),
                })
        else:
            status = "out_of_scope_bank_static_hold"
        canonical_counts[status] += 1

    battle_counts = Counter(str(row.get("queue_class") or "unknown") for row in battle_queue)
    for row in battle_queue:
        queue_class = str(row.get("queue_class") or "")
        if queue_class == "semantic_llm_review_ready":
            static_row = semantic_ready_static.get(str(row.get("abs") or "").upper(), {})
            direct_fit = bool(static_row.get("direct_encoding_fit"))
            queue.append({
                "workstream": "battle",
                "address_or_slot": str(row.get("abs") or "").upper(),
                "source": str(row.get("source_sheet") or BATTLE_QUEUE),
                "status": (
                    "battle_semantic_direct_fit_structural_hold"
                    if direct_fit
                    else "battle_semantic_encoding_hold"
                ),
                "reason": (
                    "semantic LLM result is complete and direct payload fits measured body capacity; structural preclear still required; no application"
                    if direct_fit
                    else "semantic LLM result is complete; direct payload exceeds measured body capacity; structural/storage proof required; no application"
                ),
                "batch_id": "",
                "source_batch_id": str(row.get("batch_id") or ""),
                "source_jp": str(row.get("original_jp") or ""),
                "current_ko": str(row.get("proposed_ko") or row.get("current_text") or ""),
                "prefix_hex": str(row.get("prefix_hex") or ""),
                "body_hex": str(row.get("current_payload_hex") or ""),
            })
        else:
            queue.append({
                "workstream": "battle",
                "address_or_slot": str(row.get("abs") or "").upper(),
                "source": str(row.get("source_sheet") or BATTLE_QUEUE),
                "status": str(row.get("queue_class") or "unknown"),
                "reason": str(row.get("blocking_reason") or row.get("next_static_action") or "queue hold"),
                # AM batches are source classification groups, not bounded
                # dispatch units; retain their ID separately and re-batch
                # into deterministic BT<=60 units below.
                "batch_id": "",
                "source_batch_id": str(row.get("batch_id") or ""),
                "source_jp": str(row.get("original_jp") or ""),
                "current_ko": str(row.get("current_text") or row.get("ko") or ""),
                "prefix_hex": str(row.get("prefix_hex") or ""),
                "body_hex": str(row.get("current_payload_hex") or ""),
            })

    stale_rebase_rows = 0
    for address, row in sorted(reviewed_union.items()):
        parent = parent_by_abs.get(address, "")
        if parent == before["rom"].lower() or address not in rebase_hold_abs:
            continue
        stale_rebase_rows += 1
        source = source_by_abs.get(address, "reviewed_staging")
        queue.append({
            "workstream": "battle_or_uncovered_rebase",
            "address_or_slot": address,
            "source": source,
            "status": "stale_parent_tip_rebase",
            "reason": compact_reason(
                f"parent_tip_sha256={parent or '<missing>'}",
                f"current_main_tip_sha256={before['rom'].lower()}",
                "retranslate_or_reconfirm_against_current_main_tip",
            ),
            "batch_id": "",
            "source_batch_id": str(row.get("batch_id") or ""),
            "source_jp": str(row.get("original_jp") or row.get("source_jp") or ""),
            "current_ko": str(row.get("ko") or row.get("proposed_ko") or row.get("current_text") or ""),
            "prefix_hex": str(row.get("prefix_hex") or ""),
            "body_hex": str(row.get("current_payload_hex") or row.get("source_body_sha256") or ""),
        })

    # Contract coverage guard: active battle/ID records that are absent from
    # the existing review queue are mandatory re-review targets. Existing
    # bytes are not treated as approved provenance, and no runtime evidence is
    # added while runtime confirmation remains stopped.
    battle_queue_addresses = {
        str(row.get("abs") or "").upper() for row in battle_queue
    }
    queued_addresses = {
        str(row.get("address_or_slot") or "").upper() for row in queue
    }
    voice_source_addresses = {
        str(row.get("record_start") or "").upper() for row in voice_contract_source
    }
    voice_source_by_abs = {
        str(row.get("record_start") or "").upper(): row for row in voice_contract_source
    }
    id_source_addresses = {
        str(row.get("record_start") or "").upper() for row in id_contract_source
    }
    id_source_by_abs = {
        str(row.get("record_start") or "").upper(): row for row in id_contract_source
    }
    name75_source_by_abs = {
        str(row.get("record_start") or "").upper(): row for row in name75_source
    }
    id_source_addresses.update(
        str(row.get("record_start") or "").upper() for row in name75_source
    )
    contract_gap_counts: Counter[str] = Counter()
    for contract in contracts.get("contracts", []):
        if str(contract.get("status") or "") != "active":
            continue
        address = str(contract.get("address") or "").upper()
        route = str(contract.get("route") or "")
        if route.startswith("battle"):
            if address in battle_queue_addresses or address in queued_addresses:
                continue
            source = (
                "out/script/runtime_text_residual_voice_sheet.csv"
                if address in voice_source_addresses
                else "out/script/battle_dialogue_structure_inventory.csv"
            )
            source_row = voice_source_by_abs.get(address, {})
            map_row = battle_contract_map_rows.get(address)
            if map_row:
                source = str(map_row["_source_report"])
            native_stock_fit = bool((map_row or {}).get("native_stock_dictionary_fit"))
            queue.append({
                "workstream": "battle_contract",
                "address_or_slot": address,
                "source": source,
                "status": (
                    "battle_contract_native_stock_reuse_ready"
                    if native_stock_fit
                    else "battle_contract_encoding_hold"
                    if map_row
                    else "battle_contract_unreviewed_policy_required"
                ),
                "reason": (
                    "semantic retranslation complete; exact native stock phrase token is available; apply only after static boundary audit"
                    if native_stock_fit
                    else "semantic retranslation complete; direct payload does not fit; dictionary/storage proof required"
                    if map_row
                    else "active battle contract omitted from battle review queue; no explicit LLM provenance; runtime confirmation stopped"
                ),
                "batch_id": "",
                "source_batch_id": "",
                "source_jp": str(contract.get("original_japanese") or source_row.get("original_body") or ""),
                "current_ko": str((map_row or {}).get("proposed_ko") or source_row.get("suggested_ko") or source_row.get("current_body") or contract.get("baseline_text") or ""),
                "prefix_hex": str(contract.get("source_prefix_hex") or contract.get("metadata_hex") or ""),
                "body_hex": str(contract.get("source_body_hex") or ""),
            })
            queued_addresses.add(address)
            contract_gap_counts["battle_contract"] += 1
        elif route in {"id_first", "id_continuation"}:
            if address in queued_addresses:
                continue
            source_row = name75_source_by_abs.get(address) or id_source_by_abs.get(address, {})
            map_row = id_contract_map_rows.get(address)
            native_stock_fit = bool((map_row or {}).get("native_stock_dictionary_fit"))
            queue.append({
                "workstream": "id_dialogue",
                "address_or_slot": address,
                "source": str(map_row["_source_report"]) if map_row else (
                    "out/script/name75_battle_id_duplicate_residual_sheet.csv"
                    if address in {str(row.get("record_start") or "").upper() for row in name75_source}
                    else "out/script/runtime_text_residual_id_bundle_sheet.csv"
                ),
                "status": (
                    "id_dialogue_native_stock_reuse_ready"
                    if native_stock_fit
                    else "id_dialogue_encoding_hold"
                    if map_row
                    else "id_contract_unreviewed_policy_required"
                ),
                "reason": (
                    "semantic retranslation complete; exact native stock phrase token is available; apply only after static boundary audit"
                    if native_stock_fit
                    else "semantic retranslation complete; direct payload does not fit; ID route forbids unproven ext3/dictionary storage"
                    if map_row
                    else "active ID contract has no explicit LLM provenance; dedicated ID route re-review required"
                ),
                "batch_id": "",
                "source_batch_id": "",
                "source_jp": str(source_row.get("jp") or source_row.get("original_body") or contract.get("original_japanese") or ""),
                "current_ko": str((map_row or {}).get("proposed_ko") or source_row.get("ko") or source_row.get("suggested_ko") or source_row.get("current_body") or source_row.get("catalog_ko") or contract.get("baseline_text") or ""),
                "prefix_hex": str(contract.get("source_prefix_hex") or contract.get("control_prefix_hex") or ""),
                "body_hex": str(contract.get("source_body_hex") or ""),
            })
            queued_addresses.add(address)
            contract_gap_counts["id_dialogue"] += 1

    # Attach bounded context only after all work items are assembled. Scenario
    # rows receive same-bundle records plus nearby canonical rows; battle and
    # fixed-data rows explicitly state that neighboring prose is not required.
    canonical_order = [str(row.get("abs") or "").upper() for row in canonical]
    canonical_pos = {address: index for index, address in enumerate(canonical_order)}
    bundle_rows: dict[str, list[dict[str, Any]]] = {}
    for contract in contract_by_abs.values():
        bundle_rows.setdefault(str(contract.get("bundle_id") or ""), []).append(contract)
    for values in bundle_rows.values():
        values.sort(key=lambda item: int(str(item.get("address") or "0"), 16))

    def slim_record(address: str) -> dict[str, str]:
        source = canonical_by_abs.get(address, {})
        return {
            "abs": address,
            "jp": str(source.get("jp") or ""),
            "ko": str(source.get("ko") or ""),
        }

    for item in queue:
        address = str(item.get("address_or_slot") or "").upper()
        if item.get("workstream") != "scenario":
            item["context_required"] = "no"
            item["context_bundle_json"] = "[]"
            item["context_neighbors_json"] = "[]"
            continue
        contract = contract_by_abs.get(address, {})
        bundle_id = str(contract.get("bundle_id") or "")
        item["context_required"] = "yes"
        item["context_bundle_json"] = json.dumps(
            [
                {
                    "abs": str(row.get("address") or "").upper(),
                    "line_role": str(row.get("line_role") or ""),
                    "route": str(row.get("route") or ""),
                    "jp": str(row.get("original_japanese") or ""),
                    "ko": str(row.get("baseline_text") or ""),
                }
                for row in bundle_rows.get(bundle_id, [])
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        neighbors: list[dict[str, str]] = []
        pos = canonical_pos.get(address)
        if pos is not None:
            bank = address[:2]
            for offset in (-2, -1, 1, 2):
                candidate_pos = pos + offset
                if 0 <= candidate_pos < len(canonical_order):
                    candidate = canonical_order[candidate_pos]
                    if candidate[:2] == bank:
                        neighbors.append(slim_record(candidate))
        item["context_neighbors_json"] = json.dumps(
            neighbors, ensure_ascii=False, separators=(",", ":")
        )

    # Ensure every queue row can be dispatched in a deterministic bounded
    # batch. Existing scenario/battle IDs are retained; fixed-data rows and
    # any structural gap without an ID receive a static-only FD/SG batch.
    for workstream, prefix, limit in (
        ("canonical_fixed_data", "FD", 60),
        ("scenario", "SG", 60),
        ("battle", "BT", 60),
        ("battle_or_uncovered_rebase", "RB", 60),
        ("battle_contract", "BC", 60),
        ("id_dialogue", "ID", 60),
    ):
        pending = [row for row in queue if row["workstream"] == workstream and not row.get("batch_id")]
        pending.sort(key=lambda row: row["address_or_slot"])
        for offset in range(0, len(pending), limit):
            batch_id = f"{prefix}{offset // limit + 1:04d}"
            for order, row in enumerate(pending[offset:offset + limit], 1):
                row["batch_id"] = batch_id
                row["batch_order"] = str(order)
    # Give rows with pre-existing IDs an order as well.
    for batch_id in sorted({row.get("batch_id") for row in queue if row.get("batch_id")}):
        rows = [row for row in queue if row.get("batch_id") == batch_id]
        rows.sort(key=lambda row: row["address_or_slot"])
        for order, row in enumerate(rows, 1):
            row.setdefault("batch_order", str(order))

    sheet_inventory = []
    sheet_specs = [
        ("out/script/translation_sheet.csv", "canonical_legacy_source_blocked_by_policy"),
        ("out/script/translation_sheet_partial.csv", "legacy_snapshot_not_for_translation"),
        ("out/script/translation_sheet_probe.csv", "legacy_probe_snapshot_not_for_translation"),
        ("out/script/battle_voice_ambiguous_translation_sheet.csv", "battle_queue_source"),
        ("out/script/battle_voice_placeholder_translation_sheet.csv", "battle_placeholder_queue_source"),
        ("out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv", "battle_staging_stale_parent_tip_requires_rebase"),
        ("out/script/battle_voice_inline_control_translation_sheet.csv", "battle_staging_stale_parent_tip_requires_rebase"),
        ("out/script/uncovered_translation_sheet_llm_reviewed.csv", "uncovered_staging_stale_parent_tip_requires_rebase"),
        ("out/script/runtime_text_residual_voice_sheet.csv", "battle_contract_source_static_only"),
        ("out/script/runtime_text_residual_id_bundle_sheet.csv", "id_contract_source_static_only"),
        ("out/script/name75_battle_id_duplicate_residual_sheet.csv", "id_name75_source_requires_llm_recheck"),
        ("out/script/main_translation_llm_review/structural_preclear.csv", "scenario_structural_preclear_inventory"),
        ("out/script/shared_dictionary_reclass_sheet.csv", "shared_dictionary_structural_quarantine"),
        ("out/script/display_residual_reclass_sheet.csv", "display_structural_quarantine"),
        ("out/script/aux_vetted_mixed_reclass_sheet.csv", "aux_static_reclass_audit"),
        ("out/script/rebased_llm_staging/rebound_exact.csv", "current_tip_static_exact_rebound_staging"),
        ("out/script/rebased_llm_staging/rebase_hold.csv", "current_tip_rebase_hold"),
    ]
    for rel, status in sheet_specs:
        path = ROOT / rel
        rows = read_csv(path)
        sheet_inventory.append({
            "path": rel,
            "exists": path.is_file(),
            "rows": len(rows),
            "status": status,
            "sha256": digest(path) if path.is_file() else "",
        })
    fixed_inventory_path = ROOT / "out/patch/bank64_6f_structure_inventory.json"
    fixed_worklist_path = ROOT / "out/patch/bank64_6f_production_worklist.json"
    fixed_inventory_report = {}
    if fixed_inventory_path.is_file():
        try:
            fixed_inventory_report = json.loads(fixed_inventory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fixed_inventory_report = {}
    sheet_inventory.append({
        "path": "out/patch/bank64_6f_structure_inventory.json",
        "exists": fixed_inventory_path.is_file(),
        "rows": int((fixed_inventory_report.get("counts") or {}).get("inventory_rows") or 0),
        "status": "fixed_data_current_tip_original_boundary_exclusion",
        "sha256": digest(fixed_inventory_path) if fixed_inventory_path.is_file() else "",
    })
    sheet_inventory.append({
        "path": "out/patch/bank64_6f_production_worklist.json",
        "exists": fixed_worklist_path.is_file(),
        "rows": int((fixed_inventory_report.get("counts") or {}).get("production_targets") or 0),
        "status": "fixed_data_production_targets_zero",
        "sha256": digest(fixed_worklist_path) if fixed_worklist_path.is_file() else "",
    })
    fixed_manifest_path = ROOT / "out/script/fixed_data_decoder_review_manifest.json"
    fixed_manifest = {}
    if fixed_manifest_path.is_file():
        try:
            fixed_manifest = json.loads(fixed_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fixed_manifest = {}
    sheet_inventory.append({
        "path": "out/script/fixed_data_decoder_review_manifest.json",
        "exists": fixed_manifest_path.is_file(),
        "rows": len(fixed_manifest.get("records") or []),
        "status": "fixed_stride_dedicated_decoder_manifest_read_only",
        "sha256": digest(fixed_manifest_path) if fixed_manifest_path.is_file() else "",
    })
    native_reuse_path = ROOT / "out/script/native_stock_reuse_static_plan.json"
    native_reuse = {}
    if native_reuse_path.is_file():
        try:
            native_reuse = json.loads(native_reuse_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            native_reuse = {}
    sheet_inventory.append({
        "path": "out/script/native_stock_reuse_static_plan.json",
        "exists": native_reuse_path.is_file(),
        "rows": len(native_reuse.get("candidates") or []),
        "status": "native_stock_reuse_static_plan_not_applied",
        "sha256": digest(native_reuse_path) if native_reuse_path.is_file() else "",
    })
    scenario_gap_path = ROOT / "out/script/scenario_gap_structural_manifest.json"
    scenario_gap = {}
    if scenario_gap_path.is_file():
        try:
            scenario_gap = json.loads(scenario_gap_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            scenario_gap = {}
    sheet_inventory.append({
        "path": "out/script/scenario_gap_structural_manifest.json",
        "exists": scenario_gap_path.is_file(),
        "rows": len(scenario_gap.get("records") or []),
        "status": "scenario_gap_static_contract_binding_manifest",
        "sha256": digest(scenario_gap_path) if scenario_gap_path.is_file() else "",
    })
    coverage_path = ROOT / "out/script/translation_workstream_coverage_audit.json"
    coverage = {}
    if coverage_path.is_file():
        try:
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            coverage = {}
    sheet_inventory.append({
        "path": "out/script/translation_workstream_coverage_audit.json",
        "exists": coverage_path.is_file(),
        "rows": int((coverage.get("counts") or {}).get("canonical_rows") or 0),
        "status": "current_tip_workstream_coverage_read_only",
        "sha256": digest(coverage_path) if coverage_path.is_file() else "",
    })
    semantic_ready_path = ROOT / "out/script/battle_dialogue_llm_review/semantic_ready_static_audit.json"
    semantic_ready = {}
    if semantic_ready_path.is_file():
        try:
            semantic_ready = json.loads(semantic_ready_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            semantic_ready = {}
    sheet_inventory.append({
        "path": "out/script/battle_dialogue_llm_review/semantic_ready_static_audit.json",
        "exists": semantic_ready_path.is_file(),
        "rows": len(semantic_ready.get("rows") or []),
        "status": "battle_semantic_static_structural_hold",
        "sha256": digest(semantic_ready_path) if semantic_ready_path.is_file() else "",
    })
    fixed_capacity_path = ROOT / "out/script/fixed_data_capacity_static_audit.json"
    fixed_capacity = {}
    if fixed_capacity_path.is_file():
        try:
            fixed_capacity = json.loads(fixed_capacity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fixed_capacity = {}
    sheet_inventory.append({
        "path": "out/script/fixed_data_capacity_static_audit.json",
        "exists": fixed_capacity_path.is_file(),
        "rows": len(fixed_capacity.get("records") or []),
        "status": "fixed_stride_capacity_diagnostic_only",
        "sha256": digest(fixed_capacity_path) if fixed_capacity_path.is_file() else "",
    })
    special_storage_path = ROOT / "out/script/special_route_storage_static_audit.json"
    special_storage = {}
    if special_storage_path.is_file():
        try:
            special_storage = json.loads(special_storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            special_storage = {}
    sheet_inventory.append({
        "path": "out/script/special_route_storage_static_audit.json",
        "exists": special_storage_path.is_file(),
        "rows": len(special_storage.get("rows") or []),
        "status": "special_route_storage_static_hold",
        "sha256": digest(special_storage_path) if special_storage_path.is_file() else "",
    })
    scenario_storage_path = ROOT / "out/script/scenario_storage_static_audit.json"
    scenario_storage = {}
    if scenario_storage_path.is_file():
        try:
            scenario_storage = json.loads(scenario_storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            scenario_storage = {}
    sheet_inventory.append({
        "path": "out/script/scenario_storage_static_audit.json",
        "exists": scenario_storage_path.is_file(),
        "rows": len(scenario_storage.get("rows") or []),
        "status": "scenario_storage_static_hold",
        "sha256": digest(scenario_storage_path) if scenario_storage_path.is_file() else "",
    })
    scenario_candidate_path = ROOT / "out/patch/scenario_native_stock_static_candidate.json"
    scenario_candidate = {}
    if scenario_candidate_path.is_file():
        try:
            scenario_candidate = json.loads(scenario_candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            scenario_candidate = {}
    sheet_inventory.append({
        "path": "out/patch/scenario_native_stock_static_candidate.json",
        "exists": scenario_candidate_path.is_file(),
        "rows": len(scenario_candidate.get("selected") or []),
        "status": "scenario_native_stock_candidate_not_promoted",
        "sha256": digest(scenario_candidate_path) if scenario_candidate_path.is_file() else "",
    })
    special_candidate_path = ROOT / "out/patch/special_native_stock_static_candidate.json"
    special_candidate = {}
    if special_candidate_path.is_file():
        try:
            special_candidate = json.loads(special_candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            special_candidate = {}
    sheet_inventory.append({
        "path": "out/patch/special_native_stock_static_candidate.json",
        "exists": special_candidate_path.is_file(),
        "rows": len(special_candidate.get("selected") or []),
        "status": "special_native_stock_candidate_not_promoted",
        "sha256": digest(special_candidate_path) if special_candidate_path.is_file() else "",
    })
    promotion_matrix_path = ROOT / "out/script/promotion_readiness_matrix.json"
    promotion_matrix = {}
    if promotion_matrix_path.is_file():
        try:
            promotion_matrix = json.loads(promotion_matrix_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            promotion_matrix = {}
    sheet_inventory.append({
        "path": "out/script/promotion_readiness_matrix.json",
        "exists": promotion_matrix_path.is_file(),
        "rows": int((promotion_matrix.get("counts") or {}).get("queue_rows") or 0),
        "status": "fail_closed_promotion_readiness_matrix",
        "sha256": digest(promotion_matrix_path) if promotion_matrix_path.is_file() else "",
    })
    quarantine_context_path = ROOT / "out/script/scenario_quarantine_context_static_audit.json"
    quarantine_context = {}
    if quarantine_context_path.is_file():
        try:
            quarantine_context = json.loads(quarantine_context_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            quarantine_context = {}
    sheet_inventory.append({
        "path": "out/script/scenario_quarantine_context_static_audit.json",
        "exists": quarantine_context_path.is_file(),
        "rows": len(quarantine_context.get("records") or []),
        "status": "scenario_quarantine_neighbor_context_advisory",
        "sha256": digest(quarantine_context_path) if quarantine_context_path.is_file() else "",
    })

    after = {
        "rom": digest(ROM),
        "sheet": digest(SHEET),
        "contract": digest(CONTRACT),
        "saveram": digest(SAVE),
    }
    report = {
        "schema_version": 1,
        "artifact": "translation-workstreams-static-audit/v1",
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "inputs": {
            "main_tip_sha256": before["rom"],
            "translation_sheet_sha256": before["sheet"],
            "contract_sha256": before["contract"],
            "saveram_sha256": before["saveram"],
        },
        "canonical": {
            "rows": len(canonical),
            "bank_counts": dict(sorted(bank_counts.items())),
            "status_counts": dict(sorted(canonical_counts.items())),
            "japanese_residual_by_bank": dict(sorted(bank_jp.items())),
            "control_or_nul_residual_by_bank": dict(sorted(bank_control.items())),
        },
        "scenario": {
            "manifest_count": len(scenario_manifests),
            "result_rows": len(scenario),
            "semantic_llm_rows": sum(str(r.get("new_translation_source") or "") == "llm" for r in scenario.values()),
            "semantic_quarantine_rows": sum(str(r.get("new_translation_source") or "") != "llm" for r in scenario.values()),
            "structural_preclear_rows": len(structural_preclear),
            "structural_preclear_unique_addresses": len({str(r.get("abs") or "").upper() for r in structural_preclear}),
            "gap_manifest_rows": len(scenario_gap.get("records") or []),
            "gap_manifest_counts": scenario_gap.get("counts") or {},
            "gap_manifest_path": "out/script/scenario_gap_structural_manifest.json",
        },
        "coverage": {
            "path": "out/script/translation_workstream_coverage_audit.json",
            "counts": coverage.get("counts") or {},
            "checks": coverage.get("checks") or {},
        },
        "fixed_data": {
            "inventory_rows": int((fixed_inventory_report.get("counts") or {}).get("inventory_rows") or 0),
            "inventory_production_targets": int((fixed_inventory_report.get("counts") or {}).get("production_targets") or 0),
            "inventory_checks": fixed_inventory_report.get("checks") or {},
            "inventory_path": "out/patch/bank64_6f_structure_inventory.json",
            "capacity_audit_rows": len(fixed_capacity.get("records") or []),
            "capacity_audit_counts": fixed_capacity.get("counts") or {},
            "capacity_audit_path": "out/script/fixed_data_capacity_static_audit.json",
        },
        "special_route_storage": {
            "rows": len(special_storage.get("rows") or []),
            "counts": special_storage.get("counts") or {},
            "path": "out/script/special_route_storage_static_audit.json",
        },
        "scenario_storage": {
            "rows": len(scenario_storage.get("rows") or []),
            "counts": scenario_storage.get("counts") or {},
            "path": "out/script/scenario_storage_static_audit.json",
        },
        "scenario_candidate": {
            "selected": len(scenario_candidate.get("selected") or []),
            "counts": scenario_candidate.get("counts") or {},
            "promotion_allowed": bool(scenario_candidate.get("promotion_allowed")),
            "path": "out/patch/scenario_native_stock_static_candidate.json",
        },
        "special_candidate": {
            "selected": len(special_candidate.get("selected") or []),
            "counts": special_candidate.get("counts") or {},
            "promotion_allowed": bool(special_candidate.get("promotion_allowed")),
            "path": "out/patch/special_native_stock_static_candidate.json",
        },
        "promotion_matrix": {
            "queue_rows": int((promotion_matrix.get("counts") or {}).get("queue_rows") or 0),
            "promotion_allowed": bool(promotion_matrix.get("promotion_allowed")),
            "checks": promotion_matrix.get("checks") or {},
            "path": "out/script/promotion_readiness_matrix.json",
        },
        "scenario_quarantine_context": {
            "rows": len(quarantine_context.get("records") or []),
            "counts": quarantine_context.get("counts") or {},
            "path": "out/script/scenario_quarantine_context_static_audit.json",
        },
        "battle": {
            "queue_rows": len(battle_queue),
            "queue_class_counts": dict(sorted(battle_counts.items())),
            "reviewed_result_rows": len(battle_results),
            "semantic_ready_static_rows": len(semantic_ready.get("rows") or []),
            "semantic_ready_static_counts": semantic_ready.get("counts") or {},
            "reviewed_staging_union_rows": len(reviewed_union),
            "static_exact_rebound_rows": len(rebound_rows),
            "stale_parent_tip_rebase_rows": stale_rebase_rows,
            "contract_gap_counts": dict(sorted(contract_gap_counts.items())),
            "contract_map_reviewed_rows": len(battle_contract_map_rows),
            "contract_map_counts": dict(sorted(battle_contract_map_counts.items())),
            "contract_map_review_files": [str(path.relative_to(ROOT)).replace("\\", "/") for path in battle_contract_map_files],
            "parent_tip_sha256_counts": dict(sorted(Counter(parent_by_abs.values()).items())),
            "reviewed_result_files": [str(p.relative_to(ROOT)).replace("\\", "/") for p in battle_result_files],
        },
        "id_dialogue": {
            "contract_map_reviewed_rows": len(id_contract_map_rows),
            "contract_map_counts": dict(sorted(id_contract_map_counts.items())),
            "contract_map_review_files": [str(path.relative_to(ROOT)).replace("\\", "/") for path in id_contract_map_files],
        },
        "uncovered": {"llm_reviewed_rows": len(uncovered)},
        "sheet_inventory": sheet_inventory,
        "static_queue": {
            "rows": len(queue),
            "status_counts": dict(sorted(Counter(row["status"] for row in queue).items())),
            "batch_counts": dict(sorted(Counter(row.get("batch_id") or "<missing>" for row in queue).items())),
            "source_batch_counts": dict(sorted(Counter(row.get("source_batch_id") or "<none>" for row in queue).items())),
            "all_rows_have_reason": all(bool(row.get("reason")) for row in queue),
            "all_rows_have_batch": all(bool(row.get("batch_id")) and bool(row.get("batch_order")) for row in queue),
        },
        "gates": {
            "rom_unchanged": before["rom"] == after["rom"],
            "canonical_sheet_unchanged": before["sheet"] == after["sheet"],
            "saveram_unchanged": before["saveram"] == after["saveram"],
            "contract_unchanged": before["contract"] == after["contract"],
            "legacy_runtime_heuristic_not_authoritative": True,
            "promotion_ready": False,
        },
        "promotion_block_reason": "static staging and quarantine remain; no candidate ROM was encoded or runtime-validated",
        "outputs": {
            "json": str(OUT_JSON.relative_to(ROOT)).replace("\\", "/"),
            "csv": str(OUT_CSV.relative_to(ROOT)).replace("\\", "/"),
            "batch_index": str(BATCH_INDEX.relative_to(ROOT)).replace("\\", "/"),
            "batch_directory": str(BATCH_DIR.relative_to(ROOT)).replace("\\", "/"),
        },
    }
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["workstream", "batch_id", "batch_order", "source_batch_id", "address_or_slot", "source", "status", "reason", "source_jp", "current_ko", "prefix_hex", "body_hex", "context_required", "context_bundle_json", "context_neighbors_json"]
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(queue)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "canonical_rows": len(canonical),
        "queue_rows": len(queue),
        "canonical_status_counts": dict(sorted(canonical_counts.items())),
        "battle_queue_class_counts": dict(sorted(battle_counts.items())),
        "gates": report["gates"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
