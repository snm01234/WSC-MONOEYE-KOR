#!/usr/bin/env python3
"""Collect exact native-stock dictionary reuse candidates without applying them."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIRS = {
    "battle": ROOT / "out/script/battle_dialogue_llm_review",
    "id": ROOT / "out/script/id_dialogue_llm_review",
}
OUT = ROOT / "out/script/native_stock_reuse_static_plan.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    candidates: list[dict[str, object]] = []
    reports: list[str] = []
    for route, directory in REPORT_DIRS.items():
        for path in sorted(directory.glob("*_static_map_audit.json")):
            reports.append(str(path.relative_to(ROOT)).replace("\\", "/"))
            report = json.loads(path.read_text(encoding="utf-8"))
            for row in report.get("rows") or []:
                if not row.get("native_stock_dictionary_fit"):
                    continue
                token_hex = str(row.get("native_stock_dictionary_token_hex") or "")
                capacity = int(row.get("body_capacity") or 0)
                token_len = len(bytes.fromhex(token_hex)) if token_hex else 0
                candidates.append({
                    "route_family": route,
                    "batch_id": report.get("batch_id"),
                    "abs": str(row.get("abs") or "").upper(),
                    "source_japanese": str(row.get("source_jp") or ""),
                    "proposed_korean": str(row.get("proposed_ko") or ""),
                    "native_stock_dictionary_index": str(row.get("native_stock_dictionary_index") or ""),
                    "recommended_body_hex": token_hex,
                    "recommended_body_len": token_len,
                    "body_capacity": capacity,
                    "capacity_fit": 0 < token_len <= capacity,
                    "embedded_nul": "00" in token_hex,
                    "metadata_hex_unchanged": str(row.get("metadata_hex") or ""),
                    "source_body_hex": str(row.get("source_body_hex") or ""),
                    "source_report": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "apply_allowed": False,
                    "runtime_validation_performed": False,
                })
    candidates.sort(key=lambda row: (str(row["route_family"]), str(row["abs"])))
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_native_stock_reuse_plan.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "static plan only; no ROM or sheet application and no runtime confirmation",
        "source_reports": reports,
        "counts": {
            "candidates": len(candidates),
            "capacity_fit": sum(bool(row["capacity_fit"]) for row in candidates),
            "embedded_nul": sum(bool(row["embedded_nul"]) for row in candidates),
            "apply_allowed": 0,
        },
        "candidates": candidates,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": report["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
