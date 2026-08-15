#!/usr/bin/env python3
"""Promote the user-approved intermission static exact-clean-color candidate ROM."""
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
CANDIDATE_DIR = PATCH / "intermission_static_exact_clean_color_candidate"
CANDIDATE = CANDIDATE_DIR / "intermission_static_exact_clean_color_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/intermission_static_exact_clean_color_candidate.sav"
EXACT_REPORT = CANDIDATE_DIR / "exact_state_candidate_report.json"
SAFE_DIR = PATCH / "intermission_static_focus_matched_safe"
SAFE_CANDIDATE = SAFE_DIR / "intermission_static_focus_matched_safe_candidate.wsc"
SAFE_CANDIDATE_SAVE = ROOT / "sram/intermission_static_focus_matched_safe_candidate.sav"
SAFE_REPORT = SAFE_DIR / "static_focus_matched_safe_report.json"
POST_AUDIT = PATCH / "intermission_static_exact_clean_color_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "intermission_static_exact_clean_color_promotion_report.json"

EXPECTED_TIP_SHA = "c221df4b2090b9841429fde30715164a41b3179f50771b30eda9645ae398b3e9"
EXPECTED_CANDIDATE_SHA = "33b77347f1c969c2751b24b3ec3479e63c3b5146df4015cbad3bdc0d7eaab4e1"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
TILE_SIZE = 0x20
CHECKSUM_SIZE = 2

CLEANUP_PATHS = (
    CANDIDATE,
    CANDIDATE_SAVE,
    SAFE_CANDIDATE,
    SAFE_CANDIDATE_SAVE,
    PATCH / "intermission_static_exact_state_candidate/intermission_static_exact_state_candidate.wsc",
    ROOT / "sram/intermission_static_exact_state_candidate.sav",
)


class PromotionError(RuntimeError):
    pass


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def stock_base(data: bytes) -> int:
    if len(data) != ROM_SIZE:
        raise PromotionError(f"unsupported ROM size: {len(data)}")
    return 0x800000


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-CHECKSUM_SIZE]) & 0xFFFF) == int.from_bytes(
        data[-CHECKSUM_SIZE:], "little"
    )


