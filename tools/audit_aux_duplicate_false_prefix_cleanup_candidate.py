#!/usr/bin/env python3
"""Independent static audit for the combined aux false-prefix cleanup candidate."""
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
CANDIDATE = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/aux_duplicate_false_prefix_cleanup_candidate.sav"
WORKLIST = ROOT / "out/patch/aux_duplicate_false_prefix_residual_worklist.json"
BUILD = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_candidate_audit.json"

EXPECTED_PARENT = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"
EXPECTED_CANDIDATE = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
EXPECTED_DUPLICATE_TARGETS = {"5D870B", "5DB42B"}
EXPECTED_TARGETS = {"590A2B", *EXPECTED_DUPLICATE_TARGETS}
EVENT_TARGET = {
    "abs": "590A2B",
    "bank": "59",
    "payload_capacity": 12,
    "lead_len": 1,
    "lead_hex": "18",
    "lead_text": "こ",
    "before_hex": "18E518FDC201010101010101",
    "after_hex": "E518FDC20101010101010101",
    "before_text": "こ지나치게　집착하는　것　아닌가……？",
    "after_text": "지나치게　집착하는　것　아닌가……？",
    "clean_duplicate_peers": [],
}
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def main() -> int:
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    if sha256(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if sha256(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("candidate identity drifted")

    worklist = load_object(WORKLIST)
    build = load_object(BUILD)
    duplicate_rows = [dict(row) for row in worklist.get("targets") or []]
    if {
        str(row.get("abs") or "").upper() for row in duplicate_rows
    } != EXPECTED_DUPLICATE_TARGETS:
        raise AuditError("duplicate target set drifted")
    rows = duplicate_rows + [dict(EVENT_TARGET)]
    if {str(row.get("abs") or "").upper() for row in rows} != EXPECTED_TARGETS:
        raise AuditError("combined target set drifted")
    if build.get("ok") is not True:
        raise AuditError("build report did not pass")
    if str((build.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise AuditError("build report candidate identity drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    failures: list[dict[str, Any]] = []
    target_extents: list[tuple[int, int]] = []
    target_logicals: set[int] = set()
    target_checks: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: int(item["abs"], 16)):
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        capacity = int(row["payload_capacity"])
        before = bytes.fromhex(str(row["before_hex"]))
        after = bytes.fromhex(str(row["after_hex"]))
        parent_payload = parent[sb + logical : sb + logical + capacity]
        candidate_payload = candidate[sb + logical : sb + logical + capacity]
        rendered = candidate_dictionary.expand(candidate_payload, tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if parent_payload != before:
            reasons.append("parent_payload_not_bound")
        if candidate_payload != after:
            reasons.append("candidate_payload_mismatch")
        if len(parent_payload) != len(candidate_payload) or len(candidate_payload) != capacity:
            reasons.append("payload_length_changed")
        if parent[sb + logical + capacity] != 0 or candidate[sb + logical + capacity] != 0:
            reasons.append("terminator_changed")
        if rendered != row["after_text"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in rendered):
            reasons.append("japanese_residual")
        peer_checks: list[dict[str, Any]] = []
        for peer in row.get("clean_duplicate_peers") or []:
            peer_logical = int(peer, 16)
            peer_payload = candidate[
                sb + peer_logical : sb + peer_logical + capacity
            ]
            peer_rendered = candidate_dictionary.expand(peer_payload, tbl).rstrip("\u3000 \t")
            peer_ok = peer_payload == candidate_payload and peer_rendered == rendered
            peer_checks.append(
                {
                    "abs": peer,
                    "payload_hex": peer_payload.hex().upper(),
                    "rendered": peer_rendered,
                    "ok": peer_ok,
                }
            )
            if not peer_ok:
                reasons.append(f"clean_peer_mismatch:{peer}")
        check = {
            "abs": address,
            "before_text": row["before_text"],
            "after_text": row["after_text"],
            "actual": rendered,
            "lead_hex": row["lead_hex"],
            "lead_text": row["lead_text"],
            "peers": peer_checks,
            "boundary_preserved": not any(
                reason in {"payload_length_changed", "terminator_changed"}
                for reason in reasons
            ),
            "ok": not reasons,
        }
        target_checks.append(check)
        if reasons:
            failures.append({"abs": address, "reasons": reasons, "actual": rendered})
        target_logicals.add(logical)
        target_extents.append((sb + logical, sb + logical + capacity))

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )
    runs = diff_runs(parent, candidate)
    allowed = target_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    checksum_probe = bytearray(candidate)
    checksum = update_ws_checksum(checksum_probe)
    checksum_exact = bytes(checksum_probe) == candidate
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    dictionary_banks_exact = all(
        parent[segment * 0x10000 : (segment + 1) * 0x10000]
        == candidate[segment * 0x10000 : (segment + 1) * 0x10000]
        for segment in list(range(0x11, 0x26)) + [0x77]
    )
    checks = {
        "parent_identity_exact": sha256(parent) == EXPECTED_PARENT,
        "candidate_identity_exact": sha256(candidate) == EXPECTED_CANDIDATE,
        "build_report_bound": str((build.get("parent") or {}).get("sha256", "")).lower() == EXPECTED_PARENT,
        "targets_exactly_3": len(rows) == 3,
        "all_targets_exact": not failures,
        "duplicate_peers_exact": all(
            all(peer["ok"] for peer in row["peers"]) for row in target_checks
        ),
        "non_target_invariance": invariance.get("ok") is True,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "dictionary_banks_exact": dictionary_banks_exact,
        "diffs_bounded": not unaccounted,
        "checksum_exact": checksum_exact,
        "candidate_saveram_present_and_sized": CANDIDATE_SAVE.is_file() and CANDIDATE_SAVE.stat().st_size == SAVE_SIZE,
        "main_tip_unchanged": sha256(PARENT.read_bytes()) == EXPECTED_PARENT,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_aux_duplicate_false_prefix_cleanup_candidate.py",
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
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "status": report["status"],
                "counts": report["counts"],
                "checks": checks,
                "target_checks": target_checks,
                "out": str(OUT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not ok:
        raise AuditError("duplicate false-prefix candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
