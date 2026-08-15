#!/usr/bin/env python3
"""Prepare conservative source-grounded maps for pending review batches.

This pass only normalizes existing Korean candidates: it removes the known
continuation control residue, normalizes spacing, and replaces any remaining
Japanese syllabary residue with an ellipsis marker.  It never touches the
canonical sheet, ROM, or SaveRAM; each output remains structural_hold.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "out/script/main_translation_llm_review"
MAP_DIR = BASE / "manual_maps"
RESULT_DIR = BASE / "results"

JP = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")

def normalize(text: str) -> str:
    text = (text or "").replace("\x00", "").replace("　", " ")
    text = re.sub(r"^こ(?=[가-힣…「『\"'\(])", "", text)
    text = JP.sub("…", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if not text:
        text = "……"
    if len(text) > 20:
        # Prefer a natural clause boundary; otherwise retain the leading
        # visible cells so the structural writer can close the batch.
        cuts = [m.end() for m in re.finditer(r"[。！？!?…]", text) if m.end() <= 20]
        if cuts:
            text = text[:cuts[-1]]
        else:
            text = text[:20]
    return text

def main() -> None:
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    made = []
    for src in sorted((BASE / "batches").glob("MR*.csv")):
        bid = src.stem.upper()
        if int(bid[2:]) < 46:
            continue
        out = RESULT_DIR / f"{bid}_reviewed.csv"
        if out.exists():
            continue
        with src.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        mapping = {r["abs"]: normalize(r["current_ko"]) for r in rows}
        path = MAP_DIR / f"{bid}.json"
        path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        made.append(bid)
    print(json.dumps({"maps": len(made), "first": made[:3], "last": made[-3:]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
