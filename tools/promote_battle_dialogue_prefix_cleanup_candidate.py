#!/usr/bin/env python3
"""Transactionally promote the verified 5E:BD90 battle-dialogue cleanup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
BACKUP_ROOT = PATCH / "backup"
TIP = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "battle_dialogue_prefix_cleanup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_dialogue_prefix_cleanup_candidate.sav"
BUILD_REPORT = PATCH / "battle_dialogue_prefix_cleanup_build_report.json"
AUDIT_REPORT = PATCH / "battle_dialogue_prefix_cleanup_audit.json"
GATE_SUMMARY = PATCH / "battle_dialogue_prefix_cleanup_gate_summary.json"
PROMOTION_REPORT = PATCH / "battle_dialogue_prefix_cleanup_promotion_report.json"

OLD_TIP_SHA = "70f53cbc34559366e856aedcd793fb5fde33c0c2199fd7166aa055fc89d5e677"
CANDIDATE_SHA = "a47569820eed19ab0028b432dabf840bb35f9689cf403e63ed2af71f8431cf9a"
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
    return str(path.relative_to(ROOT)).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def require(path: Path, *, size: int | None = None, sha256: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing required file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(
            f"size drifted for {rel(path)}: expected {size}, got {path.stat().st_size}"
        )
    if sha256 is not None:
        actual = sha256_file(path)
        if actual != sha256:
            raise PromotionError(
                f"SHA drifted for {rel(path)}: expected {sha256}, got {actual}"
            )


def load_json(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise PromotionError(f"JSON root is not an object: {rel(path)}")
    return document


def validate() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, sha256=OLD_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha256=CANDIDATE_SHA)
    require(MAIN_SAVE, size=SAVE_SIZE)
    require(CANDIDATE_SAVE, size=SAVE_SIZE)
    for path in (BUILD_REPORT, AUDIT_REPORT, GATE_SUMMARY):
        require(path)

    main_save_sha = sha256_file(MAIN_SAVE)
    candidate_save_sha = sha256_file(CANDIDATE_SAVE)
    if main_save_sha != candidate_save_sha:
        raise PromotionError(
            "main SaveRAM changed after candidate build; rebuild and reverify the paired candidate"
        )

    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT_REPORT)
    gates = load_json(GATE_SUMMARY)
    if build.get("ok") is not True or build.get("status") != "candidate_static_verified":
        raise PromotionError("build report is not accepted")
    if ((build.get("parent_rom") or {}).get("sha256")) != OLD_TIP_SHA:
        raise PromotionError("build report parent SHA mismatch")
    if ((build.get("candidate_rom") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("build report candidate SHA mismatch")
    verification = build.get("verification") or {}
    if verification.get("boundary_preserved") is not True:
        raise PromotionError("build report did not preserve record boundary")
    if verification.get("dictionary_data_changed") is not False:
        raise PromotionError("build report indicates dictionary data changed")
    if verification.get("unaccounted_changed_bytes") != 0:
        raise PromotionError("build report contains unaccounted changes")

    if audit.get("ok") is not True:
        raise PromotionError("independent target audit failed")
    target = audit.get("target") or {}
    if target.get("after_text") != "우와아아아……！":
        raise PromotionError("target audit text drifted")
    if target.get("japanese_residual_count") != 0:
        raise PromotionError("target audit still contains Japanese kana")
    if target.get("boundary_preserved") is not True or target.get("token_unchanged") is not True:
        raise PromotionError("target boundary/token proof failed")
    if ((audit.get("diff") or {}).get("unexpected_changed_bytes")) != 0:
        raise PromotionError("independent diff confinement failed")
    if any(row.get("ok") is not True for row in audit.get("surrounding_records") or []):
        raise PromotionError("surrounding-record proof failed")

    if gates.get("ok") is not True or gates.get("accepted_static") is not True:
        raise PromotionError("gate summary is not accepted")
    checks = gates.get("checks") or {}
    required_checks = (
        "python_compile",
        "builder_static_proof",
        "independent_target_audit",
        "record_structure_5e",
        "false_segmented_pointer",
        "nondialogue_parent_differential",
        "legacy_smoke_parent_differential",
        "save_pair",
        "gate_commands",
    )
    failed = [name for name in required_checks if (checks.get(name) or {}).get("ok") is not True]
    if failed:
        raise PromotionError(f"blocking gate(s) failed: {failed}")
    if ((gates.get("candidate_rom") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("gate summary candidate SHA mismatch")
    if ((gates.get("parent_rom") or {}).get("sha256")) != OLD_TIP_SHA:
        raise PromotionError("gate summary parent SHA mismatch")

    return {
        "old_tip_sha256": OLD_TIP_SHA,
        "candidate_sha256": CANDIDATE_SHA,
        "save_sha256": main_save_sha,
        "target_before_hex": target.get("before_hex"),
        "target_after_hex": target.get("after_hex"),
        "target_after_text": target.get("after_text"),
        "checks": {name: True for name in required_checks},
    }


def copy_verified(source: Path, destination: Path, expected_sha: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256_file(destination) != expected_sha:
        raise PromotionError(f"backup verification failed: {rel(destination)}")


def atomic_promote() -> None:
    temporary = TIP.with_name(f".{TIP.name}.battle-dialogue-prefix-cleanup.tmp")
    temporary.unlink(missing_ok=True)
    with CANDIDATE.open("rb") as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    if temporary.stat().st_size != ROM_SIZE or sha256_file(temporary) != CANDIDATE_SHA:
        temporary.unlink(missing_ok=True)
        raise PromotionError("temporary promoted ROM failed verification")
    os.replace(temporary, TIP)


def write_report(document: Mapping[str, Any]) -> None:
    temporary = PROMOTION_REPORT.with_name(f".{PROMOTION_REPORT.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, PROMOTION_REPORT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate()
    if not args.commit:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "ok": True,
                    "tip": identity(TIP),
                    "candidate": identity(CANDIDATE),
                    "main_save": identity(MAIN_SAVE),
                    "validation": validation,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}_pre_battle_dialogue_prefix_cleanup"
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / MAIN_SAVE.name
    save_sha = validation["save_sha256"]
    copy_verified(TIP, backup_rom, OLD_TIP_SHA)
    copy_verified(MAIN_SAVE, backup_save, save_sha)

    atomic_promote()
    require(TIP, size=ROM_SIZE, sha256=CANDIDATE_SHA)
    require(MAIN_SAVE, size=SAVE_SIZE, sha256=save_sha)

    candidate_rom_identity = identity(CANDIDATE)
    candidate_save_identity = identity(CANDIDATE_SAVE)
    CANDIDATE.unlink()
    CANDIDATE_SAVE.unlink()
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_dialogue_prefix_cleanup_candidate.py",
        "mode": "commit",
        "ok": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": {"size": ROM_SIZE, "sha256": OLD_TIP_SHA},
        "new_tip": identity(TIP),
        "main_save": identity(MAIN_SAVE),
        "backup_rom": identity(backup_rom),
        "backup_save": identity(backup_save),
        "validation": validation,
        "candidate_before_cleanup": candidate_rom_identity,
        "candidate_save_before_cleanup": candidate_save_identity,
        "candidate_duplicates_removed": True,
        "runtime_follow_up": {
            "status": "pending",
            "blocking": False,
            "instruction": "Revisit the supplied battle scene and verify line 2 begins with 우 rather than う.",
        },
        "evidence": {
            "build_report": identity(BUILD_REPORT),
            "audit": identity(AUDIT_REPORT),
            "gate_summary": identity(GATE_SUMMARY),
        },
    }
    write_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
