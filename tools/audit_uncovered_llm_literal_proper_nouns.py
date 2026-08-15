#!/usr/bin/env python3
"""Audit the 1,858 LLM-literal uncovered draft rows for Gundam terminology drift.

This is read-only with respect to ROM and source sheets. It checks every
unreviewed_draft row against the project's established terminology index and
emits row-level evidence for registered terms missing from the Korean draft,
unregistered katakana tokens, and terminology conflicts.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from mixed_residual_translations import load_terminology_index  # noqa: E402

SHEET = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
OUT_JSON = ROOT / "out/script/uncovered_llm_literal_proper_noun_audit.json"
OUT_CSV = ROOT / "out/script/uncovered_llm_literal_proper_noun_audit.csv"
TERMINOLOGY = [
    ROOT / "data/proper_nouns_ko.json",
    ROOT / "data/unit_names_ko.json",
    ROOT / "data/weapon_names_ko.json",
    ROOT / "data/ui_proper_nouns_ko.json",
    ROOT / "data/name75_terms_ko.json",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    index = load_terminology_index([str(path) for path in TERMINOLOGY])
    with SHEET.open("r", encoding="utf-8-sig", newline="") as fh:
        all_rows = list(csv.DictReader(fh))
    rows = [row for row in all_rows if row.get("review_status") == "unreviewed_draft"]

    registered = Counter()
    unregistered = Counter()
    conflicts = Counter()
    findings: list[dict[str, object]] = []
    rows_with_tokens = 0

    # Direct substring matching is required because this ROM often encodes the
    # long-vowel mark as FULLWIDTH HYPHEN-MINUS '－'. The generic katakana
    # tokenizer therefore splits ガト－/ジュド－/ティタ－ンズ/etc. and is not
    # sufficient for Gundam proper-noun validation.
    direct_terms: dict[str, set[str]] = {}
    for path in TERMINOLOGY:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        candidates = []
        if isinstance(document.get("entries"), list):
            candidates.extend(document["entries"])
        if isinstance(document.get("lines"), list):
            candidates.extend(document["lines"])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            jp = str(item.get("jp") or "")
            ko = str(item.get("ko") or "")
            if jp and ko:
                direct_terms.setdefault(jp, set()).add(ko)
    # User-approved project overrides take precedence over stale terminology.
    direct_terms["ブラ－ド"] = {"브래드"}
    direct_terms["カゲロウ"] = {"하루살이"}

    for row in rows:
        jp = row.get("original_jp", "")
        ko = row.get("ko", "")
        tokens = index.tokenize(jp)
        if tokens:
            rows_with_tokens += 1
        # Longest-first direct matching catches full-width long-vowel names.
        # Emit only maximal matches so ガンダム is not double-counted inside
        # デビルガンダム, etc.
        direct_matches: list[str] = []
        for term in sorted(direct_terms, key=len, reverse=True):
            if term not in jp:
                continue
            if any(term in chosen for chosen in direct_matches):
                continue
            direct_matches.append(term)
        for term in direct_matches:
            variants = sorted(direct_terms[term])
            if len(variants) == 1 and variants[0] not in ko:
                findings.append({
                    "abs": row.get("abs", ""),
                    "batch_id": row.get("batch_id", ""),
                    "kind": "direct_term_missing",
                    "token": term,
                    "variants": variants,
                    "original_jp": jp,
                    "ko": ko,
                })
            elif len(variants) > 1 and not any(value in ko for value in variants):
                findings.append({
                    "abs": row.get("abs", ""),
                    "batch_id": row.get("batch_id", ""),
                    "kind": "direct_term_conflict",
                    "token": term,
                    "variants": variants,
                    "original_jp": jp,
                    "ko": ko,
                })

        for token, is_registered in tokens:
            if not is_registered:
                unregistered[token] += 1
                findings.append({
                    "abs": row.get("abs", ""),
                    "batch_id": row.get("batch_id", ""),
                    "kind": "unregistered_katakana",
                    "token": token,
                    "variants": [],
                    "original_jp": jp,
                    "ko": ko,
                })
                continue
            registered[token] += 1
            variants = index.variants(token)
            if len(variants) != 1:
                conflicts[token] += 1
                findings.append({
                    "abs": row.get("abs", ""),
                    "batch_id": row.get("batch_id", ""),
                    "kind": "terminology_conflict",
                    "token": token,
                    "variants": list(variants),
                    "original_jp": jp,
                    "ko": ko,
                })
                continue
            expected = variants[0]
            if expected not in ko:
                findings.append({
                    "abs": row.get("abs", ""),
                    "batch_id": row.get("batch_id", ""),
                    "kind": "registered_term_missing",
                    "token": token,
                    "variants": [expected],
                    "original_jp": jp,
                    "ko": ko,
                })

    by_kind = Counter(str(item["kind"]) for item in findings)
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_uncovered_llm_literal_proper_nouns.py",
        "inputs": {
            "sheet": {"path": str(SHEET.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(SHEET)},
            "terminology": [
                {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(path)}
                for path in TERMINOLOGY
            ],
        },
        "counts": {
            "sheet_rows": len(all_rows),
            "unreviewed_draft_rows": len(rows),
            "rows_with_katakana_tokens": rows_with_tokens,
            "registered_token_types": len(registered),
            "unregistered_token_types": len(unregistered),
            "conflict_token_types": len(conflicts),
            "findings": len(findings),
            **{key: by_kind[key] for key in sorted(by_kind)},
        },
        "registered_tokens": registered.most_common(),
        "unregistered_tokens": unregistered.most_common(),
        "conflict_tokens": conflicts.most_common(),
        "findings": findings,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    fields = ["abs", "batch_id", "kind", "token", "variants", "original_jp", "ko"]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for item in findings:
            cooked = dict(item)
            cooked["variants"] = " | ".join(str(v) for v in item.get("variants", []))
            writer.writerow({key: cooked.get(key, "") for key in fields})

    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print("top_unregistered:", json.dumps(report["unregistered_tokens"][:100], ensure_ascii=False))
    print("top_conflicts:", json.dumps(report["conflict_tokens"][:50], ensure_ascii=False))
    print("json:", OUT_JSON)
    print("csv:", OUT_CSV)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
