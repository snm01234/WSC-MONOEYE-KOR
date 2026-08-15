#!/usr/bin/env python3
"""Build the 2026-08-02 UI/menu/dialogue follow-up candidate.

The current main TIP is read-only.  Fixed-size records use the shortest proven
portal that exactly fits their body: retired 2-byte stock tokens, E5 19 compact3
for 3-byte bodies, and E5 18 ext3 for bodies of four bytes or more.  Record
terminators and any measured dialogue prefix remain at their original offsets.
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
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_p2_stock_spill_candidate import _stock_phrase_cursor
from expand_dictionary import write_dictionary_slots_spill
from extract_script import split_prefix_body
from hangul_marker import marker_code
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    is_ff_page_index,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    COMPACT3_INDEX_BASE,
    COMPACT3_INDEX_END,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_compact3_index,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/ui_menu_dialogue_followup_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ui_menu_dialogue_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/ui_menu_dialogue_followup_candidate.sav"
OUT_REPORT = ROOT / "out/patch/ui_menu_dialogue_followup_report.json"
EXPECTED_MAIN_SHA256 = "1161d11c5286d353f7bc9db1ba879284641c5ea3ed8c8101383761f7b97ed77a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(text: str, tbl: Tbl) -> tuple[str, bytes]:
    normalized = normalize_ko_text(text)
    payload = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or b"\x00" in payload:
        raise BuildError(f"cannot encode target text: {text!r}")
    return normalized, payload


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def diff_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(before):
        if before[cursor] == after[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < len(before) and before[cursor] != after[cursor]:
            cursor += 1
        rows.append(
            {
                "start": f"{start:06X}",
                "end": f"{cursor:06X}",
                "length": cursor - start,
                "before_hex": before[start : min(cursor, start + 16)].hex().upper(),
                "after_hex": after[start : min(cursor, start + 16)].hex().upper(),
            }
        )
    return rows


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(start, end) for start, end in out]


def allowed(offset: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in intervals)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("main SaveRAM size drifted")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("parent_sha256") != EXPECTED_MAIN_SHA256:
        raise BuildError("spec parent identity drifted")
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 runtime is not installed")

    d_original = Dictionary(original)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    shared_payload: dict[int, bytes] = {}
    shared_rows: list[dict[str, Any]] = []
    shared_indices: set[int] = set()
    for row in spec.get("shared_dictionary", []):
        index = int(row["index"], 16)
        before = d_parent.expand_index(index, tbl)
        if strip_pad(before) != strip_pad(str(row["before"])):
            raise BuildError(
                f"shared slot {index:04X} drifted: {before!r} != {row['before']!r}"
            )
        normalized, payload = encode(str(row["ko"]), tbl)
        shared_payload[index] = payload
        shared_indices.add(index)
        shared_rows.append(
            {
                **row,
                "index": f"{index:04X}",
                "before_hex": bytes(d_parent.raw_entry(index)).hex().upper(),
                "ko_normalized": normalized,
                "encoded_hex": payload.hex().upper(),
            }
        )

    prepared: list[dict[str, Any]] = []
    seen_addresses: set[int] = set()
    for source in spec.get("records", []):
        logical = int(source["abs"], 16)
        if logical in seen_addresses:
            raise BuildError(f"duplicate target address {logical:06X}")
        seen_addresses.add(logical)
        got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        got_original = read_encoded_z_safe(original, stock_base(original) + logical, max_len=256)
        if got is None or got_original is None:
            raise BuildError(f"unreadable target record {logical:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        original_payload = bytes(got_original[0])
        preserve_prefix = bool(source.get("preserve_dialogue_prefix"))
        if preserve_prefix:
            prefix, body, kind = split_prefix_body(payload)
            if kind != "dialogue":
                raise BuildError(f"target {logical:06X} is not a dialogue record")
        else:
            prefix, body, kind = b"", payload, "direct"
        if len(body) < 2:
            raise BuildError(f"target body is too short at {logical:06X}: {len(body)}")
        normalized, encoded = encode(str(source["ko"]), tbl)
        if len(body) == 2:
            strategy = "retired_stock"
        elif len(body) == 3:
            strategy = "compact3"
        else:
            strategy = "ext3"
        try:
            current_render = d_parent.expand(body, tbl)
        except Exception as exc:
            raise BuildError(f"cannot decode current body {logical:06X}: {exc}") from exc
        prepared.append(
            {
                **source,
                "logical": logical,
                "payload_len": len(payload),
                "body_len": len(body),
                "prefix": prefix,
                "prefix_hex": prefix.hex().upper(),
                "terminator": terminator,
                "original_payload_hex": original_payload.hex().upper(),
                "before_payload_hex": payload.hex().upper(),
                "before": current_render,
                "kind": kind,
                "strategy": strategy,
                "ko_normalized": normalized,
                "encoded": encoded,
            }
        )

    # Candidate-bound retired stock slots: no current/nested/raw consumer and
    # byte-identical to the original dictionary entry.
    wanted = {
        index
        for index in range(min(d_original.stock_count, 0xF00))
        if dict_token_safe_in_zstring(index)
        and not is_ff_page_index(index)
        and index not in shared_indices
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
            original_phrase = bytes(d_original.raw_entry(index))
            parent_phrase = bytes(d_parent.raw_entry(index))
        except Exception:
            continue
        if d_original.ptrs[index] != d_parent.ptrs[index] or original_phrase != parent_phrase:
            continue
        preliminary.append(
            {
                "index": index,
                "old_pointer": d_parent.ptrs[index],
                "old_payload": parent_phrase,
                "historical": historical,
            }
        )
    raw_hits = _raw_pair_hits(parent, [row["index"] for row in preliminary])
    strong = [row for row in preliminary if not raw_hits.get(row["index"])]
    strong.sort(key=lambda row: (len(row["historical"]), row["index"]))

    short_unique: dict[bytes, dict[str, Any]] = {}
    for row in prepared:
        if row["strategy"] == "retired_stock":
            short_unique.setdefault(row["encoded"], row)
    if len(strong) < len(short_unique):
        raise BuildError(
            f"need {len(short_unique)} retired stock slots, found {len(strong)}"
        )
    retired_payload: dict[int, bytes] = {}
    retired_proofs: list[dict[str, Any]] = []
    retired_history: dict[int, set[int]] = {}
    encoded_to_stock: dict[bytes, int] = {}
    for evidence, (phrase, sample) in zip(
        strong, sorted(short_unique.items(), key=lambda item: item[1]["logical"])
    ):
        index = int(evidence["index"])
        encoded_to_stock[phrase] = index
        retired_payload[index] = phrase
        def historical_abs(value: Any) -> int:
            if isinstance(value, dict):
                value = value.get("record_abs", value.get("abs"))
            if isinstance(value, str):
                return int(value, 16)
            return int(value)

        retired_history[index] = {
            historical_abs(value) for value in evidence["historical"]
        }
        retired_proofs.append(
            {
                "index": f"{index:04X}",
                "old_pointer": f"{int(evidence['old_pointer']):04X}",
                "old_payload_hex": bytes(evidence["old_payload"]).hex().upper(),
                "historical_external_occurrences": evidence["historical"],
                "current_external_count": 0,
                "current_nested_count": 0,
                "current_raw_pair_hits": 0,
                "ko": sample["ko_normalized"],
            }
        )
    for row in prepared:
        if row["strategy"] == "retired_stock":
            row["slot"] = encoded_to_stock[row["encoded"]]
    # A 3-byte field may safely reuse a phrase that already needs a retired
    # 2-byte token elsewhere.  This consumes the original one-byte padding and
    # saves the scarce C0xx compact window for fields (notably 범용) that must
    # not render a trailing blank.
    for row in prepared:
        if row["strategy"] == "compact3" and row["encoded"] in encoded_to_stock:
            row["strategy"] = "retired_stock_pad"
            row["slot"] = encoded_to_stock[row["encoded"]]

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    free_all = sorted(int(index) for index in inventory.ext3_free)
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}

    compact_unique: dict[bytes, dict[str, Any]] = {}
    ext3_unique: dict[bytes, dict[str, Any]] = {}
    for row in prepared:
        if row["strategy"] == "compact3":
            compact_unique.setdefault(row["encoded"], row)
        elif row["strategy"] == "ext3":
            ext3_unique.setdefault(row["encoded"], row)

    # C0xx is an intentionally compact-addressable window inside bank 1C.
    # Its pointer entries are occupied by historical phrases, so they do not
    # appear in list_free_ext3_indices.  Reuse is nevertheless safe when the
    # Original+Working reference union proves both external and nested use are
    # absent; the guarded writer then appends a new phrase and retargets only
    # that dead pointer entry.
    compact_free = [
        index
        for index in range(COMPACT3_INDEX_BASE + 1, COMPACT3_INDEX_END + 1)
        if union.is_true_free(index)
    ]
    if len(compact_free) < len(compact_unique):
        raise BuildError(
            f"need {len(compact_unique)} compact3 slots, found {len(compact_free)}"
        )
    slot_payload: dict[int, bytes] = {}
    encoded_to_compact: dict[bytes, int] = {}
    for index, (phrase, _sample) in zip(
        compact_free, sorted(compact_unique.items(), key=lambda item: item[1]["logical"])
    ):
        encoded_to_compact[phrase] = index
        slot_payload[index] = phrase
        bank = bank_local_for_index(index)[0] - EXP3_SEG0
        room[bank] -= len(phrase) + 1
        if room[bank] < 0:
            raise BuildError(f"compact3 phrase room exhausted in bank {bank:02X}")
    for row in prepared:
        if row["strategy"] == "compact3":
            row["slot"] = encoded_to_compact[row["encoded"]]

    compact_reserved = set(range(COMPACT3_INDEX_BASE, COMPACT3_INDEX_END + 1))
    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in free_all:
        if index in compact_reserved or index in slot_payload:
            continue
        segment, _local = bank_local_for_index(index)
        free_by_bank[segment - EXP3_SEG0].append(index)
    for values in free_by_bank.values():
        values.sort()

    encoded_to_ext3: dict[bytes, int] = {}
    for phrase, sample in sorted(ext3_unique.items(), key=lambda item: item[1]["logical"]):
        need = len(phrase) + 1
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
        encoded_to_ext3[phrase] = index
        slot_payload[index] = phrase
    for row in prepared:
        if row["strategy"] == "ext3":
            row["slot"] = encoded_to_ext3[row["encoded"]]

    candidate = bytearray(parent)
    stock_phrase_before = _stock_phrase_cursor(parent)
    stock_selected = {**shared_payload, **retired_payload}
    _ptrs, stock_phrase_after = write_dictionary_slots_spill(
        candidate,
        stock_selected,
        allow_aux_consumers=True,
    )
    ext3_write, ext3_guard = write_ext3_slots_guarded(
        candidate,
        slot_payload,
        union=union,
        num_banks=num_banks,
        justification="2026-08-02 confirmed UI/menu/dialogue follow-up",
    )
    if not ext3_guard.ok:
        raise BuildError(f"ext3 slot guard failed: {ext3_guard.as_dict()}")

    applied: list[dict[str, Any]] = []
    expected_consumers: dict[int, set[int]] = defaultdict(set)
    for row in prepared:
        body_len = int(row["body_len"])
        if row["strategy"] in ("retired_stock", "retired_stock_pad"):
            token = token_from_dict_index(int(row["slot"]))
            expected_len = 2 if row["strategy"] == "retired_stock" else 3
            if len(token) != 2 or body_len != expected_len:
                raise BuildError(f"stock token length mismatch at {row['abs']}")
            new_body = token + (b"\x01" if row["strategy"] == "retired_stock_pad" else b"")
            slot_text = f"{int(row['slot']):04X}"
        elif row["strategy"] == "compact3":
            token = token_from_compact3_index(int(row["slot"]))
            if len(token) != 3 or body_len != 3:
                raise BuildError(f"compact3 token length mismatch at {row['abs']}")
            new_body = token
            slot_text = f"{int(row['slot']):04X}"
        else:
            token = token_from_ext3_index(int(row["slot"]), num_banks=num_banks)
            if len(token) != 4 or body_len < 4:
                raise BuildError(f"ext3 token length mismatch at {row['abs']}")
            new_body = token + b"\x01" * (body_len - 4)
            slot_text = f"{int(row['slot']):05X}"
        start = sb + int(row["logical"]) + len(row["prefix"])
        candidate[start : start + body_len] = new_body
        if candidate[int(row["terminator"])] != 0:
            raise BuildError(f"terminator moved at {row['abs']}")
        expected_consumers[int(row["slot"])].add(int(row["logical"]))
        applied.append(
            {
                "abs": row["abs"],
                "category": row["category"],
                "before": row["before"],
                "ko": row["ko_normalized"],
                "strategy": row["strategy"],
                "slot": slot_text,
                "payload_len": row["payload_len"],
                "body_len": body_len,
                "prefix_hex": row["prefix_hex"],
                "before_payload_hex": row["before_payload_hex"],
                "new_body_hex": new_body.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    d_candidate = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    decode_failures: list[dict[str, Any]] = []
    for source, result in zip(prepared, applied):
        logical = int(source["logical"])
        got = read_encoded_z_safe(candidate_bytes, sb + logical, max_len=256)
        if got is None:
            decode_failures.append({"abs": source["abs"], "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        prefix_len = len(source["prefix"])
        if len(payload) != int(source["payload_len"]) or terminator != int(source["terminator"]):
            decode_failures.append({"abs": source["abs"], "reason": "boundary"})
            continue
        if payload[:prefix_len] != source["prefix"]:
            decode_failures.append({"abs": source["abs"], "reason": "prefix"})
            continue
        rendered = d_candidate.expand(payload[prefix_len:], tbl)
        result["rendered"] = rendered
        result["ok"] = strip_pad(rendered) == strip_pad(source["ko_normalized"])
        if not result["ok"]:
            decode_failures.append(
                {
                    "abs": source["abs"],
                    "reason": "render",
                    "expected": source["ko_normalized"],
                    "actual": rendered,
                }
            )
    if decode_failures:
        raise BuildError(f"candidate decode failures: {decode_failures[:8]}")

    shared_after: list[dict[str, Any]] = []
    for row in shared_rows:
        index = int(row["index"], 16)
        rendered = d_candidate.expand_index(index, tbl)
        ok = strip_pad(rendered) == strip_pad(str(row["ko_normalized"]))
        shared_after.append({**row, "rendered": rendered, "ok": ok})
        if not ok:
            raise BuildError(f"shared slot decode failed at {index:04X}")

    candidate_union = build_reference_union(
        original, candidate_bytes, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    consumer_checks: list[dict[str, Any]] = []
    for index, expected in sorted(expected_consumers.items()):
        actual = sorted({consumer.abs for consumer in candidate_union.consumers_for(index)})
        historical = retired_history.get(index, set())

        def accounted(value: int) -> bool:
            if value in expected or value in historical:
                return True
            # The canonical union records the enclosing zstring start.  Several
            # menu targets are body addresses after a 1-6 byte control prefix.
            return any(0 <= target - value <= 8 for target in expected)

        unexpected = [value for value in actual if not accounted(value)]
        missing = [
            value
            for value in sorted(expected)
            if value not in actual
            and not any(0 <= value - start <= 8 for start in actual)
        ]
        consumer_checks.append(
            {
                "index": f"{index:05X}" if index >= 0x1000 else f"{index:04X}",
                "expected": [f"{value:06X}" for value in sorted(expected)],
                "historical_original": [
                    f"{value:06X}" for value in sorted(historical)
                ],
                "actual": [f"{value:06X}" for value in actual],
                "unexpected": [f"{value:06X}" for value in unexpected],
                "missing_from_union_scope": [f"{value:06X}" for value in missing],
                "ok": not unexpected,
            }
        )
    if any(not row["ok"] for row in consumer_checks):
        failed = [row for row in consumer_checks if not row["ok"]]
        raise BuildError(
            "candidate slot consumer proof failed: "
            + json.dumps(failed[:12], ensure_ascii=False)
        )

    selected_stock = sorted(stock_selected)
    selected_ext3_segments = sorted({bank_local_for_index(index)[0] for index in slot_payload})
    intervals: list[tuple[int, int]] = []
    for row in prepared:
        start = sb + int(row["logical"]) + len(row["prefix"])
        intervals.append((start, start + int(row["body_len"])))
    dict_bank = sb + SEG_DICT * BANK_SIZE
    for index in selected_stock:
        pointer = dict_bank + DICT_PTR_START + index * 2
        intervals.append((pointer, pointer + 2))
    intervals.append((dict_bank + stock_phrase_before, dict_bank + stock_phrase_after))
    for segment in selected_ext3_segments:
        intervals.append((segment * BANK_SIZE, (segment + 1) * BANK_SIZE))
    intervals.append((len(parent) - 2, len(parent)))
    intervals = merged(intervals)

    runs = diff_runs(parent, candidate_bytes)
    unaccounted: list[int] = []
    for run in runs:
        start, end = int(run["start"], 16), int(run["end"], 16)
        for offset in range(start, end):
            if not allowed(offset, intervals):
                unaccounted.append(offset)
                if len(unaccounted) >= 50:
                    break
        if len(unaccounted) >= 50:
            break
    if unaccounted:
        raise BuildError(
            "candidate has unaccounted changed bytes: "
            + ", ".join(f"{value:06X}" for value in unaccounted[:20])
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ui_menu_dialogue_followup_candidate.py",
        "status": "candidate_static_verified",
        "ok": True,
        "main_tip_modified": False,
        "inputs": {
            "main": {
                "path": str(MAIN.relative_to(ROOT)).replace("\\", "/"),
                "size": len(parent),
                "sha256": sha256(parent),
            },
            "spec": str(SPEC.relative_to(ROOT)).replace("\\", "/"),
        },
        "counts": {
            "shared_dictionary": len(shared_after),
            "records": len(applied),
            "by_category": dict(
                sorted(
                    {
                        category: sum(1 for row in applied if row["category"] == category)
                        for category in {row["category"] for row in applied}
                    }.items()
                )
            ),
            "by_strategy": dict(
                sorted(
                    {
                        strategy: sum(1 for row in applied if row["strategy"] == strategy)
                        for strategy in {row["strategy"] for row in applied}
                    }.items()
                )
            ),
            "retired_stock_slots": len(retired_payload),
            "compact3_slots": len(compact_unique),
            "ext3_slots": len(ext3_unique),
            "deferred": len(spec.get("deferred", [])),
        },
        "shared_dictionary": shared_after,
        "retired_slot_proof": retired_proofs,
        "records": applied,
        "deferred": spec.get("deferred", []),
        "dictionary": {
            "stock_phrase_cursor_before": f"{stock_phrase_before:04X}",
            "stock_phrase_cursor_after": f"{stock_phrase_after:04X}",
            "ext3_write": ext3_write,
            "ext3_guard": ext3_guard.as_dict(),
            "selected_ext3_segments": [f"{segment:02X}" for segment in selected_ext3_segments],
        },
        "verification": {
            "decode_failures": decode_failures,
            "consumer_checks": consumer_checks,
            "diff_runs": len(runs),
            "diff_bytes": sum(int(run["length"]) for run in runs),
            "unaccounted_changed_bytes": len(unaccounted),
            "checksum": f"{checksum:04X}",
            "candidate_size": len(candidate_bytes),
            "candidate_sha256": sha256(candidate_bytes),
            "save_size": MAIN_SAVE.stat().st_size,
            "save_sha256": hashlib.sha256(MAIN_SAVE.read_bytes()).hexdigest(),
        },
        "diff_sample": runs[:80],
        "candidate_rom": {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "size": len(candidate_bytes),
            "sha256": sha256(candidate_bytes),
        },
        "candidate_save": {
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "size": MAIN_SAVE.stat().st_size,
            "sha256": hashlib.sha256(MAIN_SAVE.read_bytes()).hexdigest(),
        },
    }

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["verification"], ensure_ascii=False, indent=2))
    print("candidate:", OUT_ROM)
    print("report:", OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
