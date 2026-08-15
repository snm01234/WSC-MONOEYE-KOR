#!/usr/bin/env python3
"""Build a diagnostic ROM that gives every ambiguous aux prefix a screen barcode.

The current aux reclassification sheet contains records whose prefix bytes decode
as Japanese glyphs statically while the body is already Korean.  Static analysis
cannot prove whether those bytes are consumed as speaker/window controls or are
actually rendered at runtime.  This test-only candidate preserves every prefix
and record boundary, then replaces only the body with a unique visible code:

    ＃000 ... ＃B02

When a screen shows only ``＃XYZ``, the prefix was consumed structurally.  When it
shows Japanese glyph(s) immediately before ``＃XYZ``, that exact manifest address
has a runtime false-prefix defect.

Bodies of four bytes or more use the already-promoted five-bank E5 18 alias.
Two/three-byte bodies use unreachable strong-retired stock dictionary slots.
The live main TIP and SaveRAM are never modified.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
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
from build_encyclopedia_ms_batch01_candidate import payload_at
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
    SEG_DICT,
    Dictionary,
    Tbl,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SHEET = ROOT / "out/script/aux_vetted_mixed_reclass_sheet.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/aux_prefix_barcode_probe_candidate.wsc"
OUT_SAVE = ROOT / "sram/aux_prefix_barcode_probe_candidate.sav"
MANIFEST_JSON = ROOT / "out/patch/aux_prefix_barcode_manifest.json"
MANIFEST_CSV = ROOT / "out/patch/aux_prefix_barcode_manifest.csv"
REPORT = ROOT / "out/patch/aux_prefix_barcode_probe_report.json"

EXPECTED_PARENT_SHA256 = "6cf6184e7e989c02b25b3e61fc28fc9e9eca354c20726b5efc7eec10918dce05"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 2_819
EXPECTED_EXT3_ROWS = 2_714
EXPECTED_STOCK_ROWS = 105
FULLWIDTH = str.maketrans("0123456789ABCDEF", "０１２３４５６７８９ＡＢＣＤＥＦ")


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding=encoding, newline="\n")
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def barcode(sequence: int) -> str:
    if not 0 <= sequence <= 0xFFF:
        raise BuildError(f"barcode sequence outside three-hex-digit range: {sequence}")
    return "＃" + f"{sequence:03X}".translate(FULLWIDTH)


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def make_diagnostic_phrase(code: str, current: str, tbl: Tbl, *, preserve_tail: bool) -> tuple[str, bytes, bool]:
    normalized = normalize_ko_text(current)
    candidates: list[tuple[str, bool]] = []
    if preserve_tail and len(normalized) >= len(code):
        candidates.append((normalize_ko_text(code + normalized[len(code) :]), True))
    candidates.append((code, False))
    errors: list[str] = []
    for phrase, tail_preserved in candidates:
        if any(is_japanese_character(ch) for ch in phrase):
            errors.append("japanese_character")
            continue
        try:
            encoded = encode_phrase(phrase, tbl)
        except Exception as exc:  # diagnostic fallback is intentionally fail-safe
            errors.append(str(exc))
            continue
        if encoded and b"\x00" not in encoded:
            return phrase, encoded, tail_preserved
        errors.append("empty_or_nul_encoding")
    raise BuildError(f"cannot encode diagnostic phrase {code}: {errors}")


def load_rows(parent: bytes, tbl: Tbl, dictionary: Any) -> list[dict[str, Any]]:
    with SHEET.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = [
            dict(row)
            for row in csv.DictReader(stream)
            if str(row.get("reclass") or "") == "ko_only_after_prefix"
        ]
    source_rows.sort(key=lambda row: int(str(row["abs"]), 16))
    if len(source_rows) != EXPECTED_ROWS:
        raise BuildError(f"sheet population drifted: {len(source_rows)} != {EXPECTED_ROWS}")
    if len({str(row.get("abs") or "").upper() for row in source_rows}) != EXPECTED_ROWS:
        raise BuildError("sheet contains duplicate addresses")

    sb = stock_base(parent)
    prepared: list[dict[str, Any]] = []
    for sequence, source in enumerate(source_rows):
        address = str(source.get("abs") or "").upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source.get("payload_capacity") or -1)
        body_capacity = int(source.get("body_capacity") or -1)
        expected = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        if str(source.get("parent_tip_sha256") or "").lower() != EXPECTED_PARENT_SHA256:
            raise BuildError(f"sheet parent binding drifted at {address}")
        if payload_capacity != len(expected) or body_capacity != payload_capacity - len(prefix):
            raise BuildError(f"sheet boundary drifted at {address}")
        if body_capacity < 2:
            raise BuildError(f"body cannot fit a diagnostic token at {address}")
        payload, terminator = payload_at(parent, logical)
        if payload != expected or not payload.startswith(prefix):
            raise BuildError(f"parent payload drifted at {address}")
        if terminator != sb + logical + payload_capacity or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {address}")
        body = payload[len(prefix) :]
        current = strip_pad(dictionary.expand(body, tbl))
        if current != str(source.get("current_text") or ""):
            raise BuildError(f"current body render drifted at {address}: {current!r}")
        if sha256(body) != str(source.get("source_body_sha256") or "").lower():
            raise BuildError(f"body digest drifted at {address}")

        code = barcode(sequence)
        phrase, encoded, tail_preserved = make_diagnostic_phrase(
            code,
            current,
            tbl,
            preserve_tail=body_capacity >= 4,
        )
        prepared.append(
            {
                "sequence": sequence,
                "code": code,
                "abs": address,
                "logical": logical,
                "bank": str(source.get("bank") or ""),
                "block": str(source.get("block") or ""),
                "prefix": prefix,
                "prefix_hex": prefix.hex().upper(),
                "prefix_rule": str(source.get("prefix_rule") or ""),
                "payload_capacity": payload_capacity,
                "body_capacity": body_capacity,
                "before_payload_hex": payload.hex().upper(),
                "before_body": current,
                "before_full_static": str(source.get("current_full_with_untrusted_prefix") or ""),
                "diagnostic_body": phrase,
                "encoded": encoded,
                "tail_preserved": tail_preserved,
                "original_jp": str(source.get("original_jp") or ""),
            }
        )

    ext3_rows = [row for row in prepared if int(row["body_capacity"]) >= 4]
    stock_rows = [row for row in prepared if int(row["body_capacity"]) < 4]
    if len(ext3_rows) != EXPECTED_EXT3_ROWS or len(stock_rows) != EXPECTED_STOCK_ROWS:
        raise BuildError(
            f"ext3/stock population drifted: {len(ext3_rows)}/{len(stock_rows)}"
        )
    if len({row["code"] for row in prepared}) != EXPECTED_ROWS:
        raise BuildError("barcode collision")
    return prepared


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    rows = load_rows(parent, tbl, parent_dictionary)
    ext3_rows = [row for row in rows if int(row["body_capacity"]) >= 4]
    stock_rows = [row for row in rows if int(row["body_capacity"]) < 4]

    allocation_input = [
        {"ko": row["diagnostic_body"], "encoded": row["encoded"]}
        for row in ext3_rows
    ]
    assignments, states = allocate_ext3(parent, allocation_input)
    candidate = bytearray(parent)
    ext3_pointer_extents: list[tuple[int, int]] = []
    ext3_phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        start = int(state["start"])
        candidate[start : start + BANK_SIZE] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        }
        ext3_pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2)
            for local in sorted(new_locals)
        )
        if int(state["cursor"]) > int(state["cursor_before"]):
            ext3_phrase_extents.append(
                (start + int(state["cursor_before"]), start + int(state["cursor"]))
            )

    retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected_retired = retired[: len(stock_rows)]
    if len(selected_retired) != EXPECTED_STOCK_ROWS:
        raise BuildError("insufficient unreachable strong-retired stock slots")
    selected_set = set(selected_retired)
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_set)
    current_nested = nested_occurrence_map(parent_dictionary, wanted=selected_set, ext3_aware=True)
    current_raw = _raw_pair_hits(parent, selected_retired)
    if any(current_external.get(i) or current_nested.get(i) or current_raw.get(i) for i in selected_retired):
        raise BuildError("selected retired stock slot is still reachable")

    stock_index_by_code = {
        row["code"]: index for row, index in zip(stock_rows, selected_retired)
    }
    stock_payloads = {
        stock_index_by_code[row["code"]]: bytes(row["encoded"])
        for row in stock_rows
    }
    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_before = list(Dictionary(candidate).ptrs)
    pointers_written, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        stock_payloads,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=_working_two_byte_external_refs(bytes(candidate)),
    )
    pointers_after = list(Dictionary(candidate).ptrs)
    if pointers_after != pointers_written:
        raise BuildError("stock pointer result differs from ROM")
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != selected_set:
        raise BuildError("stock pointer change set differs from selected retired slots")

    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        phrase = str(row["diagnostic_body"])
        if int(row["body_capacity"]) >= 4:
            info = assignments[phrase]
            token = bytes(info["token"])
            strategy = "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new"
            allocation: dict[str, Any] = {
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
                "encoded_length": len(row["encoded"]),
            }
        else:
            index = int(stock_index_by_code[row["code"]])
            token = token_from_dict_index(index)
            strategy = "strong_retired_stock"
            allocation = {
                "stock_index": f"{index:04X}",
                "encoded_length": len(row["encoded"]),
            }
        replacement = token + b"\x01" * (int(row["body_capacity"]) - len(token))
        if len(replacement) != int(row["body_capacity"]):
            raise BuildError(f"replacement length drift at {row['abs']}")
        body_start = sb + int(row["logical"]) + len(row["prefix"])
        candidate[body_start : body_start + int(row["body_capacity"])] = replacement
        target_extents.append((body_start, body_start + int(row["body_capacity"])))
        applied.append(
            {
                "sequence": int(row["sequence"]),
                "code": row["code"],
                "abs": row["abs"],
                "bank": row["bank"],
                "block": row["block"],
                "prefix_hex": row["prefix_hex"],
                "prefix_rule": row["prefix_rule"],
                "payload_capacity": int(row["payload_capacity"]),
                "body_capacity": int(row["body_capacity"]),
                "before_payload_hex": row["before_payload_hex"],
                "before_body": row["before_body"],
                "before_full_static": row["before_full_static"],
                "diagnostic_body": phrase,
                "tail_preserved": bool(row["tail_preserved"]),
                "original_jp": row["original_jp"],
                "strategy": strategy,
                "token_hex": token.hex().upper(),
                **allocation,
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        payload, terminator = payload_at(candidate_bytes, logical)
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        actual = strip_pad(candidate_dictionary.expand(payload[len(prefix) :], tbl))
        reasons: list[str] = []
        if payload[: len(prefix)] != prefix:
            reasons.append("prefix_changed")
        if actual != row["diagnostic_body"]:
            reasons.append("render_mismatch")
        if not actual.startswith(str(row["code"])):
            reasons.append("barcode_missing")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_in_diagnostic_body")
        if len(payload) != int(row["payload_capacity"]):
            reasons.append("payload_length_changed")
        if terminator != sb + logical + int(row["payload_capacity"]) or candidate_bytes[terminator] != 0:
            reasons.append("terminator_changed")
        if reasons:
            target_failures.append(
                {
                    "abs": row["abs"],
                    "code": row["code"],
                    "expected": row["diagnostic_body"],
                    "actual": actual,
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

    candidate_external = external_occurrence_map(candidate_bytes, ext3_aware=True, wanted=selected_set)
    candidate_nested = nested_occurrence_map(candidate_dictionary, wanted=selected_set, ext3_aware=True)
    expected_stock_sites: dict[int, set[str]] = defaultdict(set)
    for row in applied:
        if row["strategy"] == "strong_retired_stock":
            expected_stock_sites[int(str(row["stock_index"]), 16)].add(str(row["abs"]))
    retired_reference_failures: list[dict[str, Any]] = []
    for index in selected_retired:
        actual_sites = {
            str(ref.get("record_abs") or "").upper()
            for ref in candidate_external.get(index, [])
        }
        if actual_sites != expected_stock_sites[index] or candidate_nested.get(index):
            retired_reference_failures.append(
                {
                    "stock_index": f"{index:04X}",
                    "expected_sites": sorted(expected_stock_sites[index]),
                    "actual_sites": sorted(actual_sites),
                    "nested": candidate_nested.get(index, []),
                }
            )

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (
            stock_bank_file + DICT_PTR_START + index * 2,
            stock_bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in stock_payloads
    ]
    stock_phrase_extents = (
        [(stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)]
        if stock_cursor_after > stock_cursor_before
        else []
    )
    runs = diff_runs(parent, candidate_bytes)
    allowed = (
        target_extents
        + ext3_pointer_extents
        + ext3_phrase_extents
        + stock_pointer_extents
        + stock_phrase_extents
        + [(len(parent) - 2, len(parent))]
    )
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
    assigned_ext3_locals = {
        (int(row["page"]), int(str(row["local"]), 16))
        for row in applied
        if row["strategy"].startswith("five_bank")
    }
    all_assigned_locals_populated = all(
        int.from_bytes(
            candidate[(0x21 + page) * BANK_SIZE + local * 2 : (0x21 + page) * BANK_SIZE + local * 2 + 2],
            "little",
        )
        != 0x2000
        for page, local in assigned_ext3_locals
    )
    barcode_counts = Counter(row["code"] for row in applied)
    checks = {
        "parent_tip_exact": sha256(parent) == EXPECTED_PARENT_SHA256,
        "targets_exactly_2819": len(applied) == EXPECTED_ROWS,
        "ext3_targets_exactly_2714": len(ext3_rows) == EXPECTED_EXT3_ROWS,
        "stock_targets_exactly_105": len(stock_rows) == EXPECTED_STOCK_ROWS,
        "barcodes_unique": len(barcode_counts) == EXPECTED_ROWS and max(barcode_counts.values()) == 1,
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "retired_stock_references_exact": not retired_reference_failures,
        "all_assigned_ext3_locals_populated": all_assigned_locals_populated,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_snapshot,
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": target_failures[:50],
                    "retired_reference_failures": retired_reference_failures[:50],
                    "unaccounted": unaccounted[:50],
                    "invariance": invariance,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save_snapshot)

    manifest = {
        "schema_version": 1,
        "generated_by": "tools/build_aux_prefix_barcode_probe_candidate.py",
        "purpose": "runtime classification of ambiguous Japanese-looking aux prefixes",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "count": len(applied),
        "code_range": {"first": applied[0]["code"], "last": applied[-1]["code"]},
        "interpretation": {
            "barcode_only": "prefix was consumed structurally; do not remove it",
            "japanese_then_barcode": "runtime false-prefix confirmed at this manifest address",
            "no_barcode": "screen belongs to a different text path or an uninstrumented record",
        },
        "records": applied,
    }
    atomic_json(MANIFEST_JSON, manifest)

    csv_fields = [
        "code",
        "abs",
        "bank",
        "block",
        "prefix_hex",
        "prefix_rule",
        "strategy",
        "stock_index",
        "page",
        "physical_bank",
        "local",
        "before_body",
        "before_full_static",
        "diagnostic_body",
        "tail_preserved",
        "original_jp",
    ]
    csv_lines: list[str] = []
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=csv_fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in applied:
        writer.writerow(row)
    atomic_text(MANIFEST_CSV, "\ufeff" + buffer.getvalue())

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_aux_prefix_barcode_probe_candidate.py",
        "ok": True,
        "published": False,
        "status": "test_only_static_verified_pending_user_runtime_barcode_observation",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE, save_snapshot),
            "policy": "build-time current main SaveRAM snapshot; test-only; never promote",
        },
        "source_sheet": identity(SHEET),
        "manifest_json": identity(MANIFEST_JSON),
        "manifest_csv": identity(MANIFEST_CSV),
        "counts": {
            "targets": len(applied),
            "by_bank": dict(Counter(row["bank"] for row in applied)),
            "five_bank_ext3": len(ext3_rows),
            "strong_retired_stock": len(stock_rows),
            "tail_preserved": sum(bool(row["tail_preserved"]) for row in applied),
            "barcode_only_fallback": sum(not bool(row["tail_preserved"]) for row in applied),
            "target_failures": len(target_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "five_bank_new_or_reused_phrases": len(assignments),
            "five_bank_used_after": {
                f"{0x21 + page:02X}": len(state["used_before"])
                + sum(
                    int(info["page"]) == page and not bool(info["reused"])
                    for info in assignments.values()
                )
                for page, state in states.items()
            },
            "strong_retired_stock_indices": [f"{index:04X}" for index in selected_retired],
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
        },
        "diff": {
            "changed_bytes": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "verification": {
            "checks": checks,
            "target_failures": target_failures,
            "retired_reference_failures": retired_reference_failures,
            "non_target_invariance": invariance,
            "unaccounted_diff_runs": unaccounted,
        },
        "runtime_test": {
            "instruction": "When Japanese glyph(s) appear immediately before a ＃XYZ code, report the code or screenshot.",
            "example_false_prefix": "私＃3A7... -> look up ＃3A7 in the manifest",
            "example_control_prefix": "＃3A7... -> prefix was consumed and is not a defect",
            "candidate_saveram": "test-only; never copy back to the main SaveRAM",
        },
        "promotion": "forbidden_diagnostic_rom",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "candidate_save": report["candidate_save"],
                "counts": report["counts"],
                "diff": report["diff"],
                "manifest": str(MANIFEST_JSON.relative_to(ROOT)),
                "report": str(REPORT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
