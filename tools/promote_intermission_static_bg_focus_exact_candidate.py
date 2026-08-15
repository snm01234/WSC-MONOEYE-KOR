#!/usr/bin/env python3
"""Promote the approved intermission static-BG focus-exact candidate to main TIP."""
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
from monoeye_rom import stock_base  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_DIR = PATCH / "intermission_static_bg_focus_exact_candidate"
CANDIDATE = CANDIDATE_DIR / "intermission_static_bg_focus_exact_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/intermission_static_bg_focus_exact_candidate.sav"
BUILD_REPORT = CANDIDATE_DIR / "static_bg_focus_exact_report.json"
AUDIT_REPORT = CANDIDATE_DIR / "independent_audit.json"
FOCUS_SWEEP_REPORT = CANDIDATE_DIR / "focus_sweep_report.json"
PARENT = (
    PATCH
    / "intermission_full_rebuild_clean16_candidate"
    / "intermission_full_rebuild_clean16_candidate.wsc"
)
PROMOTION_REPORT = PATCH / "intermission_static_bg_focus_exact_promotion_report.json"
POST_AUDIT = PATCH / "intermission_static_bg_focus_exact_postpromotion_audit.json"

EXPECTED_TIP_SHA = "3b0a07f82d97a90055957dc310b6a9dc713c4d4c6aa4c75586b286e255412da9"
EXPECTED_CANDIDATE_SHA = (
    "7bec1eee4cde0d39ba15eb23b12f90932e8a2fcfa358c7115d30b2aa92147e0c"
)
EXPECTED_PARENT_SHA = "2dcfb253b0488182ce061df7b4396918564e6049c31ecdce0d1a9f2a4dd834d7"
EXPECTED_CHANGED_TIP_TO_CAND = 13901
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Cumulative tip→candidate allowlist: clean16 transition rebuild + static BG
# private wrapper. Confirm/focus atlases and the older runtime hook stay intact.
ALLOWED_LOGICAL_RANGES = (
    (0x544400, 0x54B780, "steady-state static BG atlas"),
    (0x54B780, 0x550000, "full-screen transition overlay (clean16)"),
    (0x789C4D, 0x789C52, "renderer final-call hook"),
    (0x78FCD3, 0x790000, "guarded private-tile wrapper FF tail"),
    (0x79FA8F, 0x7A0000, "private tile payload FF tail"),
)

PRESERVED_LOGICAL_RANGES = (
    (0x542000, 0x544400, "normal focus atlas"),
    (0x547CFC, 0x549A1C, "confirmation focus atlas"),
    (0x7A0600, 0x7A1000, "existing multi-bank runtime hook"),
)


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


def require(path: Path, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"wrong size: {rel(path)}")
    if sha is not None and digest(path).lower() != sha.lower():
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


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
        shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    require(temporary, ROM_SIZE, digest(source))
    os.replace(temporary, target)


