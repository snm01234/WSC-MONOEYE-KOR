#!/usr/bin/env python3
"""Archive non-core workspace artifacts for a release-ready Monoeye tree.

Default mode is a dry-run. ``--commit`` moves files into
``legacy/workspace_release_core_20260815/`` while preserving repository-relative
paths. Nothing is intentionally deleted. The current main TIP is SHA-guarded
before and after a committed cleanup.

Scope:
- aggressively minimize ``out/patch`` and ``out/script`` to current-main essentials;
- archive generated ``outputs`` workbooks/previews;
- archive unrelated ``reference`` ROM/IPS/ZIP material;
- archive historical RetroArch states, while preserving ``savebackup/`` in place;
- archive only clearly historical docs and isolated data intermediates;
- keep canonical translation/specification data, fonts, original ROM, tools, and
  current release documentation in place.
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
LEGACY_ROOT = ROOT / "legacy" / "workspace_release_core_20260815"
MANIFEST = LEGACY_ROOT / "manifest.json"

PATCH = ROOT / "out" / "patch"
SCRIPT = ROOT / "out" / "script"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
EXPECTED_MAIN_SHA256 = "c8ee51be9c5e33dfd88e7565453ff031a931aaf4948d9cd4aee35a7ec6892e86"
EXPECTED_MAIN_SIZE = 16_777_216

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
    "event_cleanup_hotfix_1_0_1_postpromotion_audit.json",
    "event_cleanup_hotfix_1_0_1_postpromotion_false_lead.json",
    "event_cleanup_hotfix_1_0_1_postpromotion_false_segptr.json",
    "event_cleanup_hotfix_1_0_1_promotion_report.json",
}
KEEP_PATCH_PREFIXES = {
    "backup/20260815_235351_pre_v1.0.1_hotfix/",
}
KEEP_SCRIPT_FILES = {
    "translation_sheet.csv",
    "excel_translate_cache.json",
    "translations_quality_all.json",
    "uncovered_translation_sheet_llm_reviewed.csv",
    "dialogue_readability_changes.json",
    "dialogue_runtime_safety_gate.json",
}

# The public distribution format is xdelta only. Historical IPS builds, seed IPS,
# and the ROM/SaveRAM produced while validating IPS are archived.
LEGACY_DIST_FILES = {
    "monoeye_ko_expanded.ips",
    "monoeye_ko_expanded_ips.json",
    "monoeye_ko_expanded_IPS_README.md",
    "monoeye_ko_from_ips.sav",
    "monoeye_ko_from_ips.wsc",
    "monoeye_ko_seed.ips",
    "monoeye_ko_seed_patch.json",
}

# These data files are isolated generated/legacy seed artifacts. The canonical
# data tree (including *_ko.json, mixed_residual_values and review batches) stays.
LEGACY_DATA_FILES = {
    "short_strings_extracted.json",
    "translations_seed_e7blank.json",
    "translations_seed_tailpad.json",
    "translations_seed_textsafe.json",
}

# Historical planning/test docs superseded by PATCH_PROGRESS + current policy /
# architecture documents. Documentation that is still referenced as a current
# technical source remains under docs/.
LEGACY_DOC_FILES = {
    "AUX_PREFIX_BARCODE_TEST.md",
    "BROAD_EXT3_EXPANSION_PLAN.md",
    "CURRENT_TIP_JAPANESE_RESIDUAL_INVENTORY.md",
    "DIALOGUE_SCREEN_WIDTH_AUDIT.md",
    "MAIN_TRANSLATION_LLM_REVIEW_PLAN.md",
    "PATCH_FOLLOW_UP_INDEX.md",
    "PATCH_FOLLOW_UP_P1_COMPLETION.md",
    "PATCH_FOLLOW_UP_P2_RESEARCH.md",
    "SCRIPT_COVERAGE_STATUS.md",
    "UNCOVERED_TRANSLATION_BATCH_WORKFLOW.md",
}

# Generated / forensic roots that are useful as history but not as release-core
# workspace content. Keep directory names available; archive their file contents.
ARCHIVE_ALL_ROOTS = {
    "outputs",
    "reference",
    "retroarch_savestate",
    "out/title_trace6",
}

# title_menu_capture is mostly generated screenshots/logs, but these three files
# are active safety/rebuild inputs and must remain available.
KEEP_TITLE_MENU_CAPTURE_FILES = {
    "bank72_atlas.json",
    "intermission_overlay_resolved.json",
    "state/intermission_r1_s00.png",
}

# Root-local runtime clutter that is not part of source/build data. The clean
# original ROM intentionally remains at repository root because xdelta creation
# uses it as the source image.
LEGACY_ROOT_FILES = {
    "Oswan.eep",
    "Oswan.exe",
    "Oswan.ini",
    "OSwan.log",
    "zlib.dll",
    "readme-jp.txt",
    "SD Gundam G Generation Mono-Eye Gundams.sav",
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


def add(rows: list[dict[str, object]], path: Path, reason: str) -> None:
    if not path.is_file():
        return
    target = LEGACY_ROOT / path.relative_to(ROOT)
    rows.append(
        {
            "source": rel(path),
            "target": rel(target),
            "bytes": path.stat().st_size,
            "suffix": path.suffix.lower() or "<none>",
            "reason": reason,
        }
    )


def collect_out(rows: list[dict[str, object]]) -> None:
    if PATCH.is_dir():
        for path in sorted((p for p in PATCH.rglob("*") if p.is_file()), key=lambda p: rel(p).lower()):
            r = path.relative_to(PATCH).as_posix()
            if r in KEEP_PATCH_FILES or any(r.startswith(prefix) for prefix in KEEP_PATCH_PREFIXES):
                continue
            add(rows, path, "noncore_out_patch_artifact")
    if SCRIPT.is_dir():
        for path in sorted((p for p in SCRIPT.rglob("*") if p.is_file()), key=lambda p: rel(p).lower()):
            r = path.relative_to(SCRIPT).as_posix()
            if r in KEEP_SCRIPT_FILES:
                continue
            add(rows, path, "noncore_out_script_intermediate")


def collect_generated_roots(rows: list[dict[str, object]]) -> None:
    for name in sorted(ARCHIVE_ALL_ROOTS):
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in sorted((p for p in base.rglob("*") if p.is_file()), key=lambda p: rel(p).lower()):
            add(rows, path, f"archive_generated_or_reference_root:{name}")

    capture = ROOT / "out" / "title_menu_capture"
    if capture.is_dir():
        for path in sorted((p for p in capture.rglob("*") if p.is_file()), key=lambda p: rel(p).lower()):
            r = path.relative_to(capture).as_posix()
            if r in KEEP_TITLE_MENU_CAPTURE_FILES:
                continue
            add(rows, path, "noncore_title_menu_capture_artifact")

    dist = ROOT / "out" / "dist"
    for name in sorted(LEGACY_DIST_FILES):
        add(rows, dist / name, "legacy_ips_distribution_artifact")


def collect_selected_data_docs(rows: list[dict[str, object]]) -> None:
    for name in sorted(LEGACY_DATA_FILES):
        add(rows, ROOT / "data" / name, "isolated_legacy_data_intermediate")
    for name in sorted(LEGACY_DOC_FILES):
        add(rows, ROOT / "docs" / name, "superseded_historical_documentation")
    for name in sorted(LEGACY_ROOT_FILES):
        add(rows, ROOT / name, "root_local_runtime_or_reference_file")


def collect() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    collect_out(rows)
    collect_generated_roots(rows)
    collect_selected_data_docs(rows)
    # Stable de-duplication by source path.
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        unique[str(row["source"])] = row
    return [unique[key] for key in sorted(unique, key=str.lower)]


def summary(rows: list[dict[str, object]]) -> dict[str, object]:
    total = sum(int(row["bytes"]) for row in rows)
    by_top = Counter(str(row["source"]).split("/", 1)[0] for row in rows)
    by_reason = Counter(str(row["reason"]) for row in rows)
    by_suffix = Counter(str(row["suffix"]) for row in rows)
    return {
        "files": len(rows),
        "bytes": total,
        "mib": round(total / 1024 / 1024, 1),
        "by_top_level": dict(sorted(by_top.items())),
        "by_reason": dict(sorted(by_reason.items())),
        "by_suffix": dict(sorted(by_suffix.items())),
        "expected_remaining": {
            "out_patch_core_files": len(KEEP_PATCH_FILES),
            "out_script_core_files": len(KEEP_SCRIPT_FILES),
            "data_files_archived": len(LEGACY_DATA_FILES),
            "docs_files_archived": len(LEGACY_DOC_FILES),
            "title_menu_capture_files_kept": len(KEEP_TITLE_MENU_CAPTURE_FILES),
            "dist_ips_files_archived": len(LEGACY_DIST_FILES),
            "dist_release_files_kept": 4,
        },
    }


def remove_empty_dirs() -> None:
    roots = [PATCH, SCRIPT, ROOT / "out" / "title_menu_capture"] + [ROOT / name for name in ARCHIVE_ALL_ROOTS]
    for base in roots:
        if not base.is_dir():
            continue
        for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass


def commit(rows: list[dict[str, object]], before: dict[str, object]) -> dict[str, object]:
    LEGACY_ROOT.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, object]] = []
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

    remove_empty_dirs()
    after = validate_main()
    if before != after:
        raise SystemExit("fatal: current main TIP changed during cleanup")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/organize_workspace_release_core.py",
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_before": before,
        "main_after": after,
        "summary": summary(moved),
        "keep_patch_files": sorted(KEEP_PATCH_FILES),
        "keep_patch_prefixes": sorted(KEEP_PATCH_PREFIXES),
        "keep_script_files": sorted(KEEP_SCRIPT_FILES),
        "legacy_dist_files": sorted(LEGACY_DIST_FILES),
        "legacy_data_files": sorted(LEGACY_DATA_FILES),
        "legacy_doc_files": sorted(LEGACY_DOC_FILES),
        "archive_all_roots": sorted(ARCHIVE_ALL_ROOTS),
        "keep_title_menu_capture_files": sorted(KEEP_TITLE_MENU_CAPTURE_FILES),
        "legacy_root_files": sorted(LEGACY_ROOT_FILES),
        "moved": moved,
        "restore": "Move target back to source. No project artifact is intentionally destroyed.",
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
            print(f"{row['bytes']}\t{row['reason']}\t{row['source']} -> {row['target']}")
    if not args.commit:
        return 0

    result = commit(rows, before)
    print(json.dumps({"ok": True, "manifest": rel(MANIFEST), "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
