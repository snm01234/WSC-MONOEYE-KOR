#!/usr/bin/env python3
"""Promote the fully audited conservative intermission transition remapper."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_DIR = PATCH / "intermission_transition_inline_private_remap_candidate"
CANDIDATE = CANDIDATE_DIR / "intermission_transition_inline_private_remap_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/intermission_transition_inline_private_remap_candidate.sav"
BUILD_REPORT = CANDIDATE_DIR / "build_report.json"
RUNTIME_AUDIT = CANDIDATE_DIR / "runtime_audit.json"
PROMOTION_REPORT = CANDIDATE_DIR / "tip_promotion_report.json"

PARENT_SHA256 = "163e8e6e4984e866b1a64d92f44765197df30c6281c92adf75acd6e552ad928a"
CANDIDATE_SHA256 = "48320a9336346bf6c6b230b7199426197a7a6321a16d4caed9989aa29c6d9c13"
ROM_SIZE = 16_777_216
STOCK_BASE = 0x800000
STORE_DX = 0x78A06E
STORE_SI = 0x78A0EB
REMAPPER_START = 0x79FDCF
REMAPPER_END = 0x79FEE1


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    result = []
    start = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            result.append((start, index))
            start = None
    if start is not None:
        result.append((start, len(before)))
    return result


def main() -> int:
    before = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    build_bytes = BUILD_REPORT.read_bytes()
    audit_bytes = RUNTIME_AUDIT.read_bytes()
    build = json.loads(build_bytes.decode("utf-8"))
    audit = json.loads(audit_bytes.decode("utf-8"))

    if len(before) != ROM_SIZE or digest(before) != PARENT_SHA256:
        raise RuntimeError("main TIP identity drifted from the restored parent")
    if len(candidate) != ROM_SIZE or digest(candidate) != CANDIDATE_SHA256:
        raise RuntimeError("candidate identity drifted")
    if not build.get("ok") or not all(build["checks"].values()):
        raise RuntimeError("builder gates are not all green")
    if not audit.get("ok") or not all(audit["checks"].values()):
        raise RuntimeError("QuickSave5 runtime gates are not all green")
    if audit["candidate_rom"]["sha256"] != CANDIDATE_SHA256:
        raise RuntimeError("runtime audit is bound to a different candidate")

    allow = (
        (STOCK_BASE + STORE_DX, STOCK_BASE + STORE_DX + 5),
        (STOCK_BASE + STORE_SI, STOCK_BASE + STORE_SI + 5),
        (STOCK_BASE + REMAPPER_START, STOCK_BASE + REMAPPER_END),
        (ROM_SIZE - 2, ROM_SIZE),
    )
    runs = diff_runs(before, candidate)
    outside = [
        (lo, hi) for lo, hi in runs if not any(start <= lo and hi <= end for start, end in allow)
    ]
    changed_bytes = sum(hi - lo for lo, hi in runs)
    if outside or changed_bytes != int(build["diff"]["changed_bytes_including_checksum"]):
        raise RuntimeError(
            f"candidate diff escaped allowlist or changed count drifted: {outside}, {changed_bytes}"
        )

    # SaveRAM is live user data.  Refresh the candidate pair if the snapshot
    # changed, but never replace the main SaveRAM during ROM promotion.
    if not CANDIDATE_SAVE.is_file() or CANDIDATE_SAVE.read_bytes() != save_before:
        atomic_bytes(CANDIDATE_SAVE, save_before)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_intermission_transition_private_store_remap"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / MAIN.name
    backup_save = backup_dir / MAIN_SAVE.name
    shutil.copy2(MAIN, backup_rom)
    shutil.copy2(MAIN_SAVE, backup_save)
    if backup_rom.read_bytes() != before or backup_save.read_bytes() != save_before:
        raise RuntimeError("backup verification failed")

    atomic_bytes(MAIN, candidate)
    after = MAIN.read_bytes()
    save_after = MAIN_SAVE.read_bytes()
    checks = {
        "backup_rom_byte_identical_to_parent": backup_rom.read_bytes() == before,
        "backup_saveram_byte_identical_to_live_snapshot": backup_save.read_bytes()
        == save_before,
        "main_tip_byte_identical_to_verified_candidate": after == candidate,
        "main_saveram_unchanged": save_after == save_before,
        "diff_bounded_to_two_stores_remapper_and_checksum": not outside,
        "changed_byte_count_matches_build_report": changed_bytes
        == int(build["diff"]["changed_bytes_including_checksum"]),
    }
    if not all(checks.values()):
        raise RuntimeError(f"post-promotion checks failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_intermission_transition_inline_private_remap_candidate.py",
        "ok": True,
        "promoted": True,
        "timestamp_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_tip": {
            "path": relative(MAIN),
            "before_sha256": PARENT_SHA256,
            "after_sha256": CANDIDATE_SHA256,
            "size": len(after),
        },
        "main_saveram": {
            "path": relative(MAIN_SAVE),
            "sha256": digest(save_before),
            "unchanged": True,
        },
        "backup": {
            "rom": relative(backup_rom),
            "saveram": relative(backup_save),
            "rom_sha256": digest(before),
            "saveram_sha256": digest(save_before),
        },
        "candidate": {
            "path": relative(CANDIDATE),
            "sha256": CANDIDATE_SHA256,
        },
        "diff": {
            "changed_runs": [[f"{lo:08X}", f"{hi:08X}"] for lo, hi in runs],
            "changed_bytes_including_checksum": changed_bytes,
            "outside_allowlist": outside,
        },
        "build_report_sha256": digest(build_bytes),
        "runtime_audit_sha256": digest(audit_bytes),
        "checks": checks,
    }
    atomic_json(PROMOTION_REPORT, report)
    atomic_json(backup_dir / "backup_manifest.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
