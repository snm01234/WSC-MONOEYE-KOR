#!/usr/bin/env python3
"""Promote the cumulative runtime-text residual Korean candidate to main TIP.

Candidate lineage:
  screen-residual main
  → ID/scenario all candidate
  → voice duplicate-proven (+3)
  → residual voice dialogue KO batch
  → remaining quarantine stub KO

ROM-only promotion. Live SaveRAM is left untouched. Intermediate test ROM/SaveRAM
pairs from this workstream are removed after a successful post-promotion audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import update_ws_checksum  # noqa: E402
from structured_token_write_guard import PROTECTED_TABLES, validate_protected_table  # noqa: E402

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "runtime_text_id_scenario_voice_proven_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/runtime_text_id_scenario_voice_proven_candidate.sav"
FAMILY = PATCH / "runtime_text_residual_families_report.json"
VOICE_KO_REPORT = PATCH / "runtime_text_residual_voice_ko_candidate_report.json"
STUB_REPORT = PATCH / "runtime_text_residual_quarantine_stub_report.json"
FALSE_SEGPTR = PATCH / "runtime_text_residual_voice_id_ko_false_segptr.json"
STRUCTURED = PATCH / "runtime_text_residual_voice_id_ko_structured_tables.json"
USER_VALIDATION = PATCH / "runtime_text_residual_voice_id_ko_user_validation.json"
ID_SHEET = SCRIPT / "runtime_text_residual_id_bundle_sheet.csv"
VOICE_SHEET = SCRIPT / "runtime_text_residual_voice_sheet.csv"
POST_AUDIT = PATCH / "runtime_text_residual_voice_id_ko_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "runtime_text_residual_voice_id_ko_promotion_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_PARENT = "03a6f1c42e9fff43a143c5bc1dd45a0fa23abc7be02e61c207b9e877facfc0d8"
EXPECTED_CANDIDATE = "525acad1b9b8b8487fd47b6581897150fcc4da7ed2cd81a7c8c37112f267bc09"
EXPECTED_CHECKSUM = "1EE3"
EXPECTED_DIFF_RUNS = 9060
EXPECTED_CHANGED_BYTES = 100022
EXPECTED_USER_STATUS = "user_runtime_validated_runtime_text_residual_voice_id_ko_candidate"
EXPECTED_AUTH = "promote runtime_text_id_scenario_voice_proven_candidate to main TIP"

CLEANUP_ROM_PAIRS = (
    PATCH / "runtime_text_id_scenario_all_candidate.wsc",
    ROOT / "sram/runtime_text_id_scenario_all_candidate.sav",
    PATCH / "runtime_text_id_scenario_voice_proven_candidate.wsc",
    ROOT / "sram/runtime_text_id_scenario_voice_proven_candidate.sav",
)
CLEANUP_SCRIPT_SHEETS = (
    SCRIPT / "runtime_text_id_scenario_all_candidate_id_sheet.csv",
    SCRIPT / "runtime_text_id_scenario_all_candidate_prefixed_sheet.csv",
    SCRIPT / "runtime_text_id_scenario_all_candidate_voice_sheet.csv",
    SCRIPT / "runtime_text_id_scenario_voice_proven_candidate_id_sheet.csv",
    SCRIPT / "runtime_text_id_scenario_voice_proven_candidate_prefixed_sheet.csv",
    SCRIPT / "runtime_text_id_scenario_voice_proven_candidate_voice_diagnostic_sheet.csv",
    SCRIPT / "runtime_text_screen_residual_candidate_dialogue.csv",
    SCRIPT / "runtime_text_screen_residual_candidate_id.csv",
    SCRIPT / "runtime_text_screen_residual_candidate_voice.csv",
)


class PromotionError(RuntimeError):
    pass


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha(path)}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"wrong size: {rel(path)}")
    if expected_sha is not None and sha(path).lower() != expected_sha.lower():
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def load_json(path: Path) -> dict[str, Any]:
    require(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root is not object: {rel(path)}")
    return value


def atomic_copy(source: Path, target: Path, *, size: int, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    require(temporary, size=size, expected_sha=sha(source))
    os.replace(temporary, target)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def diff_runs(left: bytes, right: bytes) -> list[tuple[int, int]]:
    if len(left) != len(right):
        raise PromotionError("ROM sizes differ")
    output: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            output.append((start, index))
            start = None
    if start is not None:
        output.append((start, len(left)))
    return output


def checksum_state(data: bytes) -> tuple[str, bool]:
    scratch = bytearray(data)
    value = update_ws_checksum(scratch)
    return f"{value:04X}", bytes(scratch) == data


def sheet_class_counts(path: Path) -> dict[str, int]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("classification") or "")
        counts[key] = counts.get(key, 0) + 1
    return counts


def validate_evidence() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_PARENT)
    require(TIP_SAVE, size=SAVE_SIZE)
    require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE)
    require(CANDIDATE_SAVE, size=SAVE_SIZE)

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    family = load_json(FAMILY)
    voice_ko = load_json(VOICE_KO_REPORT)
    stub = load_json(STUB_REPORT)
    segptr = load_json(FALSE_SEGPTR)
    structured = load_json(STRUCTURED)
    user = load_json(USER_VALIDATION)

    if family.get("ok") is not True:
        raise PromotionError("family residual report failed")
    tip_sha = str(((family.get("inputs") or {}).get("tip") or {}).get("sha256") or "").lower()
    if tip_sha != EXPECTED_CANDIDATE:
        raise PromotionError("family report is not bound to candidate")
    counts = family.get("counts") or {}
    if int(counts.get("all_residuals", -1)) != 0:
        raise PromotionError("candidate still has actionable residuals")
    if int(counts.get("voice_boundary_unproven_quarantine", -1)) != 0:
        raise PromotionError("candidate still has voice quarantine JP")
    by_family = counts.get("by_family") or {}
    id_family = by_family.get("id_command_bundle") or {}
    if int(id_family.get("residuals", -1)) != 0:
        raise PromotionError("ID residuals remain")
    id_classes = (id_family.get("by_classification") or {})
    if "unchanged_japanese_record" in id_classes:
        raise PromotionError("ID unchanged_japanese_record remains in family report")

    if voice_ko.get("ok") is not True or stub.get("ok") is not True:
        raise PromotionError("voice KO / stub apply reports failed")
    stub_rom = str(((stub.get("outputs") or {}).get("rom") or {}).get("sha256") or "").lower()
    if stub_rom != EXPECTED_CANDIDATE:
        raise PromotionError("stub report is not bound to candidate")

    if int(segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer sites remain")
    if structured.get("ok") is not True or int(structured.get("issue_count", -1)) != 0:
        raise PromotionError("structured-table audit failed")
    structured_sha = str(((structured.get("inputs") or {}).get("target") or {}).get("sha256") or "").lower()
    if structured_sha != EXPECTED_CANDIDATE:
        raise PromotionError("structured-table audit binding drift")

    if user.get("status") != EXPECTED_USER_STATUS or user.get("authorization") != EXPECTED_AUTH:
        raise PromotionError("explicit user authorization missing")
    if str((user.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("user validation candidate binding drift")
    runtime = user.get("runtime_result") or {}
    required = (
        "no_obvious_runtime_errors_observed",
        "id_command_unchanged_japanese_cleared",
        "voice_quarantine_japanese_cleared_except_placeholders",
        "approved_for_main_tip",
    )
    if not all(runtime.get(key) is True for key in required):
        raise PromotionError("user runtime approval incomplete")
    if runtime.get("all_residual_records_runtime_tested") is not False:
        raise PromotionError("runtime scope must not be overstated")

    id_counts = sheet_class_counts(ID_SHEET)
    voice_counts = sheet_class_counts(VOICE_SHEET)
    if id_counts.get("unchanged_japanese_record", 0) != 0:
        raise PromotionError("ID sheet still lists unchanged_japanese_record")
    if voice_counts.get("voice_boundary_unproven_quarantine", 0) != 0:
        raise PromotionError("voice sheet still lists quarantine")

    runs = diff_runs(parent, candidate)
    changed = sum(hi - lo for lo, hi in runs)
    if len(runs) != EXPECTED_DIFF_RUNS or changed != EXPECTED_CHANGED_BYTES:
        raise PromotionError(f"candidate diff drift: runs={len(runs)} bytes={changed}")
    checksum, exact = checksum_state(candidate)
    if checksum != EXPECTED_CHECKSUM or not exact:
        raise PromotionError("candidate checksum invalid")
    tables = [validate_protected_table(candidate, table) for table in PROTECTED_TABLES]
    if not all(row.get("ok") is True for row in tables):
        raise PromotionError("protected structured table check failed")

    return {
        "current_tip": identity(TIP),
        "current_saveram": identity(TIP_SAVE),
        "candidate": identity(CANDIDATE),
        "candidate_saveram": {**identity(CANDIDATE_SAVE), "action": "not_copied"},
        "family_report": identity(FAMILY),
        "voice_ko_report": identity(VOICE_KO_REPORT),
        "stub_report": identity(STUB_REPORT),
        "false_segptr_report": identity(FALSE_SEGPTR),
        "structured_report": identity(STRUCTURED),
        "user_validation": identity(USER_VALIDATION),
        "id_sheet_classes": id_counts,
        "voice_sheet_classes": voice_counts,
        "diff_runs": len(runs),
        "changed_bytes": changed,
        "checksum": checksum,
        "protected_tables": tables,
        "family_counts": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate_evidence()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    save_before = identity(TIP_SAVE)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_runtime_text_residual_voice_id_ko"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    atomic_copy(TIP, backup_rom, size=ROM_SIZE, label="backup_rom")
    atomic_copy(TIP_SAVE, backup_save, size=SAVE_SIZE, label="backup_save")
    require(backup_rom, size=ROM_SIZE, expected_sha=EXPECTED_PARENT)
    require(backup_save, size=SAVE_SIZE, expected_sha=save_before["sha256"])

    candidate_before = identity(CANDIDATE)
    atomic_copy(CANDIDATE, TIP, size=ROM_SIZE, label="promote")
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE)

    published = TIP.read_bytes()
    checksum, checksum_exact = checksum_state(published)
    tables = [validate_protected_table(published, table) for table in PROTECTED_TABLES]
    post_checks = {
        "tip_matches_candidate": sha(TIP) == EXPECTED_CANDIDATE,
        "checksum_exact": checksum_exact and checksum == EXPECTED_CHECKSUM,
        "protected_tables_exact": all(row.get("ok") is True for row in tables),
        "rollback_rom_exact": sha(backup_rom) == EXPECTED_PARENT,
        "rollback_saveram_snapshot_exact": sha(backup_save) == save_before["sha256"],
        "live_saveram_unchanged": identity(TIP_SAVE) == save_before,
        "candidate_saveram_not_copied": sha(TIP_SAVE) == save_before["sha256"],
    }
    if not all(post_checks.values()):
        raise PromotionError("post-promotion audit failed: " + json.dumps(post_checks))

    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_text_residual_voice_id_ko_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "live_saveram": identity(TIP_SAVE),
        "rollback_rom": identity(backup_rom),
        "rollback_saveram_snapshot": identity(backup_save),
        "checksum": checksum,
        "protected_tables": tables,
        "checks": post_checks,
    }
    atomic_json(POST_AUDIT, post)

    removed: list[dict[str, Any]] = []
    for path in list(CLEANUP_ROM_PAIRS) + list(CLEANUP_SCRIPT_SHEETS):
        if path.is_file():
            info = identity(path)
            path.unlink()
            removed.append(info)

    # Remove obsolete intermediate candidate JSON dumps (keep final gate reports).
    obsolete_globs = (
        "runtime_text_id_scenario_all_candidate_*.json",
        "runtime_text_id_scenario_all_parent_*.json",
        "runtime_text_id_scenario_voice_proven_candidate_*.json",
        "runtime_text_residual_voice_ko_post_families_report.json",
    )
    for pattern in obsolete_globs:
        for path in sorted(PATCH.glob(pattern)):
            if path.name in {
                PROMOTION_REPORT.name,
                POST_AUDIT.name,
                USER_VALIDATION.name,
                FALSE_SEGPTR.name,
                STRUCTURED.name,
                FAMILY.name,
                VOICE_KO_REPORT.name,
                STUB_REPORT.name,
            }:
                continue
            info = identity(path)
            path.unlink()
            removed.append(info)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_text_residual_voice_id_ko_candidate.py",
        "ok": True,
        "status": "promoted_user_validated_runtime_text_residual_voice_id_ko_candidate",
        "validation": validation,
        "published_tip": identity(TIP),
        "live_saveram": identity(TIP_SAVE),
        "candidate_before_cleanup": candidate_before,
        "rollback": {
            "directory": rel(backup_dir),
            "rom": identity(backup_rom),
            "saveram_snapshot": identity(backup_save),
        },
        "post_audit": identity(POST_AUDIT),
        "cleanup": {
            "removed": removed,
            "removed_count": len(removed),
            "bytes_reclaimed": sum(int(row["size"]) for row in removed),
            "preserved": [
                rel(FAMILY),
                rel(VOICE_KO_REPORT),
                rel(STUB_REPORT),
                rel(FALSE_SEGPTR),
                rel(STRUCTURED),
                rel(USER_VALIDATION),
                rel(POST_AUDIT),
                rel(PROMOTION_REPORT),
                rel(ID_SHEET),
                rel(VOICE_SHEET),
                "data/runtime_text_residual_new_ko_id_batch01.json",
                "data/runtime_text_residual_new_ko_id_batch02.json",
                "data/runtime_text_residual_new_ko_id_batch03.json",
                "data/runtime_text_residual_new_ko_prefixed_dialogue.json",
                "data/runtime_text_residual_new_ko_voice_batch01.json",
                "tools/build_runtime_text_residual_voice_ko_candidate.py",
                "tools/build_runtime_text_residual_quarantine_stub_candidate.py",
                "tools/promote_runtime_text_residual_voice_id_ko_candidate.py",
            ],
        },
    }
    atomic_json(PROMOTION_REPORT, promotion)
    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
