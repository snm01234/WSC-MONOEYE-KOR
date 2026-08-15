#!/usr/bin/env python3
"""Rehome metadata=5D Heero-family battle records from E5 18 to native stock.

Live main keeps speaker/portrait byte 5D, but the body is still an E5 18 ext3
portal.  Battle callers in this family consume E5 as an extra speaker/control
byte (Sig's portrait) and do not expand the portal, so the box is empty.

53 of 58 live ext3 slots are now empty; Korean is recovered from the structure
inventory render (and from the 5 still-live ext3 phrases).  Records stay
5D + native 2-byte token + 01 padding, same extent/terminator/next boundary.

Main TIP and live SaveRAM are never overwritten.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_battle_dialogue_runtime_integrated_cleanup_candidate import (
    clean,
    visible_japanese,
)
from build_p2_stock_spill_candidate import SPILL_FLOOR
from build_remaining_dialogue_candidate import covered, diff_runs
from dialogue_runtime_contracts import audit_manifest, build_manifest
from expand_dictionary import guard_hangul_slot_writes
from hangul_marker import marker_code
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    dict_token_safe_in_zstring,
    load_rom,
    patch_bank,
    read_encoded_z_safe,
    slice_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    write_le16,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INVENTORY = SCRIPT / "battle_dialogue_structure_inventory.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_metadata5d_native_only_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_metadata5d_native_only_candidate.sav"
OUT_REPORT = PATCH / "battle_metadata5d_native_only_candidate_report.json"
OUT_CONTRACT = SCRIPT / "battle_metadata5d_native_only_candidate_contracts.json"
OUT_SAFETY = PATCH / "battle_metadata5d_native_only_candidate_runtime_safety.json"
EXPECTED_PARENT_SHA = "528f28e1050257e9f3698f27cf9aa577b217c67cd8951d6030cc5592fc6e0e85"
EXPECTED_TARGETS = 58
EXPECTED_UNIQUE = 32
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
HEERO_ANCHORS = (
    0x5E00C8,
    0x5E0109,
    0x5E0143,
    0x5E016F,
    0x5E0274,
    0x5E5E34,
    0x5E5F38,
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def encode_phrase(text: str, tbl: Tbl) -> bytes:
    normalized = normalize_ko_text(text)
    encoded = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot encode Korean phrase: {text!r}")
    if b"\xE5\x18" in encoded or b"\xE5\x19" in encoded:
        raise BuildError(f"encoded phrase contains special portal: {text!r}")
    return bytes(encoded)


def occupied_phrase_ranges(bank: bytes, ptrs: list[int], skip: set[int]) -> list[tuple[int, int]]:
    occupied: list[tuple[int, int]] = []
    for index, pointer in enumerate(ptrs):
        if index in skip or pointer == 0 or pointer >= BANK_SIZE:
            continue
        end = pointer
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        occupied.append((pointer, min(BANK_SIZE, end + 1)))
    occupied.append((DICT_PTR_START, SPILL_FLOOR))
    occupied.sort()
    merged: list[list[int]] = []
    for left, right in occupied:
        if not merged or left > merged[-1][1]:
            merged.append([left, right])
        else:
            merged[-1][1] = max(merged[-1][1], right)
    return [(left, right) for left, right in merged]


def holes_in(occupied: list[tuple[int, int]], lo: int, hi: int) -> list[list[int]]:
    holes: list[list[int]] = []
    cursor = lo
    for left, right in occupied:
        if right <= lo or left >= hi:
            continue
        if cursor < left:
            holes.append([cursor, min(left, hi)])
        cursor = max(cursor, right)
    if cursor < hi:
        holes.append([cursor, hi])
    return [hole for hole in holes if hole[1] > hole[0]]


def pack_payloads(
    bank: bytearray,
    ptrs: list[int],
    slot_payload: dict[int, bytes],
) -> list[tuple[int, int]]:
    occupied = occupied_phrase_ranges(bytes(bank), ptrs, set(slot_payload))
    holes = holes_in(occupied, SPILL_FLOOR, BANK_SIZE)
    storage: list[tuple[int, int]] = []
    for index, payload in sorted(slot_payload.items(), key=lambda item: (-(len(item[1]) + 1), item[0])):
        need = len(payload) + 1
        eligible = [
            (hole[1] - hole[0], hole_id)
            for hole_id, hole in enumerate(holes)
            if hole[1] - hole[0] >= need
        ]
        if not eligible:
            raise BuildError(f"no spill hole for slot {index:04X} need={need}")
        _remain, hole_id = min(eligible)
        hole = holes[hole_id]
        local = hole[0]
        hole[0] += need
        bank[local:local + len(payload)] = payload
        bank[local + len(payload)] = 0
        ptrs[index] = local
        storage.append((local, local + need))
    return storage


def inventory_rows() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def phrase_bytes(
    *,
    body: bytes,
    ext3: Dictionary,
    tbl: Tbl,
    inventory_ko: str,
) -> tuple[str, bytes, str]:
    index = dict_index_from_ext3_token(*body[:4])
    try:
        raw = bytes(ext3.raw_entry(index))
    except Exception:
        raw = b""
    if raw and b"\xE5\x18" not in raw and b"\xE5\x19" not in raw:
        text = clean(ext3.expand(raw, tbl))
        if text and not visible_japanese(text):
            return text, raw, "live_ext3"
    text = clean(inventory_ko)
    if not text or visible_japanese(text):
        raise BuildError(f"no usable Korean for token {body[:4].hex().upper()}")
    encoded = encode_phrase(text, tbl)
    rendered = clean(ext3.expand(encoded, tbl))
    if rendered != text:
        raise BuildError(f"encode round-trip mismatch: {text!r} -> {rendered!r}")
    return text, encoded, "inventory_encode"


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(save)}")

    sb = stock_base(parent)
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    ext3 = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stock = Dictionary(parent)

    targets: list[dict[str, Any]] = []
    phrase_raw: dict[str, bytes] = {}
    phrase_source: dict[str, str] = {}
    for row in inventory_rows():
        if (
            row.get("metadata_hex", "").upper() != "5D"
            or row.get("classification") != "battle_voice_structured"
            or row.get("safe_structure_exact") != "yes"
        ):
            continue
        logical = int(row["record_start"], 16)
        rec = read_encoded_z_safe(parent, sb + logical, max_len=128)
        if rec is None:
            continue
        live, term = rec
        live_b = bytes(live)
        if live_b.startswith(b"\x5D\xE5\x18"):
            body = bytes(live_b[1:])
            kind = "meta_then_e518"
        elif live_b.startswith(b"\xE5\x18"):
            body = bytes(live_b)
            kind = "body_only"
        else:
            continue
        if len(live_b) < 3:
            raise BuildError(f"record too short for native token at {logical:06X}")
        if len(body) < 4 or any(value != 0x01 for value in body[4:]):
            continue
        source_body = bytes.fromhex(row["body_hex_original"])
        if source_body.startswith(b"\xE5\x18"):
            raise BuildError(f"source was already ext3 at {logical:06X}")
        text, raw, source = phrase_bytes(
            body=body,
            ext3=ext3,
            tbl=tbl,
            inventory_ko=row.get("current_render") or "",
        )
        old = phrase_raw.get(text)
        if old is not None and old != raw:
            raise BuildError(f"same rendered phrase has different raw payload: {text!r}")
        phrase_raw[text] = raw
        phrase_source[text] = source
        nul_run = 0
        while term + nul_run < len(parent) and parent[term + nul_run] == 0:
            nul_run += 1
        next_lead = parent[term + nul_run] if term + nul_run < len(parent) else None
        targets.append({
            "abs": f"{logical:06X}",
            "logical": logical,
            "kind": kind,
            "heero_anchor": logical in HEERO_ANCHORS,
            "metadata_hex": "5D",
            "live_len": len(live_b),
            "before_hex": live_b.hex().upper(),
            "terminator_offset": term,
            "terminator_nul_run": nul_run,
            "next_lead_hex": f"{next_lead:02X}" if next_lead is not None else "",
            "next_boundary_hex": parent[term:term + 8].hex().upper(),
            "text": text,
            "phrase_source": source,
            "source_phrase_raw_hex": raw.hex().upper(),
        })
    if len(targets) != EXPECTED_TARGETS:
        raise BuildError(f"target population drifted: {len(targets)}")
    if len(phrase_raw) != EXPECTED_UNIQUE:
        raise BuildError(f"unique phrase population drifted: {len(phrase_raw)}")

    safe_indices = [
        i for i in range(stock.stock_count) if dict_token_safe_in_zstring(i)
    ]
    wanted = set(safe_indices)
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(stock, wanted=wanted, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, safe_indices)
    free = [
        i for i in safe_indices
        if not external.get(i) and not nested.get(i) and not raw_hits.get(i)
    ]

    if len(free) < EXPECTED_UNIQUE:
        raise BuildError(
            f"insufficient current-zero-reference stock ids: {len(free)} < {EXPECTED_UNIQUE}"
        )
    top_ids = sorted(free)[:EXPECTED_UNIQUE]
    top_slot_by_text = dict(zip(sorted(phrase_raw), top_ids))
    slot_payload = {
        top_slot_by_text[text]: payload for text, payload in phrase_raw.items()
    }
    needed_storage = sum(len(payload) + 1 for payload in slot_payload.values())

    pointers_before = list(stock.ptrs)
    candidate = bytearray(parent)
    current_locs = _working_two_byte_external_refs(parent)
    guard_hangul_slot_writes(
        candidate,
        slot_payload,
        allow_aux_consumers=False,
        locs=current_locs,
    )
    bank5f = bytearray(slice_bank(candidate, SEG_DICT))
    ptrs = list(pointers_before)
    local_storage = pack_payloads(bank5f, ptrs, slot_payload)
    for index in slot_payload:
        write_le16(bank5f, DICT_PTR_START + index * 2, ptrs[index])
    patch_bank(candidate, SEG_DICT, bank5f)
    dictionary_after_stock = Dictionary(candidate)
    pointers_after = list(dictionary_after_stock.ptrs)
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != set(top_ids):
        raise BuildError("stock pointer change set differs from selected zero-ref slots")
    for index, encoded in slot_payload.items():
        if bytes(dictionary_after_stock.raw_entry(index)) != encoded:
            raise BuildError(f"stock phrase write verification failed: {index:04X}")
        if b"\xE5\x18" in encoded or b"\xE5\x19" in encoded:
            raise BuildError(f"spilled phrase contains special portal: {index:04X}")

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    pointer_extents = [
        (stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2)
        for index in sorted(top_ids)
    ]
    storage_extents = [
        (stock_bank_file + left, stock_bank_file + right) for left, right in local_storage
    ]
    phrase_end = max(right for _left, right in local_storage)

    target_extents: list[tuple[int, int]] = []
    for row in targets:
        logical = int(row["logical"])
        before = bytes.fromhex(row["before_hex"])
        top_index = top_slot_by_text[row["text"]]
        token = token_from_dict_index(top_index)
        replacement = b"\x5D" + token + b"\x01" * (len(before) - 3)
        if len(replacement) != len(before):
            raise BuildError(f"record extent changed at {logical:06X}")
        start = sb + logical
        if bytes(candidate[start:start + len(before)]) != before:
            raise BuildError(f"record parent drift at {logical:06X}")
        term = int(row["terminator_offset"])
        boundary_before = parent[term:term + 8]
        candidate[start:start + len(before)] = replacement
        if candidate[term:term + 8] != boundary_before:
            raise BuildError(f"terminator/next boundary drift at {logical:06X}")
        target_extents.append((start, start + len(before)))
        row["stock_index"] = f"{top_index:04X}"
        row["stock_token_hex"] = token.hex().upper()
        row["after_hex"] = replacement.hex().upper()

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    result_stock = Dictionary(result)

    failures: list[dict[str, Any]] = []
    for row in targets:
        logical = int(row["logical"])
        live, term = read_encoded_z_safe(result, sb + logical, max_len=128) or (b"", -1)
        if not live.startswith(b"\x5D") or live[1:3] == b"\xE5\x18":
            failures.append({"abs": row["abs"], "reason": "record_not_native"})
            continue
        if bytes(live).hex().upper() != row["after_hex"]:
            failures.append({"abs": row["abs"], "reason": "payload_mismatch"})
            continue
        rendered = clean(result_stock.expand(bytes(live[1:]), tbl))
        if rendered != row["text"] or visible_japanese(rendered):
            failures.append({"abs": row["abs"], "reason": "render", "render": rendered})
        expected_run = int(row["terminator_nul_run"])
        actual_run = 0
        while term + actual_run < len(result) and result[term + actual_run] == 0:
            actual_run += 1
        next_lead = result[term + actual_run] if term + actual_run < len(result) else None
        expected_lead = int(row["next_lead_hex"], 16) if row["next_lead_hex"] else None
        if (
            term != int(row["terminator_offset"])
            or actual_run != expected_run
            or next_lead != expected_lead
        ):
            failures.append({"abs": row["abs"], "reason": "boundary"})

    created_failures: list[dict[str, Any]] = []
    for text, index in sorted(top_slot_by_text.items()):
        raw = bytes(result_stock.raw_entry(index))
        rendered = clean(result_stock.expand(raw, tbl))
        if raw != phrase_raw[text] or rendered != text or visible_japanese(rendered):
            created_failures.append({"index": f"{index:04X}", "kind": "top", "render": rendered})

    selected = set(top_ids)
    cand_external = external_occurrence_map(result, ext3_aware=True, wanted=selected)
    intended_sites: dict[int, set[str]] = {index: set() for index in top_ids}
    for row in targets:
        intended_sites[int(row["stock_index"], 16)].add(row["abs"])
    reference_failures: list[dict[str, Any]] = []
    for index in top_ids:
        actual = {str(ref.get("record_abs") or "").upper() for ref in cand_external.get(index, [])}
        if actual != intended_sites[index]:
            reference_failures.append({
                "index": f"{index:04X}", "kind": "top_external",
                "expected": sorted(intended_sites[index]), "actual": sorted(actual),
            })

    allowed = target_extents + pointer_extents + storage_extents + [(len(parent) - 2, len(parent))]
    runs = diff_runs(parent, result)
    unaccounted = [
        {"start": f"{left:07X}", "end": f"{right:07X}"}
        for left, right in runs if not covered((left, right), allowed)
    ]
    if failures or created_failures or reference_failures or unaccounted:
        raise BuildError(
            f"postbuild verification failed: targets={failures[:3]} created={created_failures[:3]} "
            f"refs={reference_failures[:3]} diff={unaccounted[:3]}"
        )

    manifest = build_manifest(original, result, target_path=OUT_ROM)
    safety = audit_manifest(result, manifest, target_path=OUT_ROM)
    if not safety.get("ok") or int((safety.get("counts") or {}).get("hard_failures", -1)) != 0:
        raise BuildError("runtime contract audit failed")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save)
    atomic_text(OUT_CONTRACT, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    atomic_text(OUT_SAFETY, json.dumps(safety, ensure_ascii=False, indent=2) + "\n")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_metadata5d_native_only_candidate.py",
        "status": "candidate_requires_runtime_test_not_promoted",
        "parent": {"path": str(MAIN.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(parent)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(result),
            "checksum": f"{checksum:04X}",
        },
        "saveram": {
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(save),
            "copied_from_live": True,
        },
        "counts": {
            "targets": len(targets),
            "unique_phrases": len(phrase_raw),
            "body_only_restored": sum(1 for row in targets if row["kind"] == "body_only"),
            "meta_then_e518": sum(1 for row in targets if row["kind"] == "meta_then_e518"),
            "heero_anchors": sum(1 for row in targets if row["heero_anchor"]),
            "current_zero_reference_stock_ids": len(free),
            "top_slots": len(top_ids),
            "spill_floor": f"{SPILL_FLOOR:04X}",
            "spill_end": f"{phrase_end:04X}",
            "native_storage_used": needed_storage,
            "runtime_contract_hard_failures": int(safety["counts"]["hard_failures"]),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "guards": {
            "metadata_5d_restored_or_preserved": True,
            "record_extents_preserved": True,
            "terminators_and_next_boundaries_preserved": True,
            "top_level_e518_removed": True,
            "new_dictionary_entries_have_no_e518_or_e519": True,
            "current_zero_reference_slots_only": True,
            "hangul_slot_writes_guarded": True,
            "spill_does_not_overwrite_live_phrases": True,
            "target_render_exact": True,
            "runtime_contract_audit_green": True,
            "main_tip_unchanged": True,
            "saveram_copied_from_live": True,
        },
        "phrase_source": phrase_source,
        "top_slot_by_text": {text: f"{index:04X}" for text, index in sorted(top_slot_by_text.items())},
        "targets": targets,
        "changed_runs": [{"start": f"{a:07X}", "end": f"{b:07X}"} for a, b in runs],
    }
    atomic_text(OUT_REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "candidate": report["candidate"],
        "counts": report["counts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
