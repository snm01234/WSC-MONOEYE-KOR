#!/usr/bin/env python3
"""Promote the verified same-case battle-dialogue candidate transactionally.

This promotion keeps the candidate files and writes a dated ROM/SaveRAM
backup.  The candidate SaveRAM is checked against the live SaveRAM but is not
copied over it because the bytes are already identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
BACKUP_ROOT = PATCH / "backup"
TIP = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "battle_dialogue_samecase_native_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_dialogue_samecase_native_candidate.sav"
BUILD_REPORT = PATCH / "battle_dialogue_samecase_native_candidate_report.json"
PROMOTION_REPORT = PATCH / "battle_dialogue_samecase_native_promotion_report.json"

OLD_TIP_SHA = "55c2e1f3467d28e041ad0e145cad68091cf78d50f8d58f6ce6a65259acd59ca9"
CANDIDATE_SHA = "79083106361d471138392e78ccfd9698781d0f03b8f4b61ad1e61ba2d373d8be"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def require(path: Path, *, size: int | None = None, sha256: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing required file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drifted for {rel(path)}")
    if sha256 is not None and sha256_file(path) != sha256:
        raise PromotionError(f"SHA drifted for {rel(path)}")


def load_build_report() -> dict[str, Any]:
    try:
        document = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise PromotionError(f"cannot read build report: {exc}") from exc
    if not isinstance(document, dict) or document.get("ok") is not True:
        raise PromotionError("candidate build report is not green")
    candidate = document.get("candidate") or {}
    if str(candidate.get("sha256", "")).lower() != CANDIDATE_SHA:
        raise PromotionError("candidate report SHA mismatch")
    counts = document.get("counts") or {}
    if counts.get("applied") != 62 or counts.get("deferred") != 27:
        raise PromotionError("candidate counts drifted")
    checks = document.get("checks") or {}
    required = (
        "record_extents_preserved",
        "terminators_preserved",
        "next_boundary_preserved",
        "native_token_two_bytes_only",
        "native_render_exact",
        "deferred_rows_byte_exact",
        "dictionary_unchanged",
        "unexpected_diff_offsets_zero",
        "main_tip_unchanged",
    )
    failed = [name for name in required if checks.get(name) is not True]
    if failed:
        raise PromotionError(f"candidate check(s) failed: {failed}")
    return document


def validate() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, sha256=OLD_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha256=CANDIDATE_SHA)
    require(MAIN_SAVE, size=SAVE_SIZE)
    require(CANDIDATE_SAVE, size=SAVE_SIZE)
    build = load_build_report()
    main_save_sha = sha256_file(MAIN_SAVE)
    candidate_save_sha = sha256_file(CANDIDATE_SAVE)
    if main_save_sha != candidate_save_sha:
        raise PromotionError("candidate SaveRAM differs from live SaveRAM")
    return {
        "old_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "main_save": identity(MAIN_SAVE),
        "candidate_save": identity(CANDIDATE_SAVE),
        "build_report": identity(BUILD_REPORT),
        "build_counts": build.get("counts"),
    }


def copy_verified(source: Path, destination: Path, expected_sha: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256_file(destination) != expected_sha:
        raise PromotionError(f"backup verification failed: {rel(destination)}")


def unique_backup_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = BACKUP_ROOT / f"{stamp}_pre_battle_dialogue_samecase_native"
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{suffix}")
        suffix += 1
    return candidate


def atomic_promote() -> None:
    temporary = TIP.with_name(f".{TIP.name}.samecase-native.tmp")
    temporary.unlink(missing_ok=True)
    with CANDIDATE.open("rb") as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    if temporary.stat().st_size != ROM_SIZE or sha256_file(temporary) != CANDIDATE_SHA:
        temporary.unlink(missing_ok=True)
        raise PromotionError("temporary promoted ROM failed verification")
    os.replace(temporary, TIP)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, **validation}, ensure_ascii=False, indent=2))
        return 0

    backup_dir = unique_backup_dir()
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / MAIN_SAVE.name
    old_tip_sha = validation["old_tip"]["sha256"]
    save_sha = validation["main_save"]["sha256"]
    copy_verified(TIP, backup_rom, old_tip_sha)
    copy_verified(MAIN_SAVE, backup_save, save_sha)

    atomic_promote()
    require(TIP, size=ROM_SIZE, sha256=CANDIDATE_SHA)
    require(MAIN_SAVE, size=SAVE_SIZE, sha256=save_sha)

    report = {
        "schema_version": 1,
        "generated_by": rel(Path(__file__)),
        "mode": "commit",
        "ok": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": validation["old_tip"],
        "new_tip": identity(TIP),
        "main_save": identity(MAIN_SAVE),
        "candidate_before_promotion": validation["candidate"],
        "candidate_save": validation["candidate_save"],
        "backup_rom": identity(backup_rom),
        "backup_save": identity(backup_save),
        "candidate_preserved": True,
        "save_ram_action": "unchanged; candidate SaveRAM was byte-identical",
        "evidence": {"build_report": validation["build_report"]},
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
