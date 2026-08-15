#!/usr/bin/env python3
"""Promote the validated Domon/Master Asia source-retranslation candidate."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/domon_master_asia_mt_source_retranslation_candidate.wsc"
BUILD_REPORT = ROOT / "out/patch/domon_master_asia_mt_source_retranslation_candidate_report.json"
WIDTH_REPORT = ROOT / "out/patch/domon_master_asia_mt_20cell.json"
FALSE_LEAD_REPORT = ROOT / "out/patch/domon_master_asia_mt_false_lead.json"
TERMINOLOGY_REPORT = ROOT / "out/patch/domon_master_asia_mt_terminology.json"
FALSE_SEGPTR_REPORT = ROOT / "out/patch/domon_master_asia_mt_false_segptr.json"
RUNTIME_CANDIDATE_REPORT = ROOT / "out/patch/domon_master_asia_mt_runtime_regression.json"
RUNTIME_PARENT_REPORT = ROOT / "out/patch/domon_master_asia_mt_runtime_regression_parent_baseline.json"
PROMOTION_REPORT = ROOT / "out/patch/domon_master_asia_mt_source_retranslation_promotion_report.json"

EXPECTED_PARENT = "27874d922b4a0233c7eb27a4da3361e71cd5ce32276fd86f0dca4cccaabcd918"
EXPECTED_CANDIDATE = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"


class PromoteError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_bytes(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def atomic_json(path: Path, obj: dict) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    parent_sha = sha256(parent)
    candidate_sha = sha256(candidate)
    save_sha = sha256(save_before)
    if parent_sha != EXPECTED_PARENT:
        raise PromoteError(f"main TIP drifted: {parent_sha}")
    if candidate_sha != EXPECTED_CANDIDATE:
        raise PromoteError(f"candidate drifted: {candidate_sha}")
    if len(save_before) != 32768:
        raise PromoteError("live SaveRAM size is not 32768")

    build = read_json(BUILD_REPORT)
    if build.get("status") != "ready_for_main_promotion":
        raise PromoteError("build report is not promotion-ready")
    if str((build.get("parent") or {}).get("sha256", "")).lower() != EXPECTED_PARENT:
        raise PromoteError("build report parent mismatch")
    if str((build.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromoteError("build report candidate mismatch")
    checks = build.get("checks") or {}
    if not checks or not all(value is True for value in checks.values()):
        raise PromoteError(f"build checks not all true: {checks}")
    if int((build.get("summary") or {}).get("targets", 0)) != 38:
        raise PromoteError("target population drifted")

    width = read_json(WIDTH_REPORT)
    width_population = width.get("population") or {}
    if width.get("ok") is not True or int(width_population.get("offender_records", -1)) != 0 or int(width_population.get("max_line_cells", 999)) > 20:
        raise PromoteError("20-cell audit failed")
    false_lead = read_json(FALSE_LEAD_REPORT)
    if false_lead.get("ok") is not True or int((false_lead.get("counts") or {}).get("reintroduced", -1)) != 0:
        raise PromoteError("false-visible-lead audit failed")
    terminology = read_json(TERMINOLOGY_REPORT)
    terminology_counts = terminology.get("counts") or {}
    if terminology.get("status") != "clean" or any(int(terminology_counts.get(key, -1)) != 0 for key in ("active_source_hits", "dictionary_hits", "rendered_record_hits")):
        raise PromoteError("terminology audit failed")
    false_segptr = read_json(FALSE_SEGPTR_REPORT)
    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found", -1)) != 0:
        raise PromoteError("false segmented-pointer audit failed")

    # This legacy audit currently lacks its removed dictionary-meta files and
    # therefore reports BADDICT on the parent itself.  Promotion is permitted
    # only when the candidate reproduces that exact pre-existing failure set.
    runtime_parent = read_json(RUNTIME_PARENT_REPORT)
    runtime_candidate = read_json(RUNTIME_CANDIDATE_REPORT)
    if runtime_parent.get("failures") != runtime_candidate.get("failures"):
        raise PromoteError("candidate changed the legacy runtime-regression failure set")
    if int((runtime_parent.get("counts") or {}).get("failures", -1)) != int((runtime_candidate.get("counts") or {}).get("failures", -2)):
        raise PromoteError("runtime-regression baseline count drifted")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "out/patch/backup" / f"{stamp}_pre_domon_master_asia_mt_source_retranslation"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(MAIN, backup_dir / MAIN.name)
    shutil.copy2(MAIN_SAVE, backup_dir / MAIN_SAVE.name)

    atomic_bytes(MAIN, candidate)
    main_after = MAIN.read_bytes()
    save_after = MAIN_SAVE.read_bytes()
    if sha256(main_after) != EXPECTED_CANDIDATE:
        raise PromoteError("post-promotion main identity mismatch")
    if sha256(save_after) != save_sha:
        raise PromoteError("live SaveRAM changed during promotion")

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_domon_master_asia_mt_source_retranslation_candidate.py",
        "status": "promoted",
        "promoted_at": datetime.now().isoformat(timespec="seconds"),
        "parent_sha256": EXPECTED_PARENT,
        "main_sha256": EXPECTED_CANDIDATE,
        "main_checksum": str((build.get("candidate") or {}).get("checksum")),
        "targets": int((build.get("summary") or {}).get("targets", 0)),
        "backup_dir": str(backup_dir.relative_to(ROOT)),
        "saveram": {
            "sha256_before": save_sha,
            "sha256_after": sha256(save_after),
            "preserved_byte_exact": True,
        },
        "audits": {
            "build_all_checks": True,
            "20cell_offenders": int(width_population.get("offender_records", -1)),
            "20cell_max": int(width_population.get("max_line_cells", -1)),
            "false_visible_lead_reintroduced": int((false_lead.get("counts") or {}).get("reintroduced", -1)),
            "false_segmented_pointer_sites": int(false_segptr.get("sites_found", -1)),
            "terminology_active_dictionary_rendered": [
                int(terminology_counts.get("active_source_hits", -1)),
                int(terminology_counts.get("dictionary_hits", -1)),
                int(terminology_counts.get("rendered_record_hits", -1)),
            ],
            "legacy_runtime_regression_baseline_failures": int((runtime_parent.get("counts") or {}).get("failures", -1)),
            "legacy_runtime_regression_candidate_same_as_parent": True,
            "p2_terminator_audit": "not runnable: historical approval JSON is absent; target builder directly pins every edited record extent and NUL terminator",
        },
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
