#!/usr/bin/env python3
"""Aggressively archive non-core artifacts from out/patch and out/script.

Default mode is dry-run.  ``--commit`` moves non-core artifacts under
``legacy/release_core_20260815/`` while preserving their repository-relative paths.
No file is deleted.  The current main TIP is SHA-guarded before and after commit.

This cleanup is intentionally stricter than organize_current_tip_legacy_assets.py:
it is for a GitHub/release-ready workspace where out/patch and out/script should
contain only current-main essentials.
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
SCRIPT = ROOT / "out" / "script"
LEGACY_ROOT = ROOT / "legacy" / "release_core_20260815"
MANIFEST = LEGACY_ROOT / "manifest.json"

MAIN = PATCH / "monoeye_ko_expanded.wsc"
EXPECTED_MAIN_SHA256 = "d7543ad4a62d9e7a9687583e85005dc4ca137e6fa62238eb70e58492248985c9"
EXPECTED_MAIN_SIZE = 16_777_216

# Current-main development essentials only.
KEEP_PATCH_FILES = {
    "monoeye_ko_expanded.wsc",
    "monoeye_ko_expanded_8mb.wsc",
    "monoeye_ko_expanded.pre_ext3.wsc",
    "hangul_patch_pad3.tbl",
    "exp_dictionary_meta.json",
    "ext3_dictionary_meta.json",
    "dialogue_runtime_safety_gate.json",
    "lalah_sune_postpromotion_terminology_audit.json",
    "lalah_sune_postpromotion_mapping_audit.json",
    "lalah_sune_postpromotion_false_segptr.json",
    "lalah_sune_terminology_followup_promotion_report.json",
}

# Keep exactly one immediate rollback of the current main TIP.
KEEP_PATCH_PREFIXES = {
    "backup/20260815_173517_pre_lalah_sune_terminology_followup/",
}

# Current translation/re-application essentials.  Everything else in out/script
# is generated analysis, queue/batch material, historical review evidence, or a
# reproducible intermediate and is archived rather than deleted.
KEEP_SCRIPT_FILES = {
    "translation_sheet.csv",
    "excel_translate_cache.json",
    "translations_quality_all.json",
    "uncovered_translation_sheet_llm_reviewed.csv",
    "dialogue_readability_changes.json",
    "dialogue_runtime_safety_gate.json",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def validate_main() -> dict[str, object]:
    if not MAIN.is_file() or MAIN.stat().st_size != EXPECTED_MAIN_SIZE:
        raise SystemExit("refusing cleanup: current main TIP missing or wrong size")
    actual = sha256(MAIN)
    if actual != EXPECTED_MAIN_SHA256:
        raise SystemExit(
            f"refusing cleanup: main TIP SHA drifted: {actual} != {EXPECTED_MAIN_SHA256}"
        )
    return {"path": rel(MAIN), "size": MAIN.stat().st_size, "sha256": actual}


def keep_patch(path: Path) -> bool:
    r = path.relative_to(PATCH).as_posix()
    if r in KEEP_PATCH_FILES:
        return True
    return any(r.startswith(prefix) for prefix in KEEP_PATCH_PREFIXES)


def keep_script(path: Path) -> bool:
    return path.relative_to(SCRIPT).as_posix() in KEEP_SCRIPT_FILES


def collect() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for base, keeper in ((PATCH, keep_patch), (SCRIPT, keep_script)):
        if not base.is_dir():
            continue
        for path in sorted((p for p in base.rglob("*") if p.is_file()), key=lambda p: rel(p).lower()):
            if keeper(path):
                continue
            target = LEGACY_ROOT / path.relative_to(ROOT)
            rows.append(
                {
                    "source": rel(path),
                    "target": rel(target),
                    "bytes": path.stat().st_size,
                    "suffix": path.suffix.lower() or "<none>",
                }
            )
    return rows


def summary(rows: list[dict[str, object]]) -> dict[str, object]:
    by_root = Counter(str(row["source"]).split("/", 2)[1] for row in rows)
    by_suffix = Counter(str(row["suffix"]) for row in rows)
    total = sum(int(row["bytes"]) for row in rows)
    return {
        "files": len(rows),
        "bytes": total,
        "mib": round(total / 1024 / 1024, 1),
        "by_root": dict(sorted(by_root.items())),
        "by_suffix": dict(sorted(by_suffix.items())),
        "remaining_patch_files": len(KEEP_PATCH_FILES),
        "remaining_script_files": len(KEEP_SCRIPT_FILES),
        "rollback_prefixes_kept": sorted(KEEP_PATCH_PREFIXES),
    }


def commit(rows: list[dict[str, object]], before: dict[str, object]) -> dict[str, object]:
    moved: list[dict[str, object]] = []
    LEGACY_ROOT.mkdir(parents=True, exist_ok=True)
    for row in rows:
        src = ROOT / str(row["source"])
        dst = ROOT / str(row["target"])
        if not src.is_file():
            raise SystemExit(f"source disappeared: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if dst.is_file() and sha256(dst) == sha256(src):
                src.unlink()
                moved.append({**row, "mode": "deduplicated_identical"})
                continue
            raise SystemExit(f"archive target collision: {dst}")
        shutil.move(str(src), str(dst))
        moved.append({**row, "mode": "moved"})

    # Remove now-empty generated directories only under the two scoped roots.
    for base in (PATCH, SCRIPT):
        for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass

    after = validate_main()
    if after != before:
        raise SystemExit("fatal: main TIP changed during cleanup")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/organize_release_core_assets.py",
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_before": before,
        "main_after": after,
        "summary": summary(moved),
        "keep_patch_files": sorted(KEEP_PATCH_FILES),
        "keep_patch_prefixes": sorted(KEEP_PATCH_PREFIXES),
        "keep_script_files": sorted(KEEP_SCRIPT_FILES),
        "moved": moved,
        "restore": "Move target back to source. No artifact was intentionally deleted.",
    }
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Move files; default is dry-run")
    parser.add_argument("--verbose", action="store_true", help="Print every planned/moved file")
    args = parser.parse_args()

    before = validate_main()
    rows = collect()
    info = summary(rows)
    print(json.dumps({"mode": "commit" if args.commit else "dry_run", "main": before, "summary": info}, ensure_ascii=False, indent=2))
    if args.verbose:
        for row in rows:
            print(f"{row['bytes']}\t{row['source']} -> {row['target']}")
    if not args.commit:
        return 0

    result = commit(rows, before)
    print(json.dumps({"ok": True, "manifest": rel(MANIFEST), "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
