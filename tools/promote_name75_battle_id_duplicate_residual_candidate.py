#!/usr/bin/env python3
"""Promote the user-validated 128-record battle/ID duplicate translation candidate.

The transaction verifies candidate-bound evidence, backs up the current ROM and
live SaveRAM snapshot, atomically replaces only the ROM, rechecks the exact
target/diff/checksum scope, leaves live SaveRAM untouched, and removes the
obsolete candidate ROM/SaveRAM pair after a successful post-promotion audit.
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

from monoeye_rom import read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from structured_token_write_guard import PROTECTED_TABLES, validate_protected_table  # noqa: E402

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "name75_battle_id_duplicate_residual_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/name75_battle_id_duplicate_residual_candidate.sav"
SHEET = SCRIPT / "name75_battle_id_duplicate_residual_sheet.csv"
BUILD_REPORT = PATCH / "name75_battle_id_duplicate_residual_candidate_report.json"
AUDIT_REPORT = PATCH / "name75_battle_id_duplicate_residual_candidate_audit.json"
RESIDUAL_REPORT = PATCH / "name75_battle_id_duplicate_residual_candidate_residual_audit.json"
FALSE_SEGPTR = PATCH / "name75_battle_id_duplicate_residual_candidate_false_segptr.json"
STRUCTURED = PATCH / "name75_battle_id_duplicate_residual_candidate_structured_tables.json"
USER_VALIDATION = PATCH / "name75_battle_id_duplicate_residual_user_validation.json"
POST_AUDIT = PATCH / "name75_battle_id_duplicate_residual_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "name75_battle_id_duplicate_residual_promotion_report.json"
EMPTY_RESIDUAL_SHEET = SCRIPT / "name75_battle_id_duplicate_residual_candidate_residual_sheet.csv"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_PARENT = "b24d72bcc18058ad248fbfdb9359948bf1bc3e06e23db6eba89623a143719180"
EXPECTED_CANDIDATE = "29d096e6462194e226b0895a43016d30b38056c1088bffe925571ac8e466b9ea"
EXPECTED_CHECKSUM = "41CD"
EXPECTED_RECORDS = 128
EXPECTED_PHRASES = 113
EXPECTED_CHANGED_BYTES = 1241
EXPECTED_RUNS = 132
EXPECTED_USER_STATUS = "user_runtime_validated_name75_battle_id_duplicate_residual_candidate"
EXPECTED_AUTH = "promote name75_battle_id_duplicate_residual_candidate to main TIP"
PREFIX = bytes.fromhex("173418")


class PromotionError(RuntimeError):
    pass


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha(path)}


def require(path: Path, size: int | None = None, expected: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"wrong size: {rel(path)}")
    if expected is not None and sha(path).lower() != expected.lower():
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def load_json(path: Path) -> dict[str, Any]:
    require(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root is not object: {rel(path)}")
    return value


def atomic_copy(source: Path, target: Path, *, expected_size: int, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    require(temporary, expected_size, sha(source))
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
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            result.append((start, index))
            start = None
    if start is not None:
        result.append((start, len(left)))
    return result


def covered(run: tuple[int, int], extents: list[tuple[int, int]]) -> bool:
    lo, hi = run
    return any(start <= lo and hi <= end for start, end in extents)


def checksum_state(data: bytes) -> tuple[str, bool]:
    scratch = bytearray(data)
    value = update_ws_checksum(scratch)
    return f"{value:04X}", bytes(scratch) == data


def load_sheet(parent: bytes, candidate: bytes) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    with SHEET.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_RECORDS:
        raise PromotionError(f"sheet population drift: {len(rows)}")
    if len({row["jp"] for row in rows}) != EXPECTED_PHRASES:
        raise PromotionError("sheet phrase population drift")

    base_parent = stock_base(parent)
    base_candidate = stock_base(candidate)
    extents: list[tuple[int, int]] = []
    intervals: list[tuple[int, int]] = []
    for row in rows:
        record = int(row["record_start"], 16)
        body_start = int(row["body_start"], 16)
        capacity = int(row["body_capacity"])
        body = bytes.fromhex(row["body_hex"])
        if body_start != record + len(PREFIX) or len(body) != capacity:
            raise PromotionError(f"sheet record contract drift at {record:06X}")
        before = read_encoded_z_safe(parent, base_parent + record, max_len=128)
        after = read_encoded_z_safe(candidate, base_candidate + record, max_len=128)
        if before is None or after is None:
            raise PromotionError(f"unreadable target record {record:06X}")
        before_payload, before_term = bytes(before[0]), int(before[1])
        after_payload, after_term = bytes(after[0]), int(after[1])
        if before_payload != PREFIX + body:
            raise PromotionError(f"parent payload drift at {record:06X}")
        if not after_payload.startswith(PREFIX):
            raise PromotionError(f"candidate prefix drift at {record:06X}")
        if len(after_payload) != len(before_payload) or after_term - base_candidate != before_term - base_parent:
            raise PromotionError(f"candidate shape drift at {record:06X}")
        if candidate[after_term] != 0:
            raise PromotionError(f"candidate terminator missing at {record:06X}")
        if candidate.find(PREFIX + body + b"\x00", base_candidate + 0x5C0000, base_candidate + 0x5D0000) >= 0:
            raise PromotionError(f"original duplicate pattern remains for {row['jp']!r}")
        extent = (base_candidate + body_start, base_candidate + body_start + capacity)
        if any(not (extent[1] <= lo or hi <= extent[0]) for lo, hi in intervals):
            raise PromotionError(f"overlapping target at {record:06X}")
        intervals.append(extent)
        extents.append(extent)
    return rows, extents


def validate_evidence() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_PARENT)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE)
    require(CANDIDATE_SAVE, SAVE_SIZE)
    require(SHEET)

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT_REPORT)
    residual = load_json(RESIDUAL_REPORT)
    segptr = load_json(FALSE_SEGPTR)
    structured = load_json(STRUCTURED)
    user = load_json(USER_VALIDATION)

    if build.get("ok") is not True or str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("build report failed or is not candidate-bound")
    if int((build.get("counts") or {}).get("target_records") or -1) != EXPECTED_RECORDS:
        raise PromotionError("build target count drift")
    build_checks = build.get("checks") or {}
    if not build_checks or not all(value is True for value in build_checks.values()):
        raise PromotionError("build checks incomplete")

    if audit.get("ok") is not True or str(((audit.get("inputs") or {}).get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("independent audit failed or is not candidate-bound")
    audit_checks = audit.get("checks") or {}
    if not audit_checks or not all(value is True for value in audit_checks.values()):
        raise PromotionError("independent audit checks incomplete")

    counts = residual.get("counts") or {}
    if residual.get("ok") is not True or int(counts.get("new_residual_records", -1)) != 0:
        raise PromotionError("candidate residual audit failed")
    if str(((residual.get("inputs") or {}).get("tip") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("residual audit binding drift")

    if int(segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    if str(((segptr.get("inputs") or {}).get("target") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("false segmented-pointer binding drift")

    if structured.get("ok") is not True or int(structured.get("issue_count", -1)) != 0:
        raise PromotionError("structured-table audit failed")
    if str(((structured.get("inputs") or {}).get("target") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("structured-table binding drift")

    if user.get("status") != EXPECTED_USER_STATUS or user.get("authorization") != EXPECTED_AUTH:
        raise PromotionError("explicit user validation/authorization missing")
    if str((user.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        raise PromotionError("user validation binding drift")
    runtime = user.get("runtime_result") or {}
    if runtime.get("screen_proven_sig_z_id_command_translation_good") is not True or runtime.get("approved_for_main_tip") is not True:
        raise PromotionError("user runtime approval incomplete")
    if runtime.get("all_128_records_runtime_tested") is not False:
        raise PromotionError("runtime scope must not be overstated")

    rows, target_extents = load_sheet(parent, candidate)
    runs = diff_runs(parent, candidate)
    allowed = target_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [run for run in runs if not covered(run, allowed)]
    changed = sum(hi - lo for lo, hi in runs)
    if len(runs) != EXPECTED_RUNS or changed != EXPECTED_CHANGED_BYTES or unaccounted:
        raise PromotionError(
            f"candidate diff scope drift: runs={len(runs)} bytes={changed} unaccounted={unaccounted[:5]}"
        )
    checksum, exact = checksum_state(candidate)
    if checksum != EXPECTED_CHECKSUM or not exact:
        raise PromotionError("candidate checksum invalid")
    tables = [validate_protected_table(candidate, table) for table in PROTECTED_TABLES]
    if not all(row.get("ok") is True for row in tables):
        raise PromotionError("protected table verification failed")

    return {
        "current_tip": identity(TIP),
        "current_saveram": identity(TIP_SAVE),
        "candidate": identity(CANDIDATE),
        "candidate_saveram": {**identity(CANDIDATE_SAVE), "action": "not_copied"},
        "sheet": identity(SHEET),
        "build_report": identity(BUILD_REPORT),
        "audit_report": identity(AUDIT_REPORT),
        "residual_report": identity(RESIDUAL_REPORT),
        "false_segptr_report": identity(FALSE_SEGPTR),
        "structured_report": identity(STRUCTURED),
        "user_validation": identity(USER_VALIDATION),
        "records": len(rows),
        "unique_phrases": len({row["jp"] for row in rows}),
        "diff_runs": len(runs),
        "changed_bytes": changed,
        "checksum": checksum,
        "protected_tables": tables,
    }


def cleanup_candidate_pair() -> dict[str, Any]:
    removed: list[dict[str, Any]] = []
    for path in (CANDIDATE, CANDIDATE_SAVE, EMPTY_RESIDUAL_SHEET):
        if not path.is_file():
            continue
        info = identity(path)
        path.unlink()
        removed.append(info)
    return {
        "removed": removed,
        "removed_count": len(removed),
        "bytes_reclaimed": sum(int(row["size"]) for row in removed),
        "preserved": [
            rel(SHEET),
            rel(BUILD_REPORT),
            rel(AUDIT_REPORT),
            rel(RESIDUAL_REPORT),
            rel(FALSE_SEGPTR),
            rel(STRUCTURED),
            rel(USER_VALIDATION),
            rel(POST_AUDIT),
            rel(PROMOTION_REPORT),
        ],
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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_name75_battle_id_duplicate_residual"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    atomic_copy(TIP, backup_rom, expected_size=ROM_SIZE, label="backup_rom")
    atomic_copy(TIP_SAVE, backup_save, expected_size=SAVE_SIZE, label="backup_save")
    require(backup_rom, ROM_SIZE, EXPECTED_PARENT)
    require(backup_save, SAVE_SIZE, save_before["sha256"])

    candidate_identity = identity(CANDIDATE)
    atomic_copy(CANDIDATE, TIP, expected_size=ROM_SIZE, label="promote")

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
        "generated_by": "tools/promote_name75_battle_id_duplicate_residual_candidate.py",
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

    cleanup = cleanup_candidate_pair()
    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_name75_battle_id_duplicate_residual_candidate.py",
        "ok": True,
        "status": "promoted_user_validated_name75_battle_id_duplicate_residual_candidate",
        "validation": validation,
        "published_tip": identity(TIP),
        "live_saveram": identity(TIP_SAVE),
        "candidate_before_cleanup": candidate_identity,
        "rollback": {
            "directory": rel(backup_dir),
            "rom": identity(backup_rom),
            "saveram_snapshot": identity(backup_save),
        },
        "post_audit": identity(POST_AUDIT),
        "cleanup": cleanup,
    }
    atomic_json(PROMOTION_REPORT, promotion)
    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
