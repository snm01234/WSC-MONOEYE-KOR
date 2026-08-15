#!/usr/bin/env python3
"""Build deduplicated translation/reuse queues from runtime residual sheets.

ID-command rows retain the other line in the same bundle as context.  The
output never modifies ROM or SaveRAM and excludes placeholder/template rows.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHEETS = (
    ROOT / "out/script/runtime_text_residual_id_bundle_sheet.csv",
    ROOT / "out/script/runtime_text_residual_prefixed_dialogue_sheet.csv",
    ROOT / "out/script/runtime_text_residual_voice_sheet.csv",
)
REPORT = ROOT / "out/patch/runtime_text_translation_queue_report.json"
NEW_CSV = ROOT / "out/script/runtime_text_new_translation_queue.csv"
REUSE_CSV = ROOT / "out/script/runtime_text_reuse_queue.csv"
SPACE_RE = re.compile(r"[\s\u3000]+")


class QueueError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": digest(data),
    }


def normalized(value: str) -> str:
    return SPACE_RE.sub(" ", value.strip())


def load_rows() -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for path in SHEETS:
        if not path.is_file():
            raise QueueError(f"missing residual sheet: {path}")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            if int(row.get("japanese_count") or 0) <= 0:
                continue
            if row.get("classification") in {
                "placeholder_or_empty",
                "voice_boundary_unproven_quarantine",
            }:
                continue
            output.append(row)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    rows = load_rows()
    by_bundle: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("bundle_start"):
            by_bundle[row["bundle_start"]].append(row)

    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (row["family"], normalized(row["original_body"]))
        groups[key].append(row)

    new_rows: list[dict[str, Any]] = []
    reuse_rows: list[dict[str, Any]] = []
    for (family, source_key), members in sorted(
        groups.items(), key=lambda item: (item[0][0], min(int(row["record_start"], 16) for row in item[1]))
    ):
        originals = sorted({row["original_body"] for row in members})
        if len(originals) != 1:
            raise QueueError(f"normalized source collision: {family}/{source_key}: {originals}")
        ready_values = sorted({row["suggested_ko"] for row in members if row["translation_ready"] == "yes"})
        not_ready = [row for row in members if row["translation_ready"] != "yes"]
        if ready_values and not_ready:
            # A phrase has one canonical approved render; propagate it to all
            # duplicate occurrences instead of translating the duplicate again.
            if len(ready_values) != 1:
                raise QueueError(f"conflicting approved reuse values for {originals[0]!r}")
            target = reuse_rows
            ko = ready_values[0]
            status = "reuse_approved_translation"
        elif ready_values:
            if len(ready_values) != 1:
                raise QueueError(f"conflicting approved reuse values for {originals[0]!r}")
            target = reuse_rows
            ko = ready_values[0]
            status = "reuse_approved_translation"
        else:
            target = new_rows
            ko = ""
            status = "new_llm_translation_required"

        contexts: list[str] = []
        if family == "id_command_bundle":
            for member in members:
                bundle = member.get("bundle_start") or ""
                for peer in by_bundle.get(bundle, []):
                    if peer["record_start"] == member["record_start"]:
                        continue
                    contexts.append(
                        f"{bundle}:{peer.get('line_role','')}:{peer.get('original_body','')}"
                    )
        context_values = sorted(set(contexts))
        capacities = sorted({int(row["body_capacity"]) for row in members})
        record_starts = sorted({row["record_start"] for row in members}, key=lambda value: int(value, 16))
        bundles = sorted({row["bundle_start"] for row in members if row.get("bundle_start")}, key=lambda value: int(value, 16))
        prefixes = sorted({row["prefix_hex"] for row in members})
        storage = sorted({row["storage_strategy"] for row in members})
        sources = sorted({row["suggested_source"] for row in members if row.get("suggested_source")})
        target.append(
            {
                "queue_id": f"{family}:{record_starts[0]}",
                "family": family,
                "jp": originals[0],
                "ko": ko,
                "translation_source": "curated_project_data" if ko else "llm",
                "review_status": "approved" if ko else "pending",
                "status": status,
                "occurrence_count": len(record_starts),
                "record_starts": ";".join(record_starts),
                "bundle_starts": ";".join(bundles),
                "line_roles": ";".join(sorted({row.get("line_role", "") for row in members})),
                "body_capacity_min": min(capacities),
                "body_capacity_max": max(capacities),
                "prefix_hex_variants": ";".join(prefixes),
                "storage_strategies": ";".join(storage),
                "approved_sources": ";".join(sources),
                "context": " | ".join(context_values),
                "notes": "",
            }
        )

    common_fields = [
        "queue_id", "family", "jp", "ko", "translation_source", "review_status",
        "status", "occurrence_count", "record_starts", "bundle_starts", "line_roles",
        "body_capacity_min", "body_capacity_max", "prefix_hex_variants",
        "storage_strategies", "approved_sources", "context", "notes",
    ]
    write_csv(NEW_CSV, new_rows, common_fields)
    write_csv(REUSE_CSV, reuse_rows, common_fields)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_runtime_text_translation_queue.py",
        "read_only": True,
        "ok": True,
        "inputs": [identity(path) for path in SHEETS],
        "counts": {
            "residual_records": len(rows),
            "unique_source_groups": len(groups),
            "reuse_unique": len(reuse_rows),
            "reuse_records": sum(int(row["occurrence_count"]) for row in reuse_rows),
            "new_unique": len(new_rows),
            "new_records": sum(int(row["occurrence_count"]) for row in new_rows),
            "new_by_family": dict(sorted(Counter(row["family"] for row in new_rows).items())),
            "reuse_by_family": dict(sorted(Counter(row["family"] for row in reuse_rows).items())),
        },
        "outputs": {
            "new_translation_queue": identity(NEW_CSV),
            "reuse_queue": identity(REUSE_CSV),
        },
        "policy": {
            "placeholder_rows_excluded": True,
            "voice_boundary_unproven_rows_excluded": True,
            "duplicates_grouped_by_family_and_exact_normalized_source": True,
            "approved_reuse_propagates_to_same_source_duplicates": True,
            "id_bundle_peer_line_included_as_context": True,
            "new_rows_require_explicit_ko_and_approved_review_before_build": True,
        },
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
