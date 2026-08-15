#!/usr/bin/env python3
"""Promote the user-verified scouting-map event-structure repair candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"

TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "scouting_map_event_structure_repair_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/scouting_map_event_structure_repair_candidate.sav"
PARENT = PATCH / "battle_ui_action_labels_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BUILD_REPORT = PATCH / "scouting_map_event_structure_repair_report.json"
POST_AUDIT = PATCH / "scouting_map_event_structure_repair_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "scouting_map_event_structure_repair_promotion_report.json"

EXPECTED_TIP_SHA = "29f790238e02eb228db97619b9c55bc900495bfef2f76eb819d1abcfb5d430f0"
EXPECTED_PARENT_SHA = "6e63e4830e0391f00e0ccdf7d07c6b3b3309e5e3fb797cd934d20900b050e33f"
EXPECTED_CANDIDATE_SHA = "abd9c29656cef765960c1a7d9220b7cfe862f36ebff7c48a46e9893cc14770f3"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_BUILD_REPORT_SHA = "539068e08678c167138fe103f4e76ae1ca67227b05efc6560ff3dbb60e772c77"
ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768

REPAIR_EXTENTS: tuple[tuple[int, int, str], ...] = (
    (0x62D675, 0x62D683, "screen_1"),
    (0x62D688, 0x62D69B, "screen_2"),
    (0x62D6A9, 0x62D6B7, "screen_3"),
    (0x62D6BC, 0x62D6CF, "screen_4"),
)
BAD_PREFIXES = (
    bytes.fromhex("E518A033"),
    bytes.fromhex("E518A034"),
    bytes.fromhex("E518A035"),
    bytes.fromhex("E518A036"),
)

CLEANUP_PATHS = (
    PATCH / "battle_id_command_followup_candidate.wsc",
    ROOT / "sram/battle_id_command_followup_candidate.sav",
    PATCH / "battle_ui_action_labels_candidate.wsc",
    ROOT / "sram/battle_ui_action_labels_candidate.sav",
    CANDIDATE,
    CANDIDATE_SAVE,
    PATCH / "scouting_map_postbattle_dialogue_candidate.wsc",
    ROOT / "sram/scouting_map_postbattle_dialogue_candidate.sav",
    PATCH / "_policy_guard_sheet_test.log",
    PATCH / "_policy_guard_cache_test.log",
    PATCH / "_policy_guard_google_test.log",
)


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require_file(path: Path, size: int, expected_sha: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise PromotionError(f"missing or wrong-size file: {rel(path)}")
    if expected_sha is not None and digest(path) != expected_sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    require_file(temporary, ROM_SIZE, digest(source))
    os.replace(temporary, target)


def stock_base(data: bytes) -> int:
    if len(data) == ORIGINAL_SIZE:
        return 0
    if len(data) == ROM_SIZE:
        return ROM_SIZE - ORIGINAL_SIZE
    raise PromotionError(f"unsupported ROM size: {len(data)}")


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise PromotionError("diff inputs have different sizes")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def covered(run: tuple[int, int], allowed: Iterable[tuple[int, int]]) -> bool:
    lo, hi = run
    return any(lo >= a and hi <= b for a, b in allowed)


def validate() -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, EXPECTED_TIP_SHA)
    require_file(TIP_SAVE, SAVE_SIZE)
    require_file(PARENT, ROM_SIZE, EXPECTED_PARENT_SHA)
    require_file(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require_file(CANDIDATE_SAVE, SAVE_SIZE)
    require_file(ORIGINAL, ORIGINAL_SIZE, EXPECTED_ORIGINAL_SHA)
    require_file(BUILD_REPORT, BUILD_REPORT.stat().st_size, EXPECTED_BUILD_REPORT_SHA)

    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    if report.get("ok") is not True or report.get("published") is not False:
        raise PromotionError("candidate build report is not accepted/unpublished")
    checks = report.get("checks") or {}
    if not checks or not all(bool(value) for value in checks.values()):
        raise PromotionError("candidate build report did not pass every check")
    if str((report.get("candidate") or {}).get("sha256") or "") != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("candidate report binding mismatch")
    if str((report.get("parent_latest_translation_candidate") or {}).get("sha256") or "") != EXPECTED_PARENT_SHA:
        raise PromotionError("parent report binding mismatch")
    if int((report.get("counts") or {}).get("restored_structures") or 0) != 4:
        raise PromotionError("candidate report structure count mismatch")

    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    parent_base = stock_base(parent)
    original_base = stock_base(original)
    allowed = [(len(parent) - 2, len(parent))]
    records: list[dict[str, Any]] = []

    for (logical_start, logical_end, label), bad_prefix in zip(REPAIR_EXTENTS, BAD_PREFIXES):
        candidate_slice = candidate[parent_base + logical_start : parent_base + logical_end]
        original_slice = original[original_base + logical_start : original_base + logical_end]
        parent_slice = parent[parent_base + logical_start : parent_base + logical_end]
        allowed.append((parent_base + logical_start, parent_base + logical_end))
        ok = candidate_slice == original_slice and bad_prefix not in candidate_slice and parent_slice != candidate_slice
        records.append(
            {
                "label": label,
                "logical_start": f"{logical_start:06X}",
                "logical_end_exclusive": f"{logical_end:06X}",
                "candidate_matches_original": candidate_slice == original_slice,
                "bad_ext3_prefix_removed": bad_prefix not in candidate_slice,
                "parent_was_changed": parent_slice != candidate_slice,
                "ok": ok,
            }
        )
    if not all(row["ok"] for row in records):
        raise PromotionError("one or more repaired event structures failed independent validation")

    runs = diff_runs(parent, candidate)
    unaccounted = [run for run in runs if not covered(run, allowed)]
    if unaccounted:
        raise PromotionError(f"candidate differs from cumulative parent outside repair ranges: {unaccounted}")

    return {
        "current_tip": identity(TIP),
        "cumulative_parent": identity(PARENT),
        "candidate": identity(CANDIDATE),
        "original_rom": identity(ORIGINAL),
        "build_report": identity(BUILD_REPORT),
        "user_authorization": "user confirmed scouting_map_event_structure_repair_candidate.wsc works and requested main TIP promotion",
        "independent_repair_checks": records,
        "candidate_parent_diff_runs": [
            {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}", "length": hi - lo}
            for lo, hi in runs
        ],
        "main_saveram_policy": "live main SaveRAM remains untouched",
    }


def postpromotion_audit(backup_rom: Path, save_before: dict[str, Any], candidate_identity: dict[str, Any]) -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    save_after = identity(TIP_SAVE)
    if save_after != save_before:
        raise PromotionError("live main SaveRAM changed during ROM promotion")

    final = TIP.read_bytes()
    original = ORIGINAL.read_bytes()
    final_base = stock_base(final)
    original_base = stock_base(original)
    records: list[dict[str, Any]] = []
    for (logical_start, logical_end, label), bad_prefix in zip(REPAIR_EXTENTS, BAD_PREFIXES):
        final_slice = final[final_base + logical_start : final_base + logical_end]
        original_slice = original[original_base + logical_start : original_base + logical_end]
        records.append(
            {
                "label": label,
                "logical_start": f"{logical_start:06X}",
                "matches_original_structure": final_slice == original_slice,
                "bad_ext3_prefix_removed": bad_prefix not in final_slice,
                "ok": final_slice == original_slice and bad_prefix not in final_slice,
            }
        )
    checks = {
        "tip_matches_verified_candidate_sha": digest(TIP) == candidate_identity["sha256"],
        "all_four_event_structures_restored": len(records) == 4 and all(row["ok"] for row in records),
        "main_saveram_unchanged": save_after == save_before,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP_SHA,
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_scouting_map_event_structure_repair_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_rom": identity(backup_rom),
        "main_saveram_before": save_before,
        "main_saveram_after": save_after,
        "checks": checks,
        "records": records,
    }
    atomic_json(POST_AUDIT, audit)
    return audit


def cleanup(paths: Iterable[Path]) -> dict[str, Any]:
    removed: list[dict[str, Any]] = []
    missing: list[str] = []
    reclaimed = 0
    for path in paths:
        if not path.exists():
            missing.append(rel(path))
            continue
        if not path.is_file():
            raise PromotionError(f"cleanup target is not a file: {rel(path)}")
        item = identity(path)
        path.unlink()
        removed.append(item)
        reclaimed += int(item["size"])
    return {
        "removed": removed,
        "removed_count": len(removed),
        "missing_before_cleanup": missing,
        "reclaimed_bytes": reclaimed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_scouting_map_event_structure_repair"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_file(backup_rom, ROM_SIZE, EXPECTED_TIP_SHA)

    old_tip = identity(TIP)
    candidate_identity = identity(CANDIDATE)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "scouting-map-event-repair-promote")
        audit = postpromotion_audit(backup_rom, save_before, candidate_identity)
    except Exception:
        atomic_copy(backup_rom, TIP, "scouting-map-event-repair-rollback")
        raise

    cleanup_result = cleanup(CLEANUP_PATHS)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_scouting_map_event_structure_repair_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "candidate_before_cleanup": candidate_identity,
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": audit["checks"],
        "main_saveram": {
            "before": save_before,
            "after": identity(TIP_SAVE),
            "action": "left_untouched",
        },
        "cleanup": cleanup_result,
        "translation_source_policy": {
            "path": "data/translation_source_policy.json",
            "action": "legacy machine-translation assets quarantined and blocked from active sheet pipelines"
        }
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
