#!/usr/bin/env python3
"""Build a consumer/evidence matrix for non-Garrod dialogue risks.

This is a read-only companion to ``audit_dialogue_runtime_safety_gate.py``.
The safety gate intentionally fails closed; this report explains *why* each
special-route ext3 or Japanese-leading record is blocked, and whether the
current bytes still agree with older structure/lead evidence.  It never
promotes a candidate or edits a ROM.
"""
from __future__ import annotations

# RETIRED with the heuristic safety-gate implementation it accompanied.
if __name__ == "__main__":
    from legacy_dialogue_audit_quarantine import cli

    raise SystemExit(cli(__file__))
from legacy_dialogue_audit_quarantine import block

block(__file__)

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_dialogue_runtime_safety_gate import (  # noqa: E402
    DEFAULT_TARGET,
    DEFAULT_OUT as SAFETY_GATE_REPORT,
    JP_TBL_PATH,
    STRUCTURE_QUARANTINE,
    TBL_PATH,
    body_offset,
    find_rom,
    load_short_fixed_quarantine,
    make_descriptors,
    physical_widths,
    read_record,
    scan_dict_portals,
    semantic_widths,
    sha,
    stock_base,
)
from monoeye_rom import Dictionary, Tbl, load_rom  # noqa: E402

DEFAULT_OUT = ROOT / "out/patch/dialogue_runtime_evidence_matrix.json"
INVENTORY = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
BODYONLY_EVIDENCE = ROOT / "out/script/battle_dialogue_bodyonly_e518_stock_rehome_targets.csv"
RESTORED_LEADS = ROOT / "out/script/battle_dialogue_restored_lead_leakage_candidates.csv"
SAFE_LEADS = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
AMBIGUOUS_LEADS = ROOT / "out/script/battle_dialogue_false_lead_ambiguous.csv"
DUPLICATE_LEADS = ROOT / "out/script/battle_dialogue_duplicate_lead_stock_rehome_targets.csv"
CANONICAL_PREFIXED = ROOT / "data/runtime_text_residual_new_ko_prefixed_dialogue.json"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"


def identity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path.resolve()), "size": len(data), "sha256": sha(data)}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def hex_bytes(value: str) -> bytes:
    try:
        return bytes.fromhex(str(value or ""))
    except ValueError:
        return b""


def load_structure(original: bytes, target: bytes) -> dict[int, dict[str, Any]]:
    """Rebind the old structure catalog to both current source and target."""
    rows: dict[int, dict[str, Any]] = {}
    for raw in load_csv(INVENTORY):
        try:
            logical = int(str(raw.get("record_start") or ""), 16)
        except ValueError:
            continue
        metadata = hex_bytes(raw.get("metadata_hex") or "")
        source = read_record(original, logical)
        current = read_record(target, logical)
        catalog_payload = hex_bytes(raw.get("current_payload_hex") or "")
        source_payload = source[0] if source else b""
        current_payload = current[0] if current else b""
        rows[logical] = {
            "metadata_hex": metadata.hex().upper(),
            "metadata_present": bool(metadata),
            "source_metadata_exact": bool(metadata) and source_payload.startswith(metadata),
            "target_metadata_match": bool(metadata) and current_payload.startswith(metadata),
            "catalog_payload_fresh": bool(current) and current_payload == catalog_payload,
            "classification": str(raw.get("classification") or ""),
            "action": str(raw.get("action") or ""),
            "reason": str(raw.get("reason") or ""),
            "current_payload_hex": current_payload.hex().upper(),
            "current_terminator": None if current is None else f"{current[1]:06X}",
        }
    return rows


def load_bodyonly_evidence(target: bytes) -> dict[int, dict[str, Any]]:
    """Bind the old 284-row rehome sheet to current bytes without trusting it."""
    result: dict[int, dict[str, Any]] = {}
    for raw in load_csv(BODYONLY_EVIDENCE):
        try:
            logical = int(str(raw.get("abs") or ""), 16)
        except ValueError:
            continue
        before = hex_bytes(raw.get("before_hex") or "")
        current = read_record(target, logical)
        current_payload = current[0] if current else b""
        result[logical] = {
            "legacy_before_exact": bool(before) and current_payload == before,
            "legacy_before_hex": before.hex().upper(),
            "render": str(raw.get("render") or ""),
            "stock_index": str(raw.get("stock_index") or ""),
        }
    return result


