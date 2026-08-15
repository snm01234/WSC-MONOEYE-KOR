#!/usr/bin/env python3
"""Build the reviewed catalog for 149 bank-5C unused placeholders."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/patch/broad_stage2_title_ui_residual_audit.json"
OUT = ROOT / "data/broad_stage2_placeholder_ko.json"


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    if source.get("ok") is not True:
        raise SystemExit("source audit is not successful")
    rows = []
    for bucket in (source.get("records") or {}).values():
        rows.extend(bucket or [])
    selected = []
    for row in sorted(rows, key=lambda item: int(item["logical_address"])):
        if str(row.get("region") or "") != "aux":
            continue
        current = str(row.get("current_text") or "")
        if current not in {"不要", "不用"}:
            raise SystemExit(f"unexpected remaining aux text at {row['abs']}: {current!r}")
        if int(row.get("body_capacity") or 0) != 2:
            raise SystemExit(f"placeholder body is not 2 bytes at {row['abs']}")
        selected.append({
            "abs": str(row["abs"]).upper(),
            "record_id": row.get("record_id"),
            "jp": current,
            "ko": "미사용",
            "body_capacity": 2,
            "prefix_hex": row.get("prefix_hex") or "",
            "body_hex": row.get("body_hex") or "",
        })
    if len(selected) != 149:
        raise SystemExit(f"placeholder population drifted: expected 149, got {len(selected)}")
    document = {
        "schema_version": 1,
        "generated_by": "tools/build_stage2_placeholder_catalog.py",
        "description": "Reviewed semantic replacement of 148 不要 and one 不用 placeholder with 미사용.",
        "source_audit": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "counts": {"selected": len(selected), "fuyou": sum(row["jp"] == "不要" for row in selected), "fuyou_variant": sum(row["jp"] == "不用" for row in selected), "unique_korean": 1},
        "lines": selected,
    }
    temporary = OUT.with_name(f".{OUT.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, OUT)
    print(json.dumps({"ok": True, "counts": document["counts"], "out": str(OUT.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
