#!/usr/bin/env python3
"""Build a candidate fixing residual unit names and the unit/status UI cluster.

The candidate is size preserving and uses only already-proven mechanisms:

* the shared stock slot for ``ムサイ`` is corrected from ``무사아`` to ``무사이``;
* 2/3-byte records are detached to strong retired non-FF stock slots;
* records with at least four bytes use true-free ext3 slots;
* all record lengths and NUL terminators stay fixed.

The main TIP is never modified by this tool.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_p2_stock_spill_candidate import _stock_phrase_cursor
from expand_dictionary import _walk_zstring_range, write_dictionary_slots_spill
from hangul_marker import marker_code
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    is_ff_page_index,
    iter_token_refs_with_offsets,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index
from tbl_code_prefs import find_codes, flatten_codes, marker_codes, retag_with_original_codes

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
FOLLOWUP = ROOT / "data/ui_unit_followup_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
CANDIDATE = ROOT / "out/patch/ui_unit_name_followup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ui_unit_name_followup_candidate.sav"
ANALYSIS = ROOT / "out/patch/ui_unit_name_followup_analysis.json"
APPROVAL = ROOT / "out/patch/ui_unit_name_followup_approval.json"
REPORT = ROOT / "out/patch/ui_unit_name_followup_report.json"
EXPECTED_PARENT_SHA256 = "0f991fd7af76d2ec23ce322cb89dee9c15e4618ed7cd46bad41673f1a3c5af9b"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
SHARED_MUSAI_INDEX = 0x06C3


class BuildError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256_bytes(payload)}


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    atomic_write(path, (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise BuildError("ROM sizes differ")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(a, b)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(a)))
    return runs


def covered(run: tuple[int, int], extents: Iterable[tuple[int, int]]) -> bool:
    start, end = run
    cursor = start
    for lo, hi in sorted(extents):
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end


def payload_at(rom: bytes, logical: int, *, max_len: int = 256) -> bytes:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if not got:
        raise BuildError(f"record {logical:06X} is unreadable")
    return bytes(got[0])


def has_japanese(text: str) -> bool:
    return any(is_japanese_character(character) for character in text)


def load_catalog() -> dict[str, str]:
    catalog: dict[str, str] = {}
    for relative in (
        "data/unit_names_ko.json",
        "data/weapon_names_ko.json",
        "data/name75_terms_ko.json",
    ):
        document = json.loads((ROOT / relative).read_text(encoding="utf-8-sig"))
        for row in document.get("entries") or []:
            jp, ko = row.get("jp"), row.get("ko")
            if jp and ko:
                catalog.setdefault(str(jp), str(ko))
    base = json.loads((ROOT / "data/name75_base_ko.json").read_text(encoding="utf-8-sig"))
    for jp, ko in (base.get("bases") or {}).items():
        if jp and ko:
            catalog.setdefault(str(jp), str(ko))
    for jp, ko in (base.get("overrides") or {}).items():
        if jp and ko:
            catalog[str(jp)] = str(ko)
    catalog["ムサイ"] = "무사이"
    return catalog


def prepare_encoded(
    text: str,
    original_payload: bytes,
    *,
    dictionary: Dictionary,
    tbl: Tbl,
) -> tuple[str, bytes, list[dict[str, Any]]]:
    normalized = normalize_ko_text(text)
    flat = flatten_codes(original_payload, dictionary)
    tagged, code_notes = retag_with_original_codes(normalized, flat, tbl)
    encoded = try_encode_ko_text(
        tagged,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if encoded is None or b"\x00" in encoded:
        raise BuildError(f"cannot encode Korean: {text!r}")
    markers = marker_codes(tbl)
    before_markers = find_codes(flat, markers)
    after_markers = find_codes(encoded, markers)
    if before_markers != after_markers:
        raise BuildError(
            f"marker family drift for {text!r}: before={before_markers} after={after_markers}"
        )
    return normalized, encoded, code_notes


def discover_records(
    original: bytes,
    parent: bytes,
    *,
    d_original: Dictionary,
    d_parent: Dictionary,
    tbl: Tbl,
    catalog: Mapping[str, str],
    followup: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    targets: dict[int, dict[str, Any]] = {}

    for row in followup.get("ui_records") or []:
        logical = int(str(row["abs"]), 16)
        original_payload = payload_at(original, logical)
        parent_payload = payload_at(parent, logical)
        original_text = d_original.expand(original_payload, tbl)
        if original_text != row["jp"]:
            raise BuildError(
                f"UI {logical:06X} Original text drifted: {original_text!r} != {row['jp']!r}"
            )
        if parent_payload != original_payload:
            raise BuildError(f"UI {logical:06X} payload changed before this stage")
        targets[logical] = {
            "abs": f"{logical:06X}",
            "logical": logical,
            "region": "ui75_explicit",
            "jp": str(row["jp"]),
            "ko": str(row["ko"]),
            "payload": original_payload,
            "parent_render": d_parent.expand(parent_payload, tbl).rstrip("　 \t"),
        }

    scan_ranges = (
        (0x5C0000, 0x5C7900, "bank5c_unit_name_table"),
        (0x75B000, 0x75C000, "bank75_ui_name_table"),
    )
    for lo, hi, region in scan_ranges:
        for logical, original_payload, _kind in _walk_zstring_range(
            original, lo, hi, region=region, max_len=128
        ):
            jp = d_original.expand(original_payload, tbl)
            ko = catalog.get(jp)
            if not ko:
                continue
            parent_payload = payload_at(parent, logical)
            if parent_payload != original_payload:
                continue
            current = d_parent.expand(parent_payload, tbl).rstrip("　 \t")
            desired = normalize_ko_text(ko).rstrip("　 \t")
            needs_fix = has_japanese(current) or (jp == "ムサイ" and current != desired)
            if not needs_fix:
                continue
            if logical == 0x5C71E1 and jp == "ムサイ":
                # The shared slot correction fixes this record without a local rewrite.
                continue
            targets.setdefault(
                logical,
                {
                    "abs": f"{logical:06X}",
                    "logical": logical,
                    "region": region,
                    "jp": jp,
                    "ko": ko,
                    "payload": original_payload,
                    "parent_render": current,
                },
            )

    prepared: list[dict[str, Any]] = []
    for logical, row in sorted(targets.items()):
        payload = bytes(row["payload"])
        if len(payload) < 2:
            raise BuildError(f"target {logical:06X} has unsupported {len(payload)}-byte body")
        normalized, encoded, code_notes = prepare_encoded(
            str(row["ko"]), payload, dictionary=d_original, tbl=tbl
        )
        prepared.append(
            {
                **row,
                "ko_normalized": normalized,
                "encoded": encoded,
                "payload_len": len(payload),
                "code_prefs": code_notes,
                "strategy": "retired_stock" if len(payload) < 4 else "ext3",
            }
        )

    deferred = [dict(row) for row in followup.get("deferred") or []]
    return prepared, deferred


def build() -> dict[str, Any]:
    parent = PARENT.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256_bytes(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("parent TIP identity drifted")
    if not PARENT_SAVE.is_file() or PARENT_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("main 32 KiB SaveRAM is missing")

    followup = json.loads(FOLLOWUP.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 metadata is not installed")
    d_original = Dictionary(original)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    catalog = load_catalog()

    shared_spec = list(followup.get("shared_dictionary") or [])
    if len(shared_spec) != 1 or int(shared_spec[0]["index"], 16) != SHARED_MUSAI_INDEX:
        raise BuildError("shared Musai specification drifted")
    shared_row = shared_spec[0]
    if d_original.expand_index(SHARED_MUSAI_INDEX, tbl) != shared_row["jp"]:
        raise BuildError("Original Musai slot text drifted")
    if d_parent.expand_index(SHARED_MUSAI_INDEX, tbl) != shared_row["current"]:
        raise BuildError("parent Musai slot text drifted")
    shared_external = external_occurrence_map(
        parent, ext3_aware=True, wanted={SHARED_MUSAI_INDEX}
    )
    shared_nested = nested_occurrence_map(
        d_parent, wanted={SHARED_MUSAI_INDEX}, ext3_aware=True
    )
    if shared_nested.get(SHARED_MUSAI_INDEX):
        raise BuildError("Musai slot has a current nested dictionary parent")
    shared_ko, shared_encoded, _notes = prepare_encoded(
        str(shared_row["ko"]),
        bytes(d_original.raw_entry(SHARED_MUSAI_INDEX)),
        dictionary=d_original,
        tbl=tbl,
    )
    shared_slot_payload = {SHARED_MUSAI_INDEX: shared_encoded}
    shared_proof = {
        "index": f"{SHARED_MUSAI_INDEX:04X}",
        "original": str(shared_row["jp"]),
        "before": str(shared_row["current"]),
        "after": shared_ko,
        "old_pointer": f"{d_parent.ptrs[SHARED_MUSAI_INDEX]:04X}",
        "old_payload_hex": bytes(d_parent.raw_entry(SHARED_MUSAI_INDEX)).hex().upper(),
        "current_external_count": len(shared_external.get(SHARED_MUSAI_INDEX) or []),
        "current_external_occurrences": list(
            shared_external.get(SHARED_MUSAI_INDEX) or []
        ),
        "current_nested_count": 0,
    }

    prepared, deferred = discover_records(
        original,
        parent,
        d_original=d_original,
        d_parent=d_parent,
        tbl=tbl,
        catalog=catalog,
        followup=followup,
    )
    short_rows = [row for row in prepared if row["strategy"] == "retired_stock"]
    long_rows = [row for row in prepared if row["strategy"] == "ext3"]

    wanted = {
        index
        for index in range(min(d_original.stock_count, 0xF00))
        if dict_token_safe_in_zstring(index)
        and not is_ff_page_index(index)
        and index != SHARED_MUSAI_INDEX
    }
    original_external = external_occurrence_map(original, ext3_aware=False, wanted=wanted)
    parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    original_nested = nested_occurrence_map(d_original, wanted=wanted, ext3_aware=False)
    parent_nested = nested_occurrence_map(d_parent, wanted=wanted, ext3_aware=True)
    preliminary: list[dict[str, Any]] = []
    for index in sorted(wanted):
        if parent_external.get(index) or parent_nested.get(index) or original_nested.get(index):
            continue
        historical = list(original_external.get(index) or [])
        if not historical:
            continue
        try:
            original_payload = bytes(d_original.raw_entry(index))
            parent_payload = bytes(d_parent.raw_entry(index))
        except Exception:
            continue
        if not parent_payload:
            continue
        if d_original.ptrs[index] != d_parent.ptrs[index] or original_payload != parent_payload:
            continue
        preliminary.append(
            {
                "index": index,
                "old_pointer": d_parent.ptrs[index],
                "old_payload": parent_payload,
                "historical": historical,
            }
        )
    raw_hits = _raw_pair_hits(parent, [row["index"] for row in preliminary])
    strong = [row for row in preliminary if not raw_hits.get(row["index"])]
    strong.sort(key=lambda row: (len(row["historical"]), row["index"]))

    unique_short: dict[bytes, dict[str, Any]] = {}
    for row in short_rows:
        unique_short.setdefault(row["encoded"], row)
    if len(strong) < len(unique_short):
        raise BuildError(
            f"need {len(unique_short)} strong retired slots, found {len(strong)}"
        )
    retired_slot_payload: dict[int, bytes] = {}
    retired_proofs: list[dict[str, Any]] = []
    encoded_to_retired: dict[bytes, int] = {}
    for evidence, (encoded, sample) in zip(
        strong, sorted(unique_short.items(), key=lambda item: item[1]["logical"])
    ):
        index = int(evidence["index"])
        encoded_to_retired[encoded] = index
        retired_slot_payload[index] = encoded
        retired_proofs.append(
            {
                "index": f"{index:04X}",
                "old_pointer": f"{int(evidence['old_pointer']):04X}",
                "old_payload_hex": bytes(evidence["old_payload"]).hex().upper(),
                "historical_external_count": len(evidence["historical"]),
                "historical_external_occurrences": evidence["historical"],
                "current_external_count": 0,
                "original_nested_count": 0,
                "current_nested_count": 0,
                "current_raw_pair_hits": 0,
                "ko": sample["ko_normalized"],
            }
        )
    for row in short_rows:
        row["slot"] = encoded_to_retired[row["encoded"]]

    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in inventory.ext3_free:
        seg, _local = bank_local_for_index(index)
        free_by_bank[seg - EXP3_SEG0].append(index)
    for values in free_by_bank.values():
        values.sort()
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}

    unique_long: dict[bytes, dict[str, Any]] = {}
    for row in long_rows:
        unique_long.setdefault(row["encoded"], row)
    ext3_slot_payload: dict[int, bytes] = {}
    encoded_to_ext3: dict[bytes, int] = {}
    for encoded, sample in sorted(
        unique_long.items(), key=lambda item: item[1]["logical"]
    ):
        need = len(encoded) + 1
        chosen_bank = next(
            (
                bank
                for bank in sorted(room, key=lambda value: (-room[value], value))
                if room.get(bank, 0) >= need and free_by_bank.get(bank)
            ),
            None,
        )
        if chosen_bank is None:
            raise BuildError(f"no ext3 room for {sample['ko_normalized']!r}")
        index = free_by_bank[chosen_bank].pop(0)
        room[chosen_bank] -= need
        encoded_to_ext3[encoded] = index
        ext3_slot_payload[index] = encoded
    for row in long_rows:
        row["slot"] = encoded_to_ext3[row["encoded"]]

    scratch = bytearray(parent)
    stock_phrase_before = _stock_phrase_cursor(parent)
    selected_stock_payload = {**shared_slot_payload, **retired_slot_payload}
    _ptrs, stock_phrase_after = write_dictionary_slots_spill(
        scratch,
        selected_stock_payload,
        allow_aux_consumers=True,
    )
    ext3_write, ext3_guard = write_ext3_slots_guarded(
        scratch,
        ext3_slot_payload,
        union=union,
        num_banks=num_banks,
        justification="candidate-bound UI and unit-name follow-up localization",
    )

    sb = stock_base(scratch)
    record_results: list[dict[str, Any]] = []
    for row in prepared:
        if row["strategy"] == "retired_stock":
            token = token_from_dict_index(int(row["slot"]))
            new_payload = token + b"\x01" * (int(row["payload_len"]) - 2)
            slot_text = f"{int(row['slot']):04X}"
        else:
            token = token_from_ext3_index(int(row["slot"]), num_banks=num_banks)
            new_payload = token + b"\x01" * (int(row["payload_len"]) - 4)
            slot_text = f"{int(row['slot']):05X}"
        at = sb + int(row["logical"])
        scratch[at : at + len(new_payload)] = new_payload
        scratch[at + len(new_payload)] = 0
        record_results.append(
            {
                "abs": row["abs"],
                "region": row["region"],
                "jp": row["jp"],
                "before": row["parent_render"],
                "ko": row["ko_normalized"],
                "strategy": row["strategy"],
                "slot": slot_text,
                "payload_len": row["payload_len"],
                "new_payload_hex": new_payload.hex().upper(),
                "code_prefs": row["code_prefs"],
            }
        )

    checksum = update_ws_checksum(scratch)
    candidate = bytes(scratch)
    d_candidate = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    consumer_checks: list[dict[str, Any]] = []
    selected_token_occurrences = 0
    rewritten_logicals = {int(row["logical"]) for row in prepared}
    shared_consumer_logicals = {
        int(str(occurrence["record_abs"]), 16)
        for occurrence in shared_external.get(SHARED_MUSAI_INDEX) or []
    }
    detached_shared_consumers = sorted(shared_consumer_logicals & rewritten_logicals)
    consumer_addresses = sorted(shared_consumer_logicals - rewritten_logicals)
    for logical in consumer_addresses:
        parent_payload = payload_at(parent, logical)
        candidate_payload = payload_at(candidate, logical)
        if parent_payload != candidate_payload:
            failures.append(
                {"kind": "shared_consumer_payload_changed", "abs": f"{logical:06X}"}
            )
            continue
        cursor = 0
        before_pieces: list[str] = []
        after_pieces: list[str] = []
        selected_here = 0
        for index, length, offset in iter_token_refs_with_offsets(parent_payload):
            if offset > cursor:
                raw = parent_payload[cursor:offset]
                before_raw = d_parent.expand(raw, tbl)
                after_raw = d_candidate.expand(raw, tbl)
                if before_raw != after_raw:
                    failures.append(
                        {"kind": "non_token_render_drift", "abs": f"{logical:06X}", "offset": cursor}
                    )
                before_pieces.append(before_raw)
                after_pieces.append(after_raw)
            token = parent_payload[offset : offset + length]
            before_token = d_parent.expand(token, tbl)
            after_token = d_candidate.expand(token, tbl)
            if index == SHARED_MUSAI_INDEX:
                selected_token_occurrences += 1
                selected_here += 1
                if before_token != shared_row["current"] or after_token != shared_ko:
                    failures.append(
                        {
                            "kind": "Musai_substitution_mismatch",
                            "abs": f"{logical:06X}",
                            "before": before_token,
                            "after": after_token,
                        }
                    )
            elif before_token != after_token:
                failures.append(
                    {
                        "kind": "unselected_token_render_drift",
                        "abs": f"{logical:06X}",
                        "index": f"{index:05X}",
                    }
                )
            before_pieces.append(before_token)
            after_pieces.append(after_token)
            cursor = offset + length
        if cursor < len(parent_payload):
            raw = parent_payload[cursor:]
            before_raw = d_parent.expand(raw, tbl)
            after_raw = d_candidate.expand(raw, tbl)
            if before_raw != after_raw:
                failures.append(
                    {"kind": "tail_render_drift", "abs": f"{logical:06X}", "offset": cursor}
                )
            before_pieces.append(before_raw)
            after_pieces.append(after_raw)
        before_full = d_parent.expand(parent_payload, tbl)
        after_full = d_candidate.expand(candidate_payload, tbl)
        if before_full != "".join(before_pieces) or after_full != "".join(after_pieces):
            failures.append(
                {"kind": "consumer_piece_reassembly_mismatch", "abs": f"{logical:06X}"}
            )
        consumer_checks.append(
            {
                "abs": f"{logical:06X}",
                "before": before_full,
                "after": after_full,
                "selected_occurrences": selected_here,
                "payload_unchanged": True,
                "ok": True,
            }
        )

    if d_candidate.expand_index(SHARED_MUSAI_INDEX, tbl) != shared_ko:
        failures.append({"kind": "shared_slot", "index": "06C3"})
    for row in record_results:
        logical = int(row["abs"], 16)
        payload = payload_at(candidate, logical)
        got = d_candidate.expand(payload, tbl).rstrip("　 \t")
        want = str(row["ko"]).rstrip("　 \t")
        if got != want:
            failures.append(
                {"kind": "record", "abs": row["abs"], "want": want, "got": got}
            )
        if candidate[stock_base(candidate) + logical + int(row["payload_len"])] != 0:
            failures.append({"kind": "terminator", "abs": row["abs"]})
    if failures:
        raise BuildError(f"verification failed: {failures[:8]}")

    selected_stock_indices = set(selected_stock_payload)
    pointer_failures = [
        f"{index:04X}"
        for index in range(d_parent.stock_count)
        if index not in selected_stock_indices
        and d_parent.ptrs[index] != d_candidate.ptrs[index]
    ]
    if pointer_failures:
        raise BuildError(f"unselected stock pointers changed: {pointer_failures[:8]}")

    allowed: list[tuple[int, int]] = []
    stock_bank_file = stock_base(parent) + 0x5F0000
    for index in selected_stock_indices:
        start = stock_bank_file + DICT_PTR_START + index * 2
        allowed.append((start, start + 2))
    allowed.append((stock_bank_file + stock_phrase_before, stock_bank_file + stock_phrase_after))
    for row in record_results:
        start = stock_base(parent) + int(row["abs"], 16)
        allowed.append((start, start + int(row["payload_len"])))
    for index in ext3_slot_payload:
        seg, local = bank_local_for_index(index)
        allowed.append((seg * BANK_SIZE + local * 2, seg * BANK_SIZE + local * 2 + 2))
    for seg_text, end_cursor in (ext3_write.get("by_bank") or {}).items():
        seg = int(seg_text, 16)
        before_cursor = BANK_SIZE - int(inventory.ext3_bank_room[seg - EXP3_SEG0])
        allowed.append((seg * BANK_SIZE + before_cursor, seg * BANK_SIZE + int(end_cursor)))
    allowed.append((len(parent) - 2, len(parent)))

    runs = diff_runs(parent, candidate)
    unaccounted = [(start, end) for start, end in runs if not covered((start, end), allowed)]
    if unaccounted:
        raise BuildError(f"unaccounted diff runs: {unaccounted[:8]}")

    region_counts: dict[str, int] = defaultdict(int)
    for row in record_results:
        region_counts[str(row["region"])] += 1

    analysis = {
        "schema_version": 1,
        "generated_by": "tools/build_ui_unit_name_followup_candidate.py",
        "inputs": {
            "parent_tip": identity(PARENT, parent),
            "parent_save": identity(PARENT_SAVE),
            "original_rom": identity(ORIGINAL, original),
            "followup_catalog": identity(FOLLOWUP),
            "tbl": identity(TBL_PATH),
            "ext_meta": identity(EXT_META),
            "ext3_meta": identity(EXT3_META),
        },
        "counts": {
            "shared_dictionary_terms": 1,
            "shared_external_consumers": len(shared_consumer_logicals),
            "shared_consumers_payload_unchanged_checked": len(consumer_checks),
            "shared_consumers_locally_detached": len(detached_shared_consumers),
            "shared_token_occurrences": selected_token_occurrences,
            "direct_records": len(record_results),
            "short_records": len(short_rows),
            "long_records": len(long_rows),
            "short_unique_phrases": len(unique_short),
            "long_unique_phrases": len(unique_long),
            "strong_retired_slots_available": len(strong),
            "strong_retired_slots_used": len(retired_slot_payload),
            "deferred_records": len(deferred),
            "by_region": dict(sorted(region_counts.items())),
        },
        "shared_proof": shared_proof,
        "shared_consumer_checks": consumer_checks,
        "shared_consumers_locally_detached": [f"{logical:06X}" for logical in detached_shared_consumers],
        "retired_slot_proofs": retired_proofs,
        "ext3_inventory_before": inventory.as_dict(),
        "ext3_guard": ext3_guard.as_dict(),
        "deferred": deferred,
    }
    atomic_json(ANALYSIS, analysis)

    approval = {
        "schema_version": 1,
        "generated_by": "tools/build_ui_unit_name_followup_candidate.py",
        "ok": True,
        "mode": "ui_unit_name_followup",
        "parent_rom": identity(PARENT, parent),
        "candidate_rom": {
            "path": str(CANDIDATE.resolve()),
            "size": len(candidate),
            "sha256": sha256_bytes(candidate),
        },
        "followup_catalog": identity(FOLLOWUP),
        "approved_stock_slots": [f"{index:04X}" for index in sorted(selected_stock_indices)],
        "approved_ext3_slots": [f"{index:05X}" for index in sorted(ext3_slot_payload)],
        "approved_record_ranges": [
            {"abs": row["abs"], "length": row["payload_len"]}
            for row in record_results
        ],
        "proof": {
            "parent_sha_locked": True,
            "Musai_original_parent_before_after_exact": True,
            "Musai_current_nested_parents_zero": True,
            "Musai_non_detached_consumer_payloads_unchanged": True,
            "Musai_non_detached_consumer_token_substitutions_exact": True,
            "Musai_locally_detached_consumers_verified_as_direct_records": True,
            "retired_slots_current_external_zero": True,
            "retired_slots_current_nested_zero": True,
            "retired_slots_raw_pair_hits_zero": True,
            "retired_slots_original_parent_pointer_payload_equal": True,
            "ext3_slots_union_true_free": ext3_guard.ok,
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "marker_codes_preserved": True,
            "unselected_stock_pointers_unchanged": True,
            "target_render_exact": True,
            "unaccounted_diff_runs_zero": not unaccounted,
            "no_ff_page_write": True,
            "no_runtime_hook_write": True,
            "no_far_pointer_relocation": True,
        },
    }
    atomic_json(APPROVAL, approval)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ui_unit_name_followup_candidate.py",
        "status": "static_accepted_visual_check_recommended",
        "accepted_static": True,
        "published": False,
        "main_tip_modified": False,
        "parent_tip": identity(PARENT, parent),
        "candidate_rom": {
            "path": str(CANDIDATE.resolve()),
            "size": len(candidate),
            "sha256": sha256_bytes(candidate),
        },
        "candidate_save": {"path": str(CANDIDATE_SAVE.resolve()), "size": SAVE_SIZE},
        "analysis": identity(ANALYSIS),
        "approval": identity(APPROVAL),
        "targets": {
            "shared_dictionary": 1,
            "direct_records": len(record_results),
            "by_region": dict(sorted(region_counts.items())),
            "deferred": len(deferred),
        },
        "dictionary_changes": {
            "shared_stock_slots": 1,
            "retired_stock_slots": len(retired_slot_payload),
            "stock_phrase_cursor_before": f"{stock_phrase_before:04X}",
            "stock_phrase_cursor_after": f"{stock_phrase_after:04X}",
            "ext3_slots": len(ext3_slot_payload),
            "ext3_write": ext3_write,
        },
        "diff": {
            "bytes": sum(end - start for start, end in runs),
            "runs": len(runs),
            "unaccounted_runs": 0,
            "checksum": f"{checksum:04X}",
        },
        "shared_consumer_checks": {
            "external_records_total": len(shared_consumer_logicals),
            "payload_unchanged_records": len(consumer_checks),
            "locally_detached_records": len(detached_shared_consumers),
            "selected_token_occurrences": selected_token_occurrences,
            "failures": 0,
        },
        "records": record_results,
        "deferred": deferred,
        "runtime_note": (
            "Static render/structure proof is complete. Visually check the unit status screen "
            "and one unit encyclopedia/list page before or after promotion."
        ),
    }

    atomic_write(CANDIDATE, candidate)
    shutil.copy2(PARENT_SAVE, CANDIDATE_SAVE)
    report["candidate_save"] = identity(CANDIDATE_SAVE)
    atomic_json(REPORT, report)
    return report


def main() -> int:
    report = build()
    print(
        json.dumps(
            {
                "status": report["status"],
                "candidate": report["candidate_rom"],
                "save": report["candidate_save"],
                "targets": report["targets"],
                "dictionary_changes": report["dictionary_changes"],
                "diff": report["diff"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
