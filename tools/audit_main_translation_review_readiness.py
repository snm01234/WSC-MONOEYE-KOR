#!/usr/bin/env python3
"""Audit semantic review outputs against the single runtime contract.

This report is intentionally read-only.  It separates rows that are statically
eligible for a future candidate build from contract-quarantined or special
route rows.  It never treats a reviewed CSV as a ROM patch and never changes
the canonical sheet, ROM, or SaveRAM.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SHEET = ROOT / "out/script/translation_sheet.csv"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_JSON = ROOT / "out/script/main_translation_llm_review/review_readiness.json"
OUT_CSV = ROOT / "out/script/main_translation_llm_review/review_readiness.csv"
PROBE_REPORT = ROOT / "out/patch/main_translation_structural_preclear_probe.json"
JP = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_text(text: str) -> tuple[bool, str]:
    if not text:
        return False, "empty_translation"
    if len(text) > 20:
        return False, "semantic_width_over_20"
    if "\x00" in text:
        return False, "embedded_nul"
    if JP.search(text.replace("・", "")):
        return False, "japanese_syllabary_residual"
    return True, ""


def main() -> None:
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))["contracts"]
    by_abs = {str(row["address"]).upper(): row for row in contracts}
    probe_selected: set[str] = set()
    if PROBE_REPORT.is_file():
        probe = json.loads(PROBE_REPORT.read_text(encoding="utf-8"))
        if int((probe.get("counts") or {}).get("candidate_hard_failures", 1)) == 0:
            probe_selected = {str(row["abs"]).upper() for row in probe.get("selected") or []}
    rows: list[dict[str, str]] = []
    manifests = []
    for manifest_path in sorted(RESULT_DIR.glob("MR*_result_manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifests.append(manifest)
        semantic_ready_all = manifest.get("semantic_review") == "complete"
        result_path = ROOT / manifest["result"]
        with result_path.open(encoding="utf-8-sig", newline="") as fh:
            for item in csv.DictReader(fh):
                address = str(item["abs"]).upper()
                contract = by_abs.get(address)
                ok, reason = valid_text(str(item.get("proposed_ko") or ""))
                status = "contract_gap"
                detail = reason or "missing_runtime_contract"
                # A partial batch is allowed to carry fully retranslated rows
                # alongside explicit structural-quarantine rows.  The row
                # status, not the batch label alone, is authoritative here.
                row_semantic_ready = semantic_ready_all or (
                    str(item.get("new_translation_source") or "") == "llm"
                    and str(item.get("new_review_status") or "").startswith("llm_retranslated")
                )
                if not row_semantic_ready:
                    status = "semantic_review_pending"
                    detail = str(manifest.get("reason") or "source-grounded semantic LLM review pending")
                elif contract is not None:
                    if not ok:
                        status, detail = "translation_gate_fail", reason
                    elif contract["status"] == "quarantine":
                        if address in probe_selected:
                            status = "static_preclear_candidate"
                            detail = "validated_static_preclear_probe_candidate"
                        else:
                            status = "quarantine_blocked"
                            detail = str(contract.get("conflict") or contract.get("evidence") or "quarantine")
                    elif contract["route"] == "scenario_first":
                        status = "static_preclear_candidate"
                        detail = "explicit_17_xx_18_grammar_and_original_boundary"
                    else:
                        status = "special_route_probe_required"
                        detail = f"active_{contract['route']}_requires_route_specific_storage_proof"
                rows.append({
                    "abs": address,
                    "batch_id": str(item.get("batch_id") or ""),
                    "bundle_id": str(item.get("bundle_id") or ""),
                    "route": str(contract.get("route") if contract else ""),
                    "contract_status": str(contract.get("status") if contract else "missing"),
                    "readiness": status,
                    "reason": detail,
                    "result_csv": str(manifest["result"]),
                })
    counts = Counter(row["readiness"] for row in rows)
    manifest_checks = {
        "manifest_count": len(manifests),
        "all_semantic_complete": all(m.get("semantic_review") == "complete" for m in manifests),
        "all_structural_hold": all(m.get("structural_status") == "hold" for m in manifests),
        "all_not_applied": all(str(m.get("apply_status", "")).startswith("not_applied") for m in manifests),
        "all_main_tip_bound": len({str(m.get("main_tip_sha256", "")).lower() for m in manifests}) == 1,
    }
    fields = list(rows[0]) if rows else ["abs", "batch_id", "bundle_id", "route", "contract_status", "readiness", "reason", "result_csv"]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_main_translation_review_readiness.py",
        "read_only": True,
        "rows": len(rows),
        "counts": dict(sorted(counts.items())),
        "manifest_checks": manifest_checks,
        "promotion_ready_rows": 0,
        "promotion_ready_reason": "no candidate ROM has been encoded and runtime-validated from these review CSVs",
        "inputs": {
            "contract_sha256": sha(CONTRACT),
            "main_rom_sha256": sha(ROM),
            "translation_sheet_sha256": sha(SHEET),
            "saveram_sha256": sha(SAVE),
        },
        "outputs": {
            "csv": str(OUT_CSV.relative_to(ROOT)).replace("\\", "/"),
            "json": str(OUT_JSON.relative_to(ROOT)).replace("\\", "/"),
        },
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
