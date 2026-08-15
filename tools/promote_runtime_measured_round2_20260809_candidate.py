#!/usr/bin/env python3
"""Promote the user-validated runtime-measured round2 candidate transactionally.

ROM-only transaction:
- verify the exact parent/candidate identities and candidate audit evidence;
- require explicit user runtime validation bound to those hashes;
- back up the current main ROM and live SaveRAM;
- atomically replace only the main ROM with the audited candidate;
- keep live SaveRAM byte-exact (the candidate SaveRAM is runtime-test evidence
  and is intentionally not copied over the user's live main SaveRAM);
- write a promotion report suitable for rollback/audit.
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
CANDIDATE = PATCH / "runtime_measured_round2_20260809_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/runtime_measured_round2_20260809_candidate.sav"
BUILD_REPORT = PATCH / "runtime_measured_round2_20260809_candidate_report.json"
FALSE_LEAD_AUDIT = PATCH / "runtime_measured_round2_20260809_false_lead.json"
CELL_AUDIT = PATCH / "runtime_measured_round2_20260809_20cell.json"
SAFETY_PARENT = PATCH / "runtime_measured_round2_20260809_safety_parent.json"
SAFETY_CANDIDATE = PATCH / "runtime_measured_round2_20260809_safety_candidate.json"
APPROVAL = PATCH / "runtime_measured_round2_20260809_user_validation.json"
PROMOTION_REPORT = PATCH / "runtime_measured_round2_20260809_promotion_report.json"

OLD_TIP_SHA = "f11b11c05e94e6b3007061e00586bbf74424be1e36c07571f43cb6a4584bafaf"
CANDIDATE_SHA = "9402f7efc1c557746015eb6352799a79f7f66febf1eb0ad4039734028a16a9f2"
EXPECTED_CHECKSUM = 0x82A6
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


def require_file(path: Path, *, size: int | None = None, sha256: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing required file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drifted for {rel(path)}")
    if sha256 is not None and sha256_file(path) != sha256:
        raise PromotionError(f"SHA drifted for {rel(path)}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise PromotionError(f"cannot read {rel(path)}: {exc}") from exc
    if not isinstance(doc, dict):
        raise PromotionError(f"JSON root is not object: {rel(path)}")
    return doc


def checksum(path: Path) -> int:
    data = path.read_bytes()
    if len(data) != ROM_SIZE:
        raise PromotionError(f"wrong ROM size: {rel(path)}")
    calculated = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    if stored != calculated:
        raise PromotionError(
            f"checksum invalid for {rel(path)}: stored={stored:04X} calculated={calculated:04X}"
        )
    return stored


def hard_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("abs") or row.get("record_start") or row.get("address") or ""),
        str(row.get("route") or ""),
        str(row.get("reason") or ""),
        tuple(row.get("issues") or []),
    )


def validate_evidence() -> dict[str, Any]:
    require_file(TIP, size=ROM_SIZE, sha256=OLD_TIP_SHA)
    require_file(CANDIDATE, size=ROM_SIZE, sha256=CANDIDATE_SHA)
    require_file(MAIN_SAVE, size=SAVE_SIZE)
    require_file(CANDIDATE_SAVE, size=SAVE_SIZE)
    for path in (
        BUILD_REPORT,
        FALSE_LEAD_AUDIT,
        CELL_AUDIT,
        SAFETY_PARENT,
        SAFETY_CANDIDATE,
        APPROVAL,
    ):
        require_file(path)
    if checksum(CANDIDATE) != EXPECTED_CHECKSUM:
        raise PromotionError("candidate checksum drifted")

    build = load_json(BUILD_REPORT)
    if build.get("ok") is not True:
        raise PromotionError("candidate build report is not green")
    if str((build.get("parent") or {}).get("sha256") or "").lower() != OLD_TIP_SHA:
        raise PromotionError("build report parent SHA mismatch")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != CANDIDATE_SHA:
        raise PromotionError("build report candidate SHA mismatch")
    scenario = build.get("scenario") or {}
    battle = build.get("battle") or {}
    checks = build.get("checks") or {}
    if len(scenario.get("rows") or []) != 20:
        raise PromotionError("scenario target count drifted")
    if int(scenario.get("applied_cross_control_reflows_found_in_parent", -1)) != 6:
        raise PromotionError("cross-control reflow population drifted")
    if len(scenario.get("short_name_bangbang_actor_switch_native_restored") or []) != 6:
        raise PromotionError("short name!! native restoration population drifted")
    if int(battle.get("exact_e51839_kosen_family", -1)) != 16:
        raise PromotionError("E51839/こ戦 family population drifted")
    if int(battle.get("native_rehomed", -1)) != 16 or int(battle.get("metadata_restored", -1)) != 0:
        raise PromotionError("battle native-rehome policy drifted")
    required_checks = (
        "scenario_extents_preserved",
        "scenario_terminators_preserved",
        "adjacent_control_ranges_preserved",
        "6248B9_intermediate_dialogue_preserved",
        "624A13_followup_dialogue_preserved",
        "battle_extents_preserved",
        "battle_terminators_preserved",
        "battle_visible_text_preserved",
        "battle_false_lead_guard_respected",
        "battle_ext3_head_removed",
        "unexpected_diff_offsets_zero",
    )
    failed = [name for name in required_checks if checks.get(name) is not True]
    if failed:
        raise PromotionError(f"candidate build check(s) failed: {failed}")

    lead = load_json(FALSE_LEAD_AUDIT)
    if lead.get("ok") is not True:
        raise PromotionError("false-lead audit is not green")
    if str((lead.get("target") or {}).get("sha256") or "").lower() != CANDIDATE_SHA:
        raise PromotionError("false-lead audit target SHA mismatch")
    lead_counts = lead.get("counts") or {}
    if int(lead_counts.get("reintroduced", -1)) != 0 or int(lead_counts.get("clean", -1)) != 340:
        raise PromotionError("false-lead recurrence counts drifted")

    cells = load_json(CELL_AUDIT)
    cell_population = cells.get("population") or {}
    if (
        cells.get("ok") is not True
        or cells.get("width_ok") is not True
        or int(cell_population.get("offender_records", -1)) != 0
        or int(cell_population.get("max_line_cells", 999)) > 20
    ):
        raise PromotionError("20-cell audit failed")

    parent_safety = load_json(SAFETY_PARENT)
    candidate_safety = load_json(SAFETY_CANDIDATE)
    parent_rows = {hard_key(row) for row in parent_safety.get("hard_failures_rows") or []}
    candidate_rows = {hard_key(row) for row in candidate_safety.get("hard_failures_rows") or []}
    new_hard = sorted(candidate_rows - parent_rows)
    if new_hard:
        raise PromotionError(f"candidate introduced new runtime-safety hard rows: {new_hard[:5]}")
    removed_hard = len(parent_rows - candidate_rows)
    if removed_hard != 22:
        raise PromotionError(f"runtime-safety differential drifted: removed={removed_hard} expected=22")

    approval = load_json(APPROVAL)
    if approval.get("approved") is not True or approval.get("promotion_authorized") is not True:
        raise PromotionError("user promotion authorization missing")
    if str(approval.get("main_tip_sha256") or "").lower() != OLD_TIP_SHA:
        raise PromotionError("approval parent SHA mismatch")
    if str(approval.get("candidate_sha256") or "").lower() != CANDIDATE_SHA:
        raise PromotionError("approval candidate SHA mismatch")

    return {
        "old_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "main_save": identity(MAIN_SAVE),
        "runtime_test_candidate_save": identity(CANDIDATE_SAVE),
        "candidate_checksum": f"{EXPECTED_CHECKSUM:04X}",
        "build_report": identity(BUILD_REPORT),
        "false_lead_audit": identity(FALSE_LEAD_AUDIT),
        "cell_audit": identity(CELL_AUDIT),
        "safety_parent": identity(SAFETY_PARENT),
        "safety_candidate": identity(SAFETY_CANDIDATE),
        "user_validation": identity(APPROVAL),
        "runtime_safety_removed_hard_rows": removed_hard,
        "runtime_safety_new_hard_rows": 0,
    }


def unique_backup_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = BACKUP_ROOT / f"{stamp}_pre_runtime_measured_round2_20260809"
    result = base
    suffix = 1
    while result.exists():
        result = base.with_name(f"{base.name}_{suffix}")
        suffix += 1
    return result


def copy_verified(source: Path, destination: Path, expected_sha: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256_file(destination) != expected_sha:
        raise PromotionError(f"backup verification failed: {rel(destination)}")


def atomic_promote() -> None:
    staged = TIP.with_name(f".{TIP.name}.runtime-round2.{os.getpid()}.tmp")
    staged.unlink(missing_ok=True)
    with CANDIDATE.open("rb") as source, staged.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    if staged.stat().st_size != ROM_SIZE or sha256_file(staged) != CANDIDATE_SHA:
        staged.unlink(missing_ok=True)
        raise PromotionError("staged promoted ROM failed verification")
    os.replace(staged, TIP)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staged.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(staged, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate_evidence()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, **validation}, ensure_ascii=True, indent=2))
        return 0

    backup_dir = unique_backup_dir()
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / MAIN_SAVE.name
    main_save_before = MAIN_SAVE.read_bytes()
    copy_verified(TIP, backup_rom, OLD_TIP_SHA)
    copy_verified(MAIN_SAVE, backup_save, validation["main_save"]["sha256"])
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "generated_by": rel(Path(__file__)),
            "reason": "pre_runtime_measured_round2_20260809",
            "old_tip": identity(backup_rom),
            "live_save": identity(backup_save),
            "candidate": validation["candidate"],
            "user_validation": validation["user_validation"],
        },
    )

    try:
        atomic_promote()
        require_file(TIP, size=ROM_SIZE, sha256=CANDIDATE_SHA)
        if checksum(TIP) != EXPECTED_CHECKSUM:
            raise PromotionError("promoted main checksum mismatch")
        if MAIN_SAVE.read_bytes() != main_save_before:
            raise PromotionError("live main SaveRAM changed during ROM promotion")
    except Exception:
        shutil.copy2(backup_rom, TIP)
        shutil.copy2(backup_save, MAIN_SAVE)
        raise

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
        "runtime_test_candidate_save": validation["runtime_test_candidate_save"],
        "backup_rom": identity(backup_rom),
        "backup_save": identity(backup_save),
        "candidate_preserved": True,
        "candidate_save_preserved_as_runtime_test_evidence": True,
        "save_ram_action": "live main SaveRAM preserved byte-exact; runtime-mutated candidate SaveRAM not copied",
        "evidence": {
            "build_report": validation["build_report"],
            "false_lead_audit": validation["false_lead_audit"],
            "cell_audit": validation["cell_audit"],
            "safety_parent": validation["safety_parent"],
            "safety_candidate": validation["safety_candidate"],
            "user_validation": validation["user_validation"],
            "runtime_safety_removed_hard_rows": validation["runtime_safety_removed_hard_rows"],
            "runtime_safety_new_hard_rows": validation["runtime_safety_new_hard_rows"],
        },
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
