#!/usr/bin/env python3
"""Current-ROM untranslated/mixed dialogue audit.

This is the replacement for the historical report-bound audit.  It does not
consume old aux population reports, apply reports, coverage snapshots, or
translation caches.  It evaluates the ROM passed with ``--rom`` now.

Scopes:
* bank 59 runtime text: direct current-ROM scan over the maintained text extent;
* scenario/battle/id dialogue represented by a runtime contract rebuilt in
  memory from the exact target ROM.

``battle_unknown`` contracts are intentionally excluded because they are the
contract quarantine for records whose display grammar is not proven.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audit_bank59_event_width import scan_bank59_current, strip_pad  # noqa: E402
from dialogue_runtime_contracts import build_manifest  # noqa: E402
from mixed_residual_classification import (  # noqa: E402
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Tbl, find_rom, load_rom  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/current_untranslated_dialogue_audit.json"

CONTRACT_ROUTES = {
    "scenario_first",
    "scenario_continuation",
    "battle_tagged",
    "battle_body_only",
}
KNOWN_STRUCTURAL_SINGLETONS = {"こ", "な", "は"}
CURRENT_PREFIXED_SOURCE = ROOT / "data/runtime_text_residual_new_ko_prefixed_dialogue.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(text: str) -> str:
    h = hangul_character_count(text)
    j = japanese_character_count(text)
    if h and j:
        return "mixed"
    if j:
        return "jp_only"
    if h:
        return "ko_only"
    return "no_language_text"


def contract_residuals(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    checked = 0
    for contract in manifest.get("contracts") or []:
        route = str(contract.get("route") or "")
        if route not in CONTRACT_ROUTES:
            continue
        text = strip_pad(str(contract.get("baseline_text") or ""))
        if not text:
            continue
        checked += 1
        jp = japanese_character_count(text)
        if jp <= 0:
            continue
        item = {
            "address": str(contract.get("address") or "").upper(),
            "scope": "runtime_contract",
            "route": route,
            "status": str(contract.get("status") or ""),
            "classification": classify(text),
            "japanese_chars": jp,
            "hangul_chars": hangul_character_count(text),
            "text": text,
            "original_japanese": str(contract.get("original_japanese") or ""),
        }
        stripped = text.strip("　 \t")
        if jp == 1 and hangul_character_count(text) == 0 and stripped in KNOWN_STRUCTURAL_SINGLETONS:
            item["classification"] = "structural_singleton_review"
            ambiguous.append(item)
        else:
            rows.append(item)
    return rows, ambiguous, checked


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=TIP)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    target = bytes(load_rom(args.rom))
    original = bytes(load_rom(find_rom(ROOT)))
    tbl = Tbl.load(TBL)
    manifest = build_manifest(original, target, target_path=args.rom)

    bank59_rows, bank59_unreadable, bank59_end = scan_bank59_current(
        target,
        tbl,
        include_japanese_only=True,
    )
    curated = json.loads(CURRENT_PREFIXED_SOURCE.read_text(encoding="utf-8"))
    curated_addresses = {
        str(row.get("queue_id") or "").split(":")[-1].upper()
        for row in curated.get("entries") or []
        if str(row.get("queue_id") or "").startswith("prefixed_dialogue:59")
    }
    bank59_residuals: list[dict[str, Any]] = []
    bank59_ambiguous: list[dict[str, Any]] = []
    bank59_review: list[dict[str, Any]] = []
    for row in bank59_rows:
        jp = int(row.get("japanese_chars") or 0)
        hg = int(row.get("hangul_chars") or 0)
        if jp <= 0:
            continue
        text = str(row.get("text") or "")
        item = dict(row)
        item["classification"] = classify(text)
        if hg > 0 and jp == 1 and text and text[0] in KNOWN_STRUCTURAL_SINGLETONS:
            item["classification"] = "structural_lead_ambiguity"
            bank59_ambiguous.append(item)
        elif hg > 0 or str(row.get("address") or "") in curated_addresses:
            bank59_residuals.append(item)
        else:
            item["classification"] = "jp_only_unproven_review"
            bank59_review.append(item)

    contract_rows, contract_ambiguous, contract_checked = contract_residuals(manifest)

    residuals = bank59_residuals + contract_rows
    ambiguous = bank59_ambiguous + contract_ambiguous
    review_only = bank59_review
    mixed = [row for row in residuals if row.get("classification") == "mixed" or row.get("mixed_language") is True]
    jp_only = [row for row in residuals if row.get("classification") == "jp_only"]

    report = {
        "schema_version": 2,
        "generated_by": "tools/audit_current_untranslated_dialogue.py",
        "status": "clean" if not residuals and not bank59_unreadable else "residuals_found",
        "target": {"path": str(args.rom), "size": len(target), "sha256": sha(target)},
        "policy": {
            "historical_generated_inputs": [],
            "translation_cache_used": False,
            "translation_sheet_used": False,
            "runtime_contract": "rebuilt in memory from exact target",
            "bank59": f"direct current-ROM scan 590000-{bank59_end:06X}",
            "excluded_contract_routes": ["battle_unknown", "id_first", "id_continuation"],
            "known_structural_singletons": sorted(KNOWN_STRUCTURAL_SINGLETONS),
            "jp_only_unproven_records": "reported as review-only, not confirmed untranslated dialogue",
        },
        "counts": {
            "bank59_records_checked": len(bank59_rows),
            "contract_records_checked": contract_checked,
            "residuals": len(residuals),
            "mixed": len(mixed),
            "jp_only": len(jp_only),
            "structural_ambiguities": len(ambiguous),
            "review_only": len(review_only),
            "bank59_unreadable": len(bank59_unreadable),
        },
        "bank59_unreadable": bank59_unreadable,
        "residuals": residuals,
        "structural_ambiguities": ambiguous,
        "review_only": review_only,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "target_sha256": report["target"]["sha256"],
        "counts": report["counts"],
        "sample_residuals": [
            {"address": row.get("address"), "scope": row.get("scope"), "text": row.get("text")}
            for row in residuals[:30]
        ],
        "report": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "clean" else 1


if __name__ == "__main__":
    raise SystemExit(main())
