#!/usr/bin/env python3
"""Archive unused legacy sheets and leftover test ROMs around the current TIP.

The default mode is a dry-run.  ``--commit`` moves artifacts into ``legacy/``
with original relative paths preserved and never deletes a non-empty file.
The current TIP and live SaveRAM are checked before and after the move.

Keep policy:

* current TIP ``out/patch/monoeye_ko_expanded.wsc``
* gate baseline ``monoeye_ko_expanded.pre_ext3.wsc`` and 8 MiB cold-rebuild input
* live SaveRAM ``sram/monoeye_ko_expanded.sav``
* the three newest TIP rollback directories
* dictionary/runtime metadata consumed by current builders
* JSON that records the exact current TIP SHA-256
* outputs written during the current release work window (2026-08-15 17:30 KST+)
* forensic blocked sheets still read by the contract/review pipeline
  (``translation_sheet.csv``, ``excel_translate_cache.json``,
  ``translations_quality_all.json``)

Move policy:

* leftover ``out/patch/*.wsc`` test ROMs and their paired ``.sav``
* older TIP rollback directories
* unused duplicate Bing/Excel/Google sheet JSON/CSV
* leftover ``*candidate_contracts.json`` snapshots for those test ROMs
* ``out/patch`` JSON/CSV that is neither current-TIP-bound nor recent
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out" / "patch"
SCRIPT = ROOT / "out" / "script"
SRAM = ROOT / "sram"
LEGACY = ROOT / "legacy"
REPORT = PATCH / "current_tip_legacy_asset_cleanup_report.json"
MANIFEST = LEGACY / "legacy_asset_manifest.json"

TIP = PATCH / "monoeye_ko_expanded.wsc"
LIVE_SAVE = SRAM / "monoeye_ko_expanded.sav"
EXPECTED_TIP_SHA256 = (
    "d7543ad4a62d9e7a9687583e85005dc4ca137e6fa62238eb70e58492248985c9"
)
EXPECTED_TIP_SIZE = 16_777_216
EXPECTED_SAVE_SIZE = 32_768
RECENT_BACKUP_COUNT = 3
RECENT_CUTOFF = datetime(2026, 8, 15, 17, 30, 0).timestamp()

KEEP_WSC = {
    "monoeye_ko_expanded.wsc",
    "monoeye_ko_expanded.pre_ext3.wsc",
    "monoeye_ko_expanded_8mb.wsc",
}
KEEP_SRAM = {
    "monoeye_ko_expanded.sav",
    "SD Gundam G Generation Mono-Eye Gundams.sav",
}
KEEP_PATCH_METADATA = {
    "exp_dictionary_meta.json",
    "ext_dictionary_meta.json",
    "ext3_dictionary_meta.json",
    "hangul_char_map.json",
    "hangul_char_map_pad3.json",
    "hangul_patch.tbl",
    "hangul_patch_pad3.tbl",
    "free_space_pointer_allowlist.json",
    "invasion_full_line_tokens.json",
    "aux_ff_invasion_scan.json",
    "monoeye_ko_expanded_structured_token_tables.json",
    "translation_source_policy_audit.json",
    "dialogue_runtime_safety_gate.json",
    "test_rom_jp_residual_scan.json",
    "id_command_effect_compact_draft.json",
    "id_command_effect_width_inventory.json",
    "bank59_5c_name75_remaining_inventory.json",
    "archive_unused_tools.json",
    "archive_unused_tools_followup.json",
    "tool_audit.json",
}
KEEP_PATCH_PREFIXES = (
    "current_tip_",
    "id_command_effect_width_",
    "ui75_nonsentence_rollback_",
    "bank59_enc5c_name75_",
    "term_unify_",
    "weapon_enc_width13_",
    "ui_onebyte_and_map_padding_",
)
MOVE_SCRIPT_SHEETS = {
    "translation_sheet_partial.csv",
    "translation_sheet_probe.csv",
    "translations_apply_all.json",
    "translations_ep3_window.json",
    "translations_quality.json",
}
MOVE_SCRIPT_CONTRACT_SUFFIXES = (
    "candidate_contracts.json",
    "candidate_runtime_contracts.json",
    "probe_contracts.json",
)
SKIP_DIR_NAMES = {"backup"}


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


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


def contains_current_tip_sha(path: Path) -> bool:
    if path.suffix.lower() not in {".json", ".csv", ".md", ".txt"}:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return EXPECTED_TIP_SHA256 in text.lower()


def keep_patch_file(path: Path) -> bool:
    name = path.name
    if name in KEEP_PATCH_METADATA or name in KEEP_WSC:
        return True
    if name.startswith(KEEP_PATCH_PREFIXES):
        return True
    if path.stat().st_mtime >= RECENT_CUTOFF:
        return True
    if contains_current_tip_sha(path):
        return True
    return False


def add_plan(plan: list[dict[str, object]], path: Path, reason: str) -> None:
    if not path.exists():
        return
    target = LEGACY / rel(path)
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


def collect_script_plan(plan: list[dict[str, object]]) -> None:
    if not SCRIPT.is_dir():
        return
    for path in sorted(SCRIPT.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        if path.name in MOVE_SCRIPT_SHEETS:
            add_plan(plan, path, "unused_duplicate_legacy_mt_sheet")
            continue
        if path.name.endswith(MOVE_SCRIPT_CONTRACT_SUFFIXES):
            add_plan(plan, path, "leftover_test_rom_contract_snapshot")
            continue
        if path.name == "dialogue_runtime_static_candidate_contracts.json":
            add_plan(plan, path, "leftover_test_rom_contract_snapshot")


def collect_patch_rom_plan(plan: list[dict[str, object]]) -> set[str]:
    moved_stems: set[str] = set()
    if not PATCH.is_dir():
        return moved_stems
    for path in sorted(PATCH.glob("*.wsc")):
        if path.name in KEEP_WSC:
            continue
        moved_stems.add(path.stem)
        add_plan(plan, path, "leftover_script_or_json_test_rom")
    for path in sorted(PATCH.glob("*.sav")):
        if path.name == "monoeye_ko_expanded.sav":
            add_plan(plan, path, "historical_out_patch_saveram_not_live_canonical")
            continue
        if path.stem in moved_stems or path.stem not in {item.removesuffix(".wsc") for item in KEEP_WSC}:
            add_plan(plan, path, "paired_leftover_test_rom_saveram")
    return moved_stems


def collect_backup_plan(plan: list[dict[str, object]]) -> None:
    backup_root = PATCH / "backup"
    if not backup_root.is_dir():
        return
    backups = sorted(
        (path for path in backup_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in backups[RECENT_BACKUP_COUNT:]:
        add_plan(plan, path, "older_than_three_newest_tip_rollbacks")


def collect_patch_json_plan(plan: list[dict[str, object]], moved_rom_stems: set[str]) -> None:
    if not PATCH.is_dir():
        return
    planned = {str(row["path"]) for row in plan}
    for path in sorted(PATCH.iterdir(), key=lambda item: item.name.lower()):
        if path.name in SKIP_DIR_NAMES:
            continue
        if path.is_dir():
            files = [item for item in path.rglob("*") if item.is_file()]
            if not files:
                add_plan(plan, path, "empty_leftover_patch_directory")
                continue
            if any(keep_patch_file(item) for item in files):
                continue
            if any(item.suffix.lower() == ".wsc" for item in files) or path.name.endswith("_candidate"):
                add_plan(plan, path, "leftover_test_rom_or_probe_directory")
            elif all(item.stat().st_mtime < RECENT_CUTOFF for item in files):
                add_plan(plan, path, "stale_patch_probe_or_preview_directory")
            continue
        if not path.is_file():
            continue
        if rel(path) in planned:
            continue
        if path.suffix.lower() in {".wsc", ".sav", ".tbl"}:
            continue
        if keep_patch_file(path):
            continue
        if path.stem in moved_rom_stems or path.name.startswith(tuple(stem + "_" for stem in moved_rom_stems)):
            add_plan(plan, path, "json_or_report_for_leftover_test_rom")
            continue
        if path.suffix.lower() in {".json", ".csv"}:
            add_plan(plan, path, "not_current_tip_bound_and_older_than_current_window")


def collect_sram_plan(plan: list[dict[str, object]], moved_rom_stems: set[str]) -> None:
    if not SRAM.is_dir():
        return
    for path in sorted(SRAM.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        if path.name in KEEP_SRAM or path.name.startswith("monoeye_ko_expanded.sav"):
            continue
        if path.suffix.lower() not in {".sav", ".saveram"}:
            continue
        if path.stem in moved_rom_stems or path.stem.endswith("_candidate") or path.stem.endswith("_test") or path.stem.endswith("_probe"):
            add_plan(plan, path, "paired_leftover_test_rom_saveram")


def collect_plan() -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []
    collect_script_plan(plan)
    moved_stems = collect_patch_rom_plan(plan)
    collect_backup_plan(plan)
    collect_patch_json_plan(plan, moved_stems)
    collect_sram_plan(plan, moved_stems)
    return sorted(plan, key=lambda row: str(row["path"]).lower())


def summarize(plan: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(plan)
    total = sum(int(row["bytes"]) for row in rows)
    return {
        "paths": len(rows),
        "bytes": total,
        "mib": round(total / 1024 / 1024, 1),
        "by_reason": dict(sorted(Counter(str(row["reason"]) for row in rows).items())),
    }


def remove_empty_dirs() -> None:
    for root in (PATCH, SCRIPT, SRAM):
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
    report = {
        "schema_version": 1,
        "ok": True,
        "generated_by": "tools/organize_current_tip_legacy_assets.py",
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": {
            "tip_sha256": EXPECTED_TIP_SHA256,
            "recent_cutoff_local": "2026-08-15 17:30:00 +09:00",
            "tip_rollbacks_kept": RECENT_BACKUP_COUNT,
            "kept_wsc": sorted(KEEP_WSC),
            "kept_forensic_sheets": [
                "out/script/translation_sheet.csv",
                "out/script/excel_translate_cache.json",
                "out/script/translations_quality_all.json",
            ],
            "mode": "move_to_legacy",
        },
        "baseline_before": before,
        "baseline_after": after,
        "summary": summarize(moved),
        "moved": moved,
        "restore": "Move each archive_path back to path; paths are repository-relative.",
    }
    MANIFEST.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (LEGACY / "README.md").write_text(
        "# legacy\n\n"
        "현재 메인 TIP 적용 경로에서 쓰이지 않는 과거 번역 시트·테스트 ROM·후보 JSON을 "
        "삭제하지 않고 원래 상대 경로를 유지한 채 보관한다.\n\n"
        "- 기준 TIP: `out/patch/monoeye_ko_expanded.wsc`\n"
        f"- 기준 SHA-256: `{EXPECTED_TIP_SHA256.upper()}`\n"
        "- 매니페스트: `legacy/legacy_asset_manifest.json`\n"
        "- 복원: 매니페스트의 `archive_path`를 `path`로 되돌린다.\n"
        "- `out/script/translation_sheet.csv`, `excel_translate_cache.json`, "
        "`translations_quality_all.json`은 계약/리뷰 forensic 입력이라 활성 경로에 남긴다. "
        "적용(apply/merge/rebuild) 입력으로는 계속 차단된다.\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    before = validate_tip()
    plan = collect_plan()
    summary = summarize(plan)
    print(json.dumps({"mode": "commit" if args.commit else "dry_run", "summary": summary}, ensure_ascii=False, indent=2))
    for row in plan:
        print(f"{row['reason']}\t{row['bytes']}\t{row['path']} -> {row['archive_path']}")
    if not args.commit:
        print("dry-run: no files moved")
        return 0
    report = commit(plan, before)
    print(json.dumps({"ok": report["ok"], "summary": report["summary"], "manifest": rel(MANIFEST), "report": rel(REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
