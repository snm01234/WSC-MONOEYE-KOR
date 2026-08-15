#!/usr/bin/env python3
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from hangul_marker import marker_code
from monoeye_rom import Tbl
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

WORKLIST = ROOT / "out/script/dialogue_20cell_worklist.json"
BATCH_GLOB = str(ROOT / "data/dialogue_20cell_llm_batches/batch*.json")
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/script/dialogue_20cell_batch_validation.json"
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")
LIMIT = 20


def main() -> int:
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    required = [
        r["abs"].upper()
        for g in work["groups"]
        if g["mode"] == "source_retranslation_required"
        for r in g["records"]
    ]
    required_set = set(required)
    tbl = Tbl.load(TBL_PATH)

    seen: dict[str, str] = {}
    origins: dict[str, str] = {}
    conflicts: list[dict] = []
    invalid: list[dict] = []
    files = sorted(Path(p) for p in glob.glob(BATCH_GLOB))
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for raw_abs, raw_text in (doc.get("targets") or {}).items():
            address = raw_abs.upper()
            text = normalize_ko_text(str(raw_text))
            if address in seen and seen[address] != text:
                conflicts.append({
                    "abs": address,
                    "first_file": origins[address],
                    "first": seen[address],
                    "second_file": str(path.relative_to(ROOT)),
                    "second": text,
                })
                continue
            seen[address] = text
            origins[address] = str(path.relative_to(ROOT))
            reasons = []
            # The candidate builder may remove only inter-word spaces, never
            # visible non-space characters, to satisfy the hard 20-cell field.
            # Validate that same contract here instead of rejecting a harmless
            # 21-cell draft whose sole excess is one space.
            nonspace_cells = len(text.replace(" ", "").replace("\u3000", ""))
            if nonspace_cells > LIMIT:
                reasons.append(f"nonspace_cells={nonspace_cells}")
            if JP_RE.search(text):
                reasons.append("japanese_visible_character")
            encoded = try_encode_ko_text(
                text,
                tbl,
                hangul_marker_code=marker_code(),
                hangul_marker_mode="run",
            )
            if encoded is None or b"\x00" in (encoded or b""):
                reasons.append("not_encodable")
            if reasons:
                invalid.append({
                    "abs": address,
                    "file": str(path.relative_to(ROOT)),
                    "text": text,
                    "cells": len(text),
                    "nonspace_cells": nonspace_cells,
                    "reasons": reasons,
                })

    missing = sorted(required_set - set(seen))
    extras = sorted(set(seen) - required_set)
    duplicate_required = len(required) - len(required_set)
    report = {
        "ok": not conflicts and not invalid and not missing and not extras,
        "batch_files": [str(p.relative_to(ROOT)) for p in files],
        "counts": {
            "required_records": len(required),
            "required_unique": len(required_set),
            "duplicate_required_addresses": duplicate_required,
            "batch_unique_targets": len(seen),
            "missing": len(missing),
            "extras": len(extras),
            "conflicts": len(conflicts),
            "invalid": len(invalid),
        },
        "missing": missing,
        "extras": extras,
        "conflicts": conflicts,
        "invalid": invalid,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    if invalid:
        for row in invalid[:80]:
            print(row)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
