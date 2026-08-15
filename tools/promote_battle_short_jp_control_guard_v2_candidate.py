#!/usr/bin/env python3
"""Promote the user-approved bank59 short-line/control-guard V2 candidate."""
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
CANDIDATE = PATCH / "battle_short_jp_control_guard_v2_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_short_jp_control_guard_v2_candidate.sav"
BUILD_REPORT = PATCH / "battle_short_jp_control_guard_v2_candidate_report.json"
AUDIT_REPORT = PATCH / "battle_short_jp_control_guard_v2_candidate_audit.json"
STRUCTURE_REPORT = PATCH / "battle_short_jp_control_guard_v2_structure.json"
FALSE_SEGPTR_REPORT = PATCH / "battle_short_jp_control_guard_v2_false_segptr.json"
USER_VALIDATION = PATCH / "battle_short_jp_control_guard_v2_user_validation.json"
PROMOTION_REPORT = PATCH / "battle_short_jp_control_guard_v2_promotion_report.json"
POST_AUDIT = PATCH / "battle_short_jp_control_guard_v2_postpromotion_audit.json"

EXPECTED_TIP_SHA = "33b77347f1c969c2751b24b3ec3479e63c3b5146df4015cbad3bdc0d7eaab4e1"
EXPECTED_CANDIDATE_SHA = "5bd6ac50ae7a80b922c79dfa43eaa3b43af053005467f53d9a57dc4c8e7444fc"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
CHECKSUM_SIZE = 2
EXPECTED_DIFF_RUNS = (
    (0x00D94E21, 0x00D94E26),
    (0x00D951F9, 0x00D951FD),
    (0x00DF4386, 0x00DF438D),
    (0x00FFFFFE, 0x01000000),
)
EXPECTED_CHANGED_BYTES = 18

CLEANUP_PATHS = (
    PATCH / "battle_short_jp_control_guard_candidate.wsc",
    ROOT / "sram/battle_short_jp_control_guard_candidate.sav",
    PATCH / "battle_short_jp_control_guard_v2_candidate.wsc",
    ROOT / "sram/battle_short_jp_control_guard_v2_candidate.sav",
    ROOT / "tools/build_battle_short_jp_control_guard_candidate.py",
    ROOT / "tools/audit_battle_short_jp_control_guard_candidate.py",
)


class PromotionError(RuntimeError):
    pass


def digest_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


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


def require_file(path: Path, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"wrong-size file: {rel(path)}")
    if expected_sha is not None and digest(path) != expected_sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"JSON root is not an object: {rel(path)}")
    return value


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
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


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-CHECKSUM_SIZE]) & 0xFFFF) == int.from_bytes(
        data[-CHECKSUM_SIZE:], "little"
    )


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise PromotionError("ROM sizes differ")
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


