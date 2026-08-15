#!/usr/bin/env python3
"""Promote the user-validated 5C:B5C2 ID-command table repair.

The transaction backs up the current main ROM and live SaveRAM snapshot, atomically
replaces only the main ROM, verifies the protected structured tables, leaves the
live SaveRAM untouched, and removes obsolete Sig ID diagnostic ROM/SaveRAM pairs.
"""
from __future__ import annotations

import argparse
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

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from structured_token_write_guard import (  # noqa: E402
    PROTECTED_TABLES,
    logical_slice,
    validate_protected_table,
)

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "sig_id_5cb5c2_table_restore_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/sig_id_5cb5c2_table_restore_candidate.sav"
BUILD_REPORT = PATCH / "sig_id_5cb5c2_table_restore_report.json"
FALSE_SEGPTR = PATCH / "sig_id_5cb5c2_table_restore_false_segptr.json"
STRUCTURED_GUARD = PATCH / "sig_id_5cb5c2_table_restore_structured_guard.json"
USER_VALIDATION = PATCH / "sig_id_5cb5c2_table_restore_user_validation.json"
POST_AUDIT = PATCH / "sig_id_5cb5c2_table_restore_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "sig_id_5cb5c2_table_restore_promotion_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TIP = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"
EXPECTED_CANDIDATE = "b24d72bcc18058ad248fbfdb9359948bf1bc3e06e23db6eba89623a143719180"
EXPECTED_CHECKSUM = "BFC5"
EXPECTED_USER_STATUS = "user_runtime_validated_sig_id_5cb5c2_table_restore"
EXPECTED_AUTH = "promote sig_id_5cb5c2_table_restore_candidate to main TIP and add recurrence guards"
TABLE_LOGICAL = 0x5CB5C2
EXPECTED_BAD = bytes.fromhex("F573")
EXPECTED_GOOD = bytes.fromhex("F585")
EXPECTED_DIFF_RUNS = ((0x00DCB5C3, 0x00DCB5C4), (0x00FFFFFE, 0x00FFFFFF))


class PromotionError(RuntimeError):
    pass


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, size: int, expected: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise PromotionError(f"missing or wrong-size file: {rel(path)}")
    if expected is not None and digest(path).lower() != expected.lower():
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing evidence: {rel(path)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root must be object: {rel(path)}")
    return value


def atomic_copy(source: Path, target: Path, *, size: int, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    require(temporary, size, digest(source))
    os.replace(temporary, target)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def diff_runs(left: bytes, right: bytes) -> tuple[tuple[int, int], ...]:
    if len(left) != len(right):
        raise PromotionError("ROM sizes differ")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(left)))
    return tuple(runs)


def checksum_state(data: bytes) -> tuple[str, bool]:
    copy = bytearray(data)
    checksum = update_ws_checksum(copy)
    return f"{checksum:04X}", bytes(copy) == data


def protected_table_state(data: bytes) -> list[dict[str, Any]]:
    return [validate_protected_table(data, table) for table in PROTECTED_TABLES]


