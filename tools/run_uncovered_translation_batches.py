#!/usr/bin/env python3
"""Run every consecutive fully approved uncovered-text batch.

The pipeline refreshes the sheets, then builds and independently audits batches
in manifest order. It stops at the first untranslated or short-body batch, so a
later batch can never skip an unresolved predecessor.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "out/patch/uncovered_translation_batch_manifest.json"
OUT = ROOT / "out/patch/uncovered_translation_batch_pipeline_report.json"


class PipelineError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PipelineError(f"JSON root must be object: {path}")
    return value


def run(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stdout.splitlines()[-30:])
        raise PipelineError(f"command failed ({result.returncode}): {' '.join(args)}\n{tail}")


def batch_path(batch_id: str, suffix: str) -> Path:
    return ROOT / f"out/patch/uncovered_batch_{batch_id}_{suffix}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", default=None, help="optional final batch id")
    parser.add_argument("--no-refresh-sheets", action="store_true")
    args = parser.parse_args(argv)
    through = args.through.upper() if args.through else None

    if not args.no_refresh_sheets:
        run("tools/build_uncovered_translation_sheets.py")
    manifest = load_object(MANIFEST)
    if manifest.get("ok") is not True:
        raise PipelineError("batch manifest did not pass")

    completed: list[dict[str, Any]] = []
    blocked: dict[str, Any] | None = None
    batches = list(manifest.get("batches") or [])
    for row in batches:
        batch_id = str(row.get("batch_id") or "")
        if batch_id == "C000":
            completed.append({
                "batch_id": batch_id,
                "status": "existing_candidate_pending_user_test",
                "records": int(row.get("records") or 0),
                "candidate": "out/patch/next_stage_event_id_indirect_candidate.wsc",
            })
            if through == batch_id:
                break
            continue
        if row.get("status") != "approved_ready":
            blocked = {
                "batch_id": batch_id,
                "status": row.get("status"),
                "records": int(row.get("records") or 0),
                "approved_records": int(row.get("approved_records") or 0),
                "requires_direct_ext3_only": bool(row.get("requires_direct_ext3_only")),
                "reason": (
                    "translation_review_incomplete"
                    if int(row.get("approved_records") or 0) < int(row.get("records") or 0)
                    else "short_body_allocation_review_required"
                ),
            }
            break
        if row.get("requires_direct_ext3_only") is not True:
            blocked = {
                "batch_id": batch_id,
                "status": row.get("status"),
                "records": int(row.get("records") or 0),
                "approved_records": int(row.get("approved_records") or 0),
                "requires_direct_ext3_only": False,
                "reason": "short_body_allocation_review_required",
            }
            break

        run("tools/build_uncovered_translation_batch_candidate.py", "--batch-id", batch_id)
        run("tools/audit_uncovered_translation_batch_candidate.py", "--batch-id", batch_id)
        false_report = batch_path(batch_id, "false_segptr.json")
        run(
            "tools/scan_false_segptr_writes.py",
            "--target", str(batch_path(batch_id, "candidate.wsc").relative_to(ROOT)),
            "--out", str(false_report.relative_to(ROOT)),
        )
        build = load_object(batch_path(batch_id, "report.json"))
        audit = load_object(batch_path(batch_id, "audit.json"))
        false_scan = load_object(false_report)
        if not (build.get("ok") is True and audit.get("ok") is True and false_scan.get("ok") is True):
            raise PipelineError(f"one or more gates failed for {batch_id}")
        completed.append({
            "batch_id": batch_id,
            "status": audit.get("status"),
            "records": int((build.get("counts") or {}).get("new_targets") or 0),
            "cumulative_targets": int((audit.get("counts") or {}).get("cumulative_targets") or 0),
            "candidate": build.get("candidate"),
            "checksum": audit.get("checksum"),
            "false_segmented_pointer_writes": int(false_scan.get("sites_found") or 0),
        })
        if through == batch_id:
            break

    report = {
        "schema_version": 1,
        "generated_by": "tools/run_uncovered_translation_batches.py",
        "ok": True,
        "policy": {
            "strict_manifest_order": True,
            "stop_at_first_unapproved_batch": True,
            "stop_at_short_body_batch_without_reviewed_allocation": True,
            "main_tip_and_main_saveram_never_modified": True,
        },
        "completed": completed,
        "blocked_next": blocked,
        "latest_candidate": completed[-1].get("candidate") if completed else None,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
