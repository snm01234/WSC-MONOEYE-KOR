#!/usr/bin/env python3
"""Promote the accepted P2 fix0208 candidate and clean disposable P2 test files.

Dry-run is the default. Pass --commit to create a verified rollback backup,
atomically replace the main TIP ROM, and remove disposable P2 candidate ROMs,
paired SaveRAM files, obsolete gate directories, and Python caches.

The main SaveRAM is deliberately preserved because it can contain user progress.
Final fix0208 approval/report/gates and maintained regression tests are retained.
"""
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
PATCH = ROOT / "out" / "patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "p2_retired_slot_reclaim_candidate_fix0208.wsc"
CANDIDATE_SAVE = ROOT / "sram/p2_retired_slot_reclaim_candidate_fix0208.sav"
FINAL_APPROVAL = PATCH / "p2_retired_slot_reclaim_fix0208_approval.json"
FINAL_REPORT = PATCH / "p2_retired_slot_reclaim_fix0208_report.json"
FINAL_GATES = PATCH / "p2_retired_slot_reclaim_fix0208_gates"
PROMOTION_REPORT = PATCH / "p2_fix0208_promotion_cleanup_report.json"

EXPECTED_OLD_TIP_SHA256 = "ec37720a93cadd8cd91bb1ffcb490d4d89b05eb49363a38c05ed6be46d29a9cb"
EXPECTED_CANDIDATE_SHA256 = "0c6fd5c71d7ebb1f27204ebd2cff9bf889406fc483b4bd4c5b2e9156e51b8a6b"
EXPECTED_APPROVAL_SHA256 = "e308cb1760c3d0b63bf933a15b68bdd8fb5a7e297c13c5dbd9120f5d316b47c0"
EXPECTED_REPORT_SHA256 = "749219d20b940078e7113858f769525b4a4d522981b5c7de26c693aaed3a30bf"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def path_size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0
    if path.is_dir():
        total = 0
        for child in path.rglob("*"):
            if child.is_file() or child.is_symlink():
                try:
                    total += child.stat().st_size
                except FileNotFoundError:
                    pass
        return total
    return 0


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def unique_existing(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    candidates: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
    result: list[Path] = []
    selected_dirs: list[Path] = []
    for path in sorted(candidates, key=lambda item: (len(item.parts), rel(item).lower())):
        resolved = path.resolve()
        if any(resolved.is_relative_to(parent) for parent in selected_dirs):
            continue
        result.append(path)
        if path.is_dir() and not path.is_symlink():
            selected_dirs.append(resolved)
    return sorted(result, key=lambda item: rel(item).lower())


def validate_inputs() -> dict[str, Any]:
    for path in (TIP, CANDIDATE, CANDIDATE_SAVE, FINAL_APPROVAL, FINAL_REPORT, FINAL_GATES):
        if not path.exists():
            raise ValueError(f"required artifact is missing: {rel(path)}")
    if TIP.stat().st_size != ROM_SIZE or CANDIDATE.stat().st_size != ROM_SIZE:
        raise ValueError("TIP/candidate is not exactly 16 MiB")
    if CANDIDATE_SAVE.stat().st_size != SAVE_SIZE:
        raise ValueError("candidate SaveRAM is not exactly 32 KiB")

    old_tip_sha = sha256_file(TIP)
    candidate_sha = sha256_file(CANDIDATE)
    approval_sha = sha256_file(FINAL_APPROVAL)
    report_sha = sha256_file(FINAL_REPORT)
    if old_tip_sha != EXPECTED_OLD_TIP_SHA256:
        raise ValueError(f"main TIP drifted: expected {EXPECTED_OLD_TIP_SHA256}, got {old_tip_sha}")
    if candidate_sha != EXPECTED_CANDIDATE_SHA256:
        raise ValueError(f"candidate drifted: expected {EXPECTED_CANDIDATE_SHA256}, got {candidate_sha}")
    if approval_sha != EXPECTED_APPROVAL_SHA256:
        raise ValueError(f"approval drifted: expected {EXPECTED_APPROVAL_SHA256}, got {approval_sha}")
    if report_sha != EXPECTED_REPORT_SHA256:
        raise ValueError(f"report drifted: expected {EXPECTED_REPORT_SHA256}, got {report_sha}")

    report = json.loads(FINAL_REPORT.read_text(encoding="utf-8"))
    if report.get("status") != "accepted" or report.get("accepted") is not True:
        raise ValueError("final fix0208 report is not accepted")
    candidate_identity = (report.get("inputs") or {}).get("candidate_rom") or {}
    if str(candidate_identity.get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("report candidate SHA does not match locked candidate")
    targets = report.get("targets") or []
    if not isinstance(targets, list) or len(targets) != 205:
        raise ValueError("final report does not cover 205 targets")
    remaining = report.get("remaining") or {}
    if int(remaining.get("records", -1)) != 0 or int(remaining.get("unique_phrases", -1)) != 0:
        raise ValueError("final report has remaining P2 records")
    gates = report.get("gates") or {}
    failed = [
        name
        for name, value in gates.items()
        if not isinstance(value, dict) or value.get("ok") is not True
    ]
    if len(gates) != 11 or failed:
        raise ValueError(f"final report gates are not 11/11: {failed}")

    approval = json.loads(FINAL_APPROVAL.read_text(encoding="utf-8"))
    if approval.get("ok") is not True:
        raise ValueError("final fix0208 approval is not accepted")
    proof = approval.get("proof") or {}
    false_proof = [name for name, value in proof.items() if value is not True]
    if false_proof:
        raise ValueError(f"approval proof is not all true: {false_proof}")

    return {
        "old_tip_sha256": old_tip_sha,
        "candidate_sha256": candidate_sha,
        "candidate_save_sha256": sha256_file(CANDIDATE_SAVE),
        "main_save": {
            "present": TIP_SAVE.is_file(),
            "size": TIP_SAVE.stat().st_size if TIP_SAVE.is_file() else None,
            "sha256": sha256_file(TIP_SAVE) if TIP_SAVE.is_file() else None,
            "policy": "preserved_not_overwritten",
        },
        "approval_sha256": approval_sha,
        "report_sha256": report_sha,
        "targets": 205,
        "remaining_records": 0,
        "gate_count": 11,
        "proof_count": len(proof),
    }


def collect_cleanup_targets() -> tuple[list[Path], list[str]]:
    targets: list[Path] = []

    # All P2 candidate ROMs and paired test SaveRAM files become disposable once
    # the byte-identical final candidate is promoted to the main TIP.
    targets.extend(PATCH.glob("p2_*_candidate*.wsc"))
    targets.extend((ROOT / "sram").glob("p2_*_candidate*.sav"))

    # Preserve only the final fix0208 gate directory. Earlier stage gate trees
    # remain summarized by their accepted JSON reports and progress docs.
    for path in PATCH.glob("p2_*_gates"):
        if path.resolve() != FINAL_GATES.resolve():
            targets.append(path)

    # Obvious interrupted temporary files and local test caches.
    targets.extend(PATCH.glob(".p2_*.tmp"))
    for cache_name in ("__pycache__", ".pytest_cache", ".hypothesis", ".mypy_cache", ".ruff_cache"):
        targets.extend(ROOT.rglob(cache_name))
    targets.extend(ROOT.rglob("*.pyc"))
    targets.extend(ROOT.rglob("*.pyo"))

    preserved = [
        rel(FINAL_APPROVAL),
        rel(FINAL_REPORT),
        rel(FINAL_GATES),
        "tools/build_p2_slot0208_stage_name_repair_candidate.py",
        "tools/test_verify_stock_noninvasion_approval.py",
        "tools/test_mixed_residual_reference_union.py",
        "PATCH_PROGRESS.md",
        "docs/PATCH_FOLLOW_UP_P2_RESEARCH.md",
        "docs/DICT_INVASION_GUARD.md",
    ]
    return unique_existing(targets), sorted(set(preserved))


def atomic_copy(source: Path, destination: Path, expected_sha: str) -> None:
    temporary = destination.with_name(f".{destination.name}.p2-promote.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    if temporary.stat().st_size != ROM_SIZE or sha256_file(temporary) != expected_sha:
        temporary.unlink(missing_ok=True)
        raise ValueError("temporary promotion copy failed size/SHA verification")
    os.replace(temporary, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="perform promotion and cleanup")
    parser.add_argument(
        "--cleanup-caches-only",
        action="store_true",
        help="remove Python/test caches without validating or changing the promoted TIP",
    )
    args = parser.parse_args()

    if args.cleanup_caches_only:
        cache_targets: list[Path] = []
        for cache_name in (
            "__pycache__",
            ".pytest_cache",
            ".hypothesis",
            ".mypy_cache",
            ".ruff_cache",
        ):
            cache_targets.extend(ROOT.rglob(cache_name))
        cache_targets.extend(ROOT.rglob("*.pyc"))
        cache_targets.extend(ROOT.rglob("*.pyo"))
        targets = unique_existing(cache_targets)
        removed_bytes = sum(path_size(path) for path in targets)
        for path in targets:
            remove_path(path)
        print(
            json.dumps(
                {
                    "status": "caches_cleaned",
                    "removed_count": len(targets),
                    "removed_bytes": removed_bytes,
                    "removed": [rel(path) for path in targets],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    validation = validate_inputs()
    cleanup_targets, preserved = collect_cleanup_targets()
    cleanup_bytes = sum(path_size(path) for path in cleanup_targets)

    print(json.dumps({
        "mode": "commit" if args.commit else "dry_run",
        "validation": validation,
        "cleanup": {
            "paths": len(cleanup_targets),
            "bytes": cleanup_bytes,
            "targets": [rel(path) for path in cleanup_targets],
            "preserved": preserved,
        },
    }, ensure_ascii=False, indent=2))

    if not args.commit:
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_p2_fix0208"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    if backup.stat().st_size != ROM_SIZE or sha256_file(backup) != EXPECTED_OLD_TIP_SHA256:
        raise ValueError("rollback backup failed size/SHA verification")

    main_save_before = validation["main_save"]
    atomic_copy(CANDIDATE, TIP, EXPECTED_CANDIDATE_SHA256)
    promoted_sha = sha256_file(TIP)
    if promoted_sha != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("promoted TIP SHA verification failed")
    if TIP_SAVE.is_file():
        if sha256_file(TIP_SAVE) != main_save_before["sha256"]:
            raise ValueError("main SaveRAM changed during ROM promotion")

    removed: list[dict[str, Any]] = []
    for path in cleanup_targets:
        size = path_size(path)
        removed.append({"path": rel(path), "bytes": size})
        remove_path(path)

    promotion_report: dict[str, Any] = {
        "ok": True,
        "generated_by": rel(Path(__file__)),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "promotion": {
            "source": rel(CANDIDATE),
            "destination": rel(TIP),
            "old_tip_sha256": EXPECTED_OLD_TIP_SHA256,
            "promoted_tip_sha256": promoted_sha,
            "size": TIP.stat().st_size,
            "backup": rel(backup),
            "backup_sha256": sha256_file(backup),
            "approval": rel(FINAL_APPROVAL),
            "approval_sha256": EXPECTED_APPROVAL_SHA256,
            "acceptance_report": rel(FINAL_REPORT),
            "acceptance_report_sha256": EXPECTED_REPORT_SHA256,
            "targets": 205,
            "remaining_records": 0,
            "gates": "11/11",
            "main_save": main_save_before,
        },
        "cleanup": {
            "removed_count": len(removed),
            "removed_bytes": sum(item["bytes"] for item in removed),
            "removed": removed,
            "preserved": preserved,
            "policy": (
                "Removed P2 candidate/test ROMs and paired SaveRAM files, obsolete P2 gate "
                "trees, temporary files, and Python caches. Preserved final fix0208 approval, "
                "acceptance report, final gate tree, source tools/tests, and the main SaveRAM."
            ),
        },
    }
    PROMOTION_REPORT.write_text(
        json.dumps(promotion_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "status": "promoted",
        "tip_sha256": promoted_sha,
        "backup": rel(backup),
        "removed_count": len(removed),
        "removed_bytes": promotion_report["cleanup"]["removed_bytes"],
        "report": rel(PROMOTION_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
