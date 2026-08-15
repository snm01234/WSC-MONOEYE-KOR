#!/usr/bin/env python3
"""Independently audit one cumulative uncovered-text batch candidate."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_character_all_remaining_candidate import atomic_json, sha256
from build_remaining_dialogue_candidate import diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text
from scan_script_record_structure import DEFAULT_HI, DEFAULT_JP, DEFAULT_LO, scan as scan_script_structure

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
MAIN_SHA256 = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
ROM_SIZE = 16_777_216


class AuditError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def batch_path(batch_id: str, suffix: str) -> Path:
    return ROOT / f"out/patch/uncovered_batch_{batch_id}_{suffix}"


def physical_bank(offset: int) -> int:
    return offset // BANK_SIZE


def read_record_bytes(rom: bytes, logical: int) -> bytes:
    start = stock_base(rom) + logical
    end = rom.index(0, start, start + 256)
    return rom[start:end + 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", default="E001")
    args = parser.parse_args(argv)
    batch_id = args.batch_id.upper()

    report_path = batch_path(batch_id, "report.json")
    report = load_object(report_path)
    if report.get("ok") is not True or report.get("batch_id") != batch_id:
        raise AuditError("build report did not pass or batch id drifted")

    main = bytes(load_rom(MAIN))
    if len(main) != ROM_SIZE or sha256(main) != MAIN_SHA256:
        raise AuditError("main TIP identity drifted")

    parent_info = report.get("parent") or {}
    candidate_info = report.get("candidate") or {}
    parent_path = ROOT / str(parent_info.get("path") or "")
    candidate_path = ROOT / str(candidate_info.get("path") or "")
    parent = bytes(load_rom(parent_path))
    candidate = bytes(load_rom(candidate_path))
    if len(parent) != ROM_SIZE or sha256(parent) != str(parent_info.get("sha256") or ""):
        raise AuditError("parent identity mismatch")
    if len(candidate) != ROM_SIZE or sha256(candidate) != str(candidate_info.get("sha256") or ""):
        raise AuditError("candidate identity mismatch")

    parent_batch = str(report.get("parent_batch") or "")
    if parent_batch == "C000":
        parent_audit_path = ROOT / "out/patch/next_stage_event_id_indirect_candidate_audit.json"
    else:
        parent_audit_path = batch_path(parent_batch, "audit.json")
    parent_audit = load_object(parent_audit_path)
    parent_audit_candidate = parent_audit.get("candidate") or {}
    parent_audit_ok = (
        parent_audit.get("ok") is True
        and str(parent_audit_candidate.get("sha256") or "") == sha256(parent)
    )

    sheet_path = ROOT / str((report.get("sheet") or {}).get("path") or "")
    with sheet_path.open(encoding="utf-8-sig", newline="") as stream:
        sources = [dict(row) for row in csv.DictReader(stream)]
    if not sources or any(row.get("batch_id") != batch_id for row in sources):
        raise AuditError("batch sheet population drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)

    target_checks: list[dict[str, Any]] = []
    target_logicals: set[int] = set()
    target_physical_banks: set[int] = set()
    for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        target_logicals.add(logical)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source["payload_capacity"])
        body_capacity = int(source["body_capacity"])
        expected = normalize_ko_text(str(source.get("ko") or ""))
        start = sb + logical
        payload = candidate[start:start + payload_capacity]
        actual = candidate_dictionary.expand(payload[len(prefix):], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[:len(prefix)] != prefix: reasons.append("prefix_changed")
        if body_capacity != payload_capacity - len(prefix): reasons.append("sheet_boundary_invalid")
        if candidate[start + payload_capacity] != 0: reasons.append("terminator_changed")
        if actual != expected: reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual): reasons.append("japanese_residual")
        target_checks.append({
            "abs": address,
            "expected": expected,
            "actual": actual,
            "payload_hex": payload.hex().upper(),
            "ok": not reasons,
            "reasons": reasons,
        })
        target_physical_banks.add(physical_bank(start))

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )

    runs = diff_runs(parent, candidate)
    changed_banks = {physical_bank(lo) for lo, _hi in runs}
    allowed_banks = set(range(0x21, 0x26)) | target_physical_banks | {0xFF}
    unexpected_banks = sorted(changed_banks - allowed_banks)

    runtime_exact = (
        parent[sb + 0x7A0000:sb + 0x7B0000] == candidate[sb + 0x7A0000:sb + 0x7B0000]
        and parent[sb + 0x7F0000:sb + 0x800000 - 2] == candidate[sb + 0x7F0000:sb + 0x800000 - 2]
    )
    old_dictionary_exact = all(
        parent[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        == candidate[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    checksum_copy = bytearray(candidate)
    checksum = update_ws_checksum(checksum_copy)
    checksum_exact = bytes(checksum_copy) == candidate

    parent_structure = scan_script_structure(DEFAULT_JP, parent_path, DEFAULT_LO, DEFAULT_HI)
    candidate_structure = scan_script_structure(DEFAULT_JP, candidate_path, DEFAULT_LO, DEFAULT_HI)
    structure_regression_exact = (
        parent_structure.get("issues") == candidate_structure.get("issues")
        and parent_structure.get("by_kind") == candidate_structure.get("by_kind")
        and parent_structure.get("first_issues") == candidate_structure.get("first_issues")
    )

    inherited_target_addresses = {
        int(str(row.get("abs")), 16)
        for row in parent_audit.get("target_checks") or []
        if row.get("abs")
    }
    inherited_targets_unchanged = all(
        read_record_bytes(parent, logical) == read_record_bytes(candidate, logical)
        for logical in inherited_target_addresses
        if logical not in target_logicals
    )

    checks = {
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == MAIN_SHA256,
        "build_report_bound": str(candidate_info.get("sha256") or "") == sha256(candidate),
        "parent_audit_passed_and_bound": parent_audit_ok,
        "targets_match_sheet_count": len(target_checks) == int((report.get("counts") or {}).get("new_targets") or -1),
        "all_new_targets_exact": all(row["ok"] for row in target_checks),
        "inherited_targets_unchanged": inherited_targets_unchanged,
        "non_target_invariance": invariance.get("ok") is True,
        "changed_banks_expected_only": not unexpected_banks,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_dictionary_banks_11_20_exact": old_dictionary_exact,
        "checksum_exact": checksum_exact,
        "script_structure_regression_exact": structure_regression_exact,
        "candidate_saveram_present_and_sized": batch_path(batch_id, "candidate.sav").exists() and batch_path(batch_id, "candidate.sav").stat().st_size == 32768,
    }

    audit = {
        "schema_version": 1,
        "generated_by": "tools/audit_uncovered_translation_batch_candidate.py",
        "read_only": True,
        "ok": all(checks.values()),
        "status": "cumulative_candidate_static_verified_pending_user_visual_test" if all(checks.values()) else "rejected",
        "batch_id": batch_id,
        "parent_batch": parent_batch,
        "parent": parent_info,
        "candidate": candidate_info,
        "counts": {
            "new_targets": len(target_checks),
            "inherited_targets": len(inherited_target_addresses),
            "cumulative_targets": len(inherited_target_addresses | target_logicals),
            "target_failures": sum(not row["ok"] for row in target_checks),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unexpected_changed_banks": len(unexpected_banks),
        },
        "checksum": f"{checksum:04X}",
        "checks": checks,
        "target_checks": target_checks,
        "unexpected_changed_banks": [f"{bank:02X}" for bank in unexpected_banks],
        "script_structure_regression": {
            "parent_issues": parent_structure.get("issues"),
            "candidate_issues": candidate_structure.get("issues"),
            "parent_by_kind": parent_structure.get("by_kind"),
            "candidate_by_kind": candidate_structure.get("by_kind"),
            "new_issues": 0 if structure_regression_exact else "mismatch",
        },
        "failures": [row for row in target_checks if not row["ok"]],
    }
    out = batch_path(batch_id, "audit.json")
    atomic_json(out, audit)
    atomic_json(
        batch_path(batch_id, "structure.json"),
        {
            "schema_version": 1,
            "generated_by": "tools/audit_uncovered_translation_batch_candidate.py",
            "read_only": True,
            "ok": structure_regression_exact,
            "policy": "parent/candidate issue-set equality; historical issues are not reclassified as new",
            "parent": parent_info,
            "candidate": candidate_info,
            "parent_issues": parent_structure.get("issues"),
            "candidate_issues": candidate_structure.get("issues"),
            "parent_by_kind": parent_structure.get("by_kind"),
            "candidate_by_kind": candidate_structure.get("by_kind"),
            "new_issues": 0 if structure_regression_exact else "mismatch",
        },
    )
    print(json.dumps(audit, ensure_ascii=True, indent=2))
    if not audit["ok"]:
        raise AuditError("candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
