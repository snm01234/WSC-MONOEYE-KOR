#!/usr/bin/env python3
"""Promote the verified 108-record remaining-dialogue candidate to the main TIP.

The transaction is ROM-only.  The live main SaveRAM is observed before/after but
never replaced or restored.  A timestamped rollback ROM is created and verified
before the candidate is atomically installed.  The final TIP is then decoded at
all 108 target records, compared with the candidate-bound reports, and only after
that are the redundant candidate ROM/SaveRAM and intermediate structure scans
removed.
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
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_current_untranslated_dialogue import aux_body, classify_text  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STAGE_A = PATCH / "remaining_dialogue_ext3_candidate.wsc"
STAGE_A_SAVE = ROOT / "sram/remaining_dialogue_ext3_candidate.sav"
CANDIDATE = PATCH / "remaining_dialogue_complete_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/remaining_dialogue_complete_candidate.sav"
SOURCE_AUDIT = PATCH / "current_untranslated_dialogue_audit.json"
STAGE_A_REPORT = PATCH / "remaining_dialogue_ext3_report.json"
BUILD_REPORT = PATCH / "remaining_dialogue_complete_report.json"
INDEPENDENT_AUDIT = PATCH / "remaining_dialogue_candidate_audit.json"
STRUCTURE_GATE = PATCH / "remaining_dialogue_structure_delta_gate.json"
FALSE_SEGPTR_GATE = PATCH / "remaining_dialogue_false_segptr_gate.json"
POSTPROMOTION_AUDIT = PATCH / "remaining_dialogue_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "remaining_dialogue_promotion_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "exp_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"

PARENT_SHA = "31acde8c486b5ba13bc00b74ae019444608051478c5e0b874516e74f4cab8eb6"
STAGE_A_SHA = "fe8b6f05019d05034f23af2fe4577687ab6945c0c20ac26391cf8c034d7cf3fd"
CANDIDATE_SHA = "279ce819fa63bea6ee52a307dc920714bb530c2f32e344e2543134dfbccbf7f9"
SOURCE_AUDIT_SHA = "fb281cf7835647ac400e9e287930c7cebd60ca11e507a1bdba24b1e6cbea9680"
STAGE_A_REPORT_SHA = "beb770a956ef15a512728a760c622d88889368d93ee28f15e1b34e0c2349c23d"
BUILD_REPORT_SHA = "faa8a18dfb28b7856510c39f5a65e98dc1adfa24a1b3aea356346b3bbdc3f6cf"
INDEPENDENT_AUDIT_SHA = "4080be1a91a19e7fd6927d3ff1d60559f8ce80fa553c6ee2a21b925b5aaee924"
STRUCTURE_GATE_SHA = "9a1e0e06c13d3843fde3e282b8dbc94da1cc7fe4059ff3089f00f0e4b771b776"
FALSE_SEGPTR_GATE_SHA = "1a019677d2263a1c025dae7403617c37e700ea3803c9ccdb721f6ff21dddacfc"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

CLEANUP_PATHS = (
    STAGE_A,
    STAGE_A_SAVE,
    CANDIDATE,
    CANDIDATE_SAVE,
    PATCH / "remaining_dialogue_structure_parent.json",
    PATCH / "remaining_dialogue_structure_candidate.json",
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


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


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


def all_target_rows(source: dict[str, Any]) -> list[dict[str, Any]]:
    records = source.get("records") or {}
    rows: list[dict[str, Any]] = []
    for key in ("script_dialogue", "mission_dialogue", "battle_voice", "description_fragments"):
        for row in records.get(key) or []:
            item = dict(row)
            item["category"] = "description_fragment" if key == "description_fragments" else key
            rows.append(item)
    rows.sort(key=lambda row: int(str(row["abs"]), 16))
    return rows


def validate() -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, PARENT_SHA)
    require_file(STAGE_A, ROM_SIZE, STAGE_A_SHA)
    require_file(CANDIDATE, ROM_SIZE, CANDIDATE_SHA)
    require_file(TIP_SAVE, SAVE_SIZE)
    require_file(STAGE_A_SAVE, SAVE_SIZE)
    require_file(CANDIDATE_SAVE, SAVE_SIZE)

    source = load_json(SOURCE_AUDIT, SOURCE_AUDIT_SHA)
    stage_a = load_json(STAGE_A_REPORT, STAGE_A_REPORT_SHA)
    build = load_json(BUILD_REPORT, BUILD_REPORT_SHA)
    audit = load_json(INDEPENDENT_AUDIT, INDEPENDENT_AUDIT_SHA)
    structure = load_json(STRUCTURE_GATE, STRUCTURE_GATE_SHA)
    false_segptr = load_json(FALSE_SEGPTR_GATE, FALSE_SEGPTR_GATE_SHA)

    if source.get("ok") is not True or (source.get("counts") or {}).get("meaningful_untranslated_records") != 108:
        raise PromotionError("source audit is not the accepted 108-record population")
    if stage_a.get("ok") is not True or stage_a.get("published") is not False:
        raise PromotionError("stage-A report is not accepted/unpublished")
    if ((stage_a.get("parent") or {}).get("sha256")) != PARENT_SHA:
        raise PromotionError("stage-A parent binding mismatch")
    if ((stage_a.get("candidate") or {}).get("sha256")) != STAGE_A_SHA:
        raise PromotionError("stage-A candidate binding mismatch")
    stage_a_counts = stage_a.get("counts") or {}
    if stage_a_counts.get("targets") != 88 or stage_a_counts.get("target_failures") != 0 or stage_a_counts.get("unaccounted_diff_runs") != 0:
        raise PromotionError("stage-A count/gate mismatch")

    if build.get("ok") is not True or build.get("published") is not False:
        raise PromotionError("final build report is not accepted/unpublished")
    if ((build.get("candidate") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("final candidate binding mismatch")
    if ((build.get("parent_stage_a") or {}).get("sha256")) != STAGE_A_SHA:
        raise PromotionError("final stage-A binding mismatch")
    if ((build.get("source_audit") or {}).get("sha256")) != SOURCE_AUDIT_SHA:
        raise PromotionError("final source-audit binding mismatch")
    counts = build.get("counts") or {}
    if counts.get("all_targets_exact") != 108 or counts.get("stage_b_targets") != 20 or counts.get("target_failures") != 0 or counts.get("unaccounted_diff_runs") != 0:
        raise PromotionError("final build count/gate mismatch")

    if audit.get("ok") is not True:
        raise PromotionError("independent audit failed")
    audit_inputs = audit.get("inputs") or {}
    if ((audit_inputs.get("parent") or {}).get("sha256")) != PARENT_SHA:
        raise PromotionError("independent audit parent binding mismatch")
    if ((audit_inputs.get("final") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("independent audit candidate binding mismatch")
    audit_counts = audit.get("counts") or {}
    required_zero = (
        "final_japanese_residuals",
        "parent_binding_failures",
        "stage_a_target_failures",
        "stage_a_short_changed_early",
        "final_target_failures",
        "stage_a_unaccounted_diff_runs",
        "final_unaccounted_diff_runs",
    )
    if audit_counts.get("final_exact") != 108 or any(audit_counts.get(name) != 0 for name in required_zero):
        raise PromotionError("independent audit count mismatch")
    if (audit.get("preservation") or {}).get("main_tip_unchanged") is not True:
        raise PromotionError("independent audit did not preserve parent TIP")

    if structure.get("ok") is not True or structure.get("candidate_sha256") != CANDIDATE_SHA or structure.get("parent_sha256") != PARENT_SHA:
        raise PromotionError("structure delta gate mismatch")
    if structure.get("new_issues") or structure.get("missing_historical"):
        raise PromotionError("structure delta gate contains issues")
    if false_segptr.get("ok") is not True or false_segptr.get("sites_found") != 0:
        raise PromotionError("false segmented-pointer gate failed")
    target_sha = (((false_segptr.get("inputs") or {}).get("target") or {}).get("sha256"))
    if target_sha != CANDIDATE_SHA:
        raise PromotionError("false segmented-pointer target binding mismatch")

    rows = all_target_rows(source)
    if len(rows) != 108 or len({row["record_id"] for row in rows}) != 108:
        raise PromotionError("source target list is not 108 unique records")

    return {
        "parent_sha256": PARENT_SHA,
        "candidate_sha256": CANDIDATE_SHA,
        "targets": 108,
        "stage_a_targets": 88,
        "stage_b_targets": 20,
        "candidate_static_gates": "all_passed",
        "promotion_authorized_by_user": True,
        "visual_verification_note": "promotion explicitly requested; no separate per-scene visual checklist was supplied",
        "saveram_policy": "live main SaveRAM left untouched",
    }


def atomic_replace_tip() -> None:
    temporary = TIP.with_name(f".{TIP.name}.remaining-dialogue-promote.tmp")
    temporary.unlink(missing_ok=True)
    with CANDIDATE.open("rb") as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    require_file(temporary, ROM_SIZE, CANDIDATE_SHA)
    os.replace(temporary, TIP)


def diff_stats(before: bytes, after: bytes) -> dict[str, int]:
    if len(before) != len(after):
        raise PromotionError("diff inputs differ in size")
    changed = 0
    runs = 0
    inside = False
    for left, right in zip(before, after):
        different = left != right
        if different:
            changed += 1
        if different and not inside:
            runs += 1
        inside = different
    return {"changed_bytes": changed, "runs": runs}


def render_target(rom: bytes, dictionary: Any, row: dict[str, Any]) -> tuple[bytes, str]:
    logical = int(str(row["abs"]), 16)
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise PromotionError(f"unreadable post-promotion record: {logical:06X}")
    payload = bytes(got[0])
    category = str(row["category"])
    if category == "script_dialogue":
        _prefix, body, _kind = split_prefix_body(payload)
    elif category in {"mission_dialogue", "battle_voice"}:
        _prefix, body, _rule = aux_body(payload, logical >> 16)
    else:
        body = payload
    rendered = dictionary.expand(body, Tbl.load(TBL_PATH)).rstrip("\u3000 \t")
    return payload, rendered


def postpromotion_audit(parent_backup: Path, save_before: dict[str, Any]) -> dict[str, Any]:
    require_file(TIP, ROM_SIZE, CANDIDATE_SHA)
    if TIP.read_bytes() != CANDIDATE.read_bytes():
        raise PromotionError("installed TIP is not byte-identical to verified candidate")
    require_file(TIP_SAVE, SAVE_SIZE)
    save_after = identity(TIP_SAVE)
    if save_after["sha256"] != save_before["sha256"] or save_after["size"] != save_before["size"]:
        raise PromotionError("live main SaveRAM changed during promotion")

    final = TIP.read_bytes()
    before = parent_backup.read_bytes()
    source = load_json(SOURCE_AUDIT, SOURCE_AUDIT_SHA)
    rows = all_target_rows(source)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)
    tbl = Tbl.load(TBL_PATH)

    checked: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    for row in rows:
        logical = int(str(row["abs"]), 16)
        got = read_encoded_z_safe(final, stock_base(final) + logical, max_len=256)
        if got is None:
            failures.append({"record_id": row["record_id"], "reason": "unreadable"})
            continue
        payload = bytes(got[0])
        category = str(row["category"])
        if category == "script_dialogue":
            _prefix, body, _kind = split_prefix_body(payload)
        elif category in {"mission_dialogue", "battle_voice"}:
            _prefix, body, _rule = aux_body(payload, logical >> 16)
        else:
            body = payload
        rendered = dictionary.expand(body, tbl).rstrip("\u3000 \t")
        classified = classify_text(rendered)
        expected = str(row["ko"]).rstrip("\u3000 \t")
        ok = rendered == expected and int(classified["japanese"]) == 0
        if not ok:
            failures.append({
                "record_id": row["record_id"],
                "abs": row["abs"],
                "expected": expected,
                "actual": rendered,
                "japanese_chars": classified["japanese"],
            })
        checked.append({
            "record_id": row["record_id"],
            "abs": row["abs"],
            "category": category,
            "render": rendered,
            "ok": ok,
        })
        category_counts[category] = category_counts.get(category, 0) + 1
    if failures:
        raise PromotionError(f"post-promotion target verification failed: {len(failures)}")

    hook_lo = stock_base(final) + 0x7A0600
    hook_hi = stock_base(final) + 0x7A1000
    if before[hook_lo:hook_hi] != final[hook_lo:hook_hi]:
        raise PromotionError("runtime hook region changed")

    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_remaining_dialogue_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_source": identity(parent_backup),
        "main_saveram_before": save_before,
        "main_saveram_after": save_after,
        "checks": {
            "tip_matches_verified_candidate": True,
            "all_108_targets_render_exact": True,
            "target_japanese_residuals_zero": True,
            "runtime_hook_unchanged": True,
            "main_saveram_unchanged": True,
            "candidate_bound_structure_gate_transfers_by_exact_sha": True,
            "candidate_bound_false_segptr_gate_transfers_by_exact_sha": True,
        },
        "counts": {
            "targets_checked": len(checked),
            "target_failures": 0,
            "target_japanese_residuals": 0,
            "by_category": category_counts,
        },
        "diff_from_previous_tip": diff_stats(before, final),
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
        size = path.stat().st_size
        removed.append({"path": rel(path), "size": size, "sha256": digest(path)})
        path.unlink()
        reclaimed += size
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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_remaining_dialogue"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_file(backup_rom, ROM_SIZE, PARENT_SHA)

    save_before = identity(TIP_SAVE)
    candidate_before_cleanup = identity(CANDIDATE)
    stage_a_before_cleanup = identity(STAGE_A)

    atomic_replace_tip()
    final_audit = postpromotion_audit(backup_rom, save_before)
    cleanup = cleanup_files(CLEANUP_PATHS)

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_remaining_dialogue_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": {"size": ROM_SIZE, "sha256": PARENT_SHA},
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "validation": validation,
        "postpromotion_audit": identity(POSTPROMOTION_AUDIT),
        "postpromotion_checks": final_audit["checks"],
        "candidate_before_cleanup": candidate_before_cleanup,
        "stage_a_before_cleanup": stage_a_before_cleanup,
        "cleanup": cleanup,
        "main_saveram": {
            "before": save_before,
            "after": identity(TIP_SAVE),
            "action": "left_untouched",
        },
        "preserved_evidence": {
            "source_audit": identity(SOURCE_AUDIT),
            "stage_a_report": identity(STAGE_A_REPORT),
            "build_report": identity(BUILD_REPORT),
            "independent_audit": identity(INDEPENDENT_AUDIT),
            "structure_delta_gate": identity(STRUCTURE_GATE),
            "false_segptr_gate": identity(FALSE_SEGPTR_GATE),
            "postpromotion_audit": identity(POSTPROMOTION_AUDIT),
        },
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
