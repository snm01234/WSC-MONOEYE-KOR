#!/usr/bin/env python3
"""Rehome the 35 safe metadata=0F battle records from E5 18 to native stock tokens.

The target population is intentionally narrow:
- battle_voice_structured
- authoritative metadata exactly 0F
- safe_structure_exact=yes
- body_capacity >= 4
- current live record is 0F + E5 18 token + 01 padding

The 35 records preserve metadata, record extent, terminator, and next boundary.
Their 26 unique Korean phrases are copied out of ext3 and re-expressed through
ordinary two-byte stock-dictionary tokens.  Current-zero-reference stock slots
are reclaimed only after external, nested, and raw-pair reference scans prove
that they are unreachable.  Re-Pair helper entries are also ordinary native
stock entries; no E5 18/E5 19 portal appears in any newly-created entry.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import struct
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
    repair_pair_compress,
    symbol_bytes,
    symbol_size,
    visible_japanese,
)
from build_remaining_dialogue_candidate import covered, diff_runs
from dialogue_runtime_contracts import audit_manifest, build_manifest
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INVENTORY = SCRIPT / "battle_dialogue_structure_inventory.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_metadata0f_native_only_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_metadata0f_native_only_candidate.sav"
OUT_REPORT = PATCH / "battle_metadata0f_native_only_candidate_report.json"
OUT_CONTRACT = SCRIPT / "battle_metadata0f_native_only_candidate_contracts.json"
OUT_SAFETY = PATCH / "battle_metadata0f_native_only_candidate_runtime_safety.json"
EXPECTED_PARENT_SHA = "b6192a05fbfc37dc021ff2ccc9f1ee89ee50c0375c6ddfe807edc381f20e0662"
EXPECTED_TARGETS = 35
EXPECTED_UNIQUE = 26
HELPER_RULES = 63
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


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


def inventory_rows() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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
    for row in inventory_rows():
        if (
            row.get("metadata_hex", "").upper() != "0F"
            or row.get("classification") != "battle_voice_structured"
            or row.get("safe_structure_exact") != "yes"
            or int(row.get("body_capacity") or 0) < 4
        ):
            continue
        logical = int(row["record_start"], 16)
        rec = read_encoded_z_safe(parent, sb + logical, max_len=128)
        if rec is None:
            continue
        live, term = rec
        if not live.startswith(b"\x0F\xE5\x18"):
            continue
        body = bytes(live[1:])
        if len(body) < 4 or any(value != 0x01 for value in body[4:]):
            continue
        if len(body) != int(row["body_capacity"]):
            raise BuildError(f"body capacity drift at {logical:06X}")
        source_body = bytes.fromhex(row["body_hex_original"])
        if source_body.startswith(b"\xE5\x18"):
            raise BuildError(f"source was already ext3 at {logical:06X}")
        index = dict_index_from_ext3_token(*body[:4])
        raw = bytes(ext3.raw_entry(index))
        if not raw or b"\xE5\x18" in raw or b"\xE5\x19" in raw:
            raise BuildError(f"nested special portal in source phrase at {logical:06X}")
        text = clean(ext3.expand(raw, tbl))
        if not text or visible_japanese(text):
            raise BuildError(f"target phrase is not clean Korean at {logical:06X}: {text!r}")
        old = phrase_raw.get(text)
        if old is not None and old != raw:
            raise BuildError(f"same rendered phrase has different raw payload: {text!r}")
        phrase_raw[text] = raw
        nul_run = 0
        while term + nul_run < len(parent) and parent[term + nul_run] == 0:
            nul_run += 1
        next_lead = parent[term + nul_run] if term + nul_run < len(parent) else None
        targets.append({
            "abs": f"{logical:06X}",
            "logical": logical,
            "metadata_hex": "0F",
            "body_capacity": len(body),
            "before_hex": bytes(live).hex().upper(),
            "before_body_hex": body.hex().upper(),
            "terminator_offset": term,
            "terminator_nul_run": nul_run,
            "next_lead_hex": f"{next_lead:02X}" if next_lead is not None else "",
            "next_boundary_hex": parent[term:term + 8].hex().upper(),
            "text": text,
            "source_ext3_index": f"{index:05X}",
            "source_phrase_raw_hex": raw.hex().upper(),
        })
    if len(targets) != EXPECTED_TARGETS:
        raise BuildError(f"target population drifted: {len(targets)}")
    if len(phrase_raw) != EXPECTED_UNIQUE:
        raise BuildError(f"unique phrase population drifted: {len(phrase_raw)}")

    # Current-only reachability proof.  A stock id is reclaimable only when it
    # has no external record references, no nested dictionary parents, and no
    # raw pair occurrence anywhere in the ROM.
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
    if len(free) < EXPECTED_UNIQUE + HELPER_RULES:
        raise BuildError(f"insufficient current-zero-reference stock ids: {len(free)}")

    # Reclaim only phrase extents owned by zero-reference entries.  Any overlap
    # with a live entry would make the region non-exclusive and is rejected.
    extents: dict[int, tuple[int, int]] = {}
    for i in safe_indices:
        raw = bytes(stock.raw_entry(i))
        extents[i] = (stock.ptrs[i], stock.ptrs[i] + len(raw) + 1)
    free_set = set(free)
    for i in free:
        left, right = extents[i]
        for j, (other_left, other_right) in extents.items():
            if j == i or j in free_set:
                continue
            if other_left < right and left < other_right:
                raise BuildError(f"free phrase region overlaps live slot {i:04X}/{j:04X}")

    raw_regions = sorted(extents[i] for i in free)
    merged: list[list[int]] = []
    for left, right in raw_regions:
        if not merged or left > merged[-1][1]:
            merged.append([left, right])
        else:
            merged[-1][1] = max(merged[-1][1], right)
    pool_capacity = sum(right - left for left, right in merged)

    # Compress the ext3 leaf bytes only with ordinary stock helper tokens.
    sequences, rules, compression = repair_pair_compress(
        phrase_raw,
        tail_capacity=0,
        max_rules=HELPER_RULES,
    )
    if len(rules) != HELPER_RULES or compression["max_depth"] > 5:
        raise BuildError(f"helper grammar drift: {compression}")

    helper_ids = sorted(free)[:HELPER_RULES]
    top_ids = sorted(free)[HELPER_RULES:HELPER_RULES + EXPECTED_UNIQUE]
    helper_slots = {rule_id: helper_ids[rule_id] for rule_id in range(HELPER_RULES)}
    top_slot_by_text = dict(zip(sorted(phrase_raw), top_ids))

    helper_payloads: dict[int, bytes] = {}
    for rule_id, pair in enumerate(rules):
        payload = symbol_bytes(pair[0], helper_slots) + symbol_bytes(pair[1], helper_slots)
        if not payload or b"\x00" in payload or b"\xE5\x18" in payload or b"\xE5\x19" in payload:
            raise BuildError(f"invalid helper payload {rule_id}")
        helper_payloads[rule_id] = payload
    top_payloads = {
        text: b"".join(symbol_bytes(symbol, helper_slots) for symbol in sequences[text])
        for text in phrase_raw
    }
    if any(
        not payload or b"\x00" in payload or b"\xE5\x18" in payload or b"\xE5\x19" in payload
        for payload in top_payloads.values()
    ):
        raise BuildError("top native payload contains invalid/special bytes")

    entry_payloads: dict[int, bytes] = {
        helper_slots[rule_id]: payload for rule_id, payload in helper_payloads.items()
    }
    entry_payloads.update({
        top_slot_by_text[text]: payload for text, payload in top_payloads.items()
    })
    needed_storage = sum(len(payload) + 1 for payload in entry_payloads.values())
    if needed_storage > pool_capacity:
        raise BuildError(f"native phrase storage overflow: {needed_storage}>{pool_capacity}")

    # Best-fit largest-first into the disjoint merged zero-reference regions.
    bins = [{"cursor": left, "end": right} for left, right in merged]
    storage_pointer: dict[int, int] = {}
    storage_extents: list[tuple[int, int]] = []
    candidate = bytearray(parent)
    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    for index, payload in sorted(entry_payloads.items(), key=lambda item: (-(len(item[1]) + 1), item[0])):
        need = len(payload) + 1
        eligible = [
            (info["end"] - info["cursor"], bin_id)
            for bin_id, info in enumerate(bins)
            if info["end"] - info["cursor"] >= need
        ]
        if not eligible:
            raise BuildError(f"fragmented native pool cannot place slot {index:04X} need={need}")
        _remaining, bin_id = min(eligible)
        info = bins[bin_id]
        local = info["cursor"]
        info["cursor"] += need
        storage_pointer[index] = local
        file_at = stock_bank_file + local
        candidate[file_at:file_at + len(payload)] = payload
        candidate[file_at + len(payload)] = 0
        storage_extents.append((file_at, file_at + need))

    pointer_extents: list[tuple[int, int]] = []
    for index, pointer in sorted(storage_pointer.items()):
        pointer_at = stock_bank_file + DICT_PTR_START + index * 2
        struct.pack_into("<H", candidate, pointer_at, pointer)
        pointer_extents.append((pointer_at, pointer_at + 2))

    target_extents: list[tuple[int, int]] = []
    for row in targets:
        logical = int(row["logical"])
        before = bytes.fromhex(row["before_hex"])
        capacity = int(row["body_capacity"])
        top_index = top_slot_by_text[row["text"]]
        token = token_from_dict_index(top_index)
        replacement = b"\x0F" + token + b"\x01" * (capacity - 2)
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
        if not live.startswith(b"\x0F") or live[1:3] == b"\xE5\x18":
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
        if raw != top_payloads[text] or rendered != text or visible_japanese(rendered):
            created_failures.append({"index": f"{index:04X}", "kind": "top", "render": rendered})
    for rule_id, index in sorted(helper_slots.items()):
        raw = bytes(result_stock.raw_entry(index))
        if raw != helper_payloads[rule_id] or b"\xE5\x18" in raw or b"\xE5\x19" in raw:
            created_failures.append({"index": f"{index:04X}", "kind": "helper"})

    # New top ids may be referenced only by the 35 intended records; helper ids
    # only through the new native dictionary grammar.
    selected = set(storage_pointer)
    cand_external = external_occurrence_map(result, ext3_aware=True, wanted=selected)
    cand_nested = nested_occurrence_map(result_stock, wanted=selected, ext3_aware=True)
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
    for index in helper_ids:
        if cand_external.get(index):
            reference_failures.append({"index": f"{index:04X}", "kind": "helper_external"})

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
        "generated_by": "tools/build_battle_metadata0f_native_only_candidate.py",
        "status": "candidate_requires_promotion_transaction",
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "sha256": sha(result),
            "checksum": f"{checksum:04X}",
        },
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(save)},
        "counts": {
            "targets": len(targets),
            "unique_phrases": len(phrase_raw),
            "current_zero_reference_stock_ids": len(free),
            "top_slots": len(top_ids),
            "helper_slots": len(helper_ids),
            "helper_depth": compression["max_depth"],
            "native_storage_capacity": pool_capacity,
            "native_storage_used": needed_storage,
            "runtime_contract_hard_failures": int(safety["counts"]["hard_failures"]),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "guards": {
            "metadata_0f_preserved": True,
            "record_extents_preserved": True,
            "terminators_and_next_boundaries_preserved": True,
            "top_level_e518_removed_35_of_35": True,
            "new_dictionary_entries_have_no_e518_or_e519": True,
            "current_zero_reference_slots_only": True,
            "free_storage_has_no_live_overlap": True,
            "target_render_exact": True,
            "runtime_contract_audit_green": True,
            "saveram_copied_from_live": True,
        },
        "top_slot_by_text": {text: f"{index:04X}" for text, index in sorted(top_slot_by_text.items())},
        "helper_slots": {str(rule_id): f"{index:04X}" for rule_id, index in sorted(helper_slots.items())},
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
    raise SystemExit(main())
