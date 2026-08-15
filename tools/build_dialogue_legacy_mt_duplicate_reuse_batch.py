#!/usr/bin/env python3
"""Build batch002 by reusing an unambiguous approved translation of the exact same JP source.

This never translates from quarantined machine-Korean.  It only uses Japanese
source equality to reuse a Korean line that was already source-retranslated by
one of the accepted 20-cell/readability LLM passes.  Ambiguous JP strings with
more than one approved Korean rendering are excluded.
"""
from __future__ import annotations

import glob
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
LEGACY20 = ROOT / "out/script/dialogue_20cell_worklist.json"
READABILITY = ROOT / "out/script/dialogue_readability_changes.json"
BATCH1 = ROOT / "data/dialogue_legacy_mt_literal_batch001.json"
OUT = ROOT / "data/dialogue_legacy_mt_literal_batch002.json"


def canonical_space(text: str) -> str:
    return str(text).replace(" ", "　")


def main() -> int:
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    legacy = json.loads(LEGACY20.read_text(encoding="utf-8"))
    jp_by_abs = {
        str(r["abs"]).upper(): str(r.get("source_jp") or "")
        for g in legacy.get("groups") or []
        for r in g.get("records") or []
    }

    approved: dict[str, set[str]] = defaultdict(set)
    for raw in sorted(glob.glob(str(ROOT / "data/dialogue_20cell_llm_batches/batch*.json"))):
        doc = json.loads(Path(raw).read_text(encoding="utf-8"))
        if doc.get("translation_source") != "llm":
            continue
        for address, ko in (doc.get("targets") or {}).items():
            jp = jp_by_abs.get(str(address).upper(), "")
            if jp:
                approved[jp].add(canonical_space(str(ko)))

    for raw in sorted(glob.glob(str(ROOT / "data/dialogue_singleton_rewrite_batch*.json"))):
        doc = json.loads(Path(raw).read_text(encoding="utf-8"))
        if doc.get("translation_source") != "llm":
            continue
        for address, ko in (doc.get("targets") or {}).items():
            jp = jp_by_abs.get(str(address).upper(), "")
            if jp:
                approved[jp].add(canonical_space(str(ko)))

    catalog = json.loads(READABILITY.read_text(encoding="utf-8"))
    for group in catalog.get("groups") or []:
        for jp, ko in zip(group.get("source_jp_rows") or [], group.get("after_rows") or []):
            if jp:
                approved[str(jp)].add(canonical_space(str(ko)))

    batch1 = json.loads(BATCH1.read_text(encoding="utf-8"))
    already = {str(x).upper() for x in (batch1.get("targets") or {})}
    targets: dict[str, str] = {}
    evidence: list[dict[str, str]] = []
    ambiguous = 0
    same_as_current = 0
    nonportal = 0
    over20 = 0

    for row in work.get("records") or []:
        address = str(row["abs"]).upper()
        if address in already:
            continue
        candidates = approved.get(str(row.get("jp") or ""), set())
        if not candidates:
            continue
        if len(candidates) != 1:
            ambiguous += 1
            continue
        desired = next(iter(candidates))
        if row.get("route") != "existing_ext3_portal":
            nonportal += 1
            continue
        if len(desired.replace("<E62F>", "")) > 20:
            over20 += 1
            continue
        current = canonical_space(str(row.get("current_render") or ""))
        if desired == current:
            same_as_current += 1
            continue
        targets[address] = desired
        evidence.append({
            "abs": address,
            "jp": str(row.get("jp") or ""),
            "before": current,
            "after": desired,
            "proof": "exact_jp_unique_approved_source_retranslation",
        })

    doc = {
        "schema_version": 1,
        "batch": 2,
        "translation_source": "llm",
        "review_status": "approved_for_test_candidate",
        "source_policy": "reuse only a unique approved Korean translation for an exact identical Japanese source",
        "legacy_machine_translation_used_as_translation_source": False,
        "targets": dict(sorted(targets.items(), key=lambda x: int(x[0], 16))),
        "evidence": evidence,
        "summary": {
            "targets": len(targets),
            "ambiguous_jp_excluded": ambiguous,
            "same_as_current_noop_excluded": same_as_current,
            "nonportal_excluded": nonportal,
            "over20_excluded": over20,
        },
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(doc["summary"], ensure_ascii=False, indent=2))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
