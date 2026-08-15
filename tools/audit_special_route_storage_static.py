#!/usr/bin/env python3
"""Summarize battle/ID storage decisions without proving runtime behavior."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE_DIRS = {
    "battle_contract": ROOT / "out/script/battle_dialogue_llm_review",
    "id_contract": ROOT / "out/script/id_dialogue_llm_review",
}
SEMANTIC = ROOT / "out/script/battle_dialogue_llm_review/semantic_ready_static_audit.json"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
OUT = ROOT / "out/script/special_route_storage_static_audit.json"


def has_nul_hex(value: str) -> bool:
    try:
        return b"\x00" in bytes.fromhex(value) if value else False
    except ValueError:
        return True


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    rows: list[dict[str, object]] = []
    report_files: list[str] = []
    for family, directory in ROUTE_DIRS.items():
        for path in sorted(directory.glob("*_static_map_audit.json")):
            report_files.append(str(path.relative_to(ROOT)).replace("\\", "/"))
            report = json.loads(path.read_text(encoding="utf-8"))
            for item in report.get("rows") or []:
                direct = bool(item.get("direct_encoding_fit"))
                native = bool(item.get("native_stock_dictionary_fit"))
                semantic_ok = bool(item.get("semantic_quality_ok"))
                route = str(item.get("route") or "")
                if not semantic_ok:
                    decision = "semantic_quality_quarantine"
                elif direct:
                    decision = "direct_payload_candidate_structural_hold"
                elif native:
                    decision = "native_stock_token_candidate_structural_hold"
                else:
                    decision = "dictionary_or_capacity_hold"
                rows.append({
                    "family": family,
                    "batch_id": report.get("batch_id"),
                    "abs": str(item.get("abs") or "").upper(),
                    "route": route,
                    "source_japanese": str(item.get("source_jp") or ""),
                    "proposed_korean": str(item.get("proposed_ko") or ""),
                    "body_capacity": item.get("body_capacity"),
                    "direct_encoding_fit": direct,
                    "native_stock_dictionary_fit": native,
                    "semantic_quality_ok": semantic_ok,
                    "native_stock_dictionary_token_hex": str(item.get("native_stock_dictionary_token_hex") or ""),
                    "decision": decision,
                    "ext3_allowed_by_policy": route not in {"battle_body_only", "id_continuation"},
                    "compact3_allowed_by_policy": False,
                    "embedded_nul": has_nul_hex(str(item.get("native_stock_dictionary_token_hex") or ""))
                    or has_nul_hex(str(item.get("encoded_hex_direct") or "")),
                    "application_allowed": False,
                })

    semantic = json.loads(SEMANTIC.read_text(encoding="utf-8")) if SEMANTIC.is_file() else {}
    for item in semantic.get("rows") or []:
        rows.append({
            "family": "battle_semantic_ready",
            "batch_id": str(item.get("batch_id") or ""),
            "abs": str(item.get("abs") or "").upper(),
            "route": "battle_semantic_unbound",
            "source_japanese": str(item.get("source_japanese") or ""),
            "proposed_korean": str(item.get("proposed_korean") or ""),
            "body_capacity": item.get("body_capacity"),
            "direct_encoding_fit": bool(item.get("direct_encoding_fit")),
            "native_stock_dictionary_fit": False,
            "semantic_quality_ok": bool(item.get("semantic_quality_ok")),
            "native_stock_dictionary_token_hex": "",
            "decision": (
                "semantic_quality_quarantine"
                if not item.get("semantic_quality_ok")
                else
                "direct_payload_candidate_structural_hold"
                if item.get("direct_encoding_fit")
                else "dictionary_or_capacity_hold"
            ),
            "ext3_allowed_by_policy": False,
            "compact3_allowed_by_policy": False,
            "embedded_nul": False,
            "application_allowed": False,
        })

    counts: dict[str, int] = {
        "rows": len(rows),
        "battle_contract_rows": sum(row["family"] == "battle_contract" for row in rows),
        "id_contract_rows": sum(row["family"] == "id_contract" for row in rows),
        "battle_semantic_ready_rows": sum(row["family"] == "battle_semantic_ready" for row in rows),
        "direct_payload_candidates": sum(row["decision"] == "direct_payload_candidate_structural_hold" for row in rows),
        "native_stock_candidates": sum(row["decision"] == "native_stock_token_candidate_structural_hold" for row in rows),
        "dictionary_or_capacity_holds": sum(row["decision"] == "dictionary_or_capacity_hold" for row in rows),
        "semantic_quality_quarantine": sum(row["decision"] == "semantic_quality_quarantine" for row in rows),
        "embedded_nul": sum(bool(row["embedded_nul"]) for row in rows),
        "ext3_policy_forbidden": sum(not bool(row["ext3_allowed_by_policy"]) for row in rows),
        "application_allowed": 0,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_special_route_storage_static.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "static storage plan only; no route runtime proof or ROM application",
        "policy": {
            "battle_body_only_ext3": "forbidden_without_explicit_support",
            "id_continuation_ext3": "forbidden_without_explicit_support",
            "compact3": "forbidden",
            "embedded_nul": "forbidden",
            "native_stock_reuse": "candidate only; boundary/application audit still required",
        },
        "inputs": {
            "main_rom_sha256": sha(ROM),
            "contract_sha256": sha(CONTRACT),
            "map_reports": report_files,
            "semantic_ready_sha256": sha(SEMANTIC) if SEMANTIC.is_file() else "",
        },
        "counts": counts,
        "rows": rows,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
