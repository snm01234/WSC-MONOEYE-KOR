#!/usr/bin/env python3
"""Promote the cumulative broad-stage + UI width-v2 candidate to main TIP.

The transaction is ROM-only.  It validates the exact SHA chain from the current
main TIP through broad stage-2, width pass 1, and width pass 2; backs up the
current TIP; installs the candidate atomically; re-decodes all 658 cumulative
visible-text targets with the final override values; reruns residual, structure,
and false-segmented-pointer audits; verifies the live SaveRAM did not change;
and only then removes redundant candidate ROM/SaveRAM pairs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
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
CANDIDATE = PATCH / "ui_width_correction_v2_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ui_width_correction_v2_candidate.sav"

BROAD_FINAL_AUDIT = PATCH / "broad_stage2_final_candidate_audit.json"
WIDTH1_AUDIT = PATCH / "ui_width_correction_candidate_audit.json"
WIDTH2_BUILD = PATCH / "ui_width_correction_v2_report.json"
WIDTH2_AUDIT = PATCH / "ui_width_correction_v2_candidate_audit.json"
WIDTH2_RESIDUAL = PATCH / "ui_width_correction_v2_residual_audit.json"

STAGE2A_CATALOG = ROOT / "data/broad_stage2_ui_system_ko.json"
DIALOGUE_CATALOG = ROOT / "data/broad_stage2_dialogue_voice_ko.json"
TITLE_CATALOG = ROOT / "data/broad_stage2_title_ui_ko.json"
PLACEHOLDER_CATALOG = ROOT / "data/broad_stage2_placeholder_ko.json"
WIDTH1_SPEC = ROOT / "data/ui_width_corrections_ko.json"
WIDTH2_SPEC = ROOT / "data/ui_width_corrections_v2_ko.json"
STAGE2A_CLASSIFICATION = PATCH / "broad_japanese_residual_classification.json"

TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "ext_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"

POSTPROMOTION_AUDIT = PATCH / "ui_width_correction_v2_postpromotion_audit.json"
POSTPROMOTION_RESIDUAL = PATCH / "ui_width_correction_v2_postpromotion_residual_audit.json"
POSTPROMOTION_STRUCTURE = PATCH / "ui_width_correction_v2_postpromotion_structure.json"
POSTPROMOTION_FALSE_SEGPTR = PATCH / "ui_width_correction_v2_postpromotion_false_segptr.json"
PROMOTION_REPORT = PATCH / "ui_width_correction_v2_promotion_report.json"

PARENT_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
BROAD_CANDIDATE_SHA = "c9f7049873d6040c63d99144db709c80a163ba1ff679f58f139e8eadea47635c"
WIDTH1_CANDIDATE_SHA = "f1d2352a4384250df3e55fdf9ee507f366a11f12ab477cb07f4ee9a909c46c45"
CANDIDATE_SHA = "29f790238e02eb228db97619b9c55bc900495bfef2f76eb819d1abcfb5d430f0"

BROAD_FINAL_AUDIT_SHA = "8e6be201e7e9a386113eb7a36f413a07c63e30f68db5dd30f5448fcc8926df81"
WIDTH1_AUDIT_SHA = "0af5e7198bca3404d568f39e1fcc24cae0e994959fd992b0fe58ca548db49ada"
WIDTH2_BUILD_SHA = "78305643a7a369cd5588dba43375e5ff40a1ae1ece2385bd0420b2c7fe9a660e"
WIDTH2_AUDIT_SHA = "31a857b93bfc5b26fc6d27012ce81213dd20b62a7dfb02a63001a0fe0ce24d0d"
WIDTH2_RESIDUAL_SHA = "389d3151b24be69c7d3051224c10bd12b5a87f85f957ca4a0b77105975006622"

STAGE2A_CATALOG_SHA = "255f7581f4f9ddb4a89fb45b12704a4b32cd8562625f3b15f144b70b8250e7d9"
DIALOGUE_CATALOG_SHA = "fba2db8c37f3927bc749e53e7a7c53be0f329c7bbf9d85eb35e506d3fb8e7ec4"
TITLE_CATALOG_SHA = "d85b35e9aebd65805744a1eebdc44fb0d4c9801e671371edc724a65f3675e632"
PLACEHOLDER_CATALOG_SHA = "9b5885009c533449181736313cb9c57fe3847d36c81289282b3b3a9b24b8b8f4"
WIDTH1_SPEC_SHA = "6f1f82f572732904b74a03c4061d811041d805def36415978cc6754cb02fad2f"
WIDTH2_SPEC_SHA = "bb46e3122aa1187cd3e7275bb5e71f8bc3d9156e9dc279fe7d75f4ce6a4253dc"
STAGE2A_CLASSIFICATION_SHA = "2465d409de3bfc179479ac0a5e6a21d3a8eb0cfd942f552d37a3b0cf2d58e891"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 658
EXPECTED_RESIDUALS = 195
EXPECTED_STRUCTURE_ISSUES = 27

CLEANUP_PATHS = (
    PATCH / "broad_stage2_dialogue_voice_candidate.wsc",
    ROOT / "sram/broad_stage2_dialogue_voice_candidate.sav",
    PATCH / "broad_stage2_title_ui_candidate.wsc",
    ROOT / "sram/broad_stage2_title_ui_candidate.sav",
    PATCH / "broad_stage2_placeholder_candidate.wsc",
    ROOT / "sram/broad_stage2_placeholder_candidate.sav",
    PATCH / "ui_width_correction_candidate.wsc",
    ROOT / "sram/ui_width_correction_candidate.sav",
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


def require_file(path: Path, size: int, expected_sha: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size != size:
        raise PromotionError(f"invalid file: {rel(path)}")
    if expected_sha is not None and digest(path) != expected_sha:
        raise PromotionError(f"file SHA drifted: {rel(path)}")


def load_json(path: Path, expected_sha: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing JSON: {rel(path)}")
    if expected_sha is not None and digest(path) != expected_sha:
        raise PromotionError(f"JSON SHA drifted: {rel(path)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"invalid JSON root: {rel(path)}")
    return value


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
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


def report_binding(document: Mapping[str, Any], key: str) -> str:
    return str(((document.get("inputs") or {}).get(key) or {}).get("sha256") or "")


def validate_chain() -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, PARENT_SHA)
    require_file(CANDIDATE, ROM_SIZE, CANDIDATE_SHA)
    require_file(TIP_SAVE, SAVE_SIZE)
    require_file(CANDIDATE_SAVE, SAVE_SIZE)

    broad = load_json(BROAD_FINAL_AUDIT, BROAD_FINAL_AUDIT_SHA)
    width1 = load_json(WIDTH1_AUDIT, WIDTH1_AUDIT_SHA)
    build2 = load_json(WIDTH2_BUILD, WIDTH2_BUILD_SHA)
    width2 = load_json(WIDTH2_AUDIT, WIDTH2_AUDIT_SHA)
    residual2 = load_json(WIDTH2_RESIDUAL, WIDTH2_RESIDUAL_SHA)

    for label, document in (("broad", broad), ("width1", width1), ("width2", width2)):
        checks = document.get("checks") or {}
        if document.get("ok") is not True or not checks or not all(checks.values()):
            raise PromotionError(f"{label} independent audit did not pass every check")

    if report_binding(broad, "main") != PARENT_SHA or report_binding(broad, "final") != BROAD_CANDIDATE_SHA:
        raise PromotionError("broad-stage audit SHA chain mismatch")
    if report_binding(width1, "parent") != BROAD_CANDIDATE_SHA or report_binding(width1, "candidate") != WIDTH1_CANDIDATE_SHA:
        raise PromotionError("width-pass-1 audit SHA chain mismatch")
    if str((build2.get("parent") or {}).get("sha256") or "") != WIDTH1_CANDIDATE_SHA:
        raise PromotionError("width-pass-2 build parent mismatch")
    if str((build2.get("candidate") or {}).get("sha256") or "") != CANDIDATE_SHA:
        raise PromotionError("width-pass-2 build candidate mismatch")
    if build2.get("ok") is not True or build2.get("published") is not False:
        raise PromotionError("width-pass-2 build is not accepted/unpublished")
    counts2 = build2.get("counts") or {}
    if counts2.get("targets") != 4 or counts2.get("target_failures") != 0 or counts2.get("non_target_failures") != 0 or counts2.get("unaccounted_diff_runs") != 0:
        raise PromotionError("width-pass-2 build count gate mismatch")
    if report_binding(width2, "parent") != WIDTH1_CANDIDATE_SHA or report_binding(width2, "candidate") != CANDIDATE_SHA:
        raise PromotionError("width-pass-2 audit SHA chain mismatch")
    residual_inputs = residual2.get("inputs") or {}
    if residual2.get("ok") is not True or str((residual_inputs.get("tip") or {}).get("sha256") or "") != CANDIDATE_SHA:
        raise PromotionError("width-pass-2 residual audit binding mismatch")
    if int((residual2.get("counts") or {}).get("japanese_residual_records") or 0) != EXPECTED_RESIDUALS:
        raise PromotionError("width-pass-2 residual population mismatch")

    expected, prefixes = build_expected_targets()
    if len(expected) != EXPECTED_TARGETS or set(expected) != set(prefixes):
        raise PromotionError("cumulative target population is not 658 unique records")

    return {
        "parent_sha256": PARENT_SHA,
        "broad_candidate_sha256": BROAD_CANDIDATE_SHA,
        "width1_candidate_sha256": WIDTH1_CANDIDATE_SHA,
        "candidate_sha256": CANDIDATE_SHA,
        "cumulative_targets": len(expected),
        "remaining_japanese_residuals": EXPECTED_RESIDUALS,
        "candidate_static_gates": "all_passed",
        "promotion_authorized_by_user": True,
        "visual_verification_note": "user visually verified pass 1, reported four remaining overlaps, and explicitly requested promotion after pass-2 correction",
        "saveram_policy": "live main SaveRAM left untouched; candidate SaveRAM hash is not a promotion gate",
    }


def build_expected_targets() -> tuple[dict[str, str], dict[str, int]]:
    stage2a = load_json(STAGE2A_CATALOG, STAGE2A_CATALOG_SHA)
    dialogue = load_json(DIALOGUE_CATALOG, DIALOGUE_CATALOG_SHA)
    title = load_json(TITLE_CATALOG, TITLE_CATALOG_SHA)
    placeholder = load_json(PLACEHOLDER_CATALOG, PLACEHOLDER_CATALOG_SHA)
    width1 = load_json(WIDTH1_SPEC, WIDTH1_SPEC_SHA)
    width2 = load_json(WIDTH2_SPEC, WIDTH2_SPEC_SHA)
    classification = load_json(STAGE2A_CLASSIFICATION, STAGE2A_CLASSIFICATION_SHA)
    classification_by_abs = {str(row.get("abs") or "").upper(): row for row in classification.get("records") or []}

    expected: dict[str, str] = {}
    prefixes: dict[str, int] = {}

    def add(address: str, text: str, prefix_bytes: int) -> None:
        key = address.upper()
        if key in expected:
            raise PromotionError(f"duplicate cumulative target {key}")
        expected[key] = normalize_ko_text(text).rstrip("\u3000 \t")
        prefixes[key] = prefix_bytes

    for row in stage2a.get("lines") or []:
        address = str(row.get("abs") or "").upper()
        source = classification_by_abs.get(address)
        if source is None:
            raise PromotionError(f"stage2A classification row missing: {address}")
        add(address, str(row.get("ko") or ""), int(source.get("prefix_bytes") or 0))
    for row in dialogue.get("lines") or []:
        add(str(row.get("abs") or ""), str(row.get("ko") or ""), len(bytes.fromhex(str(row.get("prefix_hex") or ""))))
    for row in title.get("lines") or []:
        add(str(row.get("abs") or ""), str(row.get("ko") or ""), len(bytes.fromhex(str(row.get("prefix_hex") or ""))))
    for row in placeholder.get("lines") or []:
        add(str(row.get("abs") or ""), str(row.get("ko") or ""), len(bytes.fromhex(str(row.get("prefix_hex") or ""))))

    for override in width1.get("records") or []:
        address = str(override.get("abs") or "").upper()
        if address not in expected:
            raise PromotionError(f"width-pass-1 override is outside cumulative targets: {address}")
        expected[address] = normalize_ko_text(str(override.get("after") or "")).rstrip("\u3000 \t")
    for override in width2.get("records") or []:
        address = str(override.get("abs") or "").upper()
        if address not in expected:
            raise PromotionError(f"width-pass-2 override is outside cumulative targets: {address}")
        expected[address] = normalize_ko_text(str(override.get("after") or "")).rstrip("\u3000 \t")

    return expected, prefixes


def run_checked(arguments: list[str], allowed_returncodes: set[int] | None = None) -> None:
    allowed = allowed_returncodes or {0}
    completed = subprocess.run(arguments, cwd=ROOT, check=False)
    if completed.returncode not in allowed:
        raise PromotionError(f"post-promotion command failed ({completed.returncode}): {' '.join(arguments)}")


def issue_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row.get("abs"), row.get("kind"), row.get("orig_terminator"), row.get("target_terminator"), row.get("delta"))


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
    expected, prefixes = build_expected_targets()
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)
    base = stock_base(final)

    checked: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for address in sorted(expected, key=lambda value: int(value, 16)):
        logical = int(address, 16)
        got = read_encoded_z_safe(final, base + logical, max_len=256)
        if got is None:
            failures.append({"abs": address, "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        prefix_len = prefixes[address]
        rendered = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        japanese = sum(is_japanese_character(character) for character in rendered)
        ok = rendered == expected[address] and japanese == 0 and final[terminator] == 0
        row = {
            "abs": address,
            "expected": expected[address],
            "actual": rendered,
            "prefix_bytes": prefix_len,
            "japanese_characters": japanese,
            "ok": ok,
        }
        checked.append(row)
        if not ok:
            failures.append(row)
    if len(checked) != EXPECTED_TARGETS or failures:
        raise PromotionError(f"post-promotion cumulative target verification failed: {len(failures)}")

    width2 = load_json(WIDTH2_SPEC, WIDTH2_SPEC_SHA)
    by_abs = {row["abs"]: row for row in checked}
    width_checks: list[dict[str, Any]] = []
    for item in width2.get("records") or []:
        address = str(item.get("abs") or "").upper()
        rendered = str(by_abs[address]["actual"])
        cells = len(rendered)
        prefix_cells = int(item.get("dynamic_prefix_cells") or 0)
        combined = prefix_cells + cells
        limit = int(item.get("max_visual_cells") or 0)
        combined_limit = int(item.get("max_combined_cells") or 0)
        ok = cells <= limit and (not combined_limit or combined <= combined_limit)
        width_checks.append({
            "abs": address,
            "actual": rendered,
            "visual_cells": cells,
            "max_visual_cells": limit,
            "dynamic_prefix_cells": prefix_cells,
            "combined_cells": combined,
            "max_combined_cells": combined_limit,
            "ok": ok,
        })
    if len(width_checks) != 4 or not all(row["ok"] for row in width_checks):
        raise PromotionError("post-promotion width-v2 limits failed")

    run_checked([sys.executable, str(ROOT / "tools/audit_broad_japanese_residuals.py"), "--tip", str(TIP), "--out", str(POSTPROMOTION_RESIDUAL)])
    residual = load_json(POSTPROMOTION_RESIDUAL)
    if residual.get("ok") is not True or int((residual.get("counts") or {}).get("japanese_residual_records") or 0) != EXPECTED_RESIDUALS:
        raise PromotionError("post-promotion residual population mismatch")
    if str((((residual.get("inputs") or {}).get("tip") or {}).get("sha256") or "")) != CANDIDATE_SHA:
        raise PromotionError("post-promotion residual audit TIP binding mismatch")

    run_checked(
        [sys.executable, str(ROOT / "tools/scan_script_record_structure.py"), "--target", str(TIP), "--out", str(POSTPROMOTION_STRUCTURE)],
        allowed_returncodes={0, 1},
    )
    structure = load_json(POSTPROMOTION_STRUCTURE)
    if int(structure.get("issues") or 0) != EXPECTED_STRUCTURE_ISSUES:
        raise PromotionError("post-promotion structure issue population mismatch")

    run_checked([sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--out", str(POSTPROMOTION_FALSE_SEGPTR)])
    false_segptr = load_json(POSTPROMOTION_FALSE_SEGPTR)
    if int(false_segptr.get("sites_found") or 0) != 0:
        raise PromotionError("post-promotion false segmented-pointer writes detected")

    width2_audit = load_json(WIDTH2_AUDIT, WIDTH2_AUDIT_SHA)
    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_ui_width_correction_v2_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_source": identity(backup_rom),
        "main_saveram_before": save_before,
        "main_saveram_after": save_after,
        "checks": {
            "tip_matches_verified_candidate": True,
            "all_658_cumulative_targets_render_exact": True,
            "target_japanese_residuals_zero": True,
            "all_four_width_v2_limits_pass": True,
            "remaining_japanese_residuals_195": True,
            "structure_issues_27_inherited": True,
            "false_segmented_pointer_writes_zero": True,
            "candidate_independent_gate_transfers_by_exact_sha": width2_audit.get("ok") is True,
            "main_saveram_unchanged": True,
        },
        "counts": {
            "targets_checked": len(checked),
            "target_failures": 0,
            "width_v2_checks": len(width_checks),
            "remaining_japanese_residuals": EXPECTED_RESIDUALS,
            "structure_issues": EXPECTED_STRUCTURE_ISSUES,
            "false_segmented_pointer_writes": 0,
        },
        "diff_from_previous_tip": diff_stats(previous, final),
        "width_v2_records": width_checks,
        "records": checked,
        "postpromotion_residual": identity(POSTPROMOTION_RESIDUAL),
        "postpromotion_structure": identity(POSTPROMOTION_STRUCTURE),
        "postpromotion_false_segptr": identity(POSTPROMOTION_FALSE_SEGPTR),
    }
    if not all(audit["checks"].values()):
        raise PromotionError("post-promotion audit check aggregation failed")
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

    validation = validate_chain()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_broad_stage2_ui_width_v2"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_file(backup_rom, ROM_SIZE, PARENT_SHA)

    save_before = identity(TIP_SAVE)
    old_tip = identity(TIP)
    candidate_before_cleanup = identity(CANDIDATE)

    try:
        atomic_copy(CANDIDATE, TIP, "ui-width-v2-promote")
        final_audit = postpromotion_audit(backup_rom, save_before)
    except Exception:
        atomic_copy(backup_rom, TIP, "ui-width-v2-rollback")
        raise

    cleanup = cleanup_files(CLEANUP_PATHS)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_ui_width_correction_v2_candidate.py",
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
            "broad_final_audit": identity(BROAD_FINAL_AUDIT),
            "width1_audit": identity(WIDTH1_AUDIT),
            "width2_build": identity(WIDTH2_BUILD),
            "width2_audit": identity(WIDTH2_AUDIT),
            "width2_candidate_residual": identity(WIDTH2_RESIDUAL),
            "width2_spec": identity(WIDTH2_SPEC),
            "postpromotion_audit": identity(POSTPROMOTION_AUDIT),
            "postpromotion_residual": identity(POSTPROMOTION_RESIDUAL),
            "postpromotion_structure": identity(POSTPROMOTION_STRUCTURE),
            "postpromotion_false_segptr": identity(POSTPROMOTION_FALSE_SEGPTR),
        },
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
