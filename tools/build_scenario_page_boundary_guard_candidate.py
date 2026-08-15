#!/usr/bin/env python3
"""Build a conservative scenario page-boundary guard candidate.

Runtime A/B at 61E234 proved that replacing a stock four-byte body made of two
native dictionary tokens with one four-byte E5 18 ext3 portal can change the
2-line/page reader state across a following double-NUL boundary.  This builder
scans banks 60-63 for the same current shape and repairs only the subclass for
which the pristine body was exactly two native dictionary tokens.  The repair
keeps the Korean text but returns the record body to exactly two native tokens.

Structurally similar records whose pristine body used a mixed 2+1+1 or 1+2+1
code-unit grammar are inventory/audit-only in this candidate; changing them to
an unproven grammar would be less safe than leaving them untouched.

The promoted main TIP and live SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits, dict_token_safe_in_zstring
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs
from build_terminology_retranslation_candidate import stock_storage_proof
from hangul_marker import marker_code
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from monoeye_rom import (
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/scenario_page_boundary_guard_candidate.wsc"
OUT_SAVE = ROOT / "sram/scenario_page_boundary_guard_candidate.sav"
REPORT = ROOT / "out/patch/scenario_page_boundary_guard_candidate_report.json"

EXPECTED_MAIN_SHA = "6136fe7294f186952cfb1366bb4a38179484f4d86fe6f85af23beb3cb35e0ae0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_RISK_TOTAL = 63
EXPECTED_EXACT_DICT2 = 45
EXPECTED_MIXED = 18
EXPECTED_UNIQUE_DICT2_TEXTS = 35
EXPECTED_NOVEL_FRAGMENTS = 22

PREFIX = bytes.fromhex("173418")
EXT3_HEAD = bytes.fromhex("E518")


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def encode_text(tbl: Tbl, text: str) -> bytes:
    """Encode stock-dictionary Korean using the live EC8D Hangul run marker.

    Plain Tbl.encode_char() is insufficient for Korean stored inside stock
    dictionary phrases: the runtime dictionary expander needs the invisible
    EC8D marker before each contiguous Hangul run.  Static Dictionary.expand()
    hides EC8D, so a marker-less phrase can look correct to audits while drawing
    scattered glyphs at runtime.
    """
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if encoded is None or b"\x00" in encoded:
        raise BuildError(f"cannot marker-encode stock phrase: {text!r}")
    return encoded


def read_record(rom: bytes, logical: int, max_len: int = 128) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def original_unit_kinds(body: bytes) -> list[str]:
    kinds: list[str] = []
    i = 0
    while i < len(body):
        b = body[i]
        if 0xF0 <= b <= 0xFF and i + 1 < len(body):
            kinds.append("dict")
            i += 2
        elif 0xE0 <= b <= 0xE7 and i + 1 < len(body):
            kinds.append("glyph2")
            i += 2
        else:
            kinds.append("char1")
            i += 1
    return kinds


def scan_risks(parent: bytes, original: bytes, tbl: Tbl, dictionary: Any) -> list[dict[str, Any]]:
    sb = stock_base(parent)
    out: list[dict[str, Any]] = []
    zero = bytes([0])
    for bank in range(0x60, 0x64):
        lo = bank << 16
        hi = lo + 0x10000
        logical = lo
        while logical < hi:
            term_file = parent.find(zero, sb + logical, sb + hi)
            if term_file < 0:
                break
            term = term_file - sb
            payload = parent[sb + logical : sb + term]
            hit = (
                len(payload) == 7
                and payload[:3] == PREFIX
                and payload[3:5] == EXT3_HEAD
                and term + 2 < hi
                and parent[sb + term + 1] == 0
                and parent[sb + term + 2] == 0x18
            )
            if hit:
                second = term + 2
                second_term_file = parent.find(zero, sb + second, sb + hi)
                if second_term_file >= 0:
                    second_term = second_term_file - sb
                    third = second_term + 1
                    if third < hi and parent[sb + third] != 0:
                        # Bind to the pristine structure at the same logical address.
                        op, ot = read_record(original, logical)
                        if ot != term:
                            raise BuildError(
                                f"original terminator drift at {logical:06X}: {ot:06X}!={term:06X}"
                            )
                        if len(op) != 7 or op[:3] != PREFIX:
                            raise BuildError(f"original predecessor grammar drift at {logical:06X}")
                        osb = stock_base(original)
                        if original[osb + term + 1] != 0 or original[osb + term + 2] != 0x18:
                            raise BuildError(f"original double-NUL/18 boundary drift at {logical:06X}")
                        obody = op[3:]
                        kinds = original_unit_kinds(obody)
                        ext3_index = 0x1000 + (payload[5] << 8) + payload[6]
                        text = dictionary.expand_index(ext3_index, tbl)
                        out.append(
                            {
                                "logical": logical,
                                "bank": bank,
                                "term": term,
                                "second": second,
                                "second_term": second_term,
                                "third": third,
                                "payload": payload,
                                "original_payload": op,
                                "original_body": obody,
                                "original_kinds": kinds,
                                "ext3_index": ext3_index,
                                "text": text,
                            }
                        )
            logical = term + 1
    return out


def stock_text_map(dictionary: Any, tbl: Tbl) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for index in range(dictionary.stock_count):
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            text = dictionary.expand_index(index, tbl)
        except Exception:
            continue
        if text:
            out[text].append(index)
    return {text: sorted(values) for text, values in out.items()}


def plan_two_token_texts(
    texts: list[str], stock: dict[str, list[int]], tbl: Tbl, *, max_novel_bytes: int
) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """Deterministic union-cost planner for exact two-native-token records.

    Novel fragments must fit one proven in-place stock slot after EC8D marker
    insertion; otherwise that split is not a valid runtime plan.
    """

    def phrase_cost(text: str) -> int:
        return len(encode_text(tbl, text)) + 1

    options: dict[str, list[tuple[str, str, frozenset[str]]]] = {}
    for text in texts:
        if len(text) < 2:
            raise BuildError(f"cannot split two-token phrase {text!r}")
        rows = []
        for split in range(1, len(text)):
            left, right = text[:split], text[split:]
            novel = frozenset(part for part in (left, right) if part not in stock)
            if any(len(encode_text(tbl, part)) > max_novel_bytes for part in novel):
                continue
            rows.append((left, right, novel))
        if not rows:
            raise BuildError(
                f"no two-token split fits stock storage for {text!r} (max {max_novel_bytes} bytes)"
            )
        options[text] = rows

    chosen: dict[str, tuple[str, str, frozenset[str]]] = {}
    active: set[str] = set()
    for text in texts:
        row = min(
            options[text],
            key=lambda item: (
                sum(phrase_cost(part) for part in item[2] if part not in active),
                sum(1 for part in item[2] if part not in active),
                len(item[0]),
                item[0],
            ),
        )
        chosen[text] = row
        active.update(row[2])

    # Coordinate descent makes shared novel fragments deterministic and compact.
    for _ in range(32):
        changed = False
        for text in texts:
            other: set[str] = set()
            for other_text, item in chosen.items():
                if other_text != text:
                    other.update(item[2])
            row = min(
                options[text],
                key=lambda item: (
                    sum(phrase_cost(part) for part in other | set(item[2])),
                    len(other | set(item[2])),
                    len(item[0]),
                    item[0],
                ),
            )
            if row != chosen[text]:
                chosen[text] = row
                changed = True
        if not changed:
            break

    novel: set[str] = set()
    result: dict[str, tuple[str, str]] = {}
    for text, (left, right, needed) in chosen.items():
        result[text] = (left, right)
        novel.update(needed)
    return result, novel


def safe_unreachable_slots(parent: bytes, dictionary: Any) -> list[dict[str, Any]]:
    wanted = {
        index
        for index in range(min(dictionary.stock_count, 0xF00))
        if dict_token_safe_in_zstring(index)
    }
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(dictionary, wanted=wanted, ext3_aware=True)
    preliminary = [
        index for index in sorted(wanted) if not external.get(index) and not nested.get(index)
    ]
    raw_hits = _raw_pair_hits(parent, preliminary)
    rows: list[dict[str, Any]] = []
    for index in preliminary:
        if raw_hits.get(index):
            continue
        proof = stock_storage_proof(dictionary, index)
        if not proof["ok"]:
            continue
        rows.append(
            {
                "index": index,
                "old_len": int(proof["old_len"]),
                "entry_abs": int(proof["entry_abs"]),
                "ptr": str(proof["ptr"]),
                "proof": proof,
            }
        )
    rows.sort(key=lambda row: (-int(row["old_len"]), int(row["index"])))
    return rows


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing/wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    risks = scan_risks(parent, original, tbl, dictionary)
    if len(risks) != EXPECTED_RISK_TOTAL:
        raise BuildError(f"risk population drifted: {len(risks)} != {EXPECTED_RISK_TOTAL}")

    exact = [row for row in risks if row["original_kinds"] == ["dict", "dict"]]
    mixed = [row for row in risks if row["original_kinds"] != ["dict", "dict"]]
    if len(exact) != EXPECTED_EXACT_DICT2 or len(mixed) != EXPECTED_MIXED:
        raise BuildError(f"risk class drifted exact={len(exact)} mixed={len(mixed)}")

    texts = sorted({str(row["text"]) for row in exact})
    if len(texts) != EXPECTED_UNIQUE_DICT2_TEXTS:
        raise BuildError(f"unique exact text population drifted: {len(texts)}")
    stock = stock_text_map(dictionary, tbl)
    safe_pool = safe_unreachable_slots(parent, dictionary)
    if not safe_pool:
        raise BuildError("no safe unreachable stock storage remains")
    max_novel_bytes = max(int(row["old_len"]) for row in safe_pool)
    plans, novel = plan_two_token_texts(
        texts, stock, tbl, max_novel_bytes=max_novel_bytes
    )
    if len(novel) != EXPECTED_NOVEL_FRAGMENTS:
        raise BuildError(f"novel fragment population drifted: {len(novel)}")

    # Pick currently unreachable, raw-pair-clean, unique-storage stock slots.
    # In-place phrase rewrite avoids pointer-table changes and the exhausted spill tail.
    existing_plan_indices: set[int] = set()
    for left, right in plans.values():
        for part in (left, right):
            if part in stock:
                existing_plan_indices.add(stock[part][0])
    available = [
        row for row in safe_pool
        if int(row["index"]) not in existing_plan_indices
    ]

    fragment_payloads = {fragment: encode_text(tbl, fragment) for fragment in novel}
    assigned: dict[str, dict[str, Any]] = {}
    used_slots: set[int] = set()
    for fragment in sorted(novel, key=lambda text: (-len(fragment_payloads[text]), text)):
        encoded = fragment_payloads[fragment]
        selected = next(
            (
                row for row in available
                if int(row["index"]) not in used_slots and int(row["old_len"]) >= len(encoded)
            ),
            None,
        )
        if selected is None:
            raise BuildError(f"no safe unreachable slot can hold {fragment!r} ({len(encoded)} bytes)")
        used_slots.add(int(selected["index"]))
        assigned[fragment] = selected

    candidate = bytearray(parent)
    allowed_extents: list[tuple[int, int]] = []
    slot_rows: list[dict[str, Any]] = []
    for fragment, row in sorted(assigned.items(), key=lambda item: int(item[1]["index"])):
        encoded = fragment_payloads[fragment]
        start = int(row["entry_abs"])
        old_len = int(row["old_len"])
        candidate[start : start + len(encoded)] = encoded
        candidate[start + len(encoded)] = 0
        allowed_extents.append((start, start + old_len + 1))
        slot_rows.append(
            {
                "index": f"{int(row['index']):04X}",
                "fragment": fragment,
                "encoded_hex": encoded.hex().upper(),
                "encoded_len": len(encoded),
                "old_len": old_len,
                "entry_abs": start,
                "old_ptr": row["ptr"],
            }
        )

    # Rebuild dictionary view after phrase replacement, then write each protected
    # predecessor as exactly two ordinary native dictionary tokens.
    interim_dictionary = make_dictionary_ext3(bytes(candidate), ext_meta, ext3_meta)
    sb = stock_base(parent)
    target_rows: list[dict[str, Any]] = []
    expected_selected_occurrences: dict[int, list[int]] = defaultdict(list)

    def index_for_part(part: str) -> int:
        if part in assigned:
            return int(assigned[part]["index"])
        values = stock.get(part)
        if not values:
            raise BuildError(f"planned stock fragment disappeared: {part!r}")
        return int(values[0])

    for row in sorted(exact, key=lambda item: int(item["logical"])):
        logical = int(row["logical"])
        text = str(row["text"])
        left, right = plans[text]
        left_index = index_for_part(left)
        right_index = index_for_part(right)
        body = token_from_dict_index(left_index) + token_from_dict_index(right_index)
        if len(body) != 4 or 0 in body:
            raise BuildError(f"unsafe native body at {logical:06X}: {body.hex()}")
        before = bytes(row["payload"])
        after = PREFIX + body
        if len(after) != len(before):
            raise BuildError(f"payload extent changed at {logical:06X}")
        candidate[sb + logical : sb + logical + len(after)] = after
        allowed_extents.append((sb + logical, sb + logical + len(after)))
        if left_index in used_slots:
            expected_selected_occurrences[left_index].append(logical + 3)
        if right_index in used_slots:
            expected_selected_occurrences[right_index].append(logical + 5)
        target_rows.append(
            {
                "abs": f"{logical:06X}",
                "text": text,
                "before_hex": before.hex().upper(),
                "after_hex": after.hex().upper(),
                "original_hex": bytes(row["original_payload"]).hex().upper(),
                "parts": [left, right],
                "tokens": [f"{left_index:04X}", f"{right_index:04X}"],
                "terminator": f"{int(row['term']):06X}",
                "double_nul": [f"{int(row['term']):06X}", f"{int(row['term']) + 1:06X}"],
                "next_18": f"{int(row['second']):06X}",
                "bare_continuation": f"{int(row['third']):06X}",
            }
        )

    # Mixed-unit relatives are deliberately byte-exact to the parent.
    mixed_rows = [
        {
            "abs": f"{int(row['logical']):06X}",
            "text": row["text"],
            "current_hex": bytes(row["payload"]).hex().upper(),
            "original_hex": bytes(row["original_payload"]).hex().upper(),
            "original_kinds": row["original_kinds"],
            "status": "observe_only_unproven_mixed_native_grammar",
        }
        for row in sorted(mixed, key=lambda item: int(item["logical"]))
    ]

    # Pin all structural boundaries and all mixed records to the parent.
    for row in risks:
        term = int(row["term"])
        second = int(row["second"])
        second_term = int(row["second_term"])
        third = int(row["third"])
        for logical in (term, term + 1, second, second_term, third):
            if candidate[sb + logical] != parent[sb + logical]:
                raise BuildError(f"page boundary drift at {logical:06X}")
        if row in mixed:
            logical = int(row["logical"])
            payload = bytes(row["payload"])
            if bytes(candidate[sb + logical : sb + logical + len(payload)]) != payload:
                raise BuildError(f"mixed observe-only record changed at {logical:06X}")

    checksum = update_ws_checksum(candidate)
    allowed_extents.append((len(candidate) - 2, len(candidate)))
    candidate_bytes = bytes(candidate)
    final_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    # Phrase and target verification.  Raw-byte equality is required in addition
    # to static text expansion because EC8D maps to an empty static glyph: a
    # marker-less Korean phrase can otherwise pass expand() yet corrupt runtime
    # glyph selection.
    for fragment, row in assigned.items():
        index = int(row["index"])
        expected_raw = encode_text(tbl, fragment)
        actual_raw = bytes(final_dictionary.raw_entry(index))
        if actual_raw != expected_raw:
            raise BuildError(
                f"fragment raw marker encoding mismatch {index:04X}: "
                f"{actual_raw.hex()} != {expected_raw.hex()}"
            )
        if final_dictionary.expand_index(index, tbl) != fragment:
            raise BuildError(f"fragment slot render mismatch {index:04X}: {fragment!r}")
    for target in target_rows:
        logical = int(target["abs"], 16)
        payload, term = read_record(candidate_bytes, logical)
        if term != int(target["terminator"], 16):
            raise BuildError(f"terminator moved at {logical:06X}")
        if payload[:3] != PREFIX or len(payload) != 7:
            raise BuildError(f"candidate grammar drift at {logical:06X}")
        rendered = final_dictionary.expand(payload[3:], tbl)
        if rendered != target["text"]:
            raise BuildError(f"render mismatch at {logical:06X}: {rendered!r}")

    # Re-prove selected slot reference exactness on the finished candidate.
    selected = set(used_slots)
    candidate_external = external_occurrence_map(
        candidate_bytes, ext3_aware=True, wanted=selected
    )
    candidate_nested = nested_occurrence_map(
        final_dictionary, wanted=selected, ext3_aware=True
    )
    candidate_raw = _raw_pair_hits(candidate_bytes, sorted(selected))
    reference_failures: list[dict[str, Any]] = []
    for index in sorted(selected):
        expected = sorted(expected_selected_occurrences.get(index, []))
        external_positions = sorted(
            int(str(item["token_abs"]), 16) for item in candidate_external.get(index, [])
        )
        raw_positions = sorted(
            int(str(item["token_abs"]), 16) for item in candidate_raw.get(index, [])
        )
        if external_positions != expected or raw_positions != expected or candidate_nested.get(index):
            reference_failures.append(
                {
                    "index": f"{index:04X}",
                    "expected": [f"{value:06X}" for value in expected],
                    "external": [f"{value:06X}" for value in external_positions],
                    "raw": [f"{value:06X}" for value in raw_positions],
                    "nested": candidate_nested.get(index, []),
                }
            )
    if reference_failures:
        raise BuildError(f"selected slot reference proof failed: {reference_failures[:3]}")

    # Candidate recurrence scan: exact-dict2 family must be gone; mixed family
    # remains intentionally visible for a separate proof before any mutation.
    final_risks = scan_risks(candidate_bytes, original, tbl, final_dictionary)
    exact_residual = [row for row in final_risks if row["original_kinds"] == ["dict", "dict"]]
    mixed_residual = [row for row in final_risks if row["original_kinds"] != ["dict", "dict"]]
    if exact_residual or len(mixed_residual) != EXPECTED_MIXED:
        raise BuildError(
            f"recurrence residual drift exact={len(exact_residual)} mixed={len(mixed_residual)}"
        )

    runs = diff_runs(parent, candidate_bytes)
    outside = [run for run in runs if not covered(run, allowed_extents)]
    if outside:
        raise BuildError(f"diff outside accounted extents: {outside[:8]}")

    OUT.write_bytes(candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)

    garrod = next(row for row in target_rows if row["abs"] == "61E234")
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_page_boundary_guard_candidate.py",
        "status": "runtime_candidate_main_unchanged",
        "parent": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": sha(parent)},
        "candidate": {
            "path": "out/patch/scenario_page_boundary_guard_candidate.wsc",
            "sha256": sha(candidate_bytes),
            "size": len(candidate_bytes),
            "checksum": f"{checksum:04X}",
        },
        "saveram": {
            "path": "sram/scenario_page_boundary_guard_candidate.sav",
            "sha256": sha(OUT_SAVE.read_bytes()),
            "size": OUT_SAVE.stat().st_size,
        },
        "risk_inventory": {
            "current_shape_total": len(risks),
            "original_exact_dict2_repaired": len(exact),
            "original_mixed_observe_only": len(mixed),
            "unique_exact_dict2_texts": len(texts),
            "candidate_exact_dict2_residual": len(exact_residual),
            "candidate_mixed_residual": len(mixed_residual),
        },
        "allocation": {
            "novel_fragments": len(novel),
            "selected_unreachable_slots": len(used_slots),
            "available_unreachable_slots": len(available),
            "fragment_storage_bytes_including_nuls": sum(
                len(fragment_payloads[fragment]) + 1 for fragment in novel
            ),
            "pointer_table_changes": 0,
            "slots": slot_rows,
        },
        "garrod_61E234": garrod,
        "targets": target_rows,
        "mixed_observe_only": mixed_rows,
        "guards": {
            "target_payload_size_preserved": True,
            "target_terminators_preserved": True,
            "double_nul_and_following_controls_preserved": True,
            "selected_slots_parent_external_zero": True,
            "selected_slots_parent_nested_zero": True,
            "selected_slots_parent_raw_pair_zero": True,
            "selected_slots_unique_storage": True,
            "selected_slots_candidate_reference_exact": True,
            "exact_dict2_risky_ext3_residual_zero": True,
            "mixed_grammar_not_auto_mutated": True,
            "diff_outside_accounted_extents": len(outside),
        },
        "whole_rom_diff": {
            "runs": len(runs),
            "bytes": sum(end - start for start, end in runs),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    print(json.dumps(report["risk_inventory"], ensure_ascii=False, indent=2))
    print(json.dumps(report["allocation"], ensure_ascii=False, indent=2)[:1600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