def validate() -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, EXPECTED_TIP_SHA)
    require_file(TIP_SAVE, SAVE_SIZE)
    require_file(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require_file(CANDIDATE_SAVE, SAVE_SIZE)
    for path in (BUILD_REPORT, AUDIT_REPORT, STRUCTURE_REPORT, FALSE_SEGPTR_REPORT, USER_VALIDATION):
        require_file(path)

    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT_REPORT)
    structure = load_json(STRUCTURE_REPORT)
    false_segptr = load_json(FALSE_SEGPTR_REPORT)
    approval = load_json(USER_VALIDATION)

    if build.get("ok") is not True or build.get("published") is not False:
        raise PromotionError("V2 build report status is invalid")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("V2 build report candidate binding mismatch")
    required_build_checks = (
        "short_uses_existing_ext3_phrase",
        "gato_uses_inplace_nested_stock_phrase",
        "inplace_stock_slot_pointer_unchanged",
        "inplace_stock_slot_phrase_exact",
        "existing_stock_components_only",
        "dedicated_stock_reference_exact",
        "control_records_byte_identical_and_bodyless",
        "record_boundaries_preserved",
        "diffs_allowlisted",
        "checksum_valid",
        "main_tip_unchanged",
        "main_saveram_unchanged",
    )
    build_checks = build.get("checks") or {}
    if not all(build_checks.get(name) is True for name in required_build_checks):
        raise PromotionError("V2 build report did not pass every required check")

    if audit.get("ok") is not True or audit.get("published") is not False:
        raise PromotionError("V2 independent audit status is invalid")
    if str((audit.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("V2 independent audit candidate binding mismatch")
    if not all(value is True for value in (audit.get("checks") or {}).values()):
        raise PromotionError("V2 independent audit contains a failed check")

    structure_target = ((structure.get("inputs") or {}).get("target") or {})
    if structure.get("ok") is not True or int(structure.get("issues") or 0) != 0:
        raise PromotionError("bank59 structure report is not clean")
    if str(structure_target.get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("bank59 structure report candidate binding mismatch")

    false_target = ((false_segptr.get("inputs") or {}).get("target") or {})
    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found") or 0) != 0:
        raise PromotionError("false segmented-pointer report is not clean")
    if str(false_target.get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("false segmented-pointer report candidate binding mismatch")

    if approval.get("approved") is not True:
        raise PromotionError("user approval is missing")
    if str(approval.get("candidate_sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("user approval candidate binding mismatch")

    before = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if not checksum_ok(candidate):
        raise PromotionError("candidate WonderSwan checksum is invalid")
    runs = diff_runs(before, candidate)
    if tuple(runs) != EXPECTED_DIFF_RUNS:
        raise PromotionError(f"candidate diff runs drifted: {runs}")
    changed_bytes = sum(right - left for left, right in runs)
    if changed_bytes != EXPECTED_CHANGED_BYTES:
        raise PromotionError(f"candidate changed-byte count drifted: {changed_bytes}")

    base = 0x800000
    exact_bytes = {
        "594E1E": candidate[base + 0x594E1E : base + 0x594E26].hex().upper(),
        "5951F6": candidate[base + 0x5951F6 : base + 0x5951FD].hex().upper(),
        "5951FF": candidate[base + 0x5951FF : base + 0x595203].hex().upper(),
        "595204": candidate[base + 0x595204 : base + 0x595208].hex().upper(),
        "stock_0360": candidate[base + 0x5F4386 : base + 0x5F438D].hex().upper(),
    }
    expected_bytes = {
        "594E1E": "173418E518183001",
        "5951F6": "173418F3600101",
        "5951FF": "17280828",
        "595204": "171C080F",
        "stock_0360": "F58CF206F04400",
    }
    if exact_bytes != expected_bytes:
        raise PromotionError(f"candidate target bytes drifted: {exact_bytes}")

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "candidate_saveram_at_promotion": identity(CANDIDATE_SAVE),
        "main_saveram_before": identity(TIP_SAVE),
        "build_report": identity(BUILD_REPORT),
        "independent_audit": identity(AUDIT_REPORT),
        "structure_report": identity(STRUCTURE_REPORT),
        "false_segmented_pointer_report": identity(FALSE_SEGPTR_REPORT),
        "user_validation": identity(USER_VALIDATION),
        "diff_runs": [
            {"start": f"{left:08X}", "end_exclusive": f"{right:08X}", "length": right - left}
            for left, right in runs
        ],
        "changed_bytes_including_checksum": changed_bytes,
        "candidate_checksum_stored": candidate[-2:].hex().upper(),
        "target_bytes": exact_bytes,
        "candidate_saveram_policy": "candidate SaveRAM is runtime test data and is not copied or hash-gated",
    }


def postpromotion_audit(
    backup_rom: Path,
    save_before: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require_file(backup_rom, ROM_SIZE, EXPECTED_TIP_SHA)
    tip_data = TIP.read_bytes()
    backup_data = backup_rom.read_bytes()
    save_after = identity(TIP_SAVE)
    runs = diff_runs(backup_data, tip_data)
    checks = {
        "tip_matches_verified_candidate_sha": digest(TIP) == EXPECTED_CANDIDATE_SHA,
        "tip_checksum_valid": checksum_ok(tip_data),
        "main_saveram_unchanged": save_after == save_before,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP_SHA,
        "diff_runs_exact": tuple(runs) == EXPECTED_DIFF_RUNS,
        "changed_bytes_exact": sum(right - left for left, right in runs) == EXPECTED_CHANGED_BYTES,
        "user_validation_bound": validation["user_validation"]["sha256"] == digest(USER_VALIDATION),
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_short_jp_control_guard_v2_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_rom": identity(backup_rom),
        "main_saveram_before": save_before,
        "main_saveram_after": save_after,
        "checks": checks,
        "diff_runs": [
            {"start": f"{left:08X}", "end_exclusive": f"{right:08X}", "length": right - left}
            for left, right in runs
        ],
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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_battle_short_jp_control_guard_v2"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_file(backup_rom, ROM_SIZE, EXPECTED_TIP_SHA)

    old_tip = identity(TIP)
    candidate_identity = identity(CANDIDATE)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "battle-short-jp-control-guard-v2-promote")
        audit = postpromotion_audit(backup_rom, save_before, validation)
    except Exception:
        atomic_copy(backup_rom, TIP, "battle-short-jp-control-guard-v2-rollback")
        raise

    cleanup_result = cleanup(CLEANUP_PATHS)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_short_jp_control_guard_v2_candidate.py",
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
        "cleanup": cleanup_result,
        "main_saveram_policy": "live main SaveRAM remained untouched; candidate SaveRAM was never copied",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
