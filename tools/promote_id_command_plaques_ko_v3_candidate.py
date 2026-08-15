#!/usr/bin/env python3
"""Promote the user-approved seven-adjustment ID-plaque v3 candidate."""
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


ROOT = Path(r"D:\monoeye")
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "id_command_plaques_ko_v3_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/id_command_plaques_ko_v3_candidate.sav"
SPEC = ROOT / "data/id_command_plaque_v3_adjustments_ko.json"
BUILD_REPORT = PATCH / "id_command_plaques_ko_v3_candidate_report.json"
PREVIEW = PATCH / "id_command_plaques_ko_v3_candidate_previews/all_7_before_after.png"
PROMOTION_REPORT = PATCH / "id_command_plaques_ko_v3_promotion_report.json"
POST_AUDIT = PATCH / "id_command_plaques_ko_v3_postpromotion_audit.json"

EXPECTED_TIP_SHA = "9ba9804dac603d84efe75bff6efecfebd2b55ef7bd602671c375f97791f61d75"
EXPECTED_CANDIDATE_SHA = "27874d922b4a0233c7eb27a4da3361e71cd5ce32276fd86f0dca4cccaabcd918"
EXPECTED_SAVE_SHA = "589f47d18cbe245e544f62a92542eedaed87895794aaf072b3071d7442cde4a4"
EXPECTED_SPEC_SHA = "cc4058d7c42c760f0c6cc51ff045bee40deb3445723caf2cbf5ac0c488d96828"
EXPECTED_BUILD_REPORT_SHA = "66ea516e22aca572acde14e5cbd3e4e4d72173675d26b4597dba170faab48ebd"
EXPECTED_PREVIEW_SHA = "26ad54f481f5624b2b5bb7d2db4be0331525ff04cbaf3430d9698fc35b91fbe7"
EXPECTED_CHECKSUM = "5D9E"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ASSET_SIZE = 384
STOCK_BASE = 0x800000
EXPECTED_LOGICALS = {
    0x4C5234,
    0x4C5A54,
    0x4C5BD4,
    0x4CBA2A,
    0x4CBBAA,
    0x4CBD2A,
    0x4CE56A,
}
RESTORED_LOGICALS = {0x4CC1AA, 0x4CC52A}


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and digest(path).lower() != sha.lower():
        raise PromotionError(f"SHA-256 drift: {path}")


def checksum_bytes(data: bytes) -> dict[str, Any]:
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path, expected_size: int, expected_sha: str) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    require(temporary, size=expected_size, sha=expected_sha)
    os.replace(temporary, target)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for offset, (old, new) in enumerate(zip(before, after, strict=True)):
        if old != new and start is None:
            start = offset
        elif old == new and start is not None:
            runs.append((start, offset))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def in_allowlist(start: int, end: int, allowed: list[tuple[int, int]]) -> bool:
    return any(start >= lo and end <= hi for lo, hi in allowed)


def block_equal(before: bytes, after: bytes, logical: int) -> bool:
    start = STOCK_BASE + logical
    return before[start : start + ASSET_SIZE] == after[start : start + ASSET_SIZE]