def validate_evidence() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE)
    require(CANDIDATE_SAVE, SAVE_SIZE)

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    build = load_object(BUILD_REPORT)
    false_segptr = load_object(FALSE_SEGPTR)
    structured = load_object(STRUCTURED_GUARD)
    user = load_object(USER_VALIDATION)

    if build.get("ok") is not True:
        raise PromotionError("candidate build report failed")
    if str((((build.get("output") or {}).get("rom") or {}).get("sha256") or "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("candidate build report binding drifted")
    checks = build.get("checks") or {}
    if not checks or not all(value is True for value in checks.values()):
        raise PromotionError("candidate build checks are incomplete")

    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit failed")
    if str(((((false_segptr.get("inputs") or {}).get("target") or {}).get("sha256")) or "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("false segmented-pointer audit binding drifted")

    if structured.get("ok") is not True or int(structured.get("issue_count", -1)) != 0:
        raise PromotionError("structured-table guard report failed")
    if str((((structured.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("structured-table guard binding drifted")

    if user.get("status") != EXPECTED_USER_STATUS or user.get("authorization") != EXPECTED_AUTH:
        raise PromotionError("explicit user authorization is missing")
    if str(((user.get("candidate") or {}).get("sha256") or "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("user validation binding drifted")
    runtime = user.get("runtime_result") or {}
    if not all(runtime.get(key) is True for key in (
        "candidate_confirmed_good",
        "sig_wedna_z_id_command_regression_fixed",
        "event_error_absent",
        "approved_for_main_tip",
    )):
        raise PromotionError("user runtime validation is incomplete")

    if logical_slice(parent, TABLE_LOGICAL, 2) != EXPECTED_BAD:
        raise PromotionError("current main no longer has the expected damaged table value")
    if logical_slice(candidate, TABLE_LOGICAL, 2) != EXPECTED_GOOD:
        raise PromotionError("candidate does not restore the expected table value")
    if diff_runs(parent, candidate) != EXPECTED_DIFF_RUNS:
        raise PromotionError(f"candidate diff scope drifted: {diff_runs(parent, candidate)}")
    checksum, exact = checksum_state(candidate)
    if checksum != EXPECTED_CHECKSUM or not exact:
        raise PromotionError("candidate checksum is not exact")
    table_checks = protected_table_state(candidate)
    if not all(row["ok"] for row in table_checks):
        raise PromotionError("candidate protected-table checks failed")

    return {
        "current_tip": identity(TIP),
        "current_saveram": identity(TIP_SAVE),
        "candidate": identity(CANDIDATE),
        "candidate_test_saveram": {**identity(CANDIDATE_SAVE), "action": "not_copied"},
        "build_report": identity(BUILD_REPORT),
        "false_segptr_report": identity(FALSE_SEGPTR),
        "structured_guard_report": identity(STRUCTURED_GUARD),
        "user_validation": identity(USER_VALIDATION),
        "checksum": checksum,
        "diff_runs": [
            {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}"}
            for lo, hi in EXPECTED_DIFF_RUNS
        ],
        "protected_tables": table_checks,
    }


def cleanup_diagnostics() -> dict[str, Any]:
    removed: list[dict[str, Any]] = []
    for pattern in ("sig_id*.wsc", "sig_id*.sav"):
        directory = ROOT / "sram" if pattern.endswith(".sav") else PATCH
        for path in sorted(directory.glob(pattern)):
            if not path.is_file():
                continue
            info = identity(path)
            path.unlink()
            removed.append(info)
    return {
        "removed_files": removed,
        "removed_count": len(removed),
        "bytes_reclaimed": sum(int(row["size"]) for row in removed),
        "preserved": [
            "all JSON audit/build/promotion reports",
            "all reproducibility builders and tests",
            "main TIP and live SaveRAM",
            "timestamped rollback ROM and SaveRAM snapshot",
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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_sig_id_5cb5c2_table_restore"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    atomic_copy(TIP, backup_rom, size=ROM_SIZE, label="backup_rom")
    atomic_copy(TIP_SAVE, backup_save, size=SAVE_SIZE, label="backup_save")
    if digest(backup_rom) != EXPECTED_TIP:
        raise PromotionError("rollback ROM verification failed")
    if digest(backup_save) != save_before["sha256"]:
        raise PromotionError("rollback SaveRAM snapshot verification failed")

    candidate_identity = identity(CANDIDATE)
    atomic_copy(CANDIDATE, TIP, size=ROM_SIZE, label="promote")

    published = TIP.read_bytes()
    checksum, checksum_exact = checksum_state(published)
    post_tables = protected_table_state(published)
    post_checks = {
        "tip_matches_candidate": digest(TIP) == EXPECTED_CANDIDATE,
        "table_entry_restored": logical_slice(published, TABLE_LOGICAL, 2) == EXPECTED_GOOD,
        "all_protected_tables_exact": all(row["ok"] for row in post_tables),
        "checksum_exact": checksum_exact and checksum == EXPECTED_CHECKSUM,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP,
        "rollback_saveram_snapshot_preserved": digest(backup_save) == save_before["sha256"],
        "live_main_saveram_unchanged": identity(TIP_SAVE) == save_before,
        "candidate_saveram_not_copied": digest(TIP_SAVE) == save_before["sha256"],
    }
    if not all(post_checks.values()):
        raise PromotionError("post-promotion audit failed: " + json.dumps(post_checks))

    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_sig_id_5cb5c2_table_restore_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "main_saveram": identity(TIP_SAVE),
        "rollback_rom": identity(backup_rom),
        "rollback_saveram_snapshot": identity(backup_save),
        "checksum": checksum,
        "table_entry": logical_slice(published, TABLE_LOGICAL, 2).hex().upper(),
        "protected_tables": post_tables,
        "checks": post_checks,
    }
    atomic_json(POST_AUDIT, post)

    cleanup = cleanup_diagnostics()
    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_sig_id_5cb5c2_table_restore_candidate.py",
        "ok": True,
        "status": "promoted_user_validated_sig_id_5cb5c2_table_restore",
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
