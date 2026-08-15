#!/usr/bin/env python3
"""Archive stale workspace artifacts around the current main TIP.

The default mode is a dry-run.  ``--commit`` moves artifacts into ``legacy/``
and writes a reversible manifest; it never deletes a non-empty file.  The
current TIP and live SaveRAM are checked before and after the move.

Keep policy:

* documents, data, reference material, the current emulator profile, and
  files mentioned by the project documentation stay in place;
* recent outputs from 2026-08-09 15:00 KST onward stay in place so the latest
  unpromoted investigation remains reproducible;
* only the three newest TIP rollback directories stay in ``out/patch/backup``;
* only the three newest manual SaveRAM backups stay in ``savebackup``;
* older or undocumented outputs are moved under ``legacy/`` with their
  original relative path preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
PATCH = OUT / "patch"
SCRIPT_OUT = OUT / "script"
SRAM = ROOT / "sram"
SAVE_BACKUP = ROOT / "savebackup"
LEGACY = ROOT / "legacy"
REPORT = PATCH / "current_tip_workspace_cleanup_report.json"
MANIFEST = LEGACY / "manifest.json"

TIP = PATCH / "monoeye_ko_expanded.wsc"
LIVE_SAVE = SRAM / "monoeye_ko_expanded.sav"
EXPECTED_TIP_SHA256 = (
    "55c2e1f3467d28e041ad0e145cad68091cf78d50f8d58f6ce6a65259acd59ca9"
)
EXPECTED_TIP_SIZE = 16_777_216
EXPECTED_SAVE_SIZE = 32_768
RECENT_CUTOFF = datetime(2026, 8, 9, 15, 0, 0).timestamp()
RECENT_BACKUP_COUNT = 3
RECENT_SRAM_COUNT = 3

ACTIVE_OUT_DIRS = {"patch", "script", "bizhawk_profile"}
KEEP_PATCH_FILES = {"monoeye_ko_expanded.wsc", "monoeye_ko_expanded.sav"}


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def files_under(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted((item for item in path.rglob("*") if item.is_file()), key=rel)


PATH_TOKEN_RE = re.compile(
    r"(?i)(?:(?:out|data|tools|sram|docs|reference|savebackup|assets|\.kiro|\.cursor)[/\\][^\s`\"'<>()[\]{}|,;:]+)"
)
BARE_FILE_RE = re.compile(
    r"(?i)(?<![\w])[^\s`\"'<>()[\]{}|,;:]+\.(?:json|wsc|sav|csv|py|md|txt|bin|png|ips|zip|log|lua|tbl|state)\b"
)


def load_document_refs() -> tuple[set[str], set[str]]:
    paths = [ROOT / "README.md", ROOT / "PATCH_PROGRESS.md"]
    paths.extend(sorted((ROOT / "docs").rglob("*.md")))
    paths.extend(sorted((ROOT / ".cursor").rglob("*")))
    paths.extend(sorted((ROOT / ".kiro").rglob("*")))
    chunks: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    document_text = "\n".join(chunks).lower()
    exact: set[str] = set()
    names: set[str] = set()
    for match in PATH_TOKEN_RE.finditer(document_text):
        value = match.group(0).rstrip("`.,;:。”'\")]} ").replace("\\", "/")
        value = re.sub(r"^d:/monoeye/", "", value)
        exact.add(value)
        names.add(Path(value).name)
    names.update(match.group(0).lower() for match in BARE_FILE_RE.finditer(document_text))
    return exact, names


def documented(path: Path, references: set[str], referenced_names: set[str]) -> bool:
    normalized = rel(path).lower().replace("\\", "/")
    return normalized in references or path.name.lower() in referenced_names


def recent(path: Path) -> bool:
    return path.stat().st_mtime >= RECENT_CUTOFF


def validate_tip() -> dict[str, object]:
    if not TIP.is_file() or TIP.stat().st_size != EXPECTED_TIP_SIZE:
        raise SystemExit("refusing cleanup: current TIP is missing or has the wrong size")
    actual = digest(TIP)
    if actual != EXPECTED_TIP_SHA256:
        raise SystemExit(
            "refusing cleanup: current TIP SHA-256 drifted "
            f"(expected {EXPECTED_TIP_SHA256}, got {actual})"
        )
    if not LIVE_SAVE.is_file() or LIVE_SAVE.stat().st_size != EXPECTED_SAVE_SIZE:
        raise SystemExit("refusing cleanup: live SaveRAM is missing or has the wrong size")
    return {
        "tip": {"path": rel(TIP), "size": TIP.stat().st_size, "sha256": actual},
        "live_saveram": {
            "path": rel(LIVE_SAVE),
            "size": LIVE_SAVE.stat().st_size,
            "sha256": digest(LIVE_SAVE),
        },
    }


def archive_destination(path: Path) -> Path:
    return LEGACY / rel(path)


def add_plan(plan: list[dict[str, object]], path: Path, reason: str) -> None:
    if not path.exists():
        return
    target = archive_destination(path)
    if target.exists():
        raise SystemExit(f"refusing cleanup: archive target already exists: {target}")
    plan.append(
        {
            "path": rel(path),
            "archive_path": rel(target),
            "kind": "directory" if path.is_dir() else "file",
            "bytes": size_of(path),
            "reason": reason,
        }
    )


def collect_patch_plan(
    plan: list[dict[str, object]], references: set[str], referenced_names: set[str]
) -> None:
    if not PATCH.is_dir():
        return

    backup_root = PATCH / "backup"
    backups = sorted(
        (path for path in backup_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    ) if backup_root.is_dir() else []
    for path in backups[RECENT_BACKUP_COUNT:]:
        add_plan(plan, path, "older_than_three_newest_tip_rollbacks")

    for path in PATCH.iterdir():
        if path.name == "backup":
            continue
        if path.name == "rejected" and path.is_dir():
            for file_path in files_under(path):
                add_plan(plan, file_path, "rejected_test_or_design_output")
            continue
        if path.is_file():
            if path.name in KEEP_PATCH_FILES or documented(path, references, referenced_names) or recent(path):
                continue
            add_plan(plan, path, "undocumented_or_stale_patch_output")
            continue
        if not path.is_dir():
            continue
        for file_path in files_under(path):
            if documented(file_path, references, referenced_names) or recent(file_path):
                continue
            add_plan(plan, file_path, "undocumented_or_stale_patch_output")


def collect_script_plan(
    plan: list[dict[str, object]], references: set[str], referenced_names: set[str]
) -> None:
    if not SCRIPT_OUT.is_dir():
        return
    for path in files_under(SCRIPT_OUT):
        if documented(path, references, referenced_names) or recent(path):
            continue
        add_plan(plan, path, "undocumented_or_stale_script_output")


def collect_out_plan(
    plan: list[dict[str, object]], references: set[str], referenced_names: set[str]
) -> None:
    if not OUT.is_dir():
        return
    for path in OUT.iterdir():
        if path.name in ACTIVE_OUT_DIRS:
            continue
        if path.is_file():
            if documented(path, references, referenced_names) or recent(path):
                continue
            add_plan(plan, path, "legacy_top_level_out_artifact")
            continue
        if not path.is_dir():
            continue
        children = files_under(path)
        if not children:
            add_plan(plan, path, "empty_legacy_out_directory")
            continue
        if any(documented(child, references, referenced_names) or recent(child) for child in children):
            continue
        add_plan(plan, path, "legacy_out_test_or_design_directory")


def collect_sram_plan(
    plan: list[dict[str, object]], references: set[str], referenced_names: set[str]
) -> None:
    if not SRAM.is_dir():
        return
    candidates = sorted(
        (path for path in SRAM.iterdir() if path.is_file() and path != LIVE_SAVE),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    recent_kept = 0
    for path in candidates:
        if documented(path, references, referenced_names):
            continue
        if recent_kept < RECENT_SRAM_COUNT:
            recent_kept += 1
            continue
        add_plan(plan, path, "older_or_undocumented_candidate_saveram")


def collect_savebackup_plan(
    plan: list[dict[str, object]], references: set[str], referenced_names: set[str]
) -> None:
    if not SAVE_BACKUP.is_dir():
        return
    backups = sorted(
        (path for path in SAVE_BACKUP.iterdir() if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    kept = 0
    for path in backups:
        if documented(path, references, referenced_names):
            continue
        if kept < RECENT_BACKUP_COUNT:
            kept += 1
            continue
        add_plan(plan, path, "older_than_three_newest_manual_saveram_backups")


def collect_plan(references: set[str], referenced_names: set[str]) -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []
    collect_patch_plan(plan, references, referenced_names)
    collect_script_plan(plan, references, referenced_names)
    collect_out_plan(plan, references, referenced_names)
    collect_sram_plan(plan, references, referenced_names)
    collect_savebackup_plan(plan, references, referenced_names)
    return sorted(plan, key=lambda row: str(row["path"]).lower())


def summarize(plan: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(plan)
    reasons = Counter(str(row["reason"]) for row in rows)
    total = sum(int(row["bytes"]) for row in rows)
    return {
        "paths": len(rows),
        "bytes": total,
        "mib": round(total / 1024 / 1024, 1),
        "by_reason": dict(sorted(reasons.items())),
    }


def remove_empty_dirs() -> None:
    roots = [PATCH, SCRIPT_OUT, OUT, SRAM, SAVE_BACKUP]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass


def commit(plan: list[dict[str, object]], before: dict[str, object]) -> dict[str, object]:
    LEGACY.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, object]] = []
    for row in plan:
        source = ROOT / str(row["path"])
        target = ROOT / str(row["archive_path"])
        if not source.exists():
            raise SystemExit(f"source disappeared before move: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(row)

    remove_empty_dirs()
    after = validate_tip()
    if before["tip"] != after["tip"]:
        raise SystemExit("fatal: current TIP changed during cleanup")
    if before["live_saveram"] != after["live_saveram"]:
        raise SystemExit("fatal: live SaveRAM changed during cleanup")

    prior_rows: list[dict[str, object]] = []
    if MANIFEST.is_file():
        try:
            prior_document = json.loads(MANIFEST.read_text(encoding="utf-8"))
            prior_rows = [
                row for row in prior_document.get("moved", []) if isinstance(row, dict)
            ]
        except (OSError, ValueError, TypeError):
            prior_rows = []
    combined_by_path = {
        str(row["path"]): row for row in prior_rows + moved if "path" in row
    }
    combined = [combined_by_path[key] for key in sorted(combined_by_path)]
    report = {
        "schema_version": 1,
        "ok": True,
        "generated_by": "tools/organize_current_tip_workspace.py",
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": {
            "tip_sha256": EXPECTED_TIP_SHA256,
            "recent_cutoff_local": "2026-08-09 15:00:00 +09:00",
            "tip_rollbacks_kept": RECENT_BACKUP_COUNT,
            "manual_saveram_backups_kept": RECENT_BACKUP_COUNT,
            "candidate_saverams_kept": RECENT_SRAM_COUNT,
            "mode": "move_to_legacy",
        },
        "baseline_before": before,
        "baseline_after": after,
        "summary": summarize(combined),
        "last_run": summarize(moved),
        "moved": combined,
        "restore": "Move each archive_path back to path; paths are repository-relative.",
    }
    MANIFEST.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme = LEGACY / "README.md"
    readme.write_text(
        "# legacy\n\n"
        "현재 main TIP에 적용되지 않았거나 최근 작업 기준에서 직접 참조되지 않은 "
        "과거 테스트·설계·중간 산출물을 삭제하지 않고 원래 상대 경로를 유지한 채 보관한다.\n\n"
        "- 기준 TIP: `out/patch/monoeye_ko_expanded.wsc`\n"
        f"- 기준 SHA-256: `{EXPECTED_TIP_SHA256.upper()}`\n"
        "- 분류·이동 매니페스트: `legacy/manifest.json`\n"
        "- 원래 경로로 복원하려면 매니페스트의 `archive_path`를 `path`로 이동한다.\n"
        "- `tools/archive/`는 도구 import/doc 참조 감사로 별도 분류한 스크립트 보관소다.\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="move the planned artifacts")
    args = parser.parse_args()

    before = validate_tip()
    references, referenced_names = load_document_refs()
    plan = collect_plan(references, referenced_names)
    summary = summarize(plan)
    print(json.dumps({"mode": "commit" if args.commit else "dry_run", "summary": summary}, ensure_ascii=False, indent=2))
    for row in plan:
        print(f"{row['reason']}\t{row['bytes']}\t{row['path']} -> {row['archive_path']}")
    if not args.commit:
        print("dry-run: no files moved")
        return 0
    report = commit(plan, before)
    print(json.dumps({"ok": report["ok"], "summary": report["summary"], "manifest": rel(MANIFEST)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