def load_lead_evidence() -> dict[int, dict[str, Any]]:
    evidence: dict[int, dict[str, Any]] = {}
    if SAFE_LEADS.is_file():
        for raw in load_csv(SAFE_LEADS):
            try:
                logical = int(str(raw.get("abs") or ""), 16)
            except ValueError:
                continue
            evidence[logical] = {
                "source": "safe_lead_catalog",
                "classification": str(raw.get("classification") or ""),
                "final_disposition": "safe_text_lead",
                "screen_anchor": str(raw.get("screen_anchor") or ""),
                "candidate_payload_hex": str(raw.get("candidate_payload_hex") or ""),
            }
    if AMBIGUOUS_LEADS.is_file():
        for raw in load_csv(AMBIGUOUS_LEADS):
            try:
                logical = int(str(raw.get("abs") or ""), 16)
            except ValueError:
                continue
            # Safe evidence has priority over the broader ambiguous sheet.
            if logical in evidence and evidence[logical].get("final_disposition") == "safe_text_lead":
                continue
            evidence[logical] = {
                "source": "ambiguous_lead_catalog",
                "classification": str(raw.get("classification") or ""),
                "final_disposition": str(raw.get("final_disposition") or ""),
                "screen_anchor": str(raw.get("screen_anchor") or ""),
                "candidate_payload_hex": str(raw.get("candidate_payload_hex") or ""),
                "evidence": str(raw.get("evidence") or ""),
            }
    if RESTORED_LEADS.is_file():
        for raw in load_csv(RESTORED_LEADS):
            try:
                logical = int(str(raw.get("abs") or ""), 16)
            except ValueError:
                continue
            current = evidence.get(logical)
            if current is None:
                evidence[logical] = {
                    "source": "restored_lead_catalog",
                    "classification": str(raw.get("classification") or ""),
                    "final_disposition": "catalog_review_only",
                    "screen_anchor": str(raw.get("screen_anchor") or ""),
                    "candidate_payload_hex": str(raw.get("candidate_payload_hex") or ""),
                }
            else:
                current["restored_classification"] = str(raw.get("classification") or "")
    if DUPLICATE_LEADS.is_file():
        for raw in load_csv(DUPLICATE_LEADS):
            try:
                logical = int(str(raw.get("abs") or ""), 16)
            except ValueError:
                continue
            evidence.setdefault(
                logical,
                {
                    "source": "duplicate_lead_catalog",
                    "classification": "duplicate_visible_text",
                    "final_disposition": "safe_text_lead",
                },
            )
    return evidence


def load_canonical_addresses() -> set[int]:
    if not CANONICAL_PREFIXED.is_file():
        return set()
    document = json.loads(CANONICAL_PREFIXED.read_text(encoding="utf-8"))
    addresses: set[int] = set()
    for entry in document.get("entries") or []:
        queue_id = str(entry.get("queue_id") or "")
        if queue_id.startswith("prefixed_dialogue:"):
            try:
                addresses.add(int(queue_id.split(":", 1)[1], 16))
            except (IndexError, ValueError):
                continue
    return addresses


def ext3_evidence_class(
    *,
    route: str,
    structure: dict[str, Any] | None,
    legacy: dict[str, Any] | None,
) -> str:
    if route == "id_command_continuation":
        return "id_continuation_no_runtime_proof"
    if structure is None:
        return "body_only_no_structure_catalog"
    if not structure.get("source_metadata_exact"):
        return "body_only_source_structure_unproven"
    if structure.get("target_metadata_match"):
        return "target_metadata_present_route_recheck"
    if legacy and legacy.get("legacy_before_exact"):
        return "legacy_rehome_candidate_exact_no_runtime_proof"
    if structure.get("classification") == "text_initial_exception":
        return "text_initial_body_only_no_runtime_proof"
    return "authoritative_metadata_missing_no_runtime_proof"


