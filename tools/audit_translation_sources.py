#!/usr/bin/env python3
"""Audit translation assets against the active provenance/quarantine policy."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
from translation_source_policy import POLICY_PATH, forensic_asset, load_policy, rel

DEFAULT_OUT = ROOT / "out/patch/translation_source_policy_audit.json"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def count_csv(path: Path) -> dict[str, Any]:
    rows = 0
    nonempty_ko = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        for row in reader:
            rows += 1
            nonempty_ko += int(bool((row.get("ko") or "").strip()))
    return {"rows": rows, "nonempty_ko": nonempty_ko, "headers": headers}


def count_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    result: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key in ("engine", "description", "source", "line_count"):
            if key in payload:
                result[key] = payload[key]
        for key in ("lines", "entries", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                result[f"{key}_count"] = len(value)
            elif isinstance(value, dict):
                result[f"{key}_count"] = len(value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    policy = load_policy()
    blocked: list[dict[str, Any]] = []
    missing: list[str] = []

    paths = [str(value) for value in policy.get("blocked_exact_paths") or []]
    prefixes = [str(value) for value in policy.get("blocked_path_prefixes") or []]
    for prefix in prefixes:
        base = ROOT / prefix
        if base.is_dir():
            paths.extend(rel(path) for path in sorted(base.rglob("*")) if path.is_file())
        else:
            missing.append(prefix)

    for relative in sorted(set(paths)):
        path = forensic_asset(relative)
        if not path.is_file():
            missing.append(relative)
            continue
        item: dict[str, Any] = {
            "path": rel(path),
            "declared_path": relative,
            "size": path.stat().st_size,
            "sha256": digest(path),
            "status": "quarantined_not_for_translation_application",
        }
        try:
            if path.suffix.lower() == ".csv":
                item.update(count_csv(path))
            elif path.suffix.lower() == ".json":
                item.update(count_json(path))
        except Exception as exc:  # audit evidence should survive malformed legacy files
            item["inspection_error"] = f"{type(exc).__name__}: {exc}"
        blocked.append(item)

    generators = []
    for row in policy.get("blocked_generators") or []:
        if not isinstance(row, dict):
            continue
        path = ROOT / str(row.get("path") or "")
        generators.append(
            {
                "path": rel(path),
                "exists": path.is_file(),
                "reason": row.get("reason"),
                "status": "quarantined_execution_blocked",
            }
        )

    canonical = ROOT / str((policy.get("future_canonical_sheet") or {}).get("path") or "")
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_translation_sources.py",
        "ok": True,
        "policy": {
            "path": rel(POLICY_PATH),
            "sha256": digest(POLICY_PATH),
            "status": policy.get("status"),
        },
        "active_runtime_baseline": policy.get("active_runtime_baseline"),
        "future_canonical_sheet": {
            "path": rel(canonical),
            "exists": canonical.is_file(),
            "status": "ready" if canonical.is_file() else "not_created_yet",
        },
        "quarantined_assets": blocked,
        "quarantined_asset_count": len(blocked),
        "quarantined_bytes": sum(int(item["size"]) for item in blocked),
        "missing_declared_assets": sorted(set(missing)),
        "blocked_generators": generators,
        "enforcement": {
            "legacy_assets_retained_as_forensic_evidence": True,
            "legacy_assets_allowed_as_translation_sources": False,
            "ordinary_patch_parent_is_promoted_main_tip": True,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report": rel(args.out), "quarantined_assets": len(blocked), "quarantined_bytes": report["quarantined_bytes"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
