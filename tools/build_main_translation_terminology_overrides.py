#!/usr/bin/env python3
"""Derive address-bound terminology overrides from reviewed scenario CSVs."""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_terminology_consistency_followup_candidate import (  # noqa: E402
    canonicalize,
    load_replacements,
)

RESULTS = ROOT / "out/script/main_translation_llm_review/results"
OUT = ROOT / "data/main_translation_terminology_overrides_ko.json"
LINE_LIMIT = 20


def main() -> int:
    replacements = load_replacements()
    targets: dict[str, str] = {}
    sources: dict[str, str] = {}
    overflows: list[dict[str, object]] = []
    for path in sorted(RESULTS.glob("MR*_reviewed.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                address = str(row.get("abs") or "").upper()
                proposed = str(row.get("proposed_ko") or "").strip()
                status = str(row.get("review_status") or row.get("new_review_status") or "")
                if not address or not proposed or status != "llm_retranslated_structural_hold":
                    continue
                after, applied = canonicalize(proposed, replacements)
                if after == proposed:
                    continue
                cells = len(after.replace("<E62F>", ""))
                if cells > LINE_LIMIT:
                    overflows.append({
                        "abs": address,
                        "before": proposed,
                        "after": after,
                        "cells": cells,
                        "source": str(path.relative_to(ROOT)).replace("\\", "/"),
                    })
                    continue
                if address in targets and targets[address] != after:
                    raise RuntimeError(f"conflicting terminology override at {address}")
                targets[address] = after
                sources[address] = str(path.relative_to(ROOT)).replace("\\", "/")
    if overflows:
        print(json.dumps({"overflows": overflows}, ensure_ascii=False, indent=2))
        return 1
    payload = {
        "schema_version": 1,
        "purpose": "Latest glossary terminology overrides layered after reviewed scenario CSVs.",
        "generated_by": "tools/build_main_translation_terminology_overrides.py",
        "review_status": "approved_for_candidate_build",
        "line_limit": LINE_LIMIT,
        "target_count": len(targets),
        "targets": dict(sorted(targets.items())),
        "sources": dict(sorted(sources.items())),
    }
    tmp = OUT.with_name(f".{OUT.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, OUT)
    print(json.dumps({"ok": True, "targets": len(targets), "out": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
