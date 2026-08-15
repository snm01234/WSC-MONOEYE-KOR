#!/usr/bin/env python3
"""Remove superseded Sig-ID diagnostic JSON files after final promotion.

Dry-run is the default. Pass --commit to delete only the explicitly classified
intermediate reports. Final build, validation, guard, audit, and promotion
evidence is preserved. The cleanup refuses to run if the published main TIP
identity has drifted or if the JSON population is unexpected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = PATCH / "sig_id_test_json_cleanup_report.json"

EXPECTED_TIP_SHA256 = "b24d72bcc18058ad248fbfdb9359948bf1bc3e06e23db6eba89623a143719180"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

PRESERVE = {
    "sig_id_5cb5c2_table_restore_report.json",
    "sig_id_5cb5c2_table_restore_false_segptr.json",
    "sig_id_5cb5c2_table_restore_structured_guard.json",
    "sig_id_5cb5c2_table_restore_user_validation.json",
    "sig_id_5cb5c2_table_restore_postpromotion_audit.json",
    "sig_id_5cb5c2_table_restore_promotion_report.json",
}

DELETE = {
    "sig_id_dictionary_irq_guard_false_segptr.json",
    "sig_id_original_delta_ab_report.json",
    "sig_id_original_dict_loader_probe_false_segptr.json",
    "sig_id_p2_0585_component_probe_report.json",
    "sig_id_p2_0585_dictionary_only_probe_false_segptr.json",
    "sig_id_p2_0585_external_detach_only_probe_candidate_false_segptr.json",
    "sig_id_p2_0585_external_table_split_probe_report.json",
    "sig_id_p2_0585_nested_detach_only_probe_candidate_false_segptr.json",
    "sig_id_p2_0585_other_eight_only_probe_candidate_false_segptr.json",
    "sig_id_p2_0585_slot_preserved_probe_false_segptr.json",
    "sig_id_p2_0585_table_5cb5c2_only_probe_candidate_false_segptr.json",
    "sig_id_p2_0585_table_5cb5c2_only_structured_guard_expected_fail.json",
    "sig_id_p2_0585_targets_only_probe_candidate_false_segptr.json",
    "sig_id_p2_0585_write_split_probe_report.json",
    "sig_id_p2_aux_detachment_restore_false_segptr.json",
    "sig_id_p2_aux_detachment_restore_report.json",
    "sig_id_p2_nested_group_0208_probe_false_segptr.json",
    "sig_id_p2_nested_group_0585_probe_false_segptr.json",
    "sig_id_p2_nested_group_probe_report.json",
    "sig_id_p2_stage_01_exact_reuse_false_segptr.json",
    "sig_id_p2_stage_02_true_free_false_segptr.json",
    "sig_id_p2_stage_03_stock_spill_false_segptr.json",
    "sig_id_p2_stage_06_duplicate_batch_false_segptr.json",
    "sig_id_p2_stage_07_08_report.json",
    "sig_id_p2_stage_07_nested_duplicate_false_segptr.json",
    "sig_id_p2_stage_08_local_ext3_false_segptr.json",
    "sig_id_p2_stage_08_local_ext3_fix0208_false_segptr.json",
    "sig_id_p2_stage_bisect_report.json",
}


class CleanupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


def validate() -> dict[str, Any]:
    if not TIP.is_file() or TIP.stat().st_size != ROM_SIZE:
        raise CleanupError("main TIP missing or wrong size")
    if sha256(TIP) != EXPECTED_TIP_SHA256:
        raise CleanupError("main TIP identity drifted")
    if not SAVE.is_file() or SAVE.stat().st_size != SAVE_SIZE:
        raise CleanupError("live SaveRAM missing or wrong size")

    current = {
        path.name
        for path in PATCH.glob("sig_id*.json")
        if path.is_file() and path.name != REPORT.name
    }
    expected = PRESERVE | DELETE
    missing = sorted(expected - current)
    unexpected = sorted(current - expected)
    if missing or unexpected:
        raise CleanupError(
            "Sig-ID JSON population drifted: "
            + json.dumps({"missing": missing, "unexpected": unexpected}, ensure_ascii=False)
        )

    for name in PRESERVE:
        path = PATCH / name
        if not path.is_file() or path.stat().st_size == 0:
            raise CleanupError(f"final evidence missing or empty: {name}")

    return {
        "tip": identity(TIP),
        "live_saveram": identity(SAVE),
        "preserved": [identity(PATCH / name) for name in sorted(PRESERVE)],
        "delete": [identity(PATCH / name) for name in sorted(DELETE)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    before = validate()
    recover_bytes = sum(int(row["size"]) for row in before["delete"])
    if not args.commit:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "ok": True,
                    "baseline": before,
                    "delete_count": len(DELETE),
                    "recover_bytes": recover_bytes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    save_before = identity(SAVE)
    deleted: list[dict[str, Any]] = []
    for name in sorted(DELETE):
        path = PATCH / name
        row = identity(path)
        path.unlink()
        deleted.append(row)

    remaining = {
        path.name
        for path in PATCH.glob("sig_id*.json")
        if path.is_file() and path.name != REPORT.name
    }
    if remaining != PRESERVE:
        raise CleanupError(
            "unexpected remaining Sig-ID JSON set: "
            + json.dumps(sorted(remaining), ensure_ascii=False)
        )
    if identity(SAVE) != save_before:
        raise CleanupError("live SaveRAM changed during JSON cleanup")
    if sha256(TIP) != EXPECTED_TIP_SHA256:
        raise CleanupError("main TIP changed during JSON cleanup")

    report = {
        "schema_version": 1,
        "generated_by": "tools/cleanup_sig_id_test_json_artifacts.py",
        "ok": True,
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tip": identity(TIP),
        "live_saveram": identity(SAVE),
        "deleted_count": len(deleted),
        "recovered_bytes": sum(int(row["size"]) for row in deleted),
        "deleted": deleted,
        "preserved": [identity(PATCH / name) for name in sorted(PRESERVE)],
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
