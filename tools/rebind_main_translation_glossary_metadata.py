"""Rebind review CSV glossary metadata to the current Korean glossary."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GLOSSARY_PATH = ROOT / "data/main_translation_glossary_ko.json"
REVIEW_ROOT = ROOT / "out/script/main_translation_llm_review"
METADATA_FIELDS = [
    "glossary_ids",
    "glossary_series",
    "glossary_canonical_ko",
    "glossary_official_sources",
]


def load_entries() -> list[dict]:
    return json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))["entries"]


def matching_entries(source: str, entries: list[dict]) -> list[dict]:
    matched = []
    for entry in entries:
        terms = [entry.get("jp", ""), *(entry.get("aliases") or [])]
        if any(term and term in source for term in terms):
            matched.append(entry)
    return sorted(matched, key=lambda item: item["id"])


def rebind_file(path: Path, entries: list[dict]) -> tuple[int, int]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0]) if rows else []
    for field in METADATA_FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)

    refs = 0
    for row in rows:
        matched = matching_entries(row.get("source_jp", ""), entries)
        if matched:
            refs += 1
        row["glossary_ids"] = ";".join(item["id"] for item in matched)
        row["glossary_series"] = ";".join(
            f'{item.get("series_key", "")}:{item.get("series_title_ko", "")}'
            for item in matched
        )
        row["glossary_canonical_ko"] = ";".join(
            item.get("canonical_ko", "") for item in matched
        )
        row["glossary_official_sources"] = ";".join(
            source
            for item in matched
            for source in (item.get("sources") or [])
        )

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), refs


def main() -> None:
    entries = load_entries()
    paths = [REVIEW_ROOT / "targets.csv"]
    paths.extend(sorted((REVIEW_ROOT / "batches").glob("MR*.csv")))
    paths.extend(sorted((REVIEW_ROOT / "results").glob("MR*_reviewed.csv")))

    summaries = []
    total_rows = 0
    total_refs = 0
    for path in paths:
        rows, refs = rebind_file(path, entries)
        rel = path.relative_to(ROOT).as_posix()
        summaries.append({"file": rel, "rows": rows, "rows_with_glossary_refs": refs})
        total_rows += rows
        total_refs += refs

    manifest = {
        "schema_version": 1,
        "updated_at": date.today().isoformat(),
        "source_glossary": "data/main_translation_glossary_ko.json",
        "source_glossary_sha256": hashlib.sha256(GLOSSARY_PATH.read_bytes()).hexdigest(),
        "source_site": "https://kr.gundam-official.com/series",
        "matching_field": "source_jp",
        "added_columns": METADATA_FIELDS,
        "files": summaries,
        "result_total_rows": total_rows,
        "result_total_rows_with_glossary_refs": total_refs,
        "preserved_result_fields": [
            "proposed_ko",
            "reviewer_notes",
            "new_translation_source",
            "new_review_status",
        ],
    }
    (REVIEW_ROOT / "glossary_rebinding_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"files": len(paths), "rows": total_rows, "refs": total_refs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
