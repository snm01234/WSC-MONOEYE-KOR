#!/usr/bin/env python3
"""Build a test ROM applying non-stub ambiguous battle-voice Korean drafts.

- Excludes mass stubs 不要 / 欠番 / 不用 (already filtered in the working sheet).
- body_capacity >= 4: five-bank E5 18 + ext3 phrase storage.
- body_capacity 2..3: exact existing stock reuse or strong-retired stock spill.
- Writes out/patch/battle_voice_ambiguous_nonstub_test_candidate.{wsc,sav}
- Does NOT modify the live main TIP. SaveRAM is a current-main snapshot pair.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3
from build_encyclopedia_character_five_bank_batch02_candidate import PAGES
from build_encyclopedia_ms_batch01_candidate import exact_slots
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    verify_non_target_invariance,
)
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    find_rom,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    ws_header,
)
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SHEET = ROOT / "out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/battle_voice_ambiguous_nonstub_test_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_voice_ambiguous_nonstub_test_candidate.sav"
REPORT = ROOT / "out/patch/battle_voice_ambiguous_nonstub_test_candidate_report.json"

MAIN_SHA256 = "0668ad254ad7cd91d6efc0110546488ddcdd2c5cce04f1dd034c85a9e4169c4e"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 175
TAG_RE = re.compile(r"<[^>]+>")


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(data), "sha256": sha256(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def visible_has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in TAG_RE.sub("", text))


def load_rows(parent: bytes, tbl: Tbl) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with SHEET.open(encoding="utf-8-sig", newline="") as stream:
        sources = [dict(row) for row in csv.DictReader(stream)]
    if len(sources) != EXPECTED_ROWS:
        raise BuildError(f"sheet population drifted: {len(sources)} != {EXPECTED_ROWS}")
    tip_sha = sha256(parent)
    sb = stock_base(parent)
    ext3_rows: list[dict[str, Any]] = []
    short_rows: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source["payload_capacity"])
        body_capacity = int(source["body_capacity"])
        expected_payload = bytes.fromhex(str(source["current_payload_hex"]))
        expected_body_digest = str(source["source_body_sha256"]).lower()
        parent_tip_sha = str(source.get("parent_tip_sha256") or "").lower()
        if parent_tip_sha and parent_tip_sha != tip_sha:
            raise BuildError(f"sheet parent TIP digest drifted at {address}")
        if payload_capacity != len(expected_payload) or body_capacity != payload_capacity - len(prefix):
            raise BuildError(f"sheet boundary drifted at {address}")
        if body_capacity < 2:
            raise BuildError(f"body too short at {address}")
        current = parent[sb + logical : sb + logical + payload_capacity]
        if current != expected_payload or not current.startswith(prefix):
            raise BuildError(f"parent payload drifted at {address}")
        if parent[sb + logical + payload_capacity] != 0:
            raise BuildError(f"terminator drifted at {address}")
        if sha256(current[len(prefix) :]) != expected_body_digest:
            raise BuildError(f"body digest drifted at {address}")
        if source.get("translation_source") != "llm" or source.get("review_status") != "approved":
            raise BuildError(f"translation not approved at {address}")
        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or visible_has_japanese(ko):
            raise BuildError(f"invalid Korean at {address}: {ko!r}")
        encoded = encode_phrase(ko, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"cannot encode Korean at {address}")
        row = {
            "abs": address,
            "logical": logical,
            "batch_id": str(source.get("batch_id") or ""),
            "scope": str(source.get("scope") or ""),
            "gap": str(source.get("gap") or ""),
            "jp": str(source.get("original_jp") or ""),
            "before": str(source.get("current_text") or ""),
            "ko": ko,
            "encoded": encoded,
            "prefix": prefix,
            "prefix_len": len(prefix),
            "payload_capacity": payload_capacity,
            "body_capacity": body_capacity,
            "boundary_review_required": str(source.get("boundary_review_required") or ""),
        }
        if body_capacity >= 4:
            ext3_rows.append(row)
        else:
            short_rows.append(row)
    return ext3_rows, short_rows


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")
    original = bytes(load_rom(find_rom(ROOT)))

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    ext3_rows, short_rows = load_rows(parent, tbl)
    if len(ext3_rows) + len(short_rows) != EXPECTED_ROWS:
        raise BuildError("row split drifted")

    assignments, states = allocate_ext3(parent, ext3_rows) if ext3_rows else ({}, {})
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
            (start + local * 2, start + local * 2 + 2) for local in sorted(new_locals)
        )
        if int(state["cursor"]) > int(state["cursor_before"]):
            phrase_extents.append(
                (start + int(state["cursor_before"]), start + int(state["cursor"]))
            )

    short_phrases = {str(row["ko"]) for row in short_rows}
    exact = exact_slots(parent_dictionary, tbl, short_phrases) if short_phrases else {}
    reusable = {phrase: slots for phrase, slots in exact.items() if slots}
    new_short_phrases = sorted(short_phrases - set(reusable))
    selected_retired: list[int] = []
    stock_payloads: dict[int, bytes] = {}
    stock_assignment: dict[str, int] = {phrase: min(slots) for phrase, slots in reusable.items()}
    if new_short_phrases:
        retired = current_strong_retired_slots(original, parent, parent_dictionary)
        selected_retired = retired[: len(new_short_phrases)]
        if len(selected_retired) != len(new_short_phrases):
            raise BuildError("insufficient strong-retired stock slots")
        selected_set = set(selected_retired)
        current_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_set)
        current_nested = nested_occurrence_map(
            parent_dictionary, wanted=selected_set, ext3_aware=True
        )
        current_raw = _raw_pair_hits(parent, selected_retired)
        if any(
            current_external.get(i) or current_nested.get(i) or current_raw.get(i)
            for i in selected_retired
        ):
            raise BuildError("selected retired stock slot is still reachable")
        for phrase, index in zip(new_short_phrases, selected_retired):
            stock_assignment[phrase] = index
            stock_payloads[index] = encode_phrase(phrase, tbl)

    pointers_before = list(Dictionary(candidate).ptrs)
    stock_cursor_before = _stock_phrase_cursor(candidate)
    if stock_payloads:
        pointers_written, stock_cursor_after = write_dictionary_slots_spill(
            candidate,
            stock_payloads,
            spill_start=SPILL_FLOOR,
            allow_aux_consumers=False,
            locs=_working_two_byte_external_refs(bytes(candidate)),
        )
    else:
        pointers_written = list(Dictionary(candidate).ptrs)
        stock_cursor_after = stock_cursor_before
    pointers_after = list(Dictionary(candidate).ptrs)
    if pointers_after != pointers_written:
        raise BuildError("stock writer pointer result differs from ROM")
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != set(stock_payloads):
        raise BuildError("stock pointer change set differs from selected retired slots")

    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in ext3_rows + short_rows:
        phrase = str(row["ko"])
        if int(row["body_capacity"]) >= 4:
            info = assignments[phrase]
            token = bytes(info["token"])
            strategy = "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new"
            allocation: dict[str, Any] = {
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
            }
        else:
            index = int(stock_assignment[phrase])
            token = token_from_dict_index(index)
            if len(token) > int(row["body_capacity"]):
                raise BuildError(f"stock token too long for body at {row['abs']}")
            strategy = "existing_exact_stock" if phrase in reusable else "strong_retired_stock"
            allocation = {"stock_index": f"{index:04X}"}
        replacement = token + b"\x01" * (int(row["body_capacity"]) - len(token))
        if len(replacement) != int(row["body_capacity"]):
            raise BuildError(f"replacement length drift at {row['abs']}")
        body_start = sb + int(row["logical"]) + int(row["prefix_len"])
        candidate[body_start : body_start + int(row["body_capacity"])] = replacement
        target_extents.append((body_start, body_start + int(row["body_capacity"])))
        applied.append(
            {
                "abs": row["abs"],
                "batch_id": row["batch_id"],
                "scope": row["scope"],
                "gap": row["gap"],
                "jp": row["jp"],
                "before": row["before"],
                "after": phrase,
                "prefix_hex": bytes(row["prefix"]).hex().upper(),
                "payload_capacity": int(row["payload_capacity"]),
                "body_capacity": int(row["body_capacity"]),
                "boundary_review_required": row["boundary_review_required"],
                "strategy": strategy,
                **allocation,
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    target_failures: list[dict[str, Any]] = []
    for row in ext3_rows + short_rows:
        start = sb + int(row["logical"])
        payload = candidate_bytes[start : start + int(row["payload_capacity"])]
        actual = candidate_dictionary.expand(payload[int(row["prefix_len"]) :], tbl).rstrip(
            "\u3000 \t"
        )
        reasons: list[str] = []
        if payload[: int(row["prefix_len"])] != row["prefix"]:
            reasons.append("prefix_changed")
        if actual != row["ko"]:
            reasons.append("render_mismatch")
        if visible_has_japanese(actual):
            reasons.append("japanese_residual")
        if candidate_bytes[start + int(row["payload_capacity"])] != 0:
            reasons.append("terminator_changed")
        if reasons:
            target_failures.append(
                {"abs": row["abs"], "expected": row["ko"], "actual": actual, "reasons": reasons}
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in ext3_rows + short_rows},
    )
    runs = diff_runs(parent, candidate_bytes)
    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (
            stock_bank_file + DICT_PTR_START + index * 2,
            stock_bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in stock_payloads
    ]
    stock_phrase_extents: list[tuple[int, int]] = []
    if stock_payloads and stock_cursor_after > stock_cursor_before:
        stock_phrase_extents.append(
            (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)
        )
    allowed = (
        target_extents
        + pointer_extents
        + phrase_extents
        + stock_pointer_extents
        + stock_phrase_extents
        + [(len(parent) - 2, len(parent))]
    )
    unaccounted = [
        {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[s * BANK_SIZE : (s + 1) * BANK_SIZE]
        == candidate_bytes[s * BANK_SIZE : (s + 1) * BANK_SIZE]
        for s in range(0x11, 0x21)
    )
    page_hits_parent = {p: five.scan_range_hits(parent, p) for p in range(PAGES)}
    page_hits_candidate = {p: five.scan_range_hits(candidate_bytes, p) for p in range(PAGES)}
    expected_page_counts = {
        p: len(page_hits_parent[p])
        + sum(
            1
            for row in applied
            if row.get("page") == p
            and row["strategy"] in {"five_bank_e518_alias_new", "five_bank_e518_alias_reuse"}
        )
        for p in range(PAGES)
    }
    retired_reference_failures: list[dict[str, Any]] = []
    if selected_retired:
        after_external = external_occurrence_map(
            candidate_bytes, ext3_aware=True, wanted=set(selected_retired)
        )
        after_nested = nested_occurrence_map(
            candidate_dictionary, wanted=set(selected_retired), ext3_aware=True
        )
        for index in selected_retired:
            # consumers should be only our short targets using this index
            consumers = [
                row["abs"]
                for row in applied
                if row.get("stock_index") == f"{index:04X}"
            ]
            # nested must remain 0; external raw may include body refs - use raw check carefully
            if after_nested.get(index):
                retired_reference_failures.append(
                    {"stock_index": f"{index:04X}", "reason": "nested_refs", "consumers": consumers}
                )

    checks = {
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == MAIN_SHA256,
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "page_reference_counts_exact": all(
            len(page_hits_candidate[p]) == expected_page_counts[p] for p in range(PAGES)
        ),
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "retired_nested_refs_clear": not retired_reference_failures,
        "row_count_exact": len(applied) == EXPECTED_ROWS,
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": target_failures[:20],
                    "unaccounted": unaccounted[:20],
                    "invariance_failures": (invariance.get("failures") or [])[:10],
                    "retired_reference_failures": retired_reference_failures,
                },
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if sha256(OUT_SAVE.read_bytes()) != sha256(save_snapshot):
        raise BuildError("test SaveRAM snapshot drifted")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_voice_ambiguous_nonstub_test_candidate.py",
        "ok": True,
        "status": "test_candidate_static_verified",
        "promotion_allowed": False,
        "main_tip": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE),
            "policy": "test-only current main SaveRAM snapshot; never promote",
        },
        "sheet": identity(SHEET),
        "checksum": f"{checksum:04X}",
        "ws_checksum": f"{ws_header(candidate_bytes)['checksum']:04X}",
        "counts": {
            "targets": len(applied),
            "ext3_records": len(ext3_rows),
            "short_stock_records": len(short_rows),
            "unique_ext3_phrases": len(assignments),
            "short_reused_exact": len(reusable),
            "short_new_retired": len(stock_payloads),
            "target_failures": len(target_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "diff_runs": len(runs),
            "boundary_review_required": sum(
                1 for row in applied if row["boundary_review_required"] == "yes"
            ),
        },
        "checks": checks,
        "retired_stock_indices": [f"{index:04X}" for index in selected_retired],
        "applied": applied,
    }
    atomic_json(REPORT, report)
    summary = {k: report[k] for k in report if k != "applied"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
