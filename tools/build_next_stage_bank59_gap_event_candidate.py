#!/usr/bin/env python3
"""Build a nine-record candidate for the uncovered bank59 event gap."""
from __future__ import annotations

import json
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
CATALOG = ROOT / "data/next_stage_bank59_gap_event_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/next_stage_bank59_gap_event_candidate.wsc"
OUT_SAVE = ROOT / "sram/next_stage_bank59_gap_event_candidate.sav"
REPORT = ROOT / "out/patch/next_stage_bank59_gap_event_candidate_report.json"

EXPECTED_PARENT = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
EXPECTED_TARGETS = {
    "593E90", "593EA2", "593EB4", "593ECB", "593ED5",
    "593EEA", "593EF5", "593F04", "593F14",
}
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
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def prepare(parent: bytes, tbl: Tbl) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    catalog = load_object(CATALOG)
    if str(catalog.get("parent_tip_sha256") or "").lower() != EXPECTED_PARENT:
        raise BuildError("catalog parent identity drifted")
    provenance = catalog.get("provenance") or {}
    if not (
        provenance.get("translation_source") == "llm"
        and provenance.get("model") == "GPT-5.6 Thinking"
        and provenance.get("review_status") == "approved"
        and provenance.get("legacy_machine_translation_used") is False
    ):
        raise BuildError("translation provenance is not approved")
    sources = [dict(row) for row in catalog.get("records") or []]
    addresses = {str(row.get("abs") or "").upper() for row in sources}
    if addresses != EXPECTED_TARGETS or len(sources) != len(EXPECTED_TARGETS):
        raise BuildError("target population drifted")

    sb = stock_base(parent)
    rows: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source.get("payload_capacity") or -1)
        body_capacity = int(source.get("body_capacity") or -1)
        expected_payload = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        if payload_capacity != len(expected_payload):
            raise BuildError(f"payload capacity drifted at {address}")
        if body_capacity != payload_capacity - len(prefix) or body_capacity < 4:
            raise BuildError(f"body boundary drifted at {address}")
        current = parent[sb + logical : sb + logical + payload_capacity]
        if current != expected_payload or not current.startswith(prefix):
            raise BuildError(f"parent payload drifted at {address}")
        if parent[sb + logical + payload_capacity] != 0:
            raise BuildError(f"terminator drifted at {address}")
        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or any(is_japanese_character(ch) for ch in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        encoded = encode_phrase(ko, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"invalid encoded phrase at {address}")
        rows.append(
            {
                "abs": address,
                "logical": logical,
                "jp": str(source.get("jp") or ""),
                "current": "",
                "ko": ko,
                "encoded": encoded,
                "prefix": prefix,
                "prefix_len": len(prefix),
                "payload_capacity": payload_capacity,
                "body_capacity": body_capacity,
            }
        )
    return rows, provenance


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT:
        raise BuildError("main TIP identity drifted")
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    rows, provenance = prepare(parent, tbl)
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
        info = assignments[row["ko"]]
        token = bytes(info["token"])
        replacement = token + b"\x01" * (row["body_capacity"] - len(token))
        body_start = sb + row["logical"] + row["prefix_len"]
        candidate[body_start : body_start + row["body_capacity"]] = replacement
        target_extents.append((body_start, body_start + row["body_capacity"]))
        applied.append(
            {
                "abs": row["abs"],
                "jp": row["jp"],
                "after": row["ko"],
                "prefix_hex": row["prefix"].hex().upper(),
                "payload_capacity": row["payload_capacity"],
                "body_capacity": row["body_capacity"],
                "token_hex": token.hex().upper(),
                "strategy": "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new",
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []
    for row in rows:
        start = sb + row["logical"]
        payload = candidate_bytes[start : start + row["payload_capacity"]]
        actual = candidate_dictionary.expand(payload[row["prefix_len"] :], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[: row["prefix_len"]] != row["prefix"]:
            reasons.append("prefix_changed")
        if actual != row["ko"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")
        if candidate_bytes[start + row["payload_capacity"]] != 0:
            reasons.append("terminator_changed")
        if reasons:
            failures.append({"abs": row["abs"], "expected": row["ko"], "actual": actual, "reasons": reasons})

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={row["logical"] for row in rows},
    )
    runs = diff_runs(parent, candidate_bytes)
    allowed = target_extents + pointer_extents + phrase_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2] == candidate_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        == candidate_bytes[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    page_hits_parent = {page: five.scan_range_hits(parent, page) for page in range(PAGES)}
    page_hits_candidate = {page: five.scan_range_hits(candidate_bytes, page) for page in range(PAGES)}
    expected_page_counts = {
        page: len(page_hits_parent[page]) + sum(row["page"] == page for row in applied)
        for page in range(PAGES)
    }
    checks = {
        "parent_tip_exact": sha256(parent) == EXPECTED_PARENT,
        "targets_exactly_9": len(rows) == 9,
        "all_targets_render_exact": not failures,
        "non_target_invariance": invariance.get("ok") is True,
        "page_reference_counts_exact": all(len(page_hits_candidate[p]) == expected_page_counts[p] for p in range(PAGES)),
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "failures": failures, "unaccounted": unaccounted}, ensure_ascii=False))

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save_snapshot)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_next_stage_bank59_gap_event_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_independent_audit_and_user_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {**identity(OUT_SAVE, save_snapshot), "policy": "test-only current main SaveRAM snapshot; never promote"},
        "catalog": identity(CATALOG),
        "provenance": provenance,
        "counts": {
            "targets": len(rows),
            "unique_phrases": len(assignments),
            "target_failures": len(failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "checks": checks,
        "applied": applied,
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "test_scope": [
            "593EA2/593EB4 uploaded scene renders as two fully Korean lines",
            "all nine dialogue records in 593E8A-593F28 advance without event errors",
            "save, full emulator restart, and reload",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
