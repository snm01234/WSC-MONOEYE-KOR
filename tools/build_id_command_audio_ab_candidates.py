#!/usr/bin/env python3
"""Build A/B diagnostic ROMs for the ID-command broken-audio delay.

A: keep Korean text but replace all 20 activation-line E5 18 references with
   two ordinary stock dictionary tokens, avoiding expansion-bank mapping.
B: store the original Japanese phrases in the same kind of safe stock slots and
   point the same 20 records at them. This separates Hangul rendering from the
   expansion-bank switch without depending on the modified current dictionary.

Both are test-only candidates paired with the current live main SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC_DUP = ROOT / "data/battle_id_command_followup_ko.json"
SPEC_VAR = ROOT / "data/id_indirect_ui_activation_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_STOCK = PATCH / "id_command_audio_stock_token_candidate.wsc"
OUT_STOCK_SAVE = ROOT / "sram/id_command_audio_stock_token_candidate.sav"
OUT_ORIGINAL = PATCH / "id_command_audio_original_control_candidate.wsc"
OUT_ORIGINAL_SAVE = ROOT / "sram/id_command_audio_original_control_candidate.sav"
REPORT = PATCH / "id_command_audio_ab_report.json"

EXPECTED_MAIN = "33bd176fb8afd3869acacdfd48fbf0459718dcc5f16d310309565313a48aae52"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 20


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def load_targets(main: bytes, original: bytes, main_dictionary: Any, original_dictionary: Any, tbl: Tbl) -> list[dict[str, Any]]:
    dup = json.loads(SPEC_DUP.read_text(encoding="utf-8"))
    var = json.loads(SPEC_VAR.read_text(encoding="utf-8"))
    sources: list[dict[str, Any]] = []
    for row in dup.get("records") or []:
        if row.get("category") != "id_command_activation_duplicate":
            continue
        sources.append(
            {
                "abs": str(row["record_start"]).upper(),
                "prefix_hex": str(row.get("prefix_hex") or ""),
                "ko": str(row["ko"]),
                "category": "activation_duplicate",
            }
        )
    for row in var.get("records") or []:
        if row.get("category") != "id_command_activation_variant":
            continue
        sources.append(
            {
                "abs": str(row["abs"]).upper(),
                "prefix_hex": str(row.get("prefix_hex") or ""),
                "ko": str(row["ko"]),
                "category": "activation_variant",
            }
        )
    if len(sources) != EXPECTED_TARGETS or len({row["abs"] for row in sources}) != EXPECTED_TARGETS:
        raise BuildError("ID activation target population drifted")

    prepared: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda row: int(row["abs"], 16)):
        logical = int(source["abs"], 16)
        prefix = bytes.fromhex(source["prefix_hex"])
        current_payload, current_term = payload_at(main, logical)
        original_payload, original_term = payload_at(original, logical)
        if len(current_payload) != len(original_payload) or current_term - stock_base(main) != original_term - stock_base(original):
            raise BuildError(f"original/current record boundary differs at {source['abs']}")
        if not current_payload.startswith(prefix) or not original_payload.startswith(prefix):
            raise BuildError(f"prefix mismatch at {source['abs']}")
        current_body = current_payload[len(prefix) :]
        original_body = original_payload[len(prefix) :]
        if current_body[:2] != b"\xE5\x18":
            raise BuildError(f"current target is not E5 18 at {source['abs']}")
        ko = normalize_ko_text(source["ko"])
        current_text = main_dictionary.expand(current_body, tbl).rstrip("\u3000 \t")
        original_text = original_dictionary.expand(original_body, tbl).rstrip("\u3000 \t")
        if current_text != ko or any(is_japanese_character(ch) for ch in current_text):
            raise BuildError(f"current Korean binding failed at {source['abs']}")
        prepared.append(
            {
                **source,
                "logical": logical,
                "prefix": prefix,
                "capacity": len(current_payload),
                "body_capacity": len(current_body),
                "current_payload": current_payload,
                "current_body": current_body,
                "original_payload": original_payload,
                "original_body": original_body,
                "ko": ko,
                "current_text": current_text,
                "original_text": original_text,
            }
        )
    return prepared


def main() -> int:
    main = bytes(load_rom(MAIN))
    save_snapshot = MAIN_SAVE.read_bytes()
    original = bytes(load_rom(ORIGINAL))
    if len(main) != ROM_SIZE or sha256(main) != EXPECTED_MAIN:
        raise BuildError("promoted main TIP identity drifted")
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    main_dictionary = make_dictionary_ext3(main, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    rows = load_targets(main, original, main_dictionary, original_dictionary, tbl)
    phrases = sorted({str(row["ko"]) for row in rows})
    if len(phrases) != 2:
        raise BuildError("expected exactly two activation phrases")
    jp_by_ko: dict[str, str] = {}
    for row in rows:
        ko = str(row["ko"])
        jp = str(row["original_text"])
        previous = jp_by_ko.setdefault(ko, jp)
        if previous != jp:
            raise BuildError(f"one Korean phrase maps to multiple original phrases: {ko!r}")
    if len(set(jp_by_ko.values())) != 2:
        raise BuildError("expected exactly two original Japanese activation phrases")

    retired = current_strong_retired_slots(original, main, main_dictionary)
    wanted = set(retired)
    external = external_occurrence_map(main, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(main_dictionary, wanted=wanted, ext3_aware=True)
    raw = _raw_pair_hits(main, retired)
    safe = [index for index in retired if not external.get(index) and not nested.get(index) and not raw.get(index)]
    selected = safe[: len(phrases)]
    if len(selected) != len(phrases):
        raise BuildError("not enough unreachable stock slots")
    assignment = dict(zip(phrases, selected))
    stock_payloads = {index: encode_phrase(phrase, tbl) for phrase, index in assignment.items()}
    control_payloads = {
        assignment[phrase]: encode_phrase(jp_by_ko[phrase], tbl)
        for phrase in phrases
    }

    def prepare_stock_variant(payloads: dict[int, bytes], label: str) -> tuple[bytearray, int, int]:
        candidate = bytearray(main)
        pointers_before = list(Dictionary(candidate).ptrs)
        cursor_before = _stock_phrase_cursor(candidate)
        pointers_written, cursor_after = write_dictionary_slots_spill(
            candidate,
            payloads,
            spill_start=SPILL_FLOOR,
            allow_aux_consumers=False,
            locs=_working_two_byte_external_refs(bytes(candidate)),
        )
        if list(Dictionary(candidate).ptrs) != pointers_written:
            raise BuildError(f"{label} stock writer pointer mismatch")
        changed = {
            index
            for index, (before, after) in enumerate(zip(pointers_before, pointers_written))
            if before != after
        }
        if changed != set(selected):
            raise BuildError(f"{label} unexpected stock pointer changes")
        return candidate, cursor_before, cursor_after

    stock, stock_cursor_before, stock_cursor_after = prepare_stock_variant(stock_payloads, "Korean")
    control, control_cursor_before, control_cursor_after = prepare_stock_variant(control_payloads, "Japanese control")
    sb = stock_base(main)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        prefix = bytes(row["prefix"])
        body_capacity = int(row["body_capacity"])
        index = assignment[str(row["ko"])]
        token = token_from_dict_index(index)
        stock_body = token + b"\x01" * (body_capacity - len(token))
        if len(stock_body) != body_capacity:
            raise BuildError(f"stock replacement length drift at {row['abs']}")
        start = sb + logical
        control_body = token + b"\x01" * (body_capacity - len(token))
        stock[start : start + len(prefix)] = prefix
        stock[start + len(prefix) : start + int(row["capacity"])] = stock_body
        control[start : start + len(prefix)] = prefix
        control[start + len(prefix) : start + int(row["capacity"])] = control_body
        target_extents.append((start, start + int(row["capacity"])))
        applied.append(
            {
                "abs": row["abs"],
                "category": row["category"],
                "prefix_hex": prefix.hex().upper(),
                "body_capacity": body_capacity,
                "current_ext3_body": bytes(row["current_body"]).hex().upper(),
                "stock_index": f"{index:04X}",
                "stock_token": token.hex().upper(),
                "stock_body": stock_body.hex().upper(),
                "control_body": control_body.hex().upper(),
                "original_body": bytes(row["original_body"]).hex().upper(),
                "ko": row["ko"],
                "original_text": row["original_text"],
            }
        )

    stock_checksum = update_ws_checksum(stock)
    control_checksum = update_ws_checksum(control)
    stock_bytes = bytes(stock)
    control_bytes = bytes(control)
    stock_dictionary = make_dictionary_ext3(stock_bytes, ext_meta, ext3_meta)
    control_dictionary = make_dictionary_ext3(control_bytes, ext_meta, ext3_meta)

    stock_failures: list[dict[str, Any]] = []
    control_failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        prefix = bytes(row["prefix"])
        stock_payload, stock_term = payload_at(stock_bytes, logical)
        control_payload, control_term = payload_at(control_bytes, logical)
        stock_text = stock_dictionary.expand(stock_payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        control_text = control_dictionary.expand(control_payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        stock_reasons: list[str] = []
        control_reasons: list[str] = []
        if stock_text != row["ko"]:
            stock_reasons.append("render_mismatch")
        if stock_payload[len(prefix) : len(prefix) + 2] == b"\xE5\x18":
            stock_reasons.append("still_uses_ext3")
        if stock_term != sb + logical + int(row["capacity"]):
            stock_reasons.append("terminator_changed")
        if control_text != row["original_text"]:
            control_reasons.append("original_render_mismatch")
        if control_payload[len(prefix) : len(prefix) + 2] == b"\xE5\x18":
            control_reasons.append("control_still_uses_ext3")
        if control_term != sb + logical + int(row["capacity"]):
            control_reasons.append("terminator_changed")
        if stock_reasons:
            stock_failures.append({"abs": row["abs"], "actual": stock_text, "reasons": stock_reasons})
        if control_reasons:
            control_failures.append({"abs": row["abs"], "actual": control_text, "reasons": control_reasons})

    excluded = {int(row["logical"]) for row in rows}
    stock_invariance = verify_non_target_invariance(
        main,
        stock_bytes,
        before_dictionary=main_dictionary,
        after_dictionary=stock_dictionary,
        tbl=tbl,
        excluded=excluded,
    )
    control_invariance = verify_non_target_invariance(
        main,
        control_bytes,
        before_dictionary=main_dictionary,
        after_dictionary=control_dictionary,
        tbl=tbl,
        excluded=excluded,
    )

    selected_set = set(selected)
    stock_external = external_occurrence_map(stock_bytes, ext3_aware=True, wanted=selected_set)
    stock_nested = nested_occurrence_map(stock_dictionary, wanted=selected_set, ext3_aware=True)
    control_external = external_occurrence_map(control_bytes, ext3_aware=True, wanted=selected_set)
    control_nested = nested_occurrence_map(control_dictionary, wanted=selected_set, ext3_aware=True)
    expected_sites: dict[int, set[str]] = defaultdict(set)
    for row in applied:
        expected_sites[int(row["stock_index"], 16)].add(str(row["abs"]))
    reference_failures: list[dict[str, Any]] = []
    for index in selected:
        stock_sites = {str(ref.get("record_abs") or "").upper() for ref in stock_external.get(index, [])}
        control_sites = {str(ref.get("record_abs") or "").upper() for ref in control_external.get(index, [])}
        if (
            stock_sites != expected_sites[index]
            or control_sites != expected_sites[index]
            or stock_nested.get(index)
            or control_nested.get(index)
        ):
            reference_failures.append(
                {
                    "index": f"{index:04X}",
                    "expected": sorted(expected_sites[index]),
                    "stock_actual": sorted(stock_sites),
                    "control_actual": sorted(control_sites),
                    "stock_nested": stock_nested.get(index, []),
                    "control_nested": control_nested.get(index, []),
                }
            )

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2)
        for index in selected
    ]
    stock_phrase_extent = (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)
    control_phrase_extent = (stock_bank_file + control_cursor_before, stock_bank_file + control_cursor_after)
    stock_runs = diff_runs(main, stock_bytes)
    control_runs = diff_runs(main, control_bytes)
    stock_allowed = target_extents + stock_pointer_extents + [stock_phrase_extent, (len(main) - 2, len(main))]
    control_allowed = target_extents + stock_pointer_extents + [control_phrase_extent, (len(main) - 2, len(main))]
    stock_unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in stock_runs
        if not covered((left, right), stock_allowed)
    ]
    control_unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in control_runs
        if not covered((left, right), control_allowed)
    ]

    runtime_exact_stock = (
        main[sb + 0x7A0000 : sb + 0x7B0000] == stock_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and main[sb + 0x7F0000 : sb + 0x800000 - 2] == stock_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    runtime_exact_control = (
        main[sb + 0x7A0000 : sb + 0x7B0000] == control_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and main[sb + 0x7F0000 : sb + 0x800000 - 2] == control_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    checks = {
        "main_identity_exact": sha256(main) == EXPECTED_MAIN,
        "targets_exactly_20": len(rows) == EXPECTED_TARGETS,
        "all_current_targets_use_ext3": all(bytes(row["current_body"])[:2] == b"\xE5\x18" for row in rows),
        "two_safe_stock_slots_selected": len(selected) == 2,
        "stock_targets_render_exact": not stock_failures,
        "stock_targets_have_no_ext3": not stock_failures,
        "stock_reference_sites_exact": not reference_failures,
        "stock_non_target_invariance": stock_invariance.get("ok") is True,
        "stock_diffs_bounded": not stock_unaccounted,
        "stock_runtime_banks_exact": runtime_exact_stock,
        "japanese_stock_control_exact": not control_failures,
        "control_non_target_invariance": control_invariance.get("ok") is True,
        "control_diffs_bounded": not control_unaccounted,
        "control_runtime_banks_exact": runtime_exact_control,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_MAIN,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_snapshot,
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "stock_failures": stock_failures,
                    "control_failures": control_failures,
                    "reference_failures": reference_failures,
                    "stock_unaccounted": stock_unaccounted,
                    "control_unaccounted": control_unaccounted,
                },
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_STOCK, stock_bytes)
    shutil.copy2(MAIN_SAVE, OUT_STOCK_SAVE)
    atomic_bytes(OUT_ORIGINAL, control_bytes)
    shutil.copy2(MAIN_SAVE, OUT_ORIGINAL_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_audio_ab_candidates.py",
        "ok": True,
        "status": "static_verified_pending_user_audio_ab_test",
        "hypothesis": (
            "E5 18 expansion-dictionary rendering maps ROM1 to expansion banks while the ID-command "
            "voice/effect stream is active, so mapped dictionary bytes may be consumed as audio until "
            "the stream/wait completes. This is a strong inference pending runtime A/B confirmation."
        ),
        "parent": identity(MAIN, main),
        "stock_candidate": identity(OUT_STOCK, stock_bytes),
        "stock_candidate_save": identity(OUT_STOCK_SAVE),
        "original_control_candidate": identity(OUT_ORIGINAL, control_bytes),
        "original_control_save": identity(OUT_ORIGINAL_SAVE),
        "counts": {
            "targets": len(rows),
            "activation_duplicates": sum(row["category"] == "activation_duplicate" for row in rows),
            "activation_variants": sum(row["category"] == "activation_variant" for row in rows),
            "phrases": len(phrases),
            "stock_failures": len(stock_failures),
            "control_failures": len(control_failures),
            "stock_non_target_failures": int(stock_invariance.get("failure_count") or 0),
            "control_non_target_failures": int(control_invariance.get("failure_count") or 0),
        },
        "allocation": {
            "stock_slots": {phrase: f"{index:04X}" for phrase, index in assignment.items()},
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
            "control_cursor_before": f"{control_cursor_before:04X}",
            "control_cursor_after": f"{control_cursor_after:04X}",
        },
        "diff": {
            "stock_changed_bytes": sum(right - left for left, right in stock_runs),
            "stock_runs": len(stock_runs),
            "stock_checksum": f"{stock_checksum:04X}",
            "control_changed_bytes": sum(right - left for left, right in control_runs),
            "control_runs": len(control_runs),
            "control_checksum": f"{control_checksum:04X}",
        },
        "checks": checks,
        "records": applied,
        "runtime_test_order": [
            "Test id_command_audio_stock_token_candidate first. Korean text should remain, and broken audio should disappear if bank-switch interference is the cause.",
            "If noise remains, test id_command_audio_original_control_candidate. It renders the original Japanese phrases through ordinary stock tokens, so it also removes expansion-bank mapping.",
            "Interpretation: both clean = E5 18 bank-switch conflict confirmed; only Japanese control clean = Hangul rendering path also contributes; both noisy = cause is outside these text records.",
        ],
        "promotion": "blocked_pending_user_audio_ab_test",
    }
    atomic_json(REPORT, report)
    print(json.dumps({key: report[key] for key in ("ok", "status", "stock_candidate", "original_control_candidate", "counts", "allocation", "diff", "checks")}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
