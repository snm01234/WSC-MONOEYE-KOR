#!/usr/bin/env python3
"""Build the complete A Baoa Qu bank59 event-dialogue candidate.

The promoted character-encyclopedia TIP is the parent.  Every target has at
least four body bytes and therefore uses only the already user-validated E5 18
five-bank aliases in physical banks 21..25.  Prefix bytes, record lengths,
terminators, runtime code, old ext3 banks 11..20, the main TIP, and live
SaveRAM are never modified.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_character_all_remaining_candidate import (
    allocate_ext3,
    atomic_bytes,
    atomic_json,
    identity,
    sha256,
)
from build_encyclopedia_character_five_bank_batch02_candidate import PAGES
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    verify_non_target_invariance,
)
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
WORKLIST = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_worklist.json"
CATALOG = ROOT / "data/abaoa_qu_bank59_event_dialogue_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_candidate.wsc"
OUT_SAVE = ROOT / "sram/abaoa_qu_bank59_event_dialogue_candidate.sav"
REPORT = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_report.json"

EXPECTED_PARENT_SHA256 = "186d6d04859146420f4ae826a38a0d35377633d54587046c30046125cbceb241"
EXPECTED_TARGETS = 257
EXPECTED_UNIQUE = 250
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def prepare_rows(parent: bytes, tbl: Tbl) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    worklist = load_object(WORKLIST)
    catalog = load_object(CATALOG)
    if worklist.get("ok") is not True:
        raise BuildError("bank59 event worklist did not pass")
    if str((worklist.get("inputs") or {}).get("tip", {}).get("sha256", "")).lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("worklist is not bound to the current parent TIP")
    provenance = catalog.get("provenance") or {}
    if not (
        provenance.get("translation_source") == "llm"
        and provenance.get("model") == "GPT-5.6 Thinking"
        and provenance.get("review_status") == "approved"
        and provenance.get("legacy_machine_translation_used") is False
    ):
        raise BuildError("translation provenance is not approved")

    sources = [dict(row) for row in worklist.get("records") or []]
    translations = [dict(row) for row in catalog.get("lines") or []]
    if len(sources) != EXPECTED_TARGETS or len(translations) != EXPECTED_TARGETS:
        raise BuildError("source/catalog population drifted")
    source_by_abs = {str(row.get("abs") or "").upper(): row for row in sources}
    translation_by_abs = {str(row.get("abs") or "").upper(): row for row in translations}
    if len(source_by_abs) != EXPECTED_TARGETS or len(translation_by_abs) != EXPECTED_TARGETS:
        raise BuildError("duplicate addresses in source or catalog")
    if set(source_by_abs) != set(translation_by_abs):
        raise BuildError("catalog address set differs from worklist")

    sb = stock_base(parent)
    prepared: list[dict[str, Any]] = []
    for address in sorted(source_by_abs, key=lambda value: int(value, 16)):
        source = source_by_abs[address]
        line = translation_by_abs[address]
        if line.get("translation_source") != "llm" or line.get("review_status") != "approved":
            raise BuildError(f"line provenance drifted at {address}")
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source.get("payload_capacity") or -1)
        body_capacity = int(source.get("body_capacity") or -1)
        logical = int(address, 16)
        if body_capacity < 4 or payload_capacity != len(prefix) + body_capacity:
            raise BuildError(f"invalid body boundary at {address}")
        current_payload = parent[sb + logical : sb + logical + payload_capacity]
        expected_payload = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        if current_payload != expected_payload:
            raise BuildError(f"parent payload drifted at {address}")
        terminator = sb + logical + payload_capacity
        if terminator >= len(parent) or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {address}")

        ko = normalize_ko_text(str(line.get("ko") or ""))
        if not ko or any(is_japanese_character(character) for character in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        encoded = encode_phrase(ko, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"invalid encoded phrase at {address}")
        prepared.append(
            {
                "abs": address,
                "logical": logical,
                "jp": str(source.get("jp") or ""),
                "current": str(source.get("current") or ""),
                "ko": ko,
                "encoded": encoded,
                "prefix": prefix,
                "prefix_len": len(prefix),
                "payload_capacity": payload_capacity,
                "body_capacity": body_capacity,
                "current_payload": current_payload,
            }
        )

    if len({row["ko"] for row in prepared}) != EXPECTED_UNIQUE:
        raise BuildError("unique Korean phrase population drifted")
    return prepared, provenance


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM is missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    rows, provenance = prepare_rows(parent, tbl)

    assignments, states = allocate_ext3(parent, rows)
    candidate = bytearray(parent)
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        start = int(state["start"])
        candidate[start : start + BANK_SIZE] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        }
        pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2)
            for local in sorted(new_locals)
        )
        if int(state["cursor"]) > int(state["cursor_before"]):
            phrase_extents.append(
                (start + int(state["cursor_before"]), start + int(state["cursor"]))
            )

    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        info = assignments[str(row["ko"])]
        token = bytes(info["token"])
        body_capacity = int(row["body_capacity"])
        replacement = token + b"\x01" * (body_capacity - len(token))
        if len(replacement) != body_capacity:
            raise BuildError(f"replacement length drift at {row['abs']}")
        body_start = sb + int(row["logical"]) + int(row["prefix_len"])
        candidate[body_start : body_start + body_capacity] = replacement
        target_extents.append((body_start, body_start + body_capacity))
        applied.append(
            {
                "abs": row["abs"],
                "jp": row["jp"],
                "before": row["current"],
                "after": row["ko"],
                "prefix_hex": bytes(row["prefix"]).hex().upper(),
                "payload_capacity": int(row["payload_capacity"]),
                "body_capacity": body_capacity,
                "strategy": (
                    "five_bank_e518_alias_reuse"
                    if bool(info["reused"])
                    else "five_bank_e518_alias_new"
                ),
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        payload_capacity = int(row["payload_capacity"])
        prefix_len = int(row["prefix_len"])
        payload = candidate_bytes[sb + logical : sb + logical + payload_capacity]
        rendered = candidate_dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[:prefix_len] != bytes(row["prefix"]):
            reasons.append("prefix_changed")
        if rendered != row["ko"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(character) for character in rendered):
            reasons.append("japanese_residual")
        terminator = sb + logical + payload_capacity
        if candidate_bytes[terminator] != 0:
            reasons.append("terminator_changed")
        if reasons:
            target_failures.append(
                {
                    "abs": row["abs"],
                    "expected": row["ko"],
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
        excluded={int(row["logical"]) for row in rows},
    )

    runs = diff_runs(parent, candidate_bytes)
    allowed = target_extents + pointer_extents + phrase_extents + [
        (len(parent) - 2, len(parent))
    ]
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
    old_ext3_exact = all(
        parent[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        == candidate_bytes[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    page_hits_parent = {page: five.scan_range_hits(parent, page) for page in range(PAGES)}
    page_hits_candidate = {
        page: five.scan_range_hits(candidate_bytes, page) for page in range(PAGES)
    }
    expected_page_counts = {
        page: len(page_hits_parent[page])
        + sum(int(row["page"]) == page for row in applied)
        for page in range(PAGES)
    }
    page_counts_exact = all(
        len(page_hits_candidate[page]) == expected_page_counts[page]
        for page in range(PAGES)
    )

    screenshot_rows = {row["abs"]: row for row in applied}
    screenshot_anchors_exact = all(
        address in screenshot_rows
        for address in ("5905C3", "5905D2", "59074E")
    )
    checks = {
        "parent_tip_exact": sha256(parent) == EXPECTED_PARENT_SHA256,
        "targets_257": len(rows) == EXPECTED_TARGETS,
        "unique_phrases_250": len(assignments) == EXPECTED_UNIQUE,
        "all_body_capacities_at_least_4": all(
            int(row["body_capacity"]) >= 4 for row in rows
        ),
        "screenshot_anchors_exact": screenshot_anchors_exact,
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "page_reference_counts_exact": page_counts_exact,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256,
    }
    if not all(checks.values()):
        print(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": target_failures[:20],
                    "invariance": invariance,
                    "unaccounted": unaccounted[:20],
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        raise BuildError("A Baoa Qu bank59 candidate verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save_snapshot)
    bank_reports: list[dict[str, Any]] = []
    for page, state in states.items():
        new_infos = [
            info
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        ]
        bank_reports.append(
            {
                "page": page,
                "physical_bank": f"{int(state['segment']):02X}",
                "new_slots": len(new_infos),
                "new_record_references": sum(
                    int(row["page"]) == page for row in applied
                ),
                "reference_count_before": len(page_hits_parent[page]),
                "reference_count_after": len(page_hits_candidate[page]),
                "cursor_before": f"{int(state['cursor_before']):04X}",
                "cursor_after": f"{int(state['cursor']):04X}",
                "phrase_bytes_added": int(state["cursor"])
                - int(state["cursor_before"]),
                "phrase_room_after": BANK_SIZE - int(state["cursor"]),
            }
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_abaoa_qu_bank59_event_dialogue_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_user_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE, save_snapshot),
            "policy": "build-start live SaveRAM snapshot; mutable test-only; never promote to main",
        },
        "source_worklist": identity(WORKLIST),
        "source_catalog": identity(CATALOG),
        "provenance": provenance,
        "scope": {
            "start": "590244",
            "end_exclusive": "59265F",
            "label": "A Baoa Qu bank59 opening, battle events, and immediate aftermath",
        },
        "counts": {
            "targets": len(rows),
            "unique_phrases": len(assignments),
            "new_phrases": sum(not bool(info["reused"]) for info in assignments.values()),
            "existing_phrase_reuse": sum(bool(info["reused"]) for info in assignments.values()),
            "target_failures": len(target_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "runtime": "existing user-validated E5 18 five-bank aliases only",
            "banks": bank_reports,
        },
        "screenshot_anchors": [
            screenshot_rows["5905C3"],
            screenshot_rows["5905D2"],
            screenshot_rows["59074E"],
        ],
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
                "screenshot scene at 5905C3 and 5905D2",
                "screenshot scene at 59074E",
                "Sig/Sera dialogue through 5908F6",
                "battle events through 591FCD",
                "postbattle dialogue through 59264E",
                "battle progression, save, full emulator restart, and reload",
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
                "banks": bank_reports,
                "screenshot_anchors": report["screenshot_anchors"],
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
