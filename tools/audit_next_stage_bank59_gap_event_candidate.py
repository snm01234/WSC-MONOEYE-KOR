#!/usr/bin/env python3
"""Independent static audit for the uncovered bank59 event-gap candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base, update_ws_checksum

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/next_stage_bank59_gap_event_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/next_stage_bank59_gap_event_candidate.sav"
CATALOG = ROOT / "data/next_stage_bank59_gap_event_ko.json"
BUILD = ROOT / "out/patch/next_stage_bank59_gap_event_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/next_stage_bank59_gap_event_candidate_audit.json"

EXPECTED_PARENT = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
EXPECTED_CANDIDATE = "85010f11e8b3b0bab145fa00fa2c830f862f38a9a87ea165d9f04d283a50858c"
EXPECTED_TARGETS = {
    "593E90", "593EA2", "593EB4", "593ECB", "593ED5",
    "593EEA", "593EF5", "593F04", "593F14",
}
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def main() -> int:
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    if sha(parent) != EXPECTED_PARENT or sha(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("parent/candidate identity drifted")
    catalog = load(CATALOG)
    build = load(BUILD)
    if build.get("ok") is not True:
        raise AuditError("build report failed")
    rows = [dict(row) for row in catalog.get("records") or []]
    if {str(row.get("abs") or "").upper() for row in rows} != EXPECTED_TARGETS:
        raise AuditError("target population drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    failures: list[dict[str, Any]] = []
    target_checks: list[dict[str, Any]] = []
    target_logicals: set[int] = set()
    target_extents: list[tuple[int, int]] = []
    for row in sorted(rows, key=lambda item: int(str(item["abs"]), 16)):
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload_capacity = int(row["payload_capacity"])
        body_capacity = int(row["body_capacity"])
        parent_payload = parent[sb + logical : sb + logical + payload_capacity]
        candidate_payload = candidate[sb + logical : sb + logical + payload_capacity]
        actual = candidate_dictionary.expand(candidate_payload[len(prefix):], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        reasons: list[str] = []
        if parent_payload.hex().upper() != str(row["current_payload_hex"]).upper():
            reasons.append("parent_payload_not_bound")
        if candidate_payload[:len(prefix)] != prefix:
            reasons.append("prefix_changed")
        if actual != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")
        if len(candidate_payload) != payload_capacity or body_capacity != payload_capacity - len(prefix):
            reasons.append("capacity_changed")
        if parent[sb + logical + payload_capacity] != 0 or candidate[sb + logical + payload_capacity] != 0:
            reasons.append("terminator_changed")
        check = {
            "abs": address,
            "jp": row["jp"],
            "expected": expected,
            "actual": actual,
            "prefix_hex": prefix.hex().upper(),
            "payload_hex": candidate_payload.hex().upper(),
            "ok": not reasons,
            "reasons": reasons,
        }
        target_checks.append(check)
        if reasons:
            failures.append(check)
        target_logicals.add(logical)
        target_extents.append((sb + logical + len(prefix), sb + logical + payload_capacity))

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )
    runs = diff_runs(parent, candidate)
    build_runs = build.get("diff") or {}
    checksum_probe = bytearray(candidate)
    checksum = update_ws_checksum(checksum_probe)
    checksum_exact = bytes(checksum_probe) == candidate
    allowed_non_dictionary = target_extents + [(len(parent) - 2, len(parent))]
    # Dictionary allocations are independently bounded by the build report's
    # verified page accounting; this audit focuses on unexpected changes outside
    # target bodies, banks21-25, and checksum.
    unaccounted = []
    for lo, hi in runs:
        in_dictionary = any(
            lo >= segment * 0x10000 and hi <= (segment + 1) * 0x10000
            for segment in range(0x21, 0x26)
        )
        if not in_dictionary and not covered((lo, hi), allowed_non_dictionary):
            unaccounted.append({"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"})

    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2] == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_dictionary_exact = all(
        parent[segment * 0x10000 : (segment + 1) * 0x10000]
        == candidate[segment * 0x10000 : (segment + 1) * 0x10000]
        for segment in list(range(0x11, 0x21)) + [0x77]
    )
    checks = {
        "parent_identity_exact": sha(parent) == EXPECTED_PARENT,
        "candidate_identity_exact": sha(candidate) == EXPECTED_CANDIDATE,
        "build_report_bound": str((build.get("candidate") or {}).get("sha256", "")).lower() == EXPECTED_CANDIDATE,
        "targets_exactly_9": len(rows) == 9,
        "all_targets_exact": not failures,
        "non_target_invariance": invariance.get("ok") is True,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_dictionary_banks_exact": old_dictionary_exact,
        "diffs_outside_targets_dictionary_checksum_zero": not unaccounted,
        "checksum_exact": checksum_exact,
        "candidate_saveram_present_and_sized": CANDIDATE_SAVE.is_file() and CANDIDATE_SAVE.stat().st_size == SAVE_SIZE,
        "main_tip_unchanged": sha(PARENT.read_bytes()) == EXPECTED_PARENT,
        "build_diff_run_count_exact": int(build_runs.get("runs", -1)) == len(runs),
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_next_stage_bank59_gap_event_candidate.py",
        "read_only": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_user_visual_test" if ok else "audit_failed",
        "parent": identity(PARENT, parent),
        "candidate": identity(CANDIDATE, candidate),
        "counts": {
            "targets": len(rows),
            "target_failures": len(failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "checksum": f"{checksum:04X}",
        "checks": checks,
        "target_checks": target_checks,
        "failures": failures,
        "non_target_invariance": invariance,
        "unaccounted_diff_runs": unaccounted,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        raise AuditError("candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
