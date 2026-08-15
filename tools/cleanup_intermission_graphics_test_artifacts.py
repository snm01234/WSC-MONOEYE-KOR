#!/usr/bin/env python3
"""Remove obsolete intermission graphics test artifacts; keep builder inputs + backup."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"

KEEP_DIRS = {
    "intermission_full_rebuild_clean16_candidate",
    "intermission_all_focus_clean",
    "intermission_focus_sweep",
}
ADVANCE = "intermission_advance_left_residue_clear_candidate"
ADVANCE_KEEP_FILES = {
    "README.md",
    "static_bg_focus_exact_report.json",
    "private_tilemap_patches.json",
}
CLEANUP_REPORT = PATCH / "intermission_graphics_test_cleanup_report.json"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    plan: list[dict] = []
    bytes_planned = 0

    adv = PATCH / ADVANCE
    src_report = adv / "static_bg_focus_exact_report.json"
    dst_report = PATCH / "intermission_advance_left_residue_clear_build_report.json"

    for child in sorted(PATCH.iterdir()):
        if not child.is_dir() or not child.name.startswith("intermission"):
            continue
        if child.name in KEEP_DIRS or child.name == ADVANCE:
            continue
        size = dir_size(child)
        plan.append({"path": child.name, "bytes": size, "action": "rmtree"})
        bytes_planned += size

    if adv.is_dir():
        for item in sorted(adv.iterdir()):
            if item.name in ADVANCE_KEEP_FILES:
                continue
            size = item.stat().st_size if item.is_file() else dir_size(item)
            action = "unlink" if item.is_file() else "rmtree"
            plan.append(
                {
                    "path": f"{ADVANCE}/{item.name}{'/' if action == 'rmtree' else ''}",
                    "bytes": size,
                    "action": action,
                }
            )
            bytes_planned += size

    payload = {
        "schema_version": 1,
        "generated_by": "tools/cleanup_intermission_graphics_test_artifacts.py",
        "mode": "commit" if args.commit else "dry_run",
        "kept_dirs": sorted(KEEP_DIRS | {ADVANCE}),
        "kept_advance_evidence_files": sorted(ADVANCE_KEEP_FILES),
        "copy_build_report_to": str(
            dst_report.relative_to(ROOT)
        ).replace("\\", "/"),
        "plan": plan,
        "bytes_planned": bytes_planned,
        "bytes_planned_mib": round(bytes_planned / (1024 * 1024), 1),
    }

    if not args.commit:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if src_report.is_file():
        shutil.copy2(src_report, dst_report)

    deleted = []
    bytes_freed = 0
    for entry in plan:
        target = PATCH / entry["path"].rstrip("/")
        if entry["action"] == "rmtree":
            if target.is_dir():
                size = dir_size(target)
                shutil.rmtree(target)
                deleted.append({**entry, "bytes": size})
                bytes_freed += size
        elif entry["action"] == "unlink":
            if target.is_file():
                size = target.stat().st_size
                target.unlink()
                deleted.append({**entry, "bytes": size})
                bytes_freed += size

    result = {
        **payload,
        "ok": True,
        "deleted": deleted,
        "bytes_freed": bytes_freed,
        "bytes_freed_mib": round(bytes_freed / (1024 * 1024), 1),
        "build_report_copied": dst_report.is_file(),
    }
    CLEANUP_REPORT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
