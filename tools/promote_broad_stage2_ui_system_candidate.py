#!/usr/bin/env python3
"""Promote the verified broad stage-2A UI/system candidate to the main TIP.

The transaction is ROM-only. The current main TIP is backed up and verified,
the candidate is installed atomically, 94 reviewed records are decoded again,
and the live main SaveRAM is required to remain unchanged. After successful
post-promotion verification, redundant shared/stage-1/stage-2 candidate ROM and
SaveRAM pairs are removed while all reports and rollback evidence are retained.
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
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402

TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "broad_stage2_ui_system_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/broad_stage2_ui_system_candidate.sav"
BUILD_REPORT = PATCH / "broad_stage2_ui_system_report.json"
INDEPENDENT_AUDIT = PATCH / "broad_stage2_ui_system_candidate_audit.json"
RESIDUAL_AUDIT = PATCH / "broad_japanese_residual_after_stage2_ui_audit.json"
CLASSIFICATION = PATCH / "broad_japanese_residual_classification.json"
CATALOG = ROOT / "data/broad_stage2_ui_system_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "ext_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"
POSTPROMOTION_AUDIT = PATCH / "broad_stage2_ui_system_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "broad_stage2_ui_system_promotion_report.json"

PARENT_SHA = "279ce819fa63bea6ee52a307dc920714bb530c2f32e344e2543134dfbccbf7f9"
CANDIDATE_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
BUILD_REPORT_SHA = "9089bd3d15452b524dd9d9d2d69baee08b4d0b72b70600e46842c5c2d16c6c67"
INDEPENDENT_AUDIT_SHA = "ed8c27f04ac04a61d7200bb79401d78a70f905376f24c19f88e4203b67346392"
RESIDUAL_AUDIT_SHA = "36161c74b1d71b94cc877eaa454c453dabc9963c85f7de3fd0e7037cf894e021"
CLASSIFICATION_SHA = "2465d409de3bfc179479ac0a5e6a21d3a8eb0cfd942f552d37a3b0cf2d58e891"
CATALOG_SHA = "255f7581f4f9ddb4a89fb45b12704a4b32cd8562625f3b15f144b70b8250e7d9"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 94
EXPECTED_RESIDUALS = 759

CLEANUP_PATHS = (
    PATCH / "shared_dictionary_cleanup_candidate.wsc",
    ROOT / "sram/shared_dictionary_cleanup_candidate.sav",
    PATCH / "broad_residual_stage1_candidate.wsc",
    ROOT / "sram/broad_residual_stage1_candidate.sav",
    CANDIDATE,
    CANDIDATE_SAVE,
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
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def load_json(path: Path, expected_sha: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing report: {rel(path)}")
    if expected_sha is not None and digest(path) != expected_sha:
        raise PromotionError(f"report SHA drifted: {rel(path)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"invalid report root: {rel(path)}")
    return value


def require_file(path: Path, size: int, expected_sha: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise PromotionError(f"invalid file: {rel(path)}")
    if expected_sha is not None and digest(path) != expected_sha:
        raise PromotionError(f"file SHA drifted: {rel(path)}")


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path, label: str) -> None:
    temporary = target.with_name(f".{target.name}.{label}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as input_handle, temporary.open("wb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    require_file(temporary, ROM_SIZE, digest(source))
    os.replace(temporary, target)


def validate() -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, PARENT_SHA)
    require_file(CANDIDATE, ROM_SIZE, CANDIDATE_SHA)
    require_file(TIP_SAVE, SAVE_SIZE)
    require_file(CANDIDATE_SAVE, SAVE_SIZE)

    build = load_json(BUILD_REPORT, BUILD_REPORT_SHA)
    audit = load_json(INDEPENDENT_AUDIT, INDEPENDENT_AUDIT_SHA)
    residual = load_json(RESIDUAL_AUDIT, RESIDUAL_AUDIT_SHA)
    classification = load_json(CLASSIFICATION, CLASSIFICATION_SHA)
    catalog = load_json(CATALOG, CATALOG_SHA)

    if build.get("ok") is not True or build.get("published") is not False:
        raise PromotionError("build report is not accepted/unpublished")
    if ((build.get("main_tip") or {}).get("sha256")) != PARENT_SHA:
        raise PromotionError("build parent TIP binding mismatch")
    if ((build.get("candidate") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("build candidate binding mismatch")
    counts = build.get("counts") or {}
    if (
        counts.get("targets") != EXPECTED_TARGETS
        or counts.get("target_failures") != 0
        or counts.get("non_target_failures") != 0
        or counts.get("unaccounted_diff_runs") != 0
    ):
        raise PromotionError("build count or bounded-diff gate mismatch")

    checks = audit.get("checks") or {}
    audit_inputs = audit.get("inputs") or {}
    if audit.get("ok") is not True or not checks or not all(checks.values()):
        raise PromotionError("independent audit did not pass every check")
    if ((audit_inputs.get("main_rom") or {}).get("sha256")) != PARENT_SHA:
        raise PromotionError("independent audit parent binding mismatch")
    if ((audit_inputs.get("candidate_rom") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("independent audit candidate binding mismatch")
    audit_counts = audit.get("counts") or {}
    if audit_counts.get("targets") != EXPECTED_TARGETS or audit_counts.get("target_failures") != 0:
        raise PromotionError("independent audit target count mismatch")

    residual_inputs = residual.get("inputs") or {}
    residual_counts = residual.get("counts") or {}
    if residual.get("ok") is not True:
        raise PromotionError("candidate residual audit failed")
    if ((residual_inputs.get("tip") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("candidate residual audit binding mismatch")
    if residual_counts.get("japanese_residual_records") != EXPECTED_RESIDUALS:
        raise PromotionError("candidate residual population mismatch")

    if classification.get("ok") is not True:
        raise PromotionError("classification report failed")
    lines = catalog.get("lines") or []
    if len(lines) != EXPECTED_TARGETS or len({str(row.get("abs")) for row in lines}) != EXPECTED_TARGETS:
        raise PromotionError("catalog is not 94 unique records")

    return {
        "parent_sha256": PARENT_SHA,
        "candidate_sha256": CANDIDATE_SHA,
        "targets": EXPECTED_TARGETS,
        "remaining_japanese_residuals": EXPECTED_RESIDUALS,
        "candidate_static_gates": "all_passed",
        "promotion_authorized_by_user": True,
        "visual_verification_note": "user reported no issues and explicitly authorized main TIP promotion",
        "saveram_policy": "live main SaveRAM left untouched; candidate SaveRAM hash is not a promotion gate",
    }


def diff_stats(before: bytes, after: bytes) -> dict[str, int]:
    if len(before) != len(after):
        raise PromotionError("diff inputs differ in size")
    changed = 0
    runs = 0
    inside = False
    for left, right in zip(before, after):
        different = left != right
        changed += int(different)
        if different and not inside:
            runs += 1
        inside = different
    return {"changed_bytes": changed, "runs": runs}


def postpromotion_audit(backup_rom: Path, save_before: dict[str, Any]) -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, CANDIDATE_SHA)
    if TIP.read_bytes() != CANDIDATE.read_bytes():
        raise PromotionError("installed TIP is not byte-identical to verified candidate")
    save_after = identity(TIP_SAVE)
    if save_after != save_before:
        raise PromotionError("live main SaveRAM changed during promotion")

    final = TIP.read_bytes()
    previous = backup_rom.read_bytes()
    build = load_json(BUILD_REPORT, BUILD_REPORT_SHA)
    classification = load_json(CLASSIFICATION, CLASSIFICATION_SHA)
    catalog = load_json(CATALOG, CATALOG_SHA)
    residual = load_json(RESIDUAL_AUDIT, RESIDUAL_AUDIT_SHA)
    by_abs = {
        str(row.get("abs") or "").upper(): row
        for row in classification.get("records") or []
    }
    build_by_abs = {
        str(row.get("abs") or "").upper(): row
        for row in build.get("records") or []
    }

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)
    base = stock_base(final)

    checked: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for translation in catalog.get("lines") or []:
        address = str(translation.get("abs") or "").upper()
        source = by_abs.get(address)
        applied = build_by_abs.get(address)
        if source is None or applied is None:
            failures.append({"abs": address, "reason": "source_or_build_row_missing"})
            continue
        logical = int(address, 16)
        got = read_encoded_z_safe(final, base + logical, max_len=256)
        if got is None:
            failures.append({"abs": address, "reason": "unreadable"})
            continue
        payload = bytes(got[0])
        prefix_len = int(source.get("prefix_bytes") or 0)
        expected = normalize_ko_text(str(translation.get("ko") or "")).rstrip("\u3000 \t")
        rendered = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        japanese = sum(is_japanese_character(character) for character in rendered)
        ok = (
            rendered == expected
            and japanese == 0
            and str(applied.get("after") or "").rstrip("\u3000 \t") == expected
        )
        row = {
            "abs": address,
            "record_id": source.get("record_id"),
            "expected": expected,
            "actual": rendered,
            "japanese_characters": japanese,
            "ok": ok,
        }
        checked.append(row)
        if not ok:
            failures.append(row)

    if len(checked) != EXPECTED_TARGETS or failures:
        raise PromotionError(f"post-promotion target verification failed: {len(failures)}")

    residual_counts = residual.get("counts") or {}
    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_broad_stage2_ui_system_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_source": identity(backup_rom),
        "main_saveram_before": save_before,
        "main_saveram_after": save_after,
        "checks": {
            "tip_matches_verified_candidate": True,
            "all_94_targets_render_exact": True,
            "target_japanese_residuals_zero": True,
            "candidate_residual_audit_transfers_by_exact_sha": True,
            "independent_candidate_gate_transfers_by_exact_sha": True,
            "main_saveram_unchanged": True,
        },
        "counts": {
            "targets_checked": len(checked),
            "target_failures": 0,
            "target_japanese_residuals": 0,
            "remaining_japanese_residuals": int(residual_counts.get("japanese_residual_records") or 0),
        },
        "diff_from_previous_tip": diff_stats(previous, final),
        "records": checked,
    }
    atomic_json(POSTPROMOTION_AUDIT, audit)
    return audit


def cleanup_files(paths: Iterable[Path]) -> dict[str, Any]:
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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_broad_stage2_ui_system"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_file(backup_rom, ROM_SIZE, PARENT_SHA)

    save_before = identity(TIP_SAVE)
    candidate_before_cleanup = identity(CANDIDATE)
    old_tip = identity(TIP)

    try:
        atomic_copy(CANDIDATE, TIP, "broad-stage2-promote")
        final_audit = postpromotion_audit(backup_rom, save_before)
    except Exception:
        atomic_copy(backup_rom, TIP, "broad-stage2-rollback")
        raise

    cleanup = cleanup_files(CLEANUP_PATHS)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_broad_stage2_ui_system_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "validation": validation,
        "postpromotion_audit": identity(POSTPROMOTION_AUDIT),
        "postpromotion_checks": final_audit["checks"],
        "candidate_before_cleanup": candidate_before_cleanup,
        "cleanup": cleanup,
        "main_saveram": {
            "before": save_before,
            "after": identity(TIP_SAVE),
            "action": "left_untouched",
        },
        "preserved_evidence": {
            "build_report": identity(BUILD_REPORT),
            "independent_audit": identity(INDEPENDENT_AUDIT),
            "candidate_residual_audit": identity(RESIDUAL_AUDIT),
            "classification": identity(CLASSIFICATION),
            "catalog": identity(CATALOG),
            "postpromotion_audit": identity(POSTPROMOTION_AUDIT),
        },
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
