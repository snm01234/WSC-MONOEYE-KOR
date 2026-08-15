#!/usr/bin/env python3
"""Stage reviewed rows from a current-TIP static scenario batch.

Static workstream batches are intentionally not shaped like the historical
MR input files.  This writer bridges one such batch into the authoritative
scenario-result format while preserving explicit structural quarantine rows.
It never edits the canonical sheet, ROM, or SaveRAM.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "out/script/translation_workstreams_static_batches"
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
CONTRACT_PATH = ROOT / "out/script/dialogue_runtime_contracts.json"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"

JP_SYLLABARY = re.compile(r"[\u3040-\u30ff]")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_hex(value: str) -> str:
    return sha_bytes(bytes.fromhex(value.replace(" ", "")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("static_batch_id")
    ap.add_argument("result_batch_id")
    ap.add_argument("mapping", type=Path)
    ap.add_argument("--quarantine", action="append", default=[])
    args = ap.parse_args()

    static_id = args.static_batch_id.upper()
    result_id = args.result_batch_id.upper()
    source = STATIC_DIR / f"{static_id}.csv"
    mapping_path = args.mapping if args.mapping.is_absolute() else ROOT / args.mapping
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig", newline="")))
    contracts = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["contracts"]
    by_abs = {str(row["address"]).upper(): row for row in contracts}
    source_abs = {str(row["address_or_slot"]).upper() for row in rows}
    quarantine = {str(x).upper() for x in args.quarantine}
    if not quarantine <= source_abs:
        raise SystemExit(f"quarantine mismatch: {sorted(quarantine - source_abs)}")
    missing = sorted(source_abs - set(mapping) - quarantine)
    extra = sorted(set(mapping) - source_abs)
    if missing or extra:
        raise SystemExit(f"mapping mismatch: missing={missing} extra={extra}")

    main_sha = sha_bytes(ROM.read_bytes())
    fields = [
        "review_id", "wave", "batch_id", "batch_order", "bundle_id", "bundle_order",
        "abs", "bank", "line_role", "route", "contract_status",
        "apply_blocked_by_structure", "batch_gate_status", "source_jp",
        "source_jp_authority", "contract_original_jp", "source_sheet_jp",
        "current_ko", "source_sheet_ko", "source_sheet_stale_vs_tip",
        "explicit_llm_provenance", "completed_review_evidence", "provenance_sources",
        "review_sources", "selection_reasons", "quality_flags", "priority",
        "workflow_status", "main_tip_sha256", "source_body_sha256", "proposed_ko",
        "reviewer_notes", "new_translation_source", "new_review_status",
        "source_model", "reviewed_at", "glossary_ids", "apply_status",
        "translation_source", "review_status", "review_count",
    ]
    model = "GPT-5.6 current Codex model (Luna unavailable in this runtime)"
    out_rows: list[dict[str, str]] = []
    source_hashes: set[str] = set()
    bundle_ids: set[str] = set()
    for order, row in enumerate(rows, 1):
        address = str(row["address_or_slot"]).upper()
        contract = by_abs.get(address)
        source_jp = str(row.get("source_jp") or "")
        current_ko = str(row.get("current_ko") or "")
        body_hex = str(row.get("body_hex") or "").replace(" ", "")
        source_body_hash = sha_hex(str(contract.get("source_body_hex") or body_hex)) if contract else sha_hex(body_hex) if body_hex else ""
        if source_body_hash:
            source_hashes.add(source_body_hash)
        bundle_id = str((contract or {}).get("bundle_id") or f"scenario_{address}")
        bundle_ids.add(bundle_id)
        is_quarantine = address in quarantine
        text = "" if is_quarantine else str(mapping[address])
        if not is_quarantine and (not text or len(text) > 20 or JP_SYLLABARY.search(text) or "\x00" in text):
            raise SystemExit(f"invalid translation at {address}: {text!r}")
        contract_status = str((contract or {}).get("status") or "quarantine")
        route = str((contract or {}).get("route") or "scenario_gap")
        line_role = str((contract or {}).get("line_role") or "unknown")
        structural = "structural_quarantine" if is_quarantine else "llm_retranslated_structural_hold"
        out_rows.append({
            "review_id": f"R{address}",
            "wave": "SG_static_gap_retranslation",
            "batch_id": result_id,
            "batch_order": str(order),
            "bundle_id": bundle_id,
            "bundle_order": "1",
            "abs": address,
            "bank": address[:2],
            "line_role": line_role,
            "route": route,
            "contract_status": contract_status,
            "apply_blocked_by_structure": "yes" if is_quarantine or contract_status != "active" else "no",
            "batch_gate_status": "blocked_pending_structural_preclear",
            "source_jp": source_jp,
            "source_jp_authority": "runtime_contract_and_original_boundary" if contract else "static_gap_quarantine",
            "contract_original_jp": str((contract or {}).get("original_japanese") or source_jp),
            "source_sheet_jp": source_jp,
            "current_ko": current_ko,
            "source_sheet_ko": current_ko,
            "source_sheet_stale_vs_tip": "no",
            "explicit_llm_provenance": "no",
            "completed_review_evidence": "yes" if not is_quarantine else "no",
            "provenance_sources": "current_tip_static_gap_batch",
            "review_sources": "current_tip_static_gap_batch",
            "selection_reasons": "canonical_gap_retranslation",
            "quality_flags": "",
            "priority": "P1" if not is_quarantine else "P0",
            "workflow_status": structural,
            "main_tip_sha256": main_sha,
            "source_body_sha256": source_body_hash,
            "proposed_ko": text,
            "reviewer_notes": "원문과 인접 레코드 문맥을 반영한 LLM 재검수. 구조 quarantine은 해제하지 않음." if not is_quarantine else "caller·경계 근거가 없어 추측 번역하지 않고 구조 quarantine으로 유지.",
            "new_translation_source": "llm" if not is_quarantine else "structural_quarantine",
            "new_review_status": structural,
            "source_model": model,
            "reviewed_at": date.today().isoformat(),
            "glossary_ids": "",
            "apply_status": "not_applied_structural_preclear" if not is_quarantine else "not_applied_semantic_pending",
            "translation_source": "llm" if not is_quarantine else "",
            "review_status": structural,
            "review_count": "1" if not is_quarantine else "0",
        })

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"{result_id}_reviewed.csv"
    manifest_path = RESULT_DIR / f"{result_id}_result_manifest.json"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    # Materialized SG batches are regenerated after every queue refresh.  Keep
    # an immutable source snapshot beside the historical MR batch so the
    # manifest never points at a later batch with reused numbering.
    source_snapshot = ROOT / "out/script/main_translation_llm_review/batches" / f"{result_id}_source.csv"
    source_snapshot.parent.mkdir(parents=True, exist_ok=True)
    source_snapshot.write_bytes(source.read_bytes())
    manifest = {
        "schema_version": 1,
        "batch_id": result_id,
        "source_batch": str(source_snapshot.relative_to(ROOT)).replace("\\", "/"),
        "source_snapshot": True,
        "result": str(out.relative_to(ROOT)).replace("\\", "/"),
        "rows": len(out_rows),
        "semantic_rows": len(out_rows) - len(quarantine),
        "quarantine_rows": len(quarantine),
        "quarantine_abs": sorted(quarantine),
        "bundles": len(bundle_ids),
        "semantic_review": "partial" if quarantine else "complete",
        "structural_status": "hold",
        "apply_status": "not_applied",
        "main_tip_sha256": main_sha,
        "source_body_sha256_set": sorted(source_hashes),
        "source_model": model,
        "translation_source": "llm_with_structural_quarantine" if quarantine else "llm",
        "review_status": "llm_retranslated_structural_hold",
        "reason": "static gap rows staged; unresolved rows remain structural quarantine",
        "canonical_sheet_changed": False,
        "rom_changed": False,
        "saveram_changed": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(out), "manifest": str(manifest_path), "rows": len(out_rows), "quarantine": sorted(quarantine)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
