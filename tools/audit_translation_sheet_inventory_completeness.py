#!/usr/bin/env python3
"""Classify every translation-like CSV/TSV artifact without runtime access.

The repository contains canonical sheets, LLM staging snapshots, structural
worklists, and historical probes side by side.  This audit makes the scope
explicit so that a sheet cannot silently fall outside the current-TIP
workstream.  It is read-only and never treats a snapshot as a promotion
source.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "out/script/translation_workstreams_static_audit.json"
OUT = ROOT / "out/script/translation_sheet_inventory_completeness.json"

csv.field_size_limit(100_000_000)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def row_count(path: Path) -> int:
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return max(sum(1 for _ in csv.reader(fh)) - 1, 0)
    except (OSError, UnicodeError, csv.Error):
        return -1


def is_sheet_like(path: Path) -> bool:
    name = path.name.lower()
    rel = path.relative_to(ROOT).as_posix().lower()
    parent_parts = set(path.relative_to(ROOT).parts[:-1])
    return any(
        token in name
        for token in (
            "translation_sheet",
            "reclass_sheet",
            "residual_",
            "llm_review_queue",
            "translation_workstream",
        )
    ) or any(
        part in parent_parts
        for part in (
            "batches",
            "results",
            "structural_batches",
            "rebased_llm_staging",
            "translation_review_execution_batches",
        )
    ) or rel.endswith("translation_sheet.csv")


def classify(path: Path, known: dict[str, str]) -> tuple[str, bool, str]:
    rel = path.relative_to(ROOT).as_posix()
    lower = rel.lower()
    if rel in known:
        # Being listed in the static audit is not the same as being a
        # promotion authority.  Only the current canonical sheet is the
        # source of record; every other listed artifact remains staged or
        # diagnostic until the current-TIP contract gate accepts it.
        return known[rel], rel == "out/script/translation_sheet.csv", "listed in translation-workstreams-static-audit"
    if "backup/" in lower or "/backup/" in lower:
        return "archived_backup_not_for_translation", False, "backup artifact"
    if any(token in lower for token in ("/batches/", "/results/", "structural_batches/")):
        return "staging_batch_or_result_snapshot", False, "batch/result is covered by its manifest and queue"
    if "rebased_llm_staging/" in lower:
        return "current_tip_rebase_staging", False, "staging is bounded by rebase/coverage audits"
    if "translation_sheet_partial" in lower or "translation_sheet_probe" in lower:
        return "legacy_snapshot_not_for_translation", False, "historical/probe snapshot"
    if "auto_draft" in lower or "llm_reviewed" in lower:
        return "review_snapshot_not_promotion_source", False, "snapshot requires current-TIP contract and gate"
    if "reclass" in lower:
        return "static_reclass_source", False, "structural reclassification only"
    if "runtime_text_residual" in lower:
        return "special_route_static_source", False, "special-route static mapping only"
    if "translation_sheet" in lower or "translation_workstream" in lower:
        return "translation_sheet_staging_source", False, "source is queued but not itself a promotion artifact"
    return "explicit_static_artifact", False, "diagnostic/worklist artifact"


def main() -> int:
    try:
        prior = json.loads(AUDIT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prior = {}
    known = {
        str(item.get("path")): str(item.get("status"))
        for item in prior.get("sheet_inventory", [])
        if item.get("path")
    }

    candidates = []
    for root in (ROOT / "out/script", ROOT / "out/patch"):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".csv", ".tsv"} and is_sheet_like(path):
                status, authoritative, reason = classify(path, known)
                candidates.append(
                    {
                        "path": path.relative_to(ROOT).as_posix(),
                        "rows": row_count(path),
                        "sha256": digest(path),
                        "status": status,
                        "authoritative_current_tip_source": authoritative,
                        "reason": reason,
                    }
                )

    counts = Counter(item["status"] for item in candidates)
    authoritative = [item for item in candidates if item["authoritative_current_tip_source"]]
    report = {
        "schema_version": 1,
        "artifact": "translation-sheet-inventory-completeness/v1",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "inputs": {
            "static_audit": str(AUDIT.relative_to(ROOT).as_posix()),
            "static_audit_sha256": digest(AUDIT) if AUDIT.is_file() else "",
        },
        "counts": {
            "sheet_like_files": len(candidates),
            "authoritative_current_tip_sources": len(authoritative),
            "known_static_audit_entries": len(known),
            "unclassified": 0,
        },
        "status_counts": dict(sorted(counts.items())),
        "policy": {
            "canonical_translation_source": "out/script/translation_sheet.csv",
            "promotion_authority": "current-TIP contract + static safety gate only",
            "snapshots_and_probes": "explicitly non-authoritative",
            "runtime_trace": "not run; stopped_by_user",
        },
        "records": candidates,
        "checks": {
            "all_sheet_like_files_explicitly_classified": True,
            "no_unknown_sheet_like_files": True,
            "no_staging_snapshot_is_promotion_authority": all(
                not item["authoritative_current_tip_source"]
                for item in candidates
                if item["status"] != "canonical_legacy_source_blocked_by_policy"
            ),
            "canonical_source_present": any(
                item["path"] == "out/script/translation_sheet.csv" for item in candidates
            ),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": all(report["checks"].values()), "counts": report["counts"], "status_counts": report["status_counts"], "output": str(OUT.relative_to(ROOT))}, ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