def changed_indexes(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise PromotionError("ROM sizes differ")
    return [index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]


def load_reports() -> tuple[dict[str, Any], dict[str, Any]]:
    safe = json.loads(SAFE_REPORT.read_text(encoding="utf-8"))
    exact = json.loads(EXACT_REPORT.read_text(encoding="utf-8"))
    return safe, exact


def validate() -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, EXPECTED_TIP_SHA)
    require_file(TIP_SAVE, SAVE_SIZE)
    require_file(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require_file(CANDIDATE_SAVE, SAVE_SIZE)
    require_file(SAFE_CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require_file(SAFE_REPORT)
    require_file(EXACT_REPORT)

    safe, exact = load_reports()
    safe_checks = safe.get("verification") or {}
    exact_checks = exact.get("verification") or {}
    required_safe_checks = (
        "all_12_focus_wordings_used",
        "all_shared_tiles_byte_consistent",
        "focus_sprite_atlas_unchanged",
        "runtime_hook_region_unchanged",
        "changes_bounded_to_tile_allowlist_and_checksum",
        "main_tip_not_modified_by_builder",
    )
    required_exact_checks = (
        "quicksave_1_2_3_tilemaps_identical",
        "all_visible_background_pixels_identical_across_sources",
        "all_12_labels_exact",
        "all_patched_states_round_trip",
        "all_visible_cells_match_exact_target",
        "focus_sprite_tiles_preserved",
        "source_main_tip_unchanged",
    )
    if not all(safe_checks.get(name) is True for name in required_safe_checks):
        raise PromotionError("safe candidate report did not pass every required ROM check")
    if not all(exact_checks.get(name) is True for name in required_exact_checks):
        raise PromotionError("exact-state report did not pass every required state check")
    if safe.get("base_rom_sha256") != EXPECTED_TIP_SHA:
        raise PromotionError("safe report is not bound to the current main TIP")
    if safe.get("candidate_rom_sha256") != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("safe report candidate binding mismatch")
    if exact.get("rom_sha256") != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("exact report candidate binding mismatch")
    if exact.get("source_rom_sha256") != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("exact report source binding mismatch")
    if exact.get("rom_is_shared_tile_safe_candidate") is not True:
        raise PromotionError("exact report does not bind to the shared-tile-safe ROM")
    if len(safe.get("targets") or []) != 12:
        raise PromotionError("safe report does not cover twelve labels")
    if int(safe.get("changed_unique_rom_tiles") or 0) != 153:
        raise PromotionError("unexpected approved tile count")
    if int(safe.get("changed_rom_bytes_including_checksum") or 0) != 2733:
        raise PromotionError("unexpected approved changed-byte count")

    before = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    safe_candidate = SAFE_CANDIDATE.read_bytes()
    if candidate != safe_candidate:
        raise PromotionError("exact-clean-color candidate ROM differs from its safe source ROM")
    if not checksum_ok(candidate):
        raise PromotionError("candidate WonderSwan checksum is invalid")

    base = stock_base(before)
    allowed = bytearray(ROM_SIZE)
    tile_records: list[dict[str, Any]] = []
    for row in safe.get("changed_tiles") or []:
        logical = int(str(row["rom"]), 16)
        physical = base + logical
        if physical < base or physical + TILE_SIZE > ROM_SIZE - CHECKSUM_SIZE:
            raise PromotionError(f"approved tile outside stock ROM: {logical:06X}")
        old_tile = before[physical : physical + TILE_SIZE]
        new_tile = candidate[physical : physical + TILE_SIZE]
        if digest_bytes(old_tile) != row.get("old_sha256"):
            raise PromotionError(f"old tile hash mismatch at {logical:06X}")
        if digest_bytes(new_tile) != row.get("new_sha256"):
            raise PromotionError(f"new tile hash mismatch at {logical:06X}")
        allowed[physical : physical + TILE_SIZE] = b"\x01" * TILE_SIZE
        tile_records.append(
            {
                "logical_rom": f"{logical:06X}",
                "physical_rom": f"{physical:08X}",
                "old_sha256": row.get("old_sha256"),
                "new_sha256": row.get("new_sha256"),
                "owners": row.get("owners") or [],
            }
        )
    if len(tile_records) != 153:
        raise PromotionError("changed-tile manifest count drift")
    allowed[-CHECKSUM_SIZE:] = b"\x01" * CHECKSUM_SIZE

    differences = changed_indexes(before, candidate)
    outside = [index for index in differences if not allowed[index]]
    if outside:
        raise PromotionError(
            f"candidate differs outside approved tiles/checksum at {outside[0]:08X}"
        )
    if len(differences) != 2733:
        raise PromotionError(f"changed-byte count drift: {len(differences)}")

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "safe_source_candidate": identity(SAFE_CANDIDATE),
        "safe_build_report": identity(SAFE_REPORT),
        "exact_state_report": identity(EXACT_REPORT),
        "main_saveram": identity(TIP_SAVE),
        "user_authorization": (
            "user requested promotion based on "
            "intermission_static_exact_clean_color_candidate.wsc"
        ),
        "approved_labels": 12,
        "approved_unique_rom_tiles": len(tile_records),
        "changed_bytes_including_checksum": len(differences),
        "candidate_checksum": candidate[-2:].hex().upper(),
        "all_differences_allowlisted": True,
        "tile_records": tile_records,
        "state_only_scope_note": (
            "The supplied exact-coordinate and BG-ink-3/E appearance is serialized in "
            "the matching QuickSave states; the promoted ROM bytes are the shared-tile-safe "
            "static candidate identified by both reports."
        ),
    }


def postpromotion_audit(
    backup_rom: Path,
    save_before: dict[str, Any],
    candidate_identity: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    tip_data = TIP.read_bytes()
    save_after = identity(TIP_SAVE)
    checks = {
        "tip_matches_verified_candidate_sha": digest(TIP) == candidate_identity["sha256"],
        "tip_checksum_valid": checksum_ok(tip_data),
        "main_saveram_unchanged": save_after == save_before,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP_SHA,
        "all_differences_were_allowlisted": validation["all_differences_allowlisted"] is True,
        "twelve_label_manifest_preserved": validation["approved_labels"] == 12,
        "approved_tile_count_preserved": validation["approved_unique_rom_tiles"] == 153,
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_intermission_static_exact_clean_color_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_rom": identity(backup_rom),
        "main_saveram_before": save_before,
        "main_saveram_after": save_after,
        "checks": checks,
        "state_only_scope_note": validation["state_only_scope_note"],
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
        print(
            json.dumps(
                {"mode": "dry_run", "ok": True, "validation": validation},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_intermission_static_exact_clean_color"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_file(backup_rom, ROM_SIZE, EXPECTED_TIP_SHA)

    old_tip = identity(TIP)
    candidate_identity = identity(CANDIDATE)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "intermission-static-exact-clean-color-promote")
        audit = postpromotion_audit(
            backup_rom, save_before, candidate_identity, validation
        )
    except Exception:
        atomic_copy(backup_rom, TIP, "intermission-static-exact-clean-color-rollback")
        raise

    cleanup_result = cleanup(CLEANUP_PATHS)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_intermission_static_exact_clean_color_candidate.py",
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
        "main_saveram_policy": "live main SaveRAM remained untouched",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
