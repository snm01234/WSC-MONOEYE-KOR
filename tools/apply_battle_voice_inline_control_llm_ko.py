#!/usr/bin/env python3
"""Apply LLM Korean drafts to battle voice E62F inline-control translation sheets."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "out/script/battle_voice_inline_control_translation_sheet.csv"
BATCH_DIR = ROOT / "out/script/battle_voice_inline_control_batches"
KO_MAP = ROOT / "data/battle_voice_inline_control_llm_ko.json"
REPORT = ROOT / "out/patch/battle_voice_inline_control_llm_ko_apply_report.json"


def load_map() -> dict[str, str]:
    data = json.loads(KO_MAP.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected object: {KO_MAP}")
    return {str(k): str(v) for k, v in data.items()}


def apply_rows(rows: list[dict[str, str]], ko_map: dict[str, str]) -> dict[str, int]:
    counts = {"filled": 0, "already": 0, "missing": 0, "tag_mismatch": 0}
    for row in rows:
        jp = row.get("original_jp") or ""
        ko = ko_map.get(jp)
        if ko is None:
            counts["missing"] += 1
            continue
        if jp.count("<E62F>") != ko.count("<E62F>"):
            counts["tag_mismatch"] += 1
            raise SystemExit(f"E62F tag mismatch for {row.get('abs')}: {jp!r} -> {ko!r}")
        if (row.get("ko") or "").strip() and row.get("translation_source") == "llm_from_user_capture":
            row["workflow_status"] = "draft_ready"
            counts["already"] += 1
            continue
        row["ko"] = ko
        row["translation_source"] = "llm"
        row["review_status"] = "unreviewed_draft"
        row["workflow_status"] = "draft_ready"
        counts["filled"] += 1
    return counts


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.write_text("", encoding="utf-8")
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ko_map = load_map()
    with MASTER.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        master_rows = list(reader)
    master_counts = apply_rows(master_rows, ko_map)
    write_csv(MASTER, master_rows, fieldnames)

    batch_reports: dict[str, dict[str, int]] = {}
    for batch_path in sorted(BATCH_DIR.glob("IC*.csv")):
        with batch_path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            batch_rows = list(reader)
        batch_reports[batch_path.name] = apply_rows(batch_rows, ko_map)
        write_csv(batch_path, batch_rows, fieldnames)

    pending = sum(1 for row in master_rows if not (row.get("ko") or "").strip())
    report = {
        "schema_version": 1,
        "generated_by": "tools/apply_battle_voice_inline_control_llm_ko.py",
        "ok": pending == 0 and master_counts["missing"] == 0 and master_counts["tag_mismatch"] == 0,
        "ko_map": str(KO_MAP.relative_to(ROOT)).replace("\\", "/"),
        "master_sheet": str(MASTER.relative_to(ROOT)).replace("\\", "/"),
        "master_counts": master_counts,
        "batch_counts": batch_reports,
        "totals": {
            "records": len(master_rows),
            "with_ko": sum(1 for row in master_rows if (row.get("ko") or "").strip()),
            "pending_translation": pending,
            "draft_ready": sum(row.get("workflow_status") == "draft_ready" for row in master_rows),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
