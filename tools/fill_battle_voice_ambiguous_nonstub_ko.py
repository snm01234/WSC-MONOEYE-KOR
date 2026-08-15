#!/usr/bin/env python3
"""Fill non-stub ambiguous battle-voice rows with LLM Korean drafts.

Excludes mass stubs 不要 / 欠番 / 不用. Writes a dedicated working sheet bound
to the current TIP. Does not modify ROM or SaveRAM.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER_AMBIG = ROOT / "out/script/battle_voice_ambiguous_translation_sheet.csv"
KO_MAP = ROOT / "data/battle_voice_ambiguous_nonstub_llm_ko.json"
OUT_SHEET = ROOT / "out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv"
BATCH_DIR = ROOT / "out/script/battle_voice_ambiguous_nonstub_batches"
MANIFEST = ROOT / "out/patch/battle_voice_ambiguous_nonstub_translation_manifest.json"
REPORT = ROOT / "out/patch/battle_voice_ambiguous_nonstub_translation_sheet_report.json"
STUBS = {"不要", "欠番", "不用"}
MAX_BATCH = 48


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ko_map = json.loads(KO_MAP.read_text(encoding="utf-8"))
    with MASTER_AMBIG.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        sources = [dict(row) for row in reader]

    keep = [row for row in sources if (row.get("original_jp") or "") not in STUBS]
    keep.sort(key=lambda row: int(row["abs"], 16))
    if not keep:
        raise SystemExit("no non-stub ambiguous rows")

    # pack by gap into batches
    grouped: collections.OrderedDict[str, list[dict[str, str]]] = collections.OrderedDict()
    for row in keep:
        grouped.setdefault(row.get("gap") or "ungrouped", []).append(row)
    batches: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    for rows in grouped.values():
        if current and len(current) + len(rows) > MAX_BATCH:
            batches.append(current)
            current = []
        current.extend(rows)
        if len(current) >= MAX_BATCH:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    batch_of = {}
    for order, rows in enumerate(batches, start=1):
        batch_id = f"ANS{order:03d}"
        for row in rows:
            batch_of[row["abs"]] = (batch_id, order)

    out_rows: list[dict[str, str]] = []
    missing = 0
    for row in keep:
        jp = row["original_jp"]
        ko = str(ko_map.get(jp) or "")
        if not ko:
            missing += 1
            continue
        if jp.count("<E62F>") != ko.count("<E62F>"):
            raise SystemExit(f"E62F mismatch at {row['abs']}")
        batch_id, batch_order = batch_of[row["abs"]]
        updated = dict(row)
        updated.update(
            {
                "batch_id": batch_id,
                "batch_order": str(batch_order),
                "scope": "battle_voice_ambiguous_nonstub",
                "ko": ko,
                "translation_source": "llm",
                "review_status": "approved",
                "workflow_status": "approved",
                "notes": "; ".join(
                    part
                    for part in [
                        row.get("notes") or "",
                        "mass stubs 不要/欠番/不用 excluded",
                        "test-ROM path; TIP promotion separate",
                    ]
                    if part
                ),
            }
        )
        out_rows.append(updated)
    if missing:
        raise SystemExit(f"missing ko for {missing} rows")

    write_csv(OUT_SHEET, out_rows, fields)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    by_batch: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in out_rows:
        by_batch[row["batch_id"]].append(row)
    for batch_id, rows in by_batch.items():
        write_csv(BATCH_DIR / f"{batch_id}.csv", rows, fields)
    for stale in BATCH_DIR.glob("*.csv"):
        if stale.name not in {f"{bid}.csv" for bid in by_batch}:
            stale.unlink()

    tip_sha = out_rows[0]["parent_tip_sha256"]
    counts = {
        "records": len(out_rows),
        "unique_source_texts": len({row["original_jp"] for row in out_rows}),
        "excluded_stubs": len(sources) - len(keep),
        "banks": dict(collections.Counter(row["bank"] for row in out_rows)),
        "stub_classes": dict(collections.Counter(row["stub_class"] for row in out_rows)),
        "body_capacity_lt4": sum(int(row["body_capacity"]) < 4 for row in out_rows),
        "body_capacity_ge4": sum(int(row["body_capacity"]) >= 4 for row in out_rows),
        "batches": len(by_batch),
        "with_ko": sum(bool(row["ko"]) for row in out_rows),
    }
    manifest = {
        "schema_version": 1,
        "generated_by": "tools/fill_battle_voice_ambiguous_nonstub_ko.py",
        "ok": True,
        "main_tip_sha256": tip_sha,
        "ko_map": str(KO_MAP.relative_to(ROOT)).replace("\\", "/"),
        "sheet": str(OUT_SHEET.relative_to(ROOT)).replace("\\", "/"),
        "excluded_stubs": sorted(STUBS),
        "counts": counts,
        "batches": [
            {
                "batch_id": batch_id,
                "records": len(rows),
                "sheet": f"out/script/battle_voice_ambiguous_nonstub_batches/{batch_id}.csv",
            }
            for batch_id, rows in sorted(by_batch.items())
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "generated_by": "tools/fill_battle_voice_ambiguous_nonstub_ko.py",
        "ok": True,
        "sheet": str(OUT_SHEET.relative_to(ROOT)).replace("\\", "/"),
        "manifest": str(MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "counts": counts,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