def lead_evidence_class(
    logical: int,
    lead_evidence: dict[int, dict[str, Any]],
    canonical: set[int],
) -> str:
    if logical in canonical:
        return "canonical_queue_record_unvalidated"
    item = lead_evidence.get(logical)
    if item is None:
        return "current_residual_not_in_lead_catalog"
    disposition = str(item.get("final_disposition") or "")
    if disposition == "safe_text_lead":
        return "safe_lead_catalog_but_current_residual"
    if disposition == "unresolved_one_byte":
        return "unresolved_one_byte_lead"
    if disposition == "protected_control":
        return "protected_control_lead"
    if item.get("source") == "restored_lead_catalog":
        return "restored_lead_review"
    return "ambiguous_lead_review"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--gate-report", type=Path, default=SAFETY_GATE_REPORT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    target = bytes(load_rom(args.target))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    gate = json.loads(args.gate_report.read_text(encoding="utf-8"))
    target_hash = sha(target)
    gate_target_hash = str(((gate.get("target") or {}).get("sha256") or "")).lower()

    tbl = Tbl.load(TBL_PATH)
    jp_tbl = Tbl.load(JP_TBL_PATH)
    dictionary = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    descriptors, _bundles, _voice_runs = make_descriptors(original, jp_tbl)
    descriptor_by_abs = {int(row["logical"]): row for row in descriptors}
    structure = load_structure(original, target)
    legacy_bodyonly = load_bodyonly_evidence(target)
    lead_evidence = load_lead_evidence()
    canonical = load_canonical_addresses()
    # Keep both views: the target-bound view is useful for drift accounting,
    # while boundary classification must still know the complete reviewed
    # quarantine catalog. Otherwise a stale entry can look like a new hard
    # boundary change solely because it was filtered from the target view.
    short_fixed, short_meta = load_short_fixed_quarantine(target)
    short_fixed_catalog, _short_catalog_meta = load_short_fixed_quarantine()

    hard = gate.get("hard_failures_rows") or []
    ext3_rows = [row for row in hard if row.get("reason") == "unproven_ext3_on_special_consumer_route"]
    jp_rows = [row for row in hard if row.get("reason") == "japanese_residual_in_runtime_body"]
    boundary_rows = [
        ("hard", row)
        for row in hard
        if row.get("reason") == "next_control_lead_changed"
    ] + [
        ("review", row)
        for row in (gate.get("review_items") or [])
        if row.get("reason") == "next_control_lead_changed_into_short_fixed_quarantine"
    ]
    drift_rows = [row for row in hard if row.get("reason") == "short_fixed_quarantine_target_drift"]

    ext3_matrix: list[dict[str, Any]] = []
    for row in ext3_rows:
        logical = int(str(row.get("abs") or "0"), 16)
        descriptor = descriptor_by_abs.get(logical) or {}
        current = read_record(target, logical)
        payload = current[0] if current else b""
        offset = body_offset(descriptor, payload) if descriptor else 0
        if offset is None:
            offset = 0
        body = payload[offset:]
        portals = scan_dict_portals(body)
        rendered = dictionary.expand(body, tbl) if body else ""
        structure_row = structure.get(logical)
        legacy = legacy_bodyonly.get(logical)
        ext3_matrix.append(
            {
                "abs": f"{logical:06X}",
                "route": str(row.get("route") or ""),
                "family": str(row.get("family") or ""),
                "evidence_class": ext3_evidence_class(
                    route=str(row.get("route") or ""),
                    structure=structure_row,
                    legacy=legacy,
                ),
                "structure_metadata_hex": str((structure_row or {}).get("metadata_hex") or ""),
                "source_metadata_exact": bool((structure_row or {}).get("source_metadata_exact")),
                "target_metadata_match": bool((structure_row or {}).get("target_metadata_match")),
                "structure_catalog_payload_fresh": bool((structure_row or {}).get("catalog_payload_fresh")),
                "structure_classification": str((structure_row or {}).get("classification") or ""),
                "legacy_rehome_candidate_exact": bool((legacy or {}).get("legacy_before_exact")),
                "portals": portals,
                "rendered": rendered,
                "physical_line_cells": physical_widths(rendered),
                "semantic_line_cells": semantic_widths(rendered),
                "target_payload_hex": payload.hex().upper(),
            }
        )

    lead_matrix: list[dict[str, Any]] = []
    for row in jp_rows:
        logical = int(str(row.get("abs") or "0"), 16)
        current = read_record(target, logical)
        payload = current[0] if current else b""
        structure_row = structure.get(logical)
        item = lead_evidence.get(logical) or {}
        lead_matrix.append(
            {
                "abs": f"{logical:06X}",
                "route": str(row.get("route") or ""),
                "family": str(row.get("family") or ""),
                "profile": str(row.get("profile") or ""),
                "evidence_class": lead_evidence_class(logical, lead_evidence, canonical),
                "catalog_source": str(item.get("source") or ""),
                "catalog_classification": str(item.get("classification") or ""),
                "catalog_disposition": str(item.get("final_disposition") or ""),
                "screen_anchor": str(item.get("screen_anchor") or ""),
                "structure_metadata_hex": str((structure_row or {}).get("metadata_hex") or ""),
                "target_metadata_match": bool((structure_row or {}).get("target_metadata_match")),
                "target_payload_hex": payload.hex().upper(),
                "rendered": str(row.get("rendered") or ""),
            }
        )

    boundary_matrix: list[dict[str, Any]] = []
    for severity, row in boundary_rows:
        logical = int(str(row.get("abs") or "0"), 16)
        source = row.get("source") or {}
        try:
            # The gate stores the boundary signature but deliberately does
            # not duplicate the source terminator. Re-read the immutable
            # source record so this matrix identifies the exact next record.
            if row.get("source_next"):
                next_source = int(str(row["source_next"]), 16)
            else:
                source_info = read_record(original, logical)
                source_term = None if source_info is None else source_info[1]
                next_source = None if source_term is None else source_term + int(source.get("nul_run") or 0)
        except (TypeError, ValueError):
            next_source = None
        if severity == "review" and row.get("reason") == "next_control_lead_changed_into_short_fixed_quarantine":
            if logical in short_fixed_catalog:
                quarantine_anchor = "current_record"
            elif next_source in short_fixed_catalog:
                quarantine_anchor = "next_record"
            else:
                # The gate also treats the row itself as a quarantine
                # consumer; retain that decision even if the catalog address
                # was subsequently changed by a drift filter.
                quarantine_anchor = "gate_review_without_current_catalog_match"
            evidence_class = "into_short_fixed_quarantine"
        else:
            quarantine_anchor = None
            evidence_class = (
                "into_short_fixed_quarantine"
                if next_source in short_fixed_catalog
                else "non_quarantine_boundary_change"
            )
        boundary_matrix.append(
            {
                "abs": f"{logical:06X}",
                "severity": severity,
                "reason": str(row.get("reason") or ""),
                "route": str(row.get("route") or ""),
                "source": source,
                "target": row.get("target") or {},
                "source_next_record": None if next_source is None else f"{next_source:06X}",
                "evidence_class": evidence_class,
                "quarantine_anchor": quarantine_anchor,
            }
        )

    drift_matrix: list[dict[str, Any]] = []
    for row in drift_rows:
        logical = int(str(row.get("abs") or "0"), 16)
        current = read_record(target, logical)
        rendered = ""
        if current:
            try:
                rendered = dictionary.expand(current[0], tbl)
            except Exception:  # noqa: BLE001
                rendered = "<decode-error>"
        drift_matrix.append(
            {
                "abs": f"{logical:06X}",
                "current_payload_hex": "" if current is None else current[0].hex().upper(),
                "current_rendered": rendered,
                "short_fixed_cataloged": logical in short_fixed_catalog,
            }
        )

    counts = {
        "ext3_special_records": len(ext3_matrix),
        "ext3_by_route": dict(Counter(row["route"] for row in ext3_matrix).most_common()),
        "ext3_by_evidence_class": dict(Counter(row["evidence_class"] for row in ext3_matrix).most_common()),
        "ext3_with_current_structure_payload_snapshot_match": sum(
            row["structure_catalog_payload_fresh"] for row in ext3_matrix
        ),
        "ext3_with_target_metadata_match": sum(row["target_metadata_match"] for row in ext3_matrix),
        "ext3_legacy_rehome_exact": sum(row["legacy_rehome_candidate_exact"] for row in ext3_matrix),
        "japanese_leading_records": len(lead_matrix),
        "japanese_by_evidence_class": dict(Counter(row["evidence_class"] for row in lead_matrix).most_common()),
        "japanese_by_route": dict(Counter(row["route"] for row in lead_matrix).most_common()),
        "boundary_changes": len(boundary_matrix),
        "boundary_by_evidence_class": dict(Counter(row["evidence_class"] for row in boundary_matrix).most_common()),
        "boundary_by_severity": dict(Counter(row["severity"] for row in boundary_matrix).most_common()),
        "short_fixed_target_drift": len(drift_matrix),
        "short_fixed_matched_after_drift_filter": len(short_fixed),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_dialogue_runtime_evidence_matrix.py",
        "read_only": True,
        "ok": target_hash == gate_target_hash,
        "target": {"path": str(args.target.resolve()), "size": len(target), "sha256": target_hash},
        "original": identity(original_path),
        "gate_report": identity(args.gate_report),
        "inputs": {name: identity(path) for name, path in {
            "structure_inventory": INVENTORY,
            "bodyonly_evidence": BODYONLY_EVIDENCE,
            "restored_leads": RESTORED_LEADS,
            "safe_leads": SAFE_LEADS,
            "ambiguous_leads": AMBIGUOUS_LEADS,
            "duplicate_leads": DUPLICATE_LEADS,
            "canonical_prefixed": CANONICAL_PREFIXED,
        }.items() if path.is_file()},
        "freshness": {
            "gate_target_sha256_matches": target_hash == gate_target_hash,
            "short_fixed": short_meta,
            "structure_rows": len(structure),
        },
        "counts": counts,
        "ext3_matrix": ext3_matrix,
        "japanese_lead_matrix": lead_matrix,
        "boundary_matrix": boundary_matrix,
        "short_fixed_drift_matrix": drift_matrix,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "counts": counts, "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
