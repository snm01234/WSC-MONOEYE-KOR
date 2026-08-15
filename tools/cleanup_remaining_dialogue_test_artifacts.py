#!/usr/bin/env python3
"""Remove stale one-off remaining-dialogue test outputs after promotion.

Candidate ROM/SaveRAM cleanup is performed by the promotion transaction.  This
follow-up removes the superseded raw structure report (the retained delta gate
is authoritative) and Python bytecode caches created while building/auditing.
It appends the exact deletion inventory to the promotion report.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
REPORT = PATCH / "remaining_dialogue_promotion_report.json"
EXPECTED_TIP_SHA = "279ce819fa63bea6ee52a307dc920714bb530c2f32e344e2543134dfbccbf7f9"
TARGETS = [
    PATCH / "remaining_dialogue_structure_gate.json",
    ROOT / "tools/__pycache__/audit_current_untranslated_dialogue.cpython-314.pyc",
    ROOT / "tools/__pycache__/audit_remaining_dialogue_candidate.cpython-314.pyc",
    ROOT / "tools/__pycache__/build_remaining_dialogue_candidate.cpython-314.pyc",
    ROOT / "tools/__pycache__/promote_remaining_dialogue_candidate.cpython-314.pyc",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def main() -> int:
    if not TIP.is_file() or digest(TIP) != EXPECTED_TIP_SHA:
        raise SystemExit("current TIP is not the promoted remaining-dialogue ROM")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("ok") is not True or report.get("published") is not True:
        raise SystemExit("promotion report is not accepted/published")

    removed: list[dict[str, Any]] = []
    absent: list[str] = []
    reclaimed = 0
    for path in TARGETS:
        if not path.exists():
            absent.append(rel(path))
            continue
        if not path.is_file():
            raise SystemExit(f"cleanup target is not a file: {rel(path)}")
        size = path.stat().st_size
        removed.append({"path": rel(path), "size": size, "sha256": digest(path)})
        path.unlink()
        reclaimed += size

    report["cleanup_followup"] = {
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "removed": removed,
        "removed_count": len(removed),
        "already_absent": absent,
        "reclaimed_bytes": reclaimed,
        "policy": "superseded raw structure output and generated Python bytecode only",
    }
    report["cleanup_total_reclaimed_bytes"] = int((report.get("cleanup") or {}).get("reclaimed_bytes") or 0) + reclaimed
    atomic_json(REPORT, report)
    print(json.dumps(report["cleanup_followup"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
