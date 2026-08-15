#!/usr/bin/env python3
"""Write a semantically reviewed subset of a scenario batch.

This is intentionally a staging-only writer.  Rows excluded by ``--quarantine``
are copied from the existing result without inventing replacement text, and the
manifest remains semantically incomplete until those rows receive an
authoritative structural decision.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

from write_mr0001_review_result import glossary_ids

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "out/script/main_translation_llm_review"
RESULT_DIR = BASE / "results"


def has_japanese_syllabary(text: str) -> bool:
    return any(0x3041 <= ord(c) <= 0x3096 or 0x30A1 <= ord(c) <= 0x30FA for c in text)


def validate(abs_addr: str, text: str) -> None:
    if not text or len(text) > 20 or has_japanese_syllabary(text) or "\x00" in text:
        raise SystemExit(f"invalid translation at {abs_addr}: {text!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_id")
    ap.add_argument("mapping", type=Path)
    ap.add_argument("--quarantine", action="append", default=[],
                    help="absolute row address to retain as structural quarantine")
    args = ap.parse_args()
    bid = args.batch_id.upper()
    source = BASE / "batches" / f"{bid}.csv"
    old_result = RESULT_DIR / f"{bid}_reviewed.csv"
    mapping_path = args.mapping if args.mapping.is_absolute() else ROOT / args.mapping
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig", newline="")))
    old_rows = list(csv.DictReader(old_result.open(encoding="utf-8-sig", newline="")))
    old_by_abs = {r["abs"]: r for r in old_rows}
    quarantine = set(args.quarantine)
    source_abs = {r["abs"] for r in rows}
    if not quarantine <= source_abs:
        raise SystemExit(f"quarantine mismatch: {sorted(quarantine - source_abs)}")
    missing = sorted(source_abs - set(mapping) - quarantine)
    extra = sorted(set(mapping) - source_abs)
    if missing or extra:
        raise SystemExit(f"mapping mismatch: missing={missing} extra={extra}")
    if set(old_by_abs) != source_abs:
        raise SystemExit("existing result does not cover source batch exactly")
    fields = list(rows[0])
    for field in ["source_model", "reviewed_at", "glossary_ids", "apply_status", "translation_source", "review_status", "review_count"]:
        if field not in fields:
            fields.append(field)
    out_rows = []
    model = "GPT-5.6 current Codex model (Luna unavailable in this runtime)"
    for row in rows:
        addr = row["abs"]
        if addr in quarantine:
            item = dict(old_by_abs[addr])
            # Keep the prior proposal only as an auditable placeholder.  It is
            # never considered an LLM translation and cannot be applied.
            item.update({
                "source_model": model,
                "reviewed_at": date.today().isoformat(),
                "glossary_ids": glossary_ids(row["source_jp"]),
                "new_translation_source": "structural_quarantine",
                "new_review_status": "structural_quarantine_pending_llm",
                "apply_status": "not_applied_semantic_pending",
                "translation_source": "",
                "review_status": "structural_quarantine_pending_llm",
                "review_count": "0",
                "reviewer_notes": "원문 단편의 caller/경계 판정이 없어 추측 번역하지 않고 구조 격리함.",
            })
        else:
            text = mapping[addr]
            validate(addr, text)
            item = dict(row)
            item.update({
                "source_model": model,
                "reviewed_at": date.today().isoformat(),
                "glossary_ids": glossary_ids(row["source_jp"]),
                "proposed_ko": text,
                "reviewer_notes": "원문·인접 대사 문맥을 반영한 LLM 재번역 후보이며 runtime contract quarantine은 해제하지 않음.",
                "new_translation_source": "llm",
                "new_review_status": "llm_retranslated_structural_hold",
                "apply_status": "not_applied_structural_preclear",
                "translation_source": "llm",
                "review_status": "llm_retranslated_structural_hold",
                "review_count": "1",
            })
        out_rows.append(item)
    out = RESULT_DIR / f"{bid}_reviewed.csv"
    manifest_path = RESULT_DIR / f"{bid}_result_manifest.json"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    manifest = {
        "schema_version": 1,
        "batch_id": bid,
        "source_batch": str(source.relative_to(ROOT)).replace("\\", "/"),
        "result": str(out.relative_to(ROOT)).replace("\\", "/"),
        "rows": len(out_rows),
        "semantic_rows": len(out_rows) - len(quarantine),
        "quarantine_rows": len(quarantine),
        "quarantine_abs": sorted(quarantine),
        "bundles": len({r["bundle_id"] for r in out_rows}),
        "semantic_review": "partial",
        "structural_status": "hold",
        "apply_status": "not_applied",
        "main_tip_sha256": rows[0]["main_tip_sha256"],
        "source_body_sha256_set": sorted({r["source_body_sha256"] for r in rows}),
        "source_model": model,
        "translation_source": "llm_with_structural_quarantine",
        "review_status": "llm_retranslated_structural_hold",
        "reason": "all non-quarantine rows retranslated; unresolved fragments remain structural quarantine",
        "canonical_sheet_changed": False,
        "rom_changed": False,
        "saveram_changed": False,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(out), "manifest": str(manifest_path), "rows": len(out_rows), "quarantine": sorted(quarantine)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