def validate() -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_TIP_SHA)
    require(TIP_SAVE, SAVE_SIZE)
    require(CANDIDATE, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require(CANDIDATE_SAVE, SAVE_SIZE)
    require(PARENT, ROM_SIZE, EXPECTED_PARENT_SHA)
    require(BUILD_REPORT)
    require(AUDIT_REPORT)
    require(FOCUS_SWEEP_REPORT)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    sweep = json.loads(FOCUS_SWEEP_REPORT.read_text(encoding="utf-8"))

    if str(build.get("candidate_rom_sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("builder report candidate binding mismatch")
    if str(build.get("base_rom_sha256") or "").lower() != EXPECTED_PARENT_SHA:
        raise PromotionError("builder report is not bound to clean16 parent")
    if audit.get("ok") is not True:
        raise PromotionError("independent audit is not ok")
    if str(audit.get("candidate_rom_sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("audit candidate binding mismatch")
    cases = sweep.get("cases") or []
    if len(cases) != 12:
        raise PromotionError(f"focus sweep cases={len(cases)}, expected 12")

    verification = build.get("verification") or {}
    required = (
        "static_model_target_cells_exact",
        "static_model_non_target_cells_unchanged",
        "focus_atlas_unchanged",
        "confirm_atlas_unchanged",
        "transition_overlay_header_unchanged",
        "existing_runtime_hook_unchanged",
        "private_slots_outside_focus_reserved_bank0",
        "private_slots_focus_state_safe",
        "private_entries_preserve_palette_and_flips",
    )
    missing = [name for name in required if verification.get(name) is not True]
    if missing:
        raise PromotionError(f"builder verification failed: {missing}")

    before = TIP.read_bytes()
    after = CANDIDATE.read_bytes()
    parent = PARENT.read_bytes()
    if not checksum_ok(after):
        raise PromotionError("candidate WonderSwan checksum is invalid")

    base = stock_base(after)
    allowed = bytearray(ROM_SIZE)
    for start, end, _ in ALLOWED_LOGICAL_RANGES:
        allowed[base + start : base + end] = b"\x01" * (end - start)
    allowed[-2:] = b"\x01\x01"

    changed = [
        index for index, (left, right) in enumerate(zip(before, after)) if left != right
    ]
    outside = [index for index in changed if not allowed[index]]
    if outside:
        raise PromotionError(f"candidate differs outside allowlist at {outside[0]:08X}")
    if len(changed) != EXPECTED_CHANGED_TIP_TO_CAND:
        raise PromotionError(
            f"changed-byte count drift tip→cand: {len(changed)} != {EXPECTED_CHANGED_TIP_TO_CAND}"
        )

    preserved = {}
    for start, end, role in PRESERVED_LOGICAL_RANGES:
        sl = slice(base + start, base + end)
        if before[sl] != after[sl]:
            raise PromotionError(f"preserved region changed: {role}")
        preserved[role] = True

    parent_allowed = bytearray(ROM_SIZE)
    for start, end, _ in (
        (0x544400, 0x54B780, "static"),
        (0x789C4D, 0x789C52, "hook"),
        (0x78FCD3, 0x790000, "wrapper"),
        (0x79FA8F, 0x7A0000, "private"),
    ):
        parent_allowed[base + start : base + end] = b"\x01" * (end - start)
    parent_allowed[-2:] = b"\x01\x01"
    parent_changed = [
        index for index, (left, right) in enumerate(zip(parent, after)) if left != right
    ]
    parent_outside = [index for index in parent_changed if not parent_allowed[index]]
    if parent_outside:
        raise PromotionError(
            f"parent→candidate differs outside static package at {parent_outside[0]:08X}"
        )

    return {
        "current_tip": identity(TIP),
        "candidate": identity(CANDIDATE),
        "parent_clean16": identity(PARENT),
        "main_saveram": identity(TIP_SAVE),
        "candidate_saveram": identity(CANDIDATE_SAVE),
        "builder_report": identity(BUILD_REPORT),
        "independent_audit": identity(AUDIT_REPORT),
        "focus_sweep_report": identity(FOCUS_SWEEP_REPORT),
        "changed_bytes_tip_to_candidate": len(changed),
        "changed_bytes_parent_to_candidate": len(parent_changed),
        "allowed_logical_ranges": [
            {"start": f"{start:06X}", "end_exclusive": f"{end:06X}", "role": role}
            for start, end, role in ALLOWED_LOGICAL_RANGES
        ],
        "preserved_regions": preserved,
        "checksum_stored": after[-2:][::-1].hex().upper(),
        "all_differences_allowlisted": True,
        "independent_audit_ok": True,
        "focus_sweep_cases": len(cases),
        "user_authorization": (
            "2026-08-06 request to promote static BG focus-exact candidate to main TIP"
        ),
        "saveram_policy": (
            "main SaveRAM is left untouched; candidate .sav hash is not a promotion gate"
        ),
    }


def audit(
    backup: Path, save_before: dict[str, Any], validation: dict[str, Any]
) -> dict[str, Any]:
    require(TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
    require(backup, ROM_SIZE, EXPECTED_TIP_SHA)
    tip_bytes = TIP.read_bytes()
    checks = {
        "tip_matches_verified_candidate": digest(TIP) == EXPECTED_CANDIDATE_SHA,
        "tip_checksum_valid": checksum_ok(tip_bytes),
        "main_saveram_unchanged": identity(TIP_SAVE) == save_before,
        "rollback_rom_preserved": digest(backup) == EXPECTED_TIP_SHA,
        "all_differences_allowlisted": validation["all_differences_allowlisted"] is True,
        "focus_atlas_preserved": validation["preserved_regions"]["normal focus atlas"]
        is True,
        "confirm_atlas_preserved": validation["preserved_regions"][
            "confirmation focus atlas"
        ]
        is True,
        "runtime_hook_preserved": validation["preserved_regions"][
            "existing multi-bank runtime hook"
        ]
        is True,
        "independent_audit_ok": validation["independent_audit_ok"] is True,
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    result = {
        "schema_version": 1,
        "generated_by": "tools/promote_intermission_static_bg_focus_exact_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_rom": identity(backup),
        "main_saveram_before": save_before,
        "main_saveram_after": identity(TIP_SAVE),
        "checks": checks,
    }
    atomic_json(POST_AUDIT, result)
    return result


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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_intermission_static_bg_focus_exact"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    require(backup, ROM_SIZE, EXPECTED_TIP_SHA)
    old_tip = identity(TIP)
    save_before = identity(TIP_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, "intermission-static-bg-focus-exact-promote")
        post = audit(backup, save_before, validation)
    except Exception:
        atomic_copy(backup, TIP, "intermission-static-bg-focus-exact-rollback")
        raise
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_intermission_static_bg_focus_exact_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup),
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": post["checks"],
        "main_saveram_policy": "live main SaveRAM left untouched",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
