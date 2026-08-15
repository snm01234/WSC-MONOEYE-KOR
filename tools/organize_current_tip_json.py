#!/usr/bin/env python3
"""Keep only current-TIP/recent JSON under ``out/patch``.

The emulator consumes the ROM and SaveRAM, not these JSON files.  JSON files
are build, audit, approval, and historical evidence.  This command keeps JSON
that either contains the exact current TIP SHA-256 or was produced during the
latest work window, and moves the rest to ``legacy/out/patch`` without
deleting it.

Dry-run is the default.  ``--commit`` performs the reversible move and writes
``legacy/out_patch_json_manifest.json`` plus a short report in ``out/patch``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out" / "patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
LEGACY = ROOT / "legacy"
MANIFEST = LEGACY / "out_patch_json_manifest.json"
REPORT = PATCH / "current_tip_json_cleanup_report.json"
EXPECTED_TIP_SHA256 = (
    "55c2e1f3467d28e041ad0e145cad68091cf78d50f8d58f6ce6a65259acd59ca9"
)
RECENT_CUTOFF = datetime(2026, 8, 9, 15, 0, 0).timestamp()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate() -> dict[str, object]:
    if not TIP.is_file() or TIP.stat().st_size != 16_777_216:
        raise SystemExit("refusing JSON cleanup: current TIP is missing or wrong size")
    actual = digest(TIP)
    if actual != EXPECTED_TIP_SHA256:
        raise SystemExit(
            f"refusing JSON cleanup: current TIP drifted; expected {EXPECTED_TIP_SHA256}, got {actual}"
        )
    return {"path": rel(TIP), "size": TIP.stat().st_size, "sha256": actual}


def plan() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(PATCH.rglob("*.json"), key=rel):
        raw = path.read_text(encoding="utf-8", errors="replace").lower()
        is_current_tip = EXPECTED_TIP_SHA256 in raw
        is_recent = path.stat().st_mtime >= RECENT_CUTOFF
        if is_current_tip or is_recent:
            continue
        target = LEGACY / rel(path)
        if target.exists():
            raise SystemExit(f"refusing JSON cleanup: archive target already exists: {target}")
        rows.append(
            {
                "path": rel(path),
                "archive_path": rel(target),
                "bytes": path.stat().st_size,
                "last_write_time": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "reason": "not_current_tip_bound_and_older_than_recent_work_window",
            }
        )
    return rows


def summary(rows: list[dict[str, object]]) -> dict[str, object]:
    total = sum(int(row["bytes"]) for row in rows)
    return {
        "files": len(rows),
        "bytes": total,
        "mib": round(total / 1024 / 1024, 1),
        "by_reason": dict(Counter(str(row["reason"]) for row in rows)),
    }


def commit(rows: list[dict[str, object]], tip: dict[str, object]) -> dict[str, object]:
    LEGACY.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, object]] = []
    for row in rows:
        source = ROOT / str(row["path"])
        target = ROOT / str(row["archive_path"])
        if not source.is_file():
            raise SystemExit(f"source disappeared before move: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(row)

    after = validate()
    if after != tip:
        raise SystemExit("fatal: current TIP changed during JSON cleanup")
    report = {
        "schema_version": 1,
        "ok": True,
        "generated_by": "tools/organize_current_tip_json.py",
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": {
            "current_tip_sha256": EXPECTED_TIP_SHA256,
            "recent_cutoff_local": "2026-08-09 15:00:00 +09:00",
            "kept": "JSON containing the exact current TIP SHA-256 or written at/after the cutoff",
            "action": "move_to_legacy",
        },
        "current_tip": tip,
        "summary": summary(moved),
        "moved": moved,
        "restore": "Move each archive_path back to path; paths are repository-relative.",
    }
    MANIFEST.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    tip = validate()
    rows = plan()
    print(json.dumps({"mode": "commit" if args.commit else "dry_run", "summary": summary(rows)}, ensure_ascii=False, indent=2))
    if not args.commit:
        for row in rows:
            print(f"{row['bytes']}\t{row['path']} -> {row['archive_path']}")
        print("dry-run: no files moved")
        return 0
    report = commit(rows, tip)
    print(json.dumps({"ok": report["ok"], "summary": report["summary"], "manifest": rel(MANIFEST), "report": rel(REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
