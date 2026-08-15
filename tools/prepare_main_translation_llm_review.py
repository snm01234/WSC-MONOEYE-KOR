#!/usr/bin/env python3
"""Prepare a no-translation, current-TIP-bound LLM review inventory and batches.

This tool selects, plans, and batches work only.  It never creates a Korean
translation, changes an existing Korean string, writes a ROM, or writes a
SaveRAM.  Every editable translation/result column in its outputs is blank.
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from script_translation_scope import script_graphics_reason  # noqa: E402

POLICY = ROOT / "data/main_translation_llm_review_policy.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SOURCE_SHEET = ROOT / "out/script/translation_sheet.csv"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
LEGACY_BING = ROOT / "out/script/excel_translate_cache.json"
LEGACY_QUALITY = ROOT / "out/script/translations_quality_all.json"
KNOWN_MT_TERMS = ROOT / "out/script/machine_translation_terminology_targets.csv"

OUT_DIR = ROOT / "out/script/main_translation_llm_review"
INVENTORY_CSV = OUT_DIR / "inventory.csv"
TARGETS_CSV = OUT_DIR / "targets.csv"
EXCLUSIONS_CSV = OUT_DIR / "exclusions.csv"
STRUCTURAL_CSV = OUT_DIR / "structural_preclear.csv"
STRUCTURAL_INDEX_CSV = OUT_DIR / "structural_batch_index.csv"
BATCH_INDEX_CSV = OUT_DIR / "batch_index.csv"
PLAN_JSON = OUT_DIR / "plan.json"
PROVENANCE_JSON = OUT_DIR / "provenance_evidence.json"
BATCH_DIR = OUT_DIR / "batches"
STRUCTURAL_BATCH_DIR = OUT_DIR / "structural_batches"

SCENARIO_BANKS = {"60", "61", "62", "63"}
ROM_SIZE = 16_777_216
SPACE_RE = re.compile(r"[ \t\r\n\u3000]+")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
CONTROL_RE = re.compile(r"<(?!E62F>)[A-F0-9]{2,8}>")
LLM_RE = re.compile(r"(^|[^a-z])llm([^a-z]|$)", re.IGNORECASE)

OUTPUT_FIELDS = [
    "review_id", "wave", "batch_id", "batch_order", "bundle_id",
    "bundle_order", "abs", "bank", "line_role", "route", "contract_status",
    "apply_blocked_by_structure", "batch_gate_status", "source_jp",
    "source_jp_authority", "contract_original_jp", "source_sheet_jp",
    "current_ko", "source_sheet_ko",
    "source_sheet_stale_vs_tip", "explicit_llm_provenance", "completed_review_evidence",
    "provenance_sources", "review_sources", "selection_reasons", "quality_flags",
    "priority", "workflow_status", "main_tip_sha256", "source_body_sha256",
    "proposed_ko", "reviewer_notes", "new_translation_source", "new_review_status",
]


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def norm(text: str) -> str:
    return SPACE_RE.sub(" ", str(text or "").strip())


def is_llm_source(value: str) -> bool:
    return bool(LLM_RE.search(str(value or "").replace("-", "_") ))


def review_complete(value: str, prefixes: Iterable[str]) -> bool:
    lowered = str(value or "").strip().lower()
    return any(lowered.startswith(prefix.lower()) for prefix in prefixes)


def visible_len(text: str) -> int:
    return len(str(text or "").replace("<E62F>", "").rstrip(" \u3000\t"))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def iter_address_texts(document: Any) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """Yield address/current-wording candidates without assigning provenance."""
    if not isinstance(document, dict):
        return
    source_jp = document.get("source_jp") if isinstance(document.get("source_jp"), dict) else {}
    targets = document.get("targets")
    if isinstance(targets, dict):
        for address, value in targets.items():
            if isinstance(value, str):
                yield str(address).upper(), value, {"source_jp": source_jp.get(address, "")}
            elif isinstance(value, dict):
                text = value.get("after") or value.get("ko") or value.get("text") or ""
                if text:
                    yield str(address).upper(), str(text), value
    for key in ("entries", "lines", "records", "scenario_targets", "battle_targets"):
        values = document.get(key)
        if isinstance(values, dict):
            values = [{"abs": address, **(value if isinstance(value, dict) else {"ko": value})}
                      for address, value in values.items()]
        if not isinstance(values, list):
            continue
        for row in values:
            if not isinstance(row, dict) or not row.get("abs"):
                continue
            text = row.get("after") or row.get("ko") or row.get("current_ko") or ""
            if text:
                yield str(row["abs"]).upper(), str(text), row


@dataclass(frozen=True)
class Evidence:
    address: str
    text: str
    source: str
    translation_source: str
    review_status: str
    explicit_llm: bool
    reviewed: bool


def collect_provenance(policy: dict[str, Any]) -> tuple[dict[str, list[Evidence]], dict[str, Any]]:
    completed = (policy.get("review_status") or {}).get("completed_prefixes") or []
    by_address: dict[str, list[Evidence]] = defaultdict(list)
    source_counts: Counter[str] = Counter()
    parse_failures: list[str] = []

    json_paths = sorted((ROOT / "data").glob("*.json"))
    for path in json_paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            parse_failures.append(str(path.relative_to(ROOT)).replace("\\", "/"))
            continue
        if not isinstance(document, dict):
            continue
        provenance = document.get("provenance") if isinstance(document.get("provenance"), dict) else {}
        root_source = str(document.get("translation_source") or provenance.get("translation_source") or "")
        root_review = str(document.get("review_status") or provenance.get("review_status") or "")
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for address, text, row in iter_address_texts(document):
            row_prov = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
            trans_source = str(row.get("translation_source") or row_prov.get("translation_source") or root_source)
            review = str(row.get("review_status") or row_prov.get("review_status") or root_review)
            evidence = Evidence(
                address=address,
                text=text,
                source=rel,
                translation_source=trans_source,
                review_status=review,
                explicit_llm=is_llm_source(trans_source),
                reviewed=review_complete(review, completed),
            )
            by_address[address].append(evidence)
            source_counts[rel] += 1

    csv_paths = [
        ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv",
        ROOT / "out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv",
        ROOT / "out/script/battle_voice_inline_control_translation_sheet.csv",
    ]
    for path in csv_paths:
        if not path.is_file():
            continue
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                address = str(row.get("abs") or "").upper()
                text = str(row.get("ko") or "")
                if not address or not text:
                    continue
                trans_source = str(row.get("translation_source") or "")
                review = str(row.get("review_status") or "")
                by_address[address].append(Evidence(
                    address, text, rel, trans_source, review,
                    is_llm_source(trans_source), review_complete(review, completed),
                ))
                source_counts[rel] += 1

    summary = {
        "assets_scanned": len(json_paths) + sum(path.is_file() for path in csv_paths),
        "addresses_with_any_evidence": len(by_address),
        "evidence_rows": sum(len(rows) for rows in by_address.values()),
        "source_counts": dict(source_counts.most_common()),
        "parse_failures": parse_failures,
    }
    return by_address, summary


def legacy_maps() -> tuple[dict[str, str], dict[str, str], set[str]]:
    bing_doc = json.loads(LEGACY_BING.read_text(encoding="utf-8"))
    bing = {str(jp): str(ko) for jp, ko in (bing_doc.get("entries") or {}).items()}
    quality_doc = json.loads(LEGACY_QUALITY.read_text(encoding="utf-8"))
    quality = {
        str(row.get("abs") or "").upper(): str(row.get("ko") or "")
        for row in (quality_doc.get("lines") or [])
        if row.get("abs") and row.get("ko")
    }
    terms: set[str] = set()
    if KNOWN_MT_TERMS.is_file():
        with KNOWN_MT_TERMS.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("abs"):
                    terms.add(str(row["abs"]).upper())
    return bing, quality, terms


def quality_flags(address: str, jp: str, current: str, sheet_ko: str,
                  bing: dict[str, str], quality: dict[str, str], term_addresses: set[str]) -> list[str]:
    flags: list[str] = []
    normalized = norm(current)
    if jp in bing and normalized == norm(bing[jp]):
        flags.append("legacy_bing_exact_match")
    if address in quality and normalized == norm(quality[address]):
        flags.append("blocked_quality_exact_match")
    if address in term_addresses:
        flags.append("known_machine_translation_terminology_target")
    if JAPANESE_RE.search(current):
        flags.append("japanese_residual_in_current")
    if CONTROL_RE.search(current) or "<FF>" in current:
        flags.append("control_or_invalid_token_text")
    compact = current.replace("<E62F>", "").strip(" \u3000")
    if visible_len(compact) >= 14 and not re.search(r"[ \u3000]", compact):
        flags.append("long_dense_korean")
    jp_len = max(1, visible_len(jp))
    ko_len = visible_len(current)
    ratio = ko_len / jp_len
    if jp_len >= 8 and ratio < 0.42:
        flags.append("strong_undertranslation_ratio")
    if jp_len >= 8 and ratio > 2.35:
        flags.append("strong_expansion_ratio")
    if norm(sheet_ko) != normalized:
        flags.append("source_sheet_stale_vs_current_tip")
    return list(dict.fromkeys(flags))


def priority_for(flags: list[str], missing_llm: bool, missing_review: bool) -> str:
    if any(flag in flags for flag in ("japanese_residual_in_current", "control_or_invalid_token_text")):
        return "P0"
    if any(flag in flags for flag in (
        "legacy_bing_exact_match", "blocked_quality_exact_match",
        "known_machine_translation_terminology_target", "strong_undertranslation_ratio",
        "strong_expansion_ratio", "long_dense_korean",
    )):
        return "P1"
    if missing_llm or missing_review:
        return "P2"
    return "P3"


def wave_for(flags: list[str], missing_llm: bool, missing_review: bool) -> str:
    if flags:
        return "W1_quality_and_legacy_risk" if (missing_llm or missing_review) else "W3_reviewed_but_quality_flagged"
    return "W2_missing_llm_or_review_evidence"


def batch_rows(rows: list[dict[str, Any]], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    limits = policy["batching"]
    max_rows = int(limits["max_rows"])
    max_chars = int(limits["max_source_and_current_characters"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["bundle_id"]].append(row)
    bundles = sorted(grouped.values(), key=lambda group: int(min(row["abs"] for row in group), 16))
    output: list[dict[str, Any]] = []
    index: list[dict[str, Any]] = []
    by_wave: dict[str, list[list[dict[str, Any]]]] = defaultdict(list)
    for bundle in bundles:
        wave = min((row["wave"] for row in bundle), key=lambda x: (
            ["W1_quality_and_legacy_risk", "W2_missing_llm_or_review_evidence", "W3_reviewed_but_quality_flagged"].index(x)
        ))
        by_wave[wave].append(sorted(bundle, key=lambda row: int(row["abs"], 16)))

    batch_number = 0
    for wave in ("W1_quality_and_legacy_risk", "W2_missing_llm_or_review_evidence", "W3_reviewed_but_quality_flagged"):
        current: list[dict[str, Any]] = []
        chars = 0
        for bundle in by_wave.get(wave, []):
            bundle_chars = sum(len(row["source_jp"]) + len(row["current_ko"]) for row in bundle)
            if current and (len(current) + len(bundle) > max_rows or chars + bundle_chars > max_chars):
                batch_number += 1
                batch_id = f"MR{batch_number:04d}"
                for order, row in enumerate(current, 1):
                    row["batch_id"] = batch_id
                    row["batch_order"] = order
                    row["batch_gate_status"] = (
                        "blocked_pending_structural_preclear"
                        if any(item["apply_blocked_by_structure"] == "yes" for item in current)
                        else "ready_for_llm_review"
                    )
                    output.append(row)
                gate_status = current[0]["batch_gate_status"]
                index.append({
                    "batch_id": batch_id, "wave": wave, "rows": len(current),
                    "bundles": len({row['bundle_id'] for row in current}),
                    "first_abs": min(row["abs"] for row in current),
                    "last_abs": max(row["abs"] for row in current),
                    "priority_counts": json.dumps(dict(Counter(row["priority"] for row in current)), ensure_ascii=False),
                    "character_budget": chars, "status": gate_status,
                })
                current, chars = [], 0
            current.extend(bundle)
            chars += bundle_chars
        if current:
            batch_number += 1
            batch_id = f"MR{batch_number:04d}"
            for order, row in enumerate(current, 1):
                row["batch_id"] = batch_id
                row["batch_order"] = order
                row["batch_gate_status"] = (
                    "blocked_pending_structural_preclear"
                    if any(item["apply_blocked_by_structure"] == "yes" for item in current)
                    else "ready_for_llm_review"
                )
                output.append(row)
            gate_status = current[0]["batch_gate_status"]
            index.append({
                "batch_id": batch_id, "wave": wave, "rows": len(current),
                "bundles": len({row['bundle_id'] for row in current}),
                "first_abs": min(row["abs"] for row in current),
                "last_abs": max(row["abs"] for row in current),
                "priority_counts": json.dumps(dict(Counter(row["priority"] for row in current)), ensure_ascii=False),
                "character_budget": chars, "status": gate_status,
            })
    return output, index


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    before_main = MAIN.read_bytes()
    if len(before_main) != ROM_SIZE:
        raise SystemExit(f"main TIP size drifted: {len(before_main)}")
    main_sha = sha(before_main)
    contract_doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if str(contract_doc["baseline_target"]["sha256"]).lower() != main_sha:
        raise SystemExit("runtime contract is not bound to the current main TIP")
    contract_by_abs = {row["address"]: row for row in contract_doc["contracts"]}

    csv.field_size_limit(16 * 1024 * 1024)
    with SOURCE_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    provenance, provenance_summary = collect_provenance(policy)
    bing, quality, term_addresses = legacy_maps()

    inventory: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    structural: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    disposition_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    evidence_details: dict[str, Any] = {}

    for source_row in source_rows:
        address = str(source_row.get("abs") or "").upper()
        bank = address[:2]
        sheet_jp = str(source_row.get("jp") or "")
        sheet_ko = str(source_row.get("ko") or "")
        base = {
            "abs": address, "bank": bank, "source_sheet_jp": sheet_jp,
            "source_sheet_ko": sheet_ko, "main_tip_sha256": main_sha,
        }
        if bank not in SCENARIO_BANKS:
            disposition = "excluded_non_scenario_or_fixed_data_bank"
            exclusions.append({**base, "disposition": disposition, "reason": "bank_outside_60_63"})
            inventory.append({**base, "disposition": disposition})
            disposition_counts[disposition] += 1
            continue
        graphics = script_graphics_reason(int(address, 16))
        if graphics:
            disposition = "excluded_script_graphics_block"
            exclusions.append({**base, "disposition": disposition, "reason": graphics})
            inventory.append({**base, "disposition": disposition})
            disposition_counts[disposition] += 1
            continue
        contract = contract_by_abs.get(address)
        if contract is None:
            disposition = "structural_preclear_required"
            row = {
                **base, "disposition": disposition, "workflow_status": disposition,
                "source_jp": sheet_jp, "current_ko": sheet_ko,
                "reason": "main_sheet_row_not_bound_to_runtime_dialogue_contract",
                "proposed_ko": "", "reviewer_notes": "",
            }
            structural.append(row)
            inventory.append(row)
            disposition_counts[disposition] += 1
            continue

        current = str(contract.get("baseline_text") or "").rstrip(" \u3000\t")
        contract_jp = str(contract.get("original_japanese") or "")
        if contract["status"] == "quarantine":
            jp = sheet_jp
            jp_authority = "provisional_sheet_requires_structural_preclear"
        else:
            jp = contract_jp or sheet_jp
            jp_authority = "runtime_contract_and_original_boundary"
        matching = [ev for ev in provenance.get(address, []) if norm(ev.text) == norm(current)]
        llm_sources = sorted({ev.source for ev in matching if ev.explicit_llm})
        review_sources = sorted({ev.source for ev in matching if ev.reviewed})
        explicit_llm = bool(llm_sources)
        completed_review = bool(review_sources)
        flags = quality_flags(address, jp, current, sheet_ko, bing, quality, term_addresses)
        missing_llm = not explicit_llm
        missing_review = not completed_review
        reasons = []
        if missing_llm:
            reasons.append("missing_explicit_llm_provenance")
        if missing_review:
            reasons.append("missing_completed_review_evidence")
        if flags:
            reasons.append("quality_or_legacy_risk")
        selected = bool(reasons)
        disposition = "queued_llm_rereview" if selected else "evidence_exempt_no_quality_flag"
        row = {
            "review_id": "", "wave": wave_for(flags, missing_llm, missing_review) if selected else "",
            "batch_id": "", "batch_order": "", "bundle_id": contract["bundle_id"],
            "bundle_order": "", "abs": address, "bank": bank,
            "line_role": contract["line_role"], "route": contract["route"],
            "contract_status": contract["status"],
            "apply_blocked_by_structure": "yes" if contract["status"] == "quarantine" else "no",
            "batch_gate_status": "", "source_jp": jp,
            "source_jp_authority": jp_authority,
            "contract_original_jp": contract_jp, "source_sheet_jp": sheet_jp,
            "current_ko": current, "source_sheet_ko": sheet_ko,
            "source_sheet_stale_vs_tip": "yes" if norm(sheet_ko) != norm(current) else "no",
            "explicit_llm_provenance": "yes" if explicit_llm else "no",
            "completed_review_evidence": "yes" if completed_review else "no",
            "provenance_sources": " | ".join(llm_sources),
            "review_sources": " | ".join(review_sources),
            "selection_reasons": ";".join(reasons), "quality_flags": ";".join(flags),
            "priority": priority_for(flags, missing_llm, missing_review),
            "workflow_status": disposition, "main_tip_sha256": main_sha,
            "source_body_sha256": sha(bytes.fromhex(contract["source_body_hex"])),
            "proposed_ko": "", "reviewer_notes": "", "new_translation_source": "",
            "new_review_status": "", "disposition": disposition,
        }
        inventory.append(row)
        disposition_counts[disposition] += 1
        route_counts[contract["route"]] += 1
        status_counts[contract["status"]] += 1
        for reason in reasons:
            reason_counts[reason] += 1
        for flag in flags:
            risk_counts[flag] += 1
        if selected:
            target_rows.append(row)
        if contract["status"] == "quarantine":
            structural.append({
                "structural_batch_id": "", "structural_batch_order": "",
                "bundle_id": contract["bundle_id"], **base,
                "disposition": "structural_preclear_required",
                "workflow_status": "structural_preclear_required",
                "source_jp": sheet_jp, "contract_original_jp": contract_jp,
                "current_ko": current, "source_sheet_ko": sheet_ko,
                "reason": "runtime_contract_quarantine_role_or_prefix_unresolved",
                "proposed_ko": "", "reviewer_notes": "",
            })
        if matching:
            evidence_details[address] = [ev.__dict__ for ev in matching]

    # A selected row brings the whole runtime bundle into the review context.
    target_addresses = {row["abs"] for row in target_rows}
    target_bundles = {row["bundle_id"] for row in target_rows}
    selected_wave_by_bundle: dict[str, str] = {}
    wave_order = {
        "W1_quality_and_legacy_risk": 0,
        "W2_missing_llm_or_review_evidence": 1,
        "W3_reviewed_but_quality_flagged": 2,
    }
    for row in target_rows:
        previous = selected_wave_by_bundle.get(row["bundle_id"])
        if previous is None or wave_order[row["wave"]] < wave_order[previous]:
            selected_wave_by_bundle[row["bundle_id"]] = row["wave"]
    context_rows = [
        dict(row) for row in inventory
        if row.get("bundle_id") in target_bundles and row.get("route")
    ]
    for row in context_rows:
        if row["abs"] not in target_addresses:
            row["workflow_status"] = "context_only_no_edit"
            row["selection_reasons"] = "bundle_context_for_selected_row"
            row["wave"] = selected_wave_by_bundle[row["bundle_id"]]
        row["review_id"] = f"R{int(row['abs'], 16):06X}"

    # Preserve bundle order and assign compact review batches.
    by_bundle: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in context_rows:
        by_bundle[row["bundle_id"]].append(row)
    for rows in by_bundle.values():
        for order, row in enumerate(sorted(rows, key=lambda item: int(item["abs"], 16)), 1):
            row["bundle_order"] = order
    batched, batch_index = batch_rows(context_rows, policy)

    # W0 structural work is independently dispatchable but contains no
    # translation/result fields. Preserve bundle grouping where it exists.
    structural_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in structural:
        structural_groups[str(row.get("bundle_id") or f"gap_{row['abs']}")].append(row)
    structural_output: list[dict[str, Any]] = []
    structural_index: list[dict[str, Any]] = []
    current_structural: list[dict[str, Any]] = []
    structural_number = 0
    for group in sorted(structural_groups.values(), key=lambda rows: int(min(r["abs"] for r in rows), 16)):
        if current_structural and len(current_structural) + len(group) > 80:
            structural_number += 1
            batch_id = f"ST{structural_number:04d}"
            for order, row in enumerate(current_structural, 1):
                row["structural_batch_id"] = batch_id
                row["structural_batch_order"] = order
                structural_output.append(row)
            structural_index.append({
                "batch_id": batch_id, "wave": "W0_structural_preclear",
                "rows": len(current_structural),
                "bundles": len({r.get('bundle_id') or f"gap_{r['abs']}" for r in current_structural}),
                "first_abs": min(r["abs"] for r in current_structural),
                "last_abs": max(r["abs"] for r in current_structural),
                "status": "ready_for_structural_preclear",
            })
            current_structural = []
        current_structural.extend(group)
    if current_structural:
        structural_number += 1
        batch_id = f"ST{structural_number:04d}"
        for order, row in enumerate(current_structural, 1):
            row["structural_batch_id"] = batch_id
            row["structural_batch_order"] = order
            structural_output.append(row)
        structural_index.append({
            "batch_id": batch_id, "wave": "W0_structural_preclear",
            "rows": len(current_structural),
            "bundles": len({r.get('bundle_id') or f"gap_{r['abs']}" for r in current_structural}),
            "first_abs": min(r["abs"] for r in current_structural),
            "last_abs": max(r["abs"] for r in current_structural),
            "status": "ready_for_structural_preclear",
        })

    # Write a file per batch so future LLM work can be dispatched without
    # reparsing the full sheet.  Editable output fields remain blank.
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    for old in BATCH_DIR.glob("MR*.csv"):
        old.unlink()
    for batch in batch_index:
        rows = [row for row in batched if row["batch_id"] == batch["batch_id"]]
        write_csv(BATCH_DIR / f"{batch['batch_id']}.csv", rows, OUTPUT_FIELDS)
    STRUCTURAL_BATCH_DIR.mkdir(parents=True, exist_ok=True)
    for old in STRUCTURAL_BATCH_DIR.glob("ST*.csv"):
        old.unlink()
    structural_fields = [
        "structural_batch_id", "structural_batch_order", "bundle_id", "abs", "bank",
        "disposition", "workflow_status", "reason", "source_jp", "contract_original_jp",
        "current_ko", "source_sheet_ko", "main_tip_sha256", "proposed_ko", "reviewer_notes",
    ]
    for batch in structural_index:
        rows = [row for row in structural_output if row["structural_batch_id"] == batch["batch_id"]]
        write_csv(STRUCTURAL_BATCH_DIR / f"{batch['batch_id']}.csv", rows, structural_fields)

    inventory_fields = list(dict.fromkeys(OUTPUT_FIELDS + [
        "disposition", "source_sheet_jp", "reason",
    ]))
    exclusion_fields = [
        "abs", "bank", "disposition", "reason", "source_sheet_jp", "source_sheet_ko",
        "main_tip_sha256",
    ]
    batch_index_fields = [
        "batch_id", "wave", "rows", "bundles", "first_abs", "last_abs",
        "priority_counts", "character_budget", "status",
    ]
    write_csv(INVENTORY_CSV, inventory, inventory_fields)
    write_csv(TARGETS_CSV, sorted(batched, key=lambda row: (row["batch_id"], row["batch_order"])), OUTPUT_FIELDS)
    write_csv(EXCLUSIONS_CSV, exclusions, exclusion_fields)
    write_csv(STRUCTURAL_CSV, structural_output, structural_fields)
    write_csv(STRUCTURAL_INDEX_CSV, structural_index, [
        "batch_id", "wave", "rows", "bundles", "first_abs", "last_abs", "status",
    ])
    write_csv(BATCH_INDEX_CSV, batch_index, batch_index_fields)

    before_sha = main_sha
    after_sha = sha(MAIN.read_bytes())
    blank_result_fields = all(
        not str(row.get(field) or "").strip()
        for row in batched
        for field in ("proposed_ko", "reviewer_notes", "new_translation_source", "new_review_status")
    ) and all(not str(row.get("proposed_ko") or "").strip() for row in structural)

    summary = {
        "main_tip_sha256": main_sha,
        "source_sheet_sha256": sha(SOURCE_SHEET.read_bytes()),
        "contract_sha256": sha(CONTRACT.read_bytes()),
        "source_sheet_rows": len(source_rows),
        "runtime_contract_rows_in_sheet": sum(bool(row.get("route")) for row in inventory),
        "semantic_target_rows": len(target_rows),
        "semantic_context_rows_in_batches": len(batched),
        "semantic_batches_ready_now": sum(row["status"] == "ready_for_llm_review" for row in batch_index),
        "semantic_batches_blocked_by_structure": sum(row["status"] == "blocked_pending_structural_preclear" for row in batch_index),
        "semantic_target_bundles": len(target_bundles),
        "evidence_exempt_rows": disposition_counts["evidence_exempt_no_quality_flag"],
        "structural_preclear_rows": len(structural_output),
        "structural_preclear_batches": len(structural_index),
        "excluded_rows": len(exclusions),
        "batches": len(batch_index),
        "disposition_counts": dict(disposition_counts),
        "selection_reason_counts": dict(reason_counts),
        "quality_flag_counts": dict(risk_counts),
        "contract_route_counts": dict(route_counts),
        "contract_status_counts": dict(status_counts),
        "provenance": provenance_summary,
    }
    gates = {
        "current_main_contract_binding": contract_doc["baseline_target"]["sha256"].lower() == main_sha,
        "source_population_reconciles": len(inventory) == len(source_rows),
        "scope_reconciles": len(target_rows) + disposition_counts["evidence_exempt_no_quality_flag"]
            == sum(bool(row.get("route")) for row in inventory),
        "all_unproven_or_unreviewed_selected": all(
            row["workflow_status"] == "queued_llm_rereview"
            for row in inventory
            if row.get("route") and (
                row.get("explicit_llm_provenance") != "yes"
                or row.get("completed_review_evidence") != "yes"
            )
        ),
        "quality_flagged_rows_selected": all(
            row["workflow_status"] == "queued_llm_rereview"
            for row in inventory if row.get("quality_flags")
        ),
        "proposed_translation_fields_blank": blank_result_fields,
        "rom_unchanged": before_sha == after_sha,
        "no_saveram_write_path": True,
        "batch_limits_respected": all(
            int(row["rows"]) <= int(policy["batching"]["max_rows"])
            and int(row["character_budget"]) <= int(policy["batching"]["max_source_and_current_characters"])
            for row in batch_index
        ),
    }
    plan = {
        "schema_version": 1,
        "generated_by": "tools/prepare_main_translation_llm_review.py",
        "status": "ready_for_review_dispatch_no_translation_performed",
        "policy": policy,
        "summary": summary,
        "gates": gates,
        "overall_ok": all(gates.values()),
        "outputs": {
            "inventory": str(INVENTORY_CSV.relative_to(ROOT)).replace("\\", "/"),
            "targets": str(TARGETS_CSV.relative_to(ROOT)).replace("\\", "/"),
            "exclusions": str(EXCLUSIONS_CSV.relative_to(ROOT)).replace("\\", "/"),
            "structural_preclear": str(STRUCTURAL_CSV.relative_to(ROOT)).replace("\\", "/"),
            "structural_batch_index": str(STRUCTURAL_INDEX_CSV.relative_to(ROOT)).replace("\\", "/"),
            "structural_batch_directory": str(STRUCTURAL_BATCH_DIR.relative_to(ROOT)).replace("\\", "/"),
            "batch_index": str(BATCH_INDEX_CSV.relative_to(ROOT)).replace("\\", "/"),
            "batch_directory": str(BATCH_DIR.relative_to(ROOT)).replace("\\", "/"),
        },
        "execution_order": [
            "complete W0 structural preclear; do not translate unresolved rows",
            "dispatch W1 quality/legacy-risk batches against Japanese original and full bundle context",
            "dispatch W2 missing-provenance/review batches",
            "dispatch W3 reviewed-but-quality-flagged batches",
            "record proposed_ko, new_translation_source, new_review_status only during the future review phase",
            "build candidates from the then-current main TIP; apply contract and 20-cell gates before runtime validation",
        ],
    }
    write_json = lambda path, value: path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_json(PLAN_JSON, plan)
    write_json(PROVENANCE_JSON, {
        "schema_version": 1,
        "main_tip_sha256": main_sha,
        "summary": provenance_summary,
        "exact_current_wording_evidence": evidence_details,
    })
    print(json.dumps({"overall_ok": plan["overall_ok"], "summary": summary, "gates": gates}, ensure_ascii=False, indent=2))
    return 0 if plan["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
