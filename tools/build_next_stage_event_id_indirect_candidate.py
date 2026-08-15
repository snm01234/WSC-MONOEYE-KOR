#!/usr/bin/env python3
"""Build a cumulative next-stage event + ID/indirect cleanup candidate."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3, atomic_bytes, atomic_json, sha256
from build_encyclopedia_character_five_bank_batch02_candidate import PAGES
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text

PARENT = ROOT / "out/patch/next_stage_bank59_gap_event_candidate.wsc"
PARENT_SAVE = ROOT / "sram/next_stage_bank59_gap_event_candidate.sav"
PARENT_REPORT = ROOT / "out/patch/next_stage_bank59_gap_event_candidate_report.json"
CATALOG = ROOT / "data/id_indirect_ui_activation_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/next_stage_event_id_indirect_candidate.wsc"
OUT_SAVE = ROOT / "sram/next_stage_event_id_indirect_candidate.sav"
REPORT = ROOT / "out/patch/next_stage_event_id_indirect_candidate_report.json"

EXPECTED_PARENT = "85010f11e8b3b0bab145fa00fa2c830f862f38a9a87ea165d9f04d283a50858c"
EXPECTED_MAIN = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
EXPECTED_TARGETS = {"5C95E2", "5C9B2E", "5CA6C1", "5F287F", "5F2895", "5F28AB", "5F28C2", "5F36B1", "5F3B8B"}
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"), "size": len(data), "sha256": sha256(data)}


def main() -> int:
    parent = bytes(load_rom(PARENT))
    save_snapshot = PARENT_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT:
        raise BuildError("cumulative parent identity drifted")
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("candidate SaveRAM missing or wrong size")
    parent_report = load_object(PARENT_REPORT)
    if parent_report.get("ok") is not True or str((parent_report.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_PARENT:
        raise BuildError("parent report is not bound")

    catalog = load_object(CATALOG)
    if str(catalog.get("parent_candidate_sha256") or "").lower() != EXPECTED_PARENT:
        raise BuildError("catalog parent identity drifted")
    provenance = catalog.get("provenance") or {}
    if not (
        provenance.get("translation_source") == "llm"
        and provenance.get("model") == "GPT-5.6 Thinking"
        and provenance.get("review_status") == "approved"
        and provenance.get("legacy_machine_translation_used") is False
    ):
        raise BuildError("translation provenance not approved")
    sources = [dict(row) for row in catalog.get("records") or []]
    if {str(row.get("abs") or "").upper() for row in sources} != EXPECTED_TARGETS or len(sources) != 9:
        raise BuildError("target population drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)
    ext_rows: list[dict[str, Any]] = []
    exact_rows: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source["payload_capacity"])
        body_capacity = int(source["body_capacity"])
        expected = bytes.fromhex(str(source["current_payload_hex"]))
        current = parent[sb + logical : sb + logical + payload_capacity]
        if current != expected or payload_capacity != len(expected) or body_capacity != payload_capacity - len(prefix):
            raise BuildError(f"payload boundary drifted at {address}")
        if parent[sb + logical + payload_capacity] != 0:
            raise BuildError(f"terminator drifted at {address}")
        ko = normalize_ko_text(str(source["ko"]))
        row = {
            "abs": address,
            "logical": logical,
            "prefix": prefix,
            "prefix_len": len(prefix),
            "payload_capacity": payload_capacity,
            "body_capacity": body_capacity,
            "jp": str(source["jp"]),
            "ko": ko,
            "category": str(source["category"]),
        }
        if source["strategy"] == "ext3":
            if body_capacity < 4 or any(is_japanese_character(ch) for ch in ko):
                raise BuildError(f"invalid ext3 target at {address}")
            row["encoded"] = encode_phrase(ko, tbl)
            ext_rows.append(row)
        elif source["strategy"] == "existing_exact_token":
            replacement = bytes.fromhex(str(source["replacement_hex"]))
            if len(replacement) != body_capacity:
                raise BuildError(f"exact replacement size drifted at {address}")
            source_abs = int(str(source["source_exact_abs"]), 16)
            source_payload = parent[sb + source_abs : sb + source_abs + 2]
            if replacement[:2] != source_payload:
                raise BuildError(f"exact source token drifted at {address}")
            row["replacement"] = replacement
            row["source_exact_abs"] = str(source["source_exact_abs"])
            exact_rows.append(row)
        else:
            raise BuildError(f"unknown strategy at {address}")

    assignments, states = allocate_ext3(parent, ext_rows)
    candidate = bytearray(parent)
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        start = int(state["start"])
        candidate[start : start + BANK_SIZE] = state["bank"]
        new_locals = {int(info["local"]) for info in assignments.values() if int(info["page"]) == page and not bool(info["reused"])}
        pointer_extents.extend((start + local * 2, start + local * 2 + 2) for local in sorted(new_locals))
        if int(state["cursor"]) > int(state["cursor_before"]):
            phrase_extents.append((start + int(state["cursor_before"]), start + int(state["cursor"])))

    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in ext_rows + exact_rows:
        if row in ext_rows:
            info = assignments[row["ko"]]
            token = bytes(info["token"])
            replacement = token + b"\x01" * (row["body_capacity"] - len(token))
            strategy = "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new"
            allocation = {"page": int(info["page"]), "physical_bank": f"{int(info['segment']):02X}", "local": f"{int(info['local']):04X}", "pointer": f"{int(info['pointer']):04X}", "token_hex": token.hex().upper()}
        else:
            replacement = bytes(row["replacement"])
            strategy = "existing_exact_two_byte_token"
            allocation = {"source_exact_abs": row["source_exact_abs"], "token_hex": replacement[:2].hex().upper()}
        body_start = sb + row["logical"] + row["prefix_len"]
        candidate[body_start : body_start + row["body_capacity"]] = replacement
        target_extents.append((body_start, body_start + row["body_capacity"]))
        applied.append({"abs": row["abs"], "jp": row["jp"], "after": row["ko"], "category": row["category"], "prefix_hex": row["prefix"].hex().upper(), "payload_capacity": row["payload_capacity"], "body_capacity": row["body_capacity"], "strategy": strategy, **allocation})

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []
    for row in ext_rows + exact_rows:
        start = sb + row["logical"]
        payload = candidate_bytes[start : start + row["payload_capacity"]]
        actual = candidate_dictionary.expand(payload[row["prefix_len"] :], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[:row["prefix_len"]] != row["prefix"]:
            reasons.append("prefix_changed")
        if actual != row["ko"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")
        if candidate_bytes[start + row["payload_capacity"]] != 0:
            reasons.append("terminator_changed")
        if reasons:
            failures.append({"abs": row["abs"], "expected": row["ko"], "actual": actual, "reasons": reasons})

    # Verify all nine parent event records survived exactly.
    event_failures: list[dict[str, Any]] = []
    for event in parent_report.get("applied") or []:
        logical = int(str(event["abs"]), 16)
        prefix = bytes.fromhex(str(event.get("prefix_hex") or ""))
        capacity = int(event["payload_capacity"])
        payload = candidate_bytes[sb + logical : sb + logical + capacity]
        actual = candidate_dictionary.expand(payload[len(prefix):], tbl).rstrip("\u3000 \t")
        if actual != str(event["after"]).rstrip("\u3000 \t"):
            event_failures.append({"abs": event["abs"], "actual": actual, "expected": event["after"]})

    invariance = verify_non_target_invariance(parent, candidate_bytes, before_dictionary=parent_dictionary, after_dictionary=candidate_dictionary, tbl=tbl, excluded={row["logical"] for row in ext_rows + exact_rows})
    runs = diff_runs(parent, candidate_bytes)
    allowed = target_extents + pointer_extents + phrase_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [{"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"} for lo, hi in runs if not covered((lo, hi), allowed)]
    runtime_exact = parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate_bytes[sb + 0x7A0000 : sb + 0x7B0000] and parent[sb + 0x7F0000 : sb + 0x800000 - 2] == candidate_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    page_hits_parent = {page: five.scan_range_hits(parent, page) for page in range(PAGES)}
    page_hits_candidate = {page: five.scan_range_hits(candidate_bytes, page) for page in range(PAGES)}
    expected_page_counts = {page: len(page_hits_parent[page]) + sum(int(row["page"]) == page for row in applied if "page" in row) for page in range(PAGES)}
    checks = {
        "parent_candidate_exact": sha256(parent) == EXPECTED_PARENT,
        "new_targets_exactly_9": len(ext_rows) + len(exact_rows) == 9,
        "cumulative_event_targets_exactly_9": len(parent_report.get("applied") or []) == 9 and not event_failures,
        "all_new_targets_render_exact": not failures,
        "non_target_invariance": invariance.get("ok") is True,
        "page_reference_counts_exact": all(len(page_hits_candidate[p]) == expected_page_counts[p] for p in range(PAGES)),
        "runtime_banks_7a_7f_exact": runtime_exact,
        "diffs_bounded": not unaccounted,
        "main_tip_unchanged": sha256((ROOT / "out/patch/monoeye_ko_expanded.wsc").read_bytes()) == EXPECTED_MAIN,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "failures": failures, "event_failures": event_failures, "unaccounted": unaccounted}, ensure_ascii=False))

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save_snapshot)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_next_stage_event_id_indirect_candidate.py",
        "ok": True,
        "status": "cumulative_candidate_static_verified_pending_independent_audit_and_user_visual_test",
        "base_main_tip_sha256": EXPECTED_MAIN,
        "parent_candidate": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {**identity(OUT_SAVE, save_snapshot), "policy": "test-only; never promote SaveRAM"},
        "catalog": identity(CATALOG),
        "counts": {"event_parent_targets": 9, "new_targets": 9, "cumulative_targets": 18, "new_unique_ext3_phrases": len(assignments), "target_failures": len(failures), "event_regression_failures": len(event_failures), "non_target_records_checked": int(invariance.get("records_checked") or 0), "non_target_failures": int(invariance.get("failure_count") or 0), "unaccounted_diff_runs": len(unaccounted)},
        "checks": checks,
        "applied": applied,
        "diff_from_parent_candidate": {"changed_bytes": sum(hi - lo for lo, hi in runs), "runs": len(runs), "checksum": f"{checksum:04X}"},
        "test_scope": ["next-stage 593E8A-593F28 event scene", "three Minovsky-particle activation variants", "four ID-command effect descriptions", "indirect and shooting command labels", "save, full emulator restart, and reload"],
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
