#!/usr/bin/env python3
"""Independent audit for the cumulative 18-record event/ID/indirect candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/next_stage_event_id_indirect_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/next_stage_event_id_indirect_candidate.sav"
EVENT_CATALOG = ROOT / "data/next_stage_bank59_gap_event_ko.json"
EXTRA_CATALOG = ROOT / "data/id_indirect_ui_activation_ko.json"
EVENT_BUILD = ROOT / "out/patch/next_stage_bank59_gap_event_candidate_report.json"
EXTRA_BUILD = ROOT / "out/patch/next_stage_event_id_indirect_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/next_stage_event_id_indirect_candidate_audit.json"

EXPECTED_MAIN = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
EXPECTED_CANDIDATE = "99ddfa32a81317e448b168fd4ae0a22b1dfbfd47542b26dfcda544e7e1b8b4ed"
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


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"), "size": len(payload), "sha256": sha(payload)}


def main() -> int:
    main = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    if sha(main) != EXPECTED_MAIN or sha(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("main/candidate identity drifted")
    event_catalog = load(EVENT_CATALOG)
    extra_catalog = load(EXTRA_CATALOG)
    event_build = load(EVENT_BUILD)
    extra_build = load(EXTRA_BUILD)
    if event_build.get("ok") is not True or extra_build.get("ok") is not True:
        raise AuditError("build evidence failed")

    rows: list[dict[str, Any]] = []
    for source, phase in ((event_catalog, "next_stage_event"), (extra_catalog, "id_indirect")):
        for row in source.get("records") or []:
            item = dict(row)
            item["phase"] = phase
            rows.append(item)
    if len(rows) != 18 or len({str(row["abs"]).upper() for row in rows}) != 18:
        raise AuditError("cumulative target population drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    main_dictionary = make_dictionary_ext3(main, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(main)

    failures: list[dict[str, Any]] = []
    checks_rows: list[dict[str, Any]] = []
    target_logicals: set[int] = set()
    for row in sorted(rows, key=lambda item: int(str(item["abs"]), 16)):
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        capacity = int(row["payload_capacity"])
        payload = candidate[sb + logical : sb + logical + capacity]
        actual = candidate_dictionary.expand(payload[len(prefix):], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[:len(prefix)] != prefix:
            reasons.append("prefix_changed")
        if actual != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")
        if candidate[sb + logical + capacity] != 0:
            reasons.append("terminator_changed")
        check = {"abs": address, "phase": row["phase"], "jp": row["jp"], "expected": expected, "actual": actual, "payload_hex": payload.hex().upper(), "ok": not reasons, "reasons": reasons}
        checks_rows.append(check)
        if reasons:
            failures.append(check)
        target_logicals.add(logical)

    invariance = verify_non_target_invariance(main, candidate, before_dictionary=main_dictionary, after_dictionary=candidate_dictionary, tbl=tbl, excluded=target_logicals)
    runs = diff_runs(main, candidate)
    checksum_probe = bytearray(candidate)
    checksum = update_ws_checksum(checksum_probe)
    checksum_exact = bytes(checksum_probe) == candidate
    runtime_exact = main[sb + 0x7A0000 : sb + 0x7B0000] == candidate[sb + 0x7A0000 : sb + 0x7B0000] and main[sb + 0x7F0000 : sb + 0x800000 - 2] == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    old_dictionary_exact = all(main[s * 0x10000 : (s + 1) * 0x10000] == candidate[s * 0x10000 : (s + 1) * 0x10000] for s in list(range(0x11, 0x21)) + [0x77])
    target_physical_banks = {
        (sb + int(str(row["abs"]), 16)) // 0x10000 for row in rows
    }
    allowed_banks = set(range(0x21, 0x26)) | target_physical_banks | {0xFF}
    unaccounted_bank_runs = [
        {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}", "bank": f"{lo // 0x10000:02X}"}
        for lo, hi in runs
        if (lo // 0x10000) not in allowed_banks
    ]
    checks = {
        "main_identity_exact": sha(main) == EXPECTED_MAIN,
        "candidate_identity_exact": sha(candidate) == EXPECTED_CANDIDATE,
        "event_build_bound": str((event_build.get("candidate") or {}).get("sha256", "")).lower() == "85010f11e8b3b0bab145fa00fa2c830f862f38a9a87ea165d9f04d283a50858c",
        "cumulative_build_bound": str((extra_build.get("candidate") or {}).get("sha256", "")).lower() == EXPECTED_CANDIDATE,
        "targets_exactly_18": len(rows) == 18,
        "all_targets_exact": not failures,
        "non_target_invariance": invariance.get("ok") is True,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_dictionary_banks_exact": old_dictionary_exact,
        "changed_banks_expected_only": not unaccounted_bank_runs,
        "checksum_exact": checksum_exact,
        "candidate_saveram_present_and_sized": CANDIDATE_SAVE.is_file() and CANDIDATE_SAVE.stat().st_size == SAVE_SIZE,
        "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_next_stage_event_id_indirect_candidate.py",
        "read_only": True,
        "ok": ok,
        "status": "cumulative_candidate_static_verified_pending_user_visual_test" if ok else "audit_failed",
        "main": identity(MAIN, main),
        "candidate": identity(CANDIDATE, candidate),
        "counts": {"targets": len(rows), "target_failures": len(failures), "non_target_records_checked": int(invariance.get("records_checked") or 0), "non_target_failures": int(invariance.get("failure_count") or 0), "diff_runs_from_main": len(runs), "unexpected_bank_diff_runs": len(unaccounted_bank_runs)},
        "checksum": f"{checksum:04X}",
        "checks": checks,
        "target_checks": checks_rows,
        "failures": failures,
        "non_target_invariance": invariance,
        "unexpected_bank_diff_runs": unaccounted_bank_runs,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        raise AuditError("cumulative candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
