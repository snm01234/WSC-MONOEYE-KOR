#!/usr/bin/env python3
"""Build human/machine-readable change reports for the 567 singleton rewrites."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "out/script/dialogue_20cell_worklist.json"
BATCHES = tuple(ROOT / f"data/dialogue_singleton_rewrite_batch{i:03d}.json" for i in range(1, 8))
OUT_JSON = ROOT / "out/script/dialogue_singleton_rewrite_changes.json"
OUT_MD = ROOT / "out/script/dialogue_singleton_rewrite_changes.md"
EXPECTED = 567


def esc(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    source = {}
    for group in work.get("groups") or []:
        records = group.get("records") or []
        if group.get("mode") != "reflow_current_nonspace_exact" or len(records) != 1:
            continue
        address = str(records[0]["abs"]).upper()
        auto = list(group.get("auto_after") or [])
        if len(auto) != 1 or address in source:
            raise RuntimeError(f"worklist shape drift at {address}")
        source[address] = {
            "jp": str(records[0].get("source_jp") or ""),
            "pre_reflow_korean": str(records[0].get("current") or ""),
            "legacy_compacted": str(auto[0]),
        }
    targets = {}
    for batch_no, path in enumerate(BATCHES, 1):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for raw_address, raw_text in (doc.get("targets") or {}).items():
            address = str(raw_address).upper()
            if address in targets:
                raise RuntimeError(f"duplicate target {address}")
            targets[address] = {"after": str(raw_text), "batch": batch_no}
    if len(source) != EXPECTED or set(source) != set(targets):
        raise RuntimeError(f"coverage drift source={len(source)} target={len(targets)}")

    rows = []
    for address in sorted(source, key=lambda x: int(x, 16)):
        row = {
            "abs": address,
            **source[address],
            **targets[address],
            "before_cells": len(source[address]["legacy_compacted"]),
            "after_cells": len(targets[address]["after"]),
            "reason": "legacy singleton space-only reflow replaced with source-grounded natural Korean spacing/wording",
        }
        rows.append(row)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_singleton_rewrite_report.py",
        "count": len(rows),
        "rows": rows,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Singleton dialogue rewrite changes",
        "",
        "기존 1행 `space_only_reflow`로 공백이 과도하게 삭제된 567개 대사를 일본어 원문 기준으로 다시 구성한 변경표입니다.",
        "",
        "| 주소 | 일본어 원문 | reflow 전 한글 | 기존 압축 표시 | 최종 한글 | 셀 |",
        "|---|---|---|---|---|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['abs']}` | {esc(row['jp'])} | {esc(row['pre_reflow_korean'])} | "
            f"{esc(row['legacy_compacted'])} | {esc(row['after'])} | {row['after_cells']} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(rows), "json": str(OUT_JSON), "markdown": str(OUT_MD)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
