#!/usr/bin/env python3
"""Build the cumulative STAGE17t + global 20-cell + Radish terminology candidate.

Parent is the runtime-confirmed 최후의 승리자 follow-up candidate.  The builder
never modifies the parent.  All dialogue rewrites replace one existing four-byte
E5 18 portal with another four-byte portal and allocate phrases only in true-free
ordinary ext3 slots that are outside the active five-page runtime alias range.
Two same-length stock dictionary entries are rewritten in-place for the project
standard 라디쉬/라디쉬의.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from apply_name75_ko import ext3_bank_room  # noqa: E402
from build_dialogue_20cell_candidate import encode, ext3_index  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_stage16t_event_followup_candidate import (  # noqa: E402
    BuildError,
    identity,
    payload_at,
    sha,
    strip_pad,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from patch_3byte_dict_token import (  # noqa: E402
    INDEX_BASE,
    list_free_ext3_indices,
    token_from_ext3_index,
    write_ext3_dictionary_slots,
)

PATCH = ROOT / "out/patch"
PARENT = PATCH / "final_winner_stage_followup_candidate.wsc"
PARENT_TBL = PATCH / "final_winner_stage_followup_candidate.tbl"
PARENT_SAVE = ROOT / "sram/final_winner_stage_followup_candidate.sav"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
SPEC = ROOT / "data/global_dialogue_20cell_followup_ko.json"
OUT_ROM = PATCH / "stage17t_global_20cell_followup_candidate.wsc"
OUT_TBL = PATCH / "stage17t_global_20cell_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/stage17t_global_20cell_followup_candidate.sav"
REPORT = PATCH / "stage17t_global_20cell_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "be3dcab4ec68ae0cf165bde07409563ace28b7c6fbf83cec567ac0f5071aed3c"
EXPECTED_TBL_SHA = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
RUNTIME_ALIAS_PAGE_COUNT = 5
RUNTIME_ALIAS_LOCAL_START = 0x0600
RUNTIME_ALIAS_SEG0 = 0x21
EXPECTED_WIDTH_COUNT = 60
EXPECTED_SPILL_COUNT = 1
EXPECTED_RADISH_RECORD_COUNT = 10
EXPECTED_STOCK_REWRITE_COUNT = 2
EXPECTED_LOGICAL_DICTIONARY_TERM_REWRITE_COUNT = 5
EXPECTED_ALIAS_DICTIONARY_TERM_REWRITE_COUNT = 4


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def active_dictionary(
    rom: bytes | bytearray,
    ext_meta: dict[str, Any],
    ext3_meta: dict[str, Any],
) -> Dictionary:
    """Decode the currently live composite runtime with its proven 5-page alias."""
    base = make_dictionary(rom, ext_meta)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16 or str(ext3_meta.get("exp_seg0") or "11").upper() != "11":
        raise BuildError("ext3 metadata drifted")
    return Dictionary(
        rom,
        count=base.count,
        ext_ptr_off=base.ext_ptr_off,
        ext_seg=base.ext_seg,
        stock_count=base.stock_count,
        ext_in_expansion=base.ext_in_expansion,
        ext3_ptr_off=0,
        ext3_seg=0x11,
        ext3_banks=num_banks,
        ext3_alias_page_count=RUNTIME_ALIAS_PAGE_COUNT,
        ext3_alias_local_start=RUNTIME_ALIAS_LOCAL_START,
        ext3_alias_seg=RUNTIME_ALIAS_SEG0,
    )


def allocate_unique_phrases(
    parent: bytes,
    encoded_to_text: dict[bytes, str],
    *,
    num_banks: int,
) -> tuple[dict[int, bytes], dict[bytes, int]]:
    room = ext3_bank_room(parent, num_banks)
    used_tokens: set[bytes] = set()
    cursor = 0
    while True:
        hit = parent.find(b"\xE5\x18", cursor)
        if hit < 0:
            break
        if hit + 4 <= len(parent):
            used_tokens.add(parent[hit : hit + 4])
        cursor = hit + 2

    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in list_free_ext3_indices(parent, num_banks=num_banks):
        off = index - INDEX_BASE
        page, local = off >> 12, off & 0x0FFF
        if page < RUNTIME_ALIAS_PAGE_COUNT and local >= RUNTIME_ALIAS_LOCAL_START:
            continue
        token = token_from_ext3_index(index, num_banks=num_banks)
        if token in used_tokens:
            continue
        free_by_bank[page].append(index)
    for values in free_by_bank.values():
        values.sort()

    slot_payload: dict[int, bytes] = {}
    encoded_to_index: dict[bytes, int] = {}
    for encoded, text in sorted(encoded_to_text.items(), key=lambda item: (-len(item[0]), item[1])):
        need = len(encoded) + 1
        choices = [
            (room.get(page, 0) - need, page)
            for page in sorted(free_by_bank)
            if free_by_bank[page] and room.get(page, 0) >= need
        ]
        if not choices:
            raise BuildError(f"no runtime-safe ext3 room for {text!r} ({need} bytes)")
        _remain, page = min(choices)
        index = free_by_bank[page].pop(0)
        room[page] -= need
        slot_payload[index] = encoded
        encoded_to_index[encoded] = index
    return slot_payload, encoded_to_index


def main() -> int:
    parent = PARENT.read_bytes()
    tbl_bytes = PARENT_TBL.read_bytes()
    save = PARENT_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent candidate identity drifted: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("parent TBL identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"parent candidate SaveRAM size drifted: {len(save)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    width = dict(spec.get("width_rewrites") or {})
    spill = dict(spec.get("semantic_spill_rewrites") or {})
    radish = dict(spec.get("radish_record_rewrites") or {})
    stock = dict(spec.get("stock_dictionary_rewrites") or {})
    logical_terms = dict(spec.get("logical_dictionary_term_rewrites") or {})
    alias_terms = list(spec.get("alias_dictionary_term_rewrites") or [])
    if len(width) != EXPECTED_WIDTH_COUNT:
        raise BuildError(f"width rewrite count drifted: {len(width)}")
    if len(spill) != EXPECTED_SPILL_COUNT:
        raise BuildError(f"spill rewrite count drifted: {len(spill)}")
    if len(radish) != EXPECTED_RADISH_RECORD_COUNT:
        raise BuildError(f"Radish record count drifted: {len(radish)}")
    if len(stock) != EXPECTED_STOCK_REWRITE_COUNT:
        raise BuildError(f"stock dictionary rewrite count drifted: {len(stock)}")
    if len(logical_terms) != EXPECTED_LOGICAL_DICTIONARY_TERM_REWRITE_COUNT:
        raise BuildError(f"logical dictionary term rewrite count drifted: {len(logical_terms)}")
    if len(alias_terms) != EXPECTED_ALIAS_DICTIONARY_TERM_REWRITE_COUNT:
        raise BuildError(f"alias dictionary term rewrite count drifted: {len(alias_terms)}")

    target_maps = [("width", width), ("semantic_spill", spill), ("radish", radish)]
    desired_by_address: dict[str, str] = {}
    kind_by_address: dict[str, str] = {}
    for kind, mapping in target_maps:
        for address, text in mapping.items():
            address = address.upper()
            if address in desired_by_address and desired_by_address[address] != text:
                raise BuildError(f"conflicting target text at {address}")
            if len(text) > int(spec.get("cell_limit") or 20):
                raise BuildError(f"source spec over 20 cells at {address}: {text!r}")
            desired_by_address[address] = text
            kind_by_address[address] = kind

    tbl = Tbl.load(PARENT_TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = active_dictionary(parent, ext_meta, ext3_meta)

    prepared: list[dict[str, Any]] = []
    old_token_targets: defaultdict[bytes, list[dict[str, Any]]] = defaultdict(list)
    encoded_to_text: dict[bytes, str] = {}
    for address, desired in sorted(desired_by_address.items()):
        logical = int(address, 16)
        raw, term = payload_at(parent, logical, max_len=512)
        pos = raw.find(b"\xE5\x18")
        if pos not in (0, 1, 3) or raw.find(b"\xE5\x18", pos + 2) >= 0:
            raise BuildError(f"{address} is not a single direct E5 18 record: {raw.hex().upper()}")
        old_token = raw[pos : pos + 4]
        old_index = ext3_index(old_token)
        if old_index is None:
            raise BuildError(f"cannot decode old ext3 index at {address}")
        current = strip_pad(parent_dictionary.expand(raw[pos:], tbl))
        kind = kind_by_address[address]
        if kind == "width" and len(current) <= 20:
            raise BuildError(f"width target no longer exceeds 20 cells at {address}: {current!r}")
        if kind == "semantic_spill" and address == "627963" and "영혼의" not in current:
            raise BuildError(f"627963 spill signature drifted: {current!r}")
        if kind == "radish" and not any(term in current for term in ("래디시", "라디시", "라디슈", "래디슈")):
            raise BuildError(f"Radish source signature drifted at {address}: {current!r}")
        encoded = encode(desired, tbl)
        encoded_to_text.setdefault(encoded, desired)
        job = {
            "abs": address,
            "logical": logical,
            "kind": kind,
            "before_text": current,
            "ko": desired,
            "encoded": encoded,
            "old_token": old_token,
            "old_index": int(old_index),
            "token_pos": pos,
            "record_before": raw.hex().upper(),
            "terminator": term,
        }
        prepared.append(job)
        old_token_targets[old_token].append(job)

    for token, jobs in old_token_targets.items():
        occurrences = parent.count(token)
        if occurrences != len(jobs):
            raise BuildError(
                f"old token consumer drift {token.hex().upper()}: parent={occurrences}, targets={len(jobs)}"
            )
        if len({job["ko"] for job in jobs}) != 1:
            raise BuildError(f"shared old token has divergent target text: {token.hex().upper()}")

    slot_payload, encoded_to_index = allocate_unique_phrases(
        parent, encoded_to_text, num_banks=num_banks
    )
    for encoded, index in encoded_to_index.items():
        off = index - INDEX_BASE
        page, local = off >> 12, off & 0x0FFF
        if page < RUNTIME_ALIAS_PAGE_COUNT and local >= RUNTIME_ALIAS_LOCAL_START:
            raise BuildError(f"unsafe runtime alias allocation escaped filter: {index:05X}")

    candidate = bytearray(parent)
    write_info = write_ext3_dictionary_slots(candidate, slot_payload, num_banks=num_banks)
    if int(write_info.get("skipped_overflow") or 0) or int(write_info.get("written") or 0) != len(slot_payload):
        raise BuildError(f"ext3 write incomplete: {write_info}")

    allowed: list[tuple[int, int]] = []
    changes: list[dict[str, Any]] = []
    sb = stock_base(candidate)
    for job in prepared:
        index = encoded_to_index[job["encoded"]]
        new_token = token_from_ext3_index(index, num_banks=num_banks)
        logical = int(job["logical"])
        pos = int(job["token_pos"])
        before_raw, before_term = payload_at(parent, logical, max_len=512)
        file_pos = sb + logical + pos
        candidate[file_pos : file_pos + 4] = new_token
        after_raw, after_term = payload_at(candidate, logical, max_len=512)
        if before_term != after_term or len(before_raw) != len(after_raw):
            raise BuildError(f"record extent/terminator drift at {logical:06X}")
        if after_raw[:pos] != before_raw[:pos] or after_raw[pos + 4 :] != before_raw[pos + 4 :]:
            raise BuildError(f"non-token record bytes changed at {logical:06X}")
        allowed.append((file_pos, file_pos + 4))
        changes.append(
            {
                "abs": job["abs"],
                "kind": job["kind"],
                "before_text": job["before_text"],
                "after_text": job["ko"],
                "cells": len(job["ko"]),
                "token_pos": pos,
                "old_token": job["old_token"].hex().upper(),
                "old_index": f"{int(job['old_index']):05X}",
                "new_token": new_token.hex().upper(),
                "new_index": f"{index:05X}",
                "record_before": before_raw.hex().upper(),
                "record_after": after_raw.hex().upper(),
                "terminator": f"{after_term:06X}",
            }
        )

    stock_changes: list[dict[str, Any]] = []
    # Stock dictionary entries are same-length Korean replacements; no pointer
    # or table size changes are allowed.
    for index_hex, row in sorted(stock.items()):
        index = int(index_hex, 16)
        before_text = strip_pad(parent_dictionary.expand_index(index, tbl))
        if before_text != row["before"]:
            raise BuildError(f"stock dictionary source drift {index_hex}: {before_text!r}")
        raw_before = parent_dictionary.raw_entry(index)
        raw_after = encode(str(row["after"]), tbl)
        if len(raw_before) != len(raw_after):
            raise BuildError(f"stock dictionary length drift {index_hex}: {len(raw_before)} != {len(raw_after)}")
        abs_off = parent_dictionary.entry_abs(index)
        candidate[abs_off : abs_off + len(raw_after)] = raw_after
        allowed.append((abs_off, abs_off + len(raw_after)))
        stock_changes.append(
            {
                "index": index_hex.upper(),
                "before": row["before"],
                "after": row["after"],
                "entry_abs": f"{abs_off:07X}",
                "raw_before": raw_before.hex().upper(),
                "raw_after": raw_after.hex().upper(),
            }
        )

    logical_term_changes: list[dict[str, Any]] = []
    for index_hex, row in sorted(logical_terms.items()):
        index = int(index_hex, 16)
        before_text = strip_pad(parent_dictionary.expand_index(index, tbl))
        if before_text != row["before"]:
            raise BuildError(f"logical dictionary source drift {index_hex}: {before_text!r}")
        raw_before = parent_dictionary.raw_entry(index)
        raw_after = encode(str(row["after"]), tbl)
        if len(raw_before) != len(raw_after):
            raise BuildError(
                f"logical dictionary length drift {index_hex}: {len(raw_before)} != {len(raw_after)}"
            )
        abs_off = parent_dictionary.entry_abs(index)
        candidate[abs_off : abs_off + len(raw_after)] = raw_after
        allowed.append((abs_off, abs_off + len(raw_after)))
        logical_term_changes.append(
            {
                "index": index_hex.upper(),
                "before": row["before"],
                "after": row["after"],
                "entry_abs": f"{abs_off:07X}",
                "raw_before": raw_before.hex().upper(),
                "raw_after": raw_after.hex().upper(),
            }
        )

    alias_term_changes: list[dict[str, Any]] = []
    alias_allowed: list[tuple[int, int]] = []
    seen_alias_locations: set[tuple[int, int]] = set()
    for row in alias_terms:
        seg = int(str(row["segment"]), 16)
        local = int(str(row["local"]), 16)
        if not (0x21 <= seg <= 0x25 and 0 <= local < 0x0A00):
            raise BuildError(f"invalid runtime alias dictionary location {seg:02X}:{local:04X}")
        if (seg, local) in seen_alias_locations:
            raise BuildError(f"duplicate runtime alias dictionary location {seg:02X}:{local:04X}")
        seen_alias_locations.add((seg, local))
        base = seg * BANK_SIZE
        ptr = int.from_bytes(parent[base + local * 2 : base + local * 2 + 2], "little")
        if not (0x2000 < ptr < BANK_SIZE):
            raise BuildError(f"invalid runtime alias pointer {seg:02X}:{local:04X} -> {ptr:04X}")
        end = parent.find(b"\x00", base + ptr, base + BANK_SIZE)
        if end < 0:
            raise BuildError(f"unterminated runtime alias phrase {seg:02X}:{local:04X}")
        raw_before = parent[base + ptr : end]
        before_text = strip_pad(parent_dictionary.expand(raw_before, tbl))
        if before_text != row["before"]:
            raise BuildError(
                f"runtime alias phrase source drift {seg:02X}:{local:04X}: {before_text!r}"
            )
        raw_after = encode(str(row["after"]), tbl)
        if len(raw_before) != len(raw_after):
            raise BuildError(
                f"runtime alias phrase length drift {seg:02X}:{local:04X}: "
                f"{len(raw_before)} != {len(raw_after)}"
            )
        abs_off = base + ptr
        candidate[abs_off : abs_off + len(raw_after)] = raw_after
        allowed.append((abs_off, abs_off + len(raw_after)))
        alias_allowed.append((abs_off, abs_off + len(raw_after)))
        alias_term_changes.append(
            {
                "segment": f"{seg:02X}",
                "local": f"{local:04X}",
                "pointer": f"{ptr:04X}",
                "before": row["before"],
                "after": row["after"],
                "phrase_abs": f"{abs_off:07X}",
                "raw_before": raw_before.hex().upper(),
                "raw_after": raw_after.hex().upper(),
            }
        )

    final_dictionary = active_dictionary(candidate, ext_meta, ext3_meta)
    focused_render: dict[str, str] = {}
    for row in changes:
        logical = int(row["abs"], 16)
        raw, _term = payload_at(candidate, logical, max_len=512)
        pos = int(row["token_pos"])
        rendered = strip_pad(final_dictionary.expand(raw[pos:], tbl))
        if rendered != row["after_text"]:
            raise BuildError(f"final render mismatch {row['abs']}: {rendered!r} != {row['after_text']!r}")
        focused_render[row["abs"]] = rendered
    for index_hex, row in stock.items():
        rendered = strip_pad(final_dictionary.expand_index(int(index_hex, 16), tbl))
        if rendered != row["after"]:
            raise BuildError(f"final stock dictionary mismatch {index_hex}: {rendered!r}")
    for index_hex, row in logical_terms.items():
        rendered = strip_pad(final_dictionary.expand_index(int(index_hex, 16), tbl))
        if rendered != row["after"]:
            raise BuildError(f"final logical dictionary mismatch {index_hex}: {rendered!r}")
    for row in alias_term_changes:
        seg = int(row["segment"], 16)
        ptr = int(row["pointer"], 16)
        base = seg * BANK_SIZE
        end = candidate.find(b"\x00", base + ptr, base + BANK_SIZE)
        rendered = strip_pad(final_dictionary.expand(bytes(candidate[base + ptr : end]), tbl))
        if rendered != row["after"]:
            raise BuildError(
                f"final runtime alias phrase mismatch {row['segment']}:{row['local']}: {rendered!r}"
            )

    for index, encoded in slot_payload.items():
        seg, local = final_dictionary._ext3_bank_local(index)
        if final_dictionary._ext3_is_alias(index):
            raise BuildError(f"allocated slot is runtime alias after build: {index:05X}")
        bank = slice_expansion_bank(candidate, seg)
        ptr = struct.unpack_from("<H", bank, local * 2)[0]
        allowed.append((seg * BANK_SIZE + local * 2, seg * BANK_SIZE + local * 2 + 2))
        allowed.append((seg * BANK_SIZE + ptr, seg * BANK_SIZE + ptr + len(encoded) + 1))

    # Physical alias banks 21-25 may differ only in the four explicitly
    # approved same-length Radish phrase bodies above.  Pointer tables and all
    # other bytes remain parent-exact; the global diff allowlist below enforces
    # this fail-closed.
    alias_bank_runs = [
        run
        for run in diff_runs(parent, bytes(candidate))
        if any(run[0] < (seg + 1) * BANK_SIZE and run[1] > seg * BANK_SIZE for seg in range(0x21, 0x26))
    ]
    alias_unexpected = [run for run in alias_bank_runs if not covered(run, alias_allowed)]
    if alias_unexpected:
        raise BuildError(f"unexpected runtime alias-bank changes: {alias_unexpected[:8]}")
    if candidate[0x5C0000 : 0x5D0000] != parent[0x5C0000 : 0x5D0000]:
        raise BuildError("logical/physical bank5C changed")

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:12]}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")
    if PARENT.read_bytes() != parent:
        raise BuildError("parent candidate changed while building")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_TBL, tbl_bytes)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage17t_global_20cell_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_validation_required",
        "promotion_performed": False,
        "inputs": {
            "parent_candidate": identity(PARENT, parent),
            "parent_tbl": identity(PARENT_TBL, tbl_bytes),
            "parent_runtime_test_saveram": identity(PARENT_SAVE, save),
            "spec": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, result),
            "candidate_tbl": identity(OUT_TBL, tbl_bytes),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "counts": {
            "width_rewrites": len(width),
            "semantic_spill_rewrites": len(spill),
            "radish_record_rewrites": len(radish),
            "stock_dictionary_rewrites": len(stock),
            "logical_dictionary_term_rewrites": len(logical_terms),
            "alias_dictionary_term_rewrites": len(alias_terms),
            "record_rewrites_total": len(changes),
            "unique_new_phrases": len(slot_payload),
        },
        "allocation": {
            "strategy": "runtime-non-alias true-free ext3 slots; same-length E518 token swaps",
            "runtime_alias_pages": RUNTIME_ALIAS_PAGE_COUNT,
            "runtime_alias_local_start": f"{RUNTIME_ALIAS_LOCAL_START:04X}",
            "writer": write_info,
        },
        "changes": {
            "records": sorted(changes, key=lambda row: row["abs"]),
            "stock_dictionary": stock_changes,
            "logical_dictionary_terms": logical_term_changes,
            "alias_dictionary_terms": alias_term_changes,
        },
        "focused_render": focused_render,
        "checks": {
            "all_requested_text_max_20_cells": max(len(text) for text in desired_by_address.values()) <= 20,
            "record_extent_and_terminators_preserved": True,
            "record_non_token_bytes_preserved": True,
            "old_token_consumers_fully_accounted": True,
            "new_slots_outside_runtime_alias_range": True,
            "physical_alias_banks_21_25_only_approved_phrase_diffs": not alias_unexpected,
            "bank5c_exact_parent": True,
            "candidate_tbl_exact_parent": OUT_TBL.read_bytes() == tbl_bytes,
            "candidate_saveram_exact_parent_test_state": OUT_SAVE.read_bytes() == save,
            "diff_allowlist_clean": not unexpected,
            "ws_checksum_valid": True,
        },
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "changed_runs": len(runs),
            "unexpected_runs": len(unexpected),
        },
        "ws_checksum": f"{checksum:04X}",
        "runtime_validation_targets": [
            "STAGE17t 63D321: 현재 제단의 문이라는 우주요새입니다。가 끝까지 표시되는지",
            "STAGE17t 627963/627969: 그러니까…… / 그걸 당신이 하는 거야……가 서로 침범하지 않는지",
            "STAGE17t 6271E7/6271EE: 여기는 헨켄！ / 라디쉬도 문제없다！！가 정상 표시되는지",
            "라디쉬 전역 표준화가 대사/도감/함선명에서 이전 변형 없이 표시되는지",
            "기존 최후의 승리자 수정과 초상/스프라이트/이벤트 진행에 회귀가 없는지",
        ],
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["outputs"]["candidate_rom"],
                "counts": report["counts"],
                "max_cells": max(len(text) for text in desired_by_address.values()),
                "allocation": report["allocation"],
                "diff": report["diff"],
                "checksum": report["ws_checksum"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
