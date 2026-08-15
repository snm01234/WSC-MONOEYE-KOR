#!/usr/bin/env python3
"""Audit the already-retranslated short battle candidates without applying them."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "out/script/battle_dialogue_llm_review/results/battle_voice_ambiguous_nonstub_ready_reviewed.csv"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
OUT = ROOT / "out/script/battle_dialogue_llm_review/semantic_ready_static_audit.json"

import sys
sys.path.insert(0, str(ROOT / "tools"))
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    csv.field_size_limit(10**9)
    with INPUT.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    tbl = Tbl.load(TBL)
    details = []
    for row in rows:
        proposed = str(row.get("proposed_ko") or "")
        encoded = try_encode_ko_text(
            normalize_ko_text(proposed), tbl,
            hangul_marker_code=marker_code(), hangul_marker_mode="run",
        )
        quality_ok = bool(proposed) and any("\uac00" <= ch <= "\ud7a3" for ch in proposed) and len(proposed) <= 20 and "\x00" not in proposed and not any(
            "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff"
            for ch in proposed
        )
        capacity = int(row.get("body_capacity") or 0)
        direct_fit = bool(encoded) and b"\x00" not in encoded and len(encoded) <= capacity
        details.append({
            "abs": str(row.get("abs") or "").upper(),
            "batch_id": str(row.get("batch_id") or ""),
            "source_japanese": str(row.get("original_jp") or ""),
            "current_text": str(row.get("current_text") or ""),
            "proposed_korean": proposed,
            "body_capacity": capacity,
            "encoded_len_direct": len(encoded) if encoded else None,
            "encoded_hex_direct": encoded.hex().upper() if encoded else "",
            "embedded_nul_direct": bool(encoded and b"\x00" in encoded),
            "semantic_quality_ok": quality_ok,
            "semantic_quality_reason": (
                "ok" if quality_ok else "missing_hangul_or_japanese_control_residual_or_empty"
            ),
            "direct_encoding_fit": direct_fit,
            "review_status": str(row.get("review_status") or ""),
            "new_review_status": str(row.get("new_review_status") or ""),
            "source_model": str(row.get("source_model") or ""),
            "application_allowed": False,
        })
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_semantic_ready_static.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "battle semantic result still requires structural preclear and route-specific storage proof",
        "inputs": {
            "result_csv": str(INPUT.relative_to(ROOT)).replace("\\", "/"),
            "result_sha256": sha(INPUT),
            "main_rom_sha256": sha(ROM),
            "tbl_sha256": sha(TBL),
        },
        "counts": {
            "rows": len(details),
            "semantic_quality_ok": sum(bool(r["semantic_quality_ok"]) for r in details),
            "direct_encoding_fit": sum(bool(r["direct_encoding_fit"]) for r in details),
            "embedded_nul_direct": sum(bool(r["embedded_nul_direct"]) for r in details),
            "encoding_hold": sum(not bool(r["direct_encoding_fit"]) for r in details),
            "application_allowed": 0,
        },
        "rows": details,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": report["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
