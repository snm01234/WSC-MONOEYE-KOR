#!/usr/bin/env python3
"""Build the safe aux false-prefix cleanup candidate (two duplicate-proven battle lines plus one user-confirmed event line)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
WORKLIST = ROOT / "out/patch/aux_duplicate_false_prefix_residual_worklist.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_candidate.wsc"
OUT_SAVE = ROOT / "sram/aux_duplicate_false_prefix_cleanup_candidate.sav"
REPORT = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_report.json"

EXPECTED_PARENT_SHA256 = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"
EXPECTED_DUPLICATE_TARGETS = {"5D870B", "5DB42B"}
EXPECTED_TARGETS = {"590A2B", *EXPECTED_DUPLICATE_TARGETS}
EVENT_TARGET = {
    "abs": "590A2B",
    "bank": "59",
    "payload_capacity": 12,
    "lead_len": 1,
    "lead_hex": "18",
    "lead_text": "こ",
    "original_payload_hex": "18F553271EE04CFC2BF1911D",
    "original_text": "こだわりすぎではないか……？",
    "before_hex": "18E518FDC201010101010101",
    "after_hex": "E518FDC20101010101010101",
    "before_text": "こ지나치게　집착하는　것　아닌가……？",
    "after_text": "지나치게　집착하는　것　아닌가……？",
    "clean_duplicate_peers": [],
    "proof": {
        "original_sentence_starts_with_printable_kana": True,
        "runtime_residual_matches_preserved_first_glyph": True,
        "approved_korean_body_exact": True,
        "record_length_preserved": True,
        "terminator_position_preserved": True,
        "only_lead_removed_and_padding_extended": True,
    },
}
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
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


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM is missing or wrong size")

    worklist = load_object(WORKLIST)
    if worklist.get("ok") is not True or worklist.get("status") != "safe_candidate_build_authorized":
        raise BuildError("safe duplicate worklist did not pass")
    if str((worklist.get("inputs") or {}).get("tip", {}).get("sha256", "")).lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("worklist is not bound to current main TIP")
    duplicate_rows = [dict(row) for row in worklist.get("targets") or []]
    duplicate_addresses = {
        str(row.get("abs") or "").upper() for row in duplicate_rows
    }
    if (
        duplicate_addresses != EXPECTED_DUPLICATE_TARGETS
        or len(duplicate_rows) != len(EXPECTED_DUPLICATE_TARGETS)
    ):
        raise BuildError("duplicate target population drifted")
    if not all(all((row.get("proof") or {}).values()) for row in duplicate_rows):
        raise BuildError("duplicate proof is incomplete")
    rows = duplicate_rows + [dict(EVENT_TARGET)]
    addresses = {str(row.get("abs") or "").upper() for row in rows}
    if addresses != EXPECTED_TARGETS or len(rows) != len(EXPECTED_TARGETS):
        raise BuildError("combined target population drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate = bytearray(parent)
    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []

    for row in sorted(rows, key=lambda item: int(item["abs"], 16)):
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        capacity = int(row["payload_capacity"])
        before = bytes.fromhex(str(row["before_hex"]))
        after = bytes.fromhex(str(row["after_hex"]))
        if len(before) != capacity or len(after) != capacity:
            raise BuildError(f"payload capacity mismatch at {address}")
        start = sb + logical
        current = bytes(candidate[start : start + capacity])
        if current != before:
            raise BuildError(f"parent payload drifted at {address}")
        if candidate[start + capacity] != 0:
            raise BuildError(f"terminator drifted at {address}")
        if after != before[int(row["lead_len"]) :] + b"\x01" * int(row["lead_len"]):
            raise BuildError(f"shift/padding proof drifted at {address}")
        candidate[start : start + capacity] = after
        target_extents.append((start, start + capacity))
        applied.append(
            {
                "abs": address,
                "before_hex": before.hex().upper(),
                "after_hex": after.hex().upper(),
                "before_text": row["before_text"],
                "after_text": row["after_text"],
                "lead_hex": row["lead_hex"],
                "lead_text": row["lead_text"],
                "clean_duplicate_peers": row["clean_duplicate_peers"],
                "payload_capacity": capacity,
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        capacity = int(row["payload_capacity"])
        payload = candidate_bytes[sb + logical : sb + logical + capacity]
        rendered = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload.hex().upper() != row["after_hex"]:
            reasons.append("payload_mismatch")
        if rendered != row["after_text"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in rendered):
            reasons.append("japanese_residual")
        if candidate_bytes[sb + logical + capacity] != 0:
            reasons.append("terminator_changed")
        for peer in row["clean_duplicate_peers"]:
            peer_logical = int(peer, 16)
            peer_payload = candidate_bytes[
                sb + peer_logical : sb + peer_logical + capacity
            ]
            if peer_payload != payload:
                reasons.append(f"clean_peer_mismatch:{peer}")
        if reasons:
            target_failures.append(
                {
                    "abs": row["abs"],
                    "expected": row["after_text"],
                    "actual": rendered,
                    "reasons": reasons,
                }
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["abs"], 16) for row in applied},
    )
    runs = diff_runs(parent, candidate_bytes)
    allowed = target_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    dictionary_banks_exact = all(
        parent[segment * 0x10000 : (segment + 1) * 0x10000]
        == candidate_bytes[segment * 0x10000 : (segment + 1) * 0x10000]
        for segment in list(range(0x11, 0x26)) + [0x77]
    )
    checks = {
        "parent_tip_exact": sha256(parent) == EXPECTED_PARENT_SHA256,
        "targets_exactly_3": len(applied) == 3,
        "all_targets_render_exact": not target_failures,
        "duplicate_peers_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "dictionary_banks_exact": dictionary_banks_exact,
        "diffs_bounded": not unaccounted,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256,
    }
    if not all(checks.values()):
        print(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": target_failures,
                    "invariance": invariance,
                    "unaccounted": unaccounted,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        raise BuildError("duplicate false-prefix candidate verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save_snapshot)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_aux_duplicate_false_prefix_cleanup_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_user_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE, save_snapshot),
            "policy": "build-start main SaveRAM snapshot; mutable test-only; never promote to main",
        },
        "source_worklist": identity(WORKLIST),
        "counts": {
            "targets": len(applied),
            "target_failures": len(target_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "verification": {
            "checks": checks,
            "target_failures": target_failures,
            "non_target_invariance": invariance,
            "unaccounted_diff_runs": unaccounted,
        },
        "diff": {
            "changed_bytes": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "applied": applied,
        "test_scope": {
            "required": [
                "590A2B: event second line no longer starts with こ",
                "5D870B: second line no longer starts with 私",
                "5DB42B: battle line no longer starts with 見",
                "event and battle dialogue advance and return",
                "save, full emulator restart, and reload",
            ],
            "candidate_saveram": "test-only; never copy back to main",
        },
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "candidate_save": report["candidate_save"],
                "counts": report["counts"],
                "applied": applied,
                "diff": report["diff"],
                "report": str(REPORT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