def validate() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(TIP_SAVE, size=SAVE_SIZE, sha=EXPECTED_SAVE_SHA)
    require(CANDIDATE_SAVE, size=SAVE_SIZE, sha=EXPECTED_SAVE_SHA)
    require(SPEC, sha=EXPECTED_SPEC_SHA)
    require(BUILD_REPORT, sha=EXPECTED_BUILD_REPORT_SHA)
    require(PREVIEW, sha=EXPECTED_PREVIEW_SHA)

    tip = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_checksum = checksum_bytes(candidate)
    if not candidate_checksum["valid"] or candidate_checksum["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum mismatch: {candidate_checksum}")

    build = load_json(BUILD_REPORT)
    if build.get("ok") is not True or not all((build.get("checks") or {}).values()):
        raise PromotionError("candidate build report did not pass every gate")
    if (build.get("candidate") or {}).get("sha256", "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build report candidate binding mismatch")
    if (build.get("candidate") or {}).get("ws_checksum") != EXPECTED_CHECKSUM:
        raise PromotionError("build report checksum binding mismatch")
    if (build.get("parent") or {}).get("sha256", "").lower() != EXPECTED_TIP_SHA:
        raise PromotionError("build report parent binding mismatch")
    if (build.get("paired_saveram") or {}).get("sha256", "").lower() != EXPECTED_SAVE_SHA:
        raise PromotionError("build report SaveRAM binding mismatch")
    counts = build.get("counts") or {}
    if counts.get("adjustments") != 7 or counts.get("diff_runs_including_checksum") != 368:
        raise PromotionError("build report count mismatch")
    if counts.get("changed_bytes_including_checksum") != 949:
        raise PromotionError("build report changed-byte mismatch")

    spec = load_json(SPEC)
    adjustments = spec.get("adjustments") or []
    logicals = {int(row["logical"], 16) for row in adjustments}
    if len(adjustments) != 7 or logicals != EXPECTED_LOGICALS:
        raise PromotionError("v3 adjustment inventory mismatch")
    if logicals & RESTORED_LOGICALS:
        raise PromotionError("restored plaque unexpectedly present in v3 adjustments")

    restored_checks = {
        "foot_bind_matches_current_main": block_equal(tip, candidate, 0x4CC1AA),
        "hp_recovery_matches_current_main": block_equal(tip, candidate, 0x4CC52A),
    }
    if not all(restored_checks.values()):
        raise PromotionError(f"restored plaque mismatch: {restored_checks}")

    allowed = [
        (STOCK_BASE + logical, STOCK_BASE + logical + ASSET_SIZE)
        for logical in sorted(EXPECTED_LOGICALS)
    ]
    allowed.append((ROM_SIZE - 2, ROM_SIZE))
    runs = diff_runs(tip, candidate)
    unexpected = [(start, end) for start, end in runs if not in_allowlist(start, end, allowed)]
    if unexpected or len(runs) != 368 or sum(end - start for start, end in runs) != 949:
        raise PromotionError(f"independent diff audit failed: unexpected={unexpected}")

    return {
        "current_tip": identity(TIP),
        "approved_candidate": identity(CANDIDATE),
        "candidate_checksum": candidate_checksum,
        "main_saveram": identity(TIP_SAVE),
        "candidate_saveram": identity(CANDIDATE_SAVE),
        "spec": identity(SPEC),
        "build_report": identity(BUILD_REPORT),
        "comparison_preview": identity(PREVIEW),
        "build_checks": build["checks"],
        "restored_plaque_checks": restored_checks,
        "independent_diff_audit": {
            "runs": len(runs),
            "changed_bytes": sum(end - start for start, end in runs),
            "unexpected_runs": unexpected,
        },
        "approval_basis": "user confirmed the seven-adjustment v3 test ROM and requested main promotion",
        "promotion_scope": "WSC TIP only; main and candidate SaveRAM remain immutable",
    }


def post_audit(
    backup_rom: Path,
    main_save_before: dict[str, Any],
    candidate_save_before: dict[str, Any],
) -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)
    main = TIP.read_bytes()
    backup = backup_rom.read_bytes()
    tip_checksum = checksum_bytes(main)
    checks = {
        "main_tip_matches_approved_candidate": digest(TIP) == EXPECTED_CANDIDATE_SHA,
        "main_tip_checksum_valid": tip_checksum["valid"] and tip_checksum["stored"] == EXPECTED_CHECKSUM,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP_SHA,
        "main_saveram_unchanged": identity(TIP_SAVE) == main_save_before,
        "candidate_saveram_unchanged": identity(CANDIDATE_SAVE) == candidate_save_before,
        "candidate_rom_unchanged": digest(CANDIDATE) == EXPECTED_CANDIDATE_SHA,
        "spec_unchanged": digest(SPEC) == EXPECTED_SPEC_SHA,
        "build_report_unchanged": digest(BUILD_REPORT) == EXPECTED_BUILD_REPORT_SHA,
        "comparison_preview_unchanged": digest(PREVIEW) == EXPECTED_PREVIEW_SHA,
        "foot_bind_preserved_from_old_main": block_equal(backup, main, 0x4CC1AA),
        "hp_recovery_preserved_from_old_main": block_equal(backup, main, 0x4CC52A),
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    result = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_plaques_ko_v3_candidate.py",
        "ok": True,
        "main_tip": identity(TIP),
        "main_tip_checksum": tip_checksum,
        "approved_candidate": identity(CANDIDATE),
        "rollback_rom": identity(backup_rom),
        "main_saveram_before": main_save_before,
        "main_saveram_after": identity(TIP_SAVE),
        "candidate_saveram_before": candidate_save_before,
        "candidate_saveram_after": identity(CANDIDATE_SAVE),
        "checks": checks,
    }
    atomic_json(POST_AUDIT, result)
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_id_command_plaques_ko_v3"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)

    old_tip = identity(TIP)
    main_save_before = identity(TIP_SAVE)
    candidate_save_before = identity(CANDIDATE_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
        post = post_audit(backup_rom, main_save_before, candidate_save_before)
    except Exception:
        atomic_copy(backup_rom, TIP, ROM_SIZE, EXPECTED_TIP_SHA)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_plaques_ko_v3_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "approved_candidate": identity(CANDIDATE),
        "rollback_rom": identity(backup_rom),
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": post["checks"],
        "saveram_policy": "main and candidate SaveRAM files were preserved byte-identically",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
