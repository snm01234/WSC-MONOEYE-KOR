#!/usr/bin/env python3
"""Build the 2x20-cell dialogue readability candidate from the current main TIP.

Policy implemented here:
* every reviewed row is <=20 display cells;
* word-boundary-only cases move a whole Korean token to row 2 instead of
  splitting it at the 20-cell edge;
* legacy 2-row space-only reflow groups that deleted >=3 spaces use the
  source-grounded rewrite catalog instead of further space deletion;
* compact3 stays disabled. Short records remain on ordinary two-byte
  dictionary tokens. Existing identical phrases are reused when possible and
  only two union-proven-dead extended slots are repurposed for phrases that
  have no safe existing token.

The current main TIP is never overwritten. Record extents, prefixes, and NUL
terminators are fixed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_dialogue_20cell_candidate import (
    alias_bank_cursor,
    encode,
    ext3_index,
    write_alias_ext3_slots_guarded,
)
from extract_script import split_prefix_body
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    guard_slot_writes,
)
from monoeye_rom import (
    BANK_SIZE,
    COMPACT3_INDEX_BASE,
    COMPACT3_INDEX_END,
    Tbl,
    dict_token_safe_in_zstring,
    le16,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
WORKLIST = ROOT / "out/script/dialogue_readability_worklist.json"
CATALOG = ROOT / "out/script/dialogue_readability_changes.json"
SHORT_ROUTES = ROOT / "out/script/dialogue_readability_short_routes.json"
LEGACY_20CELL_WORKLIST = ROOT / "out/script/dialogue_20cell_worklist.json"
SINGLETON_BATCHES = tuple(
    ROOT / f"data/dialogue_singleton_rewrite_batch{i:03d}.json"
    for i in range(1, 8)
)
FALSE_LEAD_SAFE = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/dialogue_readability_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_readability_candidate.sav"
OUT_REPORT = ROOT / "out/patch/dialogue_readability_report.json"
EXPECTED_MAIN = "8287c930a2193d5842783a5f49167aa77550e16139bdc76674c61e2602f2cff1"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")
EXPECTED_SINGLETON_REWRITES = 567
EXPECTED_FALSE_LEADS = 264

# Current union scan proves these are the only two dead extended two-byte
# slots. Keep the longer phrase in the larger physical slot.
DEDICATED_EXT2 = {
    "62CE34": 0x0FB9,  # 있습니다！, encoded len 11, old capacity 16
    "636C52": 0x0FA3,  # 플투！, encoded len 7, old capacity 9
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


def in_intervals(off: int, intervals: list[tuple[int, int]]) -> bool:
    return any(a <= off < b for a, b in intervals)


def diff_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    i = 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < len(before) and before[i] != after[i]:
            i += 1
        runs.append({
            "start": f"{start:07X}",
            "end": f"{i:07X}",
            "length": i - start,
            "before_hex": before[start:min(i, start + 24)].hex().upper(),
            "after_hex": after[start:min(i, start + 24)].hex().upper(),
        })
    return runs


def decode_record(rom: bytes, dictionary, tbl: Tbl, logical: int) -> dict[str, Any]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    payload, term = bytes(got[0]), int(got[1])
    prefix, body, kind = split_prefix_body(payload)
    return {
        "payload": payload,
        "terminator": term,
        "prefix": prefix,
        "body": body,
        "kind": kind,
        "text": dictionary.expand(body, tbl).rstrip("　 \t"),
    }


def source_record_map(work: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for key in ("semantic_rewrite_groups", "word_boundary_reflow_only_groups"):
        for group in work.get(key) or []:
            for record in group.get("records") or []:
                address = str(record["abs"]).upper()
                if address in rows:
                    raise BuildError(f"duplicate worklist address {address}")
                rows[address] = record
    return rows


def catalog_after_map(catalog: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    after: dict[str, str] = {}
    classification: dict[str, str] = {}
    for group in catalog.get("groups") or []:
        addresses = list(group.get("addresses") or [])
        texts = list(group.get("after_rows") or [])
        if len(addresses) != len(texts):
            raise BuildError(f"catalog address/text mismatch {group.get('group_id')}")
        for address, text in zip(addresses, texts):
            key = str(address).upper()
            if key in after:
                raise BuildError(f"duplicate catalog address {key}")
            after[key] = str(text)
            classification[key] = str(group.get("classification") or "")
    return after, classification


def load_singleton_rewrites() -> dict[str, dict[str, str]]:
    legacy = json.loads(LEGACY_20CELL_WORKLIST.read_text(encoding="utf-8"))
    expected: dict[str, dict[str, str]] = {}
    for group in legacy.get("groups") or []:
        records = group.get("records") or []
        if group.get("mode") != "reflow_current_nonspace_exact" or len(records) != 1:
            continue
        address = str(records[0]["abs"]).upper()
        auto = list(group.get("auto_after") or [])
        if len(auto) != 1:
            raise BuildError(f"singleton auto-after shape drift {address}")
        if address in expected:
            raise BuildError(f"duplicate singleton worklist address {address}")
        expected[address] = {
            "before": str(auto[0]),
            "jp": str(records[0].get("source_jp") or ""),
        }
    if len(expected) != EXPECTED_SINGLETON_REWRITES:
        raise BuildError(
            f"singleton worklist population drifted: {len(expected)} != {EXPECTED_SINGLETON_REWRITES}"
        )

    targets: dict[str, str] = {}
    for path in SINGLETON_BATCHES:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for raw_address, raw_text in (doc.get("targets") or {}).items():
            address = str(raw_address).upper()
            if address in targets:
                raise BuildError(f"duplicate singleton batch address {address}")
            targets[address] = str(raw_text)
    if set(targets) != set(expected):
        raise BuildError(
            f"singleton batch coverage mismatch missing={sorted(set(expected)-set(targets))[:10]} "
            f"extra={sorted(set(targets)-set(expected))[:10]}"
        )

    out: dict[str, dict[str, str]] = {}
    for address in sorted(expected, key=lambda x: int(x, 16)):
        desired = targets[address]
        if len(desired.replace("<E62F>", "")) > 20 or JP_RE.search(desired):
            raise BuildError(f"invalid singleton target {address}: {desired!r}")
        if len(desired) >= 17 and not any(ch in desired for ch in {" ", "　"}):
            raise BuildError(f"dense singleton target has no spacing {address}: {desired!r}")
        out[address] = {
            "before": expected[address]["before"],
            "after": desired,
            "jp": expected[address]["jp"],
        }
    return out


def load_false_lead_rows() -> list[dict[str, str]]:
    with FALSE_LEAD_SAFE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_FALSE_LEADS:
        raise BuildError(f"false-lead population drifted: {len(rows)} != {EXPECTED_FALSE_LEADS}")
    seen: set[str] = set()
    for row in rows:
        address = str(row["abs"]).upper()
        if address in seen:
            raise BuildError(f"duplicate false-lead address {address}")
        seen.add(address)
    return rows


def exact_two_byte_phrases(dictionary, tbl: Tbl) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for index in range(min(dictionary.count, 0x1000)):
        if not dict_token_safe_in_zstring(index):
            continue
        token = token_from_dict_index(index)
        if len(token) != 2:
            continue
        try:
            text = dictionary.expand(dictionary.raw_entry(index), tbl).rstrip("　 \t")
        except Exception:
            continue
        result.setdefault(text, []).append(index)
    return result


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN:
        raise BuildError("current main TIP identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("current main SaveRAM missing or wrong size")

    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    short_routes = json.loads(SHORT_ROUTES.read_text(encoding="utf-8"))
    singleton = load_singleton_rewrites()
    false_leads = load_false_lead_rows()
    if not catalog.get("summary", {}).get("width_ok") or not catalog.get("summary", {}).get("encoding_ok"):
        raise BuildError("readability catalog has not passed width/encoding validation")
    if int(catalog.get("summary", {}).get("total_records") or 0) != 1412:
        raise BuildError("catalog target count drifted")
    if short_routes.get("summary", {}).get("needs_new_or_keep_current") != 2:
        raise BuildError("short-route analysis no longer resolves to exactly two new phrases")
    if short_routes.get("summary", {}).get("unsupported") != 0:
        raise BuildError("short-route analysis has unsupported rows")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    if ext3_meta.get("compact3") not in (None, False):
        raise BuildError("accepted runtime unexpectedly enables compact3")
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    if tuple(inventory.ext_free) != (0x0FA3, 0x0FB9):
        raise BuildError(f"true-free extended 2-byte slots drifted: {inventory.ext_free}")

    source = source_record_map(work)
    after, classification = catalog_after_map(catalog)
    if set(source) != set(after):
        raise BuildError(
            f"work/catalog coverage mismatch missing={sorted(set(source)-set(after))[:10]} "
            f"extra={sorted(set(after)-set(source))[:10]}"
        )

    sb = stock_base(parent)
    prepared: list[dict[str, Any]] = []
    for address in sorted(source, key=lambda x: int(x, 16)):
        logical = int(address, 16)
        rec = decode_record(parent, dictionary, tbl, logical)
        expected = source[address]
        expected_payload = bytes.fromhex(str(expected["current_payload_hex"]))
        if rec["payload"] != expected_payload:
            raise BuildError(f"parent payload drift {address}")
        if rec["text"] != str(expected["current_main"]):
            raise BuildError(f"parent render drift {address}: {rec['text']!r} != {expected['current_main']!r}")
        desired = after[address]
        if len(desired.replace("<E62F>", "")) > 20 or JP_RE.search(desired):
            raise BuildError(f"invalid final target {address}: {desired!r}")
        prepared.append({
            "abs": address,
            "logical": logical,
            "before": rec["text"],
            "after": desired,
            "classification": classification[address],
            "payload_hex": rec["payload"].hex().upper(),
            "prefix_hex": rec["prefix"].hex().upper(),
            "body_hex": rec["body"].hex().upper(),
            "body_len": len(rec["body"]),
            "terminator": f"{rec['terminator'] - sb:06X}",
            "old_ext3_index": None if ext3_index(rec["body"]) is None else f"{ext3_index(rec['body']):05X}",
        })

    original_readability_addresses = {row["abs"] for row in prepared}
    if original_readability_addresses & set(singleton):
        raise BuildError("singleton rewrite unexpectedly overlaps 2-row readability target")
    for address in sorted(singleton, key=lambda x: int(x, 16)):
        logical = int(address, 16)
        rec = decode_record(parent, dictionary, tbl, logical)
        spec = singleton[address]
        if rec["text"] != spec["before"]:
            raise BuildError(
                f"singleton parent render drift {address}: {rec['text']!r} != {spec['before']!r}"
            )
        desired = spec["after"]
        # Fail closed on the exact encoded path used by the ROM builder, not
        # merely on Python character length.
        encode(desired, tbl)
        old_index = ext3_index(rec["body"])
        if old_index is None or len(rec["body"]) < 4:
            raise BuildError(f"singleton target is not ext3-backed {address}")
        prepared.append({
            "abs": address,
            "logical": logical,
            "before": rec["text"],
            "after": desired,
            "classification": "singleton_source_rewrite",
            "source_jp": spec["jp"],
            "payload_hex": rec["payload"].hex().upper(),
            "prefix_hex": rec["prefix"].hex().upper(),
            "body_hex": rec["body"].hex().upper(),
            "body_len": len(rec["body"]),
            "terminator": f"{rec['terminator'] - sb:06X}",
            "old_ext3_index": f"{old_index:05X}",
        })

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    strategies: dict[str, str] = {}
    exact = exact_two_byte_phrases(dictionary, tbl)

    # All changed ordinary short-token rows either reuse an existing identical
    # two-byte phrase or use one of the two proven-dead extended slots below.
    short_new_rows: dict[str, dict[str, Any]] = {}
    ext3_changed: list[dict[str, Any]] = []
    noops = 0
    for row in prepared:
        address = row["abs"]
        if row["after"] == row["before"]:
            strategies[address] = "unchanged_already_acceptable"
            noops += 1
            continue
        body = bytes.fromhex(row["body_hex"])
        old_ext3 = ext3_index(body)
        if old_ext3 is not None:
            ext3_changed.append(row)
            continue

        if address in DEDICATED_EXT2:
            short_new_rows[address] = row
            continue
        choices = exact.get(row["after"], [])
        if not choices:
            raise BuildError(f"changed short row has no existing 2-byte phrase {address}: {row['after']!r}")
        index = choices[0]
        token = token_from_dict_index(index)
        if len(token) != 2 or len(body) < 2:
            raise BuildError(f"bad two-byte reuse route {address}/{index:04X}")
        start = sb + row["logical"] + len(bytes.fromhex(row["prefix_hex"]))
        current = bytes(candidate[start:start + len(body)])
        if current != body:
            raise BuildError(f"short body drift before rewrite {address}")
        new_body = token + (b"\x01" * (len(body) - 2))
        candidate[start:start + len(body)] = new_body
        allowed.append((start, start + len(body)))
        row["new_index"] = f"{index:04X}"
        row["new_token_hex"] = token.hex().upper()
        row["new_body_hex"] = new_body.hex().upper()
        strategies[address] = "reuse_existing_2byte_phrase"

    # Repurpose exactly two true-free extended phrases in-place. No pointer is
    # moved, and every physical alias/interior-pointer hazard is checked.
    ext_seg = int(str(ext_meta.get("ext_seg") or "10"), 16)
    ext_base = ext_seg * BANK_SIZE
    ext_ptr_off = int(ext_meta.get("ext_ptr_off") or 0)
    stock_count = int(ext_meta.get("stock_count") or 3831)
    slot_count = int(ext_meta.get("slot_count") or 265)
    ptrs = [le16(parent, ext_base + ext_ptr_off + i * 2) for i in range(slot_count)]
    ext2_reports: list[dict[str, Any]] = []
    for address, index in DEDICATED_EXT2.items():
        row = short_new_rows.get(address)
        if row is None:
            raise BuildError(f"dedicated ext2 target missing: {address}")
        if index not in inventory.ext_free or not union.is_true_free(index):
            raise BuildError(f"dedicated ext2 slot not true-free: {index:04X}")
        if union.consumers_for(index) or union.parents_of(index):
            raise BuildError(f"dedicated ext2 slot has consumer/parent: {index:04X}")
        local = index - stock_count
        if not 0 <= local < slot_count:
            raise BuildError(f"dedicated slot outside extended dictionary: {index:04X}")
        ptr = ptrs[local]
        old_payload = bytes(dictionary.raw_entry(index))
        aliases = [stock_count + i for i, value in enumerate(ptrs) if value == ptr]
        interior = [stock_count + i for i, value in enumerate(ptrs) if ptr < value <= ptr + len(old_payload)]
        encoded = encode(row["after"], tbl)
        if aliases != [index] or interior:
            raise BuildError(f"ext2 storage alias hazard {index:04X}: aliases={aliases} interior={interior}")
        if len(encoded) > len(old_payload):
            raise BuildError(f"ext2 payload does not fit {index:04X}: {len(encoded)}>{len(old_payload)}")
        outcome = guard_slot_writes(candidate, {index: encoded}, union=union, require_free=True)
        if not outcome.ok:
            raise BuildError(f"ext2 guard refused {index:04X}: {outcome.as_dict()}")
        entry_abs = int(dictionary.entry_abs(index))
        if entry_abs != ext_base + ptr:
            raise BuildError(f"ext2 physical entry mismatch {index:04X}")
        candidate[entry_abs:entry_abs + len(encoded)] = encoded
        candidate[entry_abs + len(encoded)] = 0
        allowed.append((entry_abs, entry_abs + len(old_payload) + 1))

        body = bytes.fromhex(row["body_hex"])
        token = token_from_dict_index(index)
        if len(token) != 2 or len(body) < 2:
            raise BuildError(f"ext2 token/body length mismatch {address}")
        start = sb + row["logical"] + len(bytes.fromhex(row["prefix_hex"]))
        if bytes(candidate[start:start + len(body)]) != body:
            raise BuildError(f"dedicated short body drift {address}")
        new_body = token + (b"\x01" * (len(body) - 2))
        candidate[start:start + len(body)] = new_body
        allowed.append((start, start + len(body)))
        row["new_index"] = f"{index:04X}"
        row["new_token_hex"] = token.hex().upper()
        row["new_body_hex"] = new_body.hex().upper()
        strategies[address] = "true_free_ext2_phrase_retarget"
        ext2_reports.append({
            "abs": address,
            "slot": f"{index:04X}",
            "phrase": row["after"],
            "old_capacity": len(old_payload),
            "new_len": len(encoded),
            "entry_abs": f"{entry_abs:07X}",
            "pointer": f"{ptr:04X}",
            "aliases": [f"{x:04X}" for x in aliases],
            "interior": [f"{x:04X}" for x in interior],
            "guard": outcome.as_dict(),
        })

    # Changed ext3 records are detached onto fresh true-free alias slots. This
    # avoids mutating any existing phrase that might be shared by other callers.
    alias_free = [
        index for index in inventory.ext3_free
        if dictionary._ext3_is_alias(index)
        and not (COMPACT3_INDEX_BASE <= index <= COMPACT3_INDEX_END)
        and dict_token_safe_in_zstring(index)
        and len(token_from_dict_index(index)) == 4
    ]
    if len(alias_free) < len(ext3_changed):
        raise BuildError(f"not enough alias ext3 slots: need {len(ext3_changed)} have {len(alias_free)}")

    # Do not consume free slots in numeric order: alias indices are grouped by
    # physical bank and that can overflow bank 21 long before the total alias
    # capacity is exhausted. Allocate each phrase to the bank with the most
    # remaining phrase room.
    free_by_seg: dict[int, list[int]] = {}
    for index in alias_free:
        seg, _local = dictionary._ext3_bank_local(index)
        free_by_seg.setdefault(int(seg), []).append(index)
    room_by_seg = {
        seg: BANK_SIZE - alias_bank_cursor(slice_expansion_bank(parent, seg))
        for seg in free_by_seg
    }
    assigned: dict[str, int] = {}
    alias_payloads: dict[int, bytes] = {}
    for row in ext3_changed:
        payload = encode(row["after"], tbl)
        need = len(payload) + 1
        candidates = [
            seg for seg, slots in free_by_seg.items()
            if slots and room_by_seg[seg] >= need
        ]
        if not candidates:
            raise BuildError(
                f"alias phrase capacity exhausted at {row['abs']} need={need} "
                f"rooms={room_by_seg}"
            )
        seg = max(candidates, key=lambda value: room_by_seg[value])
        index = free_by_seg[seg].pop(0)
        assigned[row["abs"]] = index
        alias_payloads[index] = payload
        room_by_seg[seg] -= need
    before_alias = bytes(candidate)
    alias_write = write_alias_ext3_slots_guarded(candidate, alias_payloads, union=union, dictionary=dictionary)
    for off, (a, b) in enumerate(zip(before_alias, candidate)):
        if a != b:
            allowed.append((off, off + 1))

    for row in ext3_changed:
        address = row["abs"]
        index = assigned[address]
        token = token_from_dict_index(index)
        body = bytes.fromhex(row["body_hex"])
        if len(token) != 4 or len(body) < 4:
            raise BuildError(f"ext3 target body too short {address}")
        start = sb + row["logical"] + len(bytes.fromhex(row["prefix_hex"]))
        if bytes(candidate[start:start + len(body)]) != body:
            raise BuildError(f"ext3 body drift before retarget {address}")
        new_body = token + (b"\x01" * (len(body) - 4))
        candidate[start:start + len(body)] = new_body
        allowed.append((start, start + len(body)))
        row["new_index"] = f"{index:05X}"
        row["new_token_hex"] = token.hex().upper()
        row["new_body_hex"] = new_body.hex().upper()
        strategies[address] = "true_free_alias_ext3_retarget"

    # Remove the 264 sentence-initial Japanese code units that an earlier
    # portrait repair incorrectly reintroduced as metadata.  Every row is now
    # an ext3 Korean token immediately after the proven-visible lead, so this is
    # a fixed-extent left shift: keep the token unchanged and fill the vacated
    # tail bytes with 01 padding.  No dictionary entry or NUL boundary moves.
    false_lead_reports: list[dict[str, Any]] = []
    for spec in false_leads:
        address = str(spec["abs"]).upper()
        logical = int(address, 16)
        lead = bytes.fromhex(str(spec["lead_hex"]))
        extent = len(bytes.fromhex(str(spec["candidate_payload_hex"])))
        at = sb + logical
        current = bytes(candidate[at:at + extent])
        if not current.startswith(lead):
            raise BuildError(f"proven visible false lead missing before cleanup {address}")
        rest = current[len(lead):]
        if len(rest) < 4 or rest[:2] != b"\xE5\x18" or any(byte != 0x01 for byte in rest[4:]):
            raise BuildError(f"false-lead body is not token-only ext3 {address}: {rest.hex().upper()}")
        if candidate[at + extent] != 0:
            raise BuildError(f"false-lead terminator drift before cleanup {address}")
        render = dictionary.expand(rest, tbl).rstrip("　 \t")
        visible_parts = render.split("<E62F>")
        if JP_RE.search(render.replace("<E62F>", "")) or max(map(len, visible_parts), default=0) > 20:
            raise BuildError(f"false-lead Korean body invalid {address}: {render!r}")
        rebuilt = rest + (b"\x01" * len(lead))
        if len(rebuilt) != extent or not rebuilt.startswith(b"\xE5\x18"):
            raise BuildError(f"false-lead fixed-extent rebuild failed {address}")
        candidate[at:at + extent] = rebuilt
        allowed.append((at, at + extent))
        false_lead_reports.append({
            "abs": address,
            "lead_hex_removed": lead.hex().upper(),
            "lead_text_removed": spec.get("lead_text") or spec.get("lead_text_removed") or "",
            "before_payload_hex": current.hex().upper(),
            "after_payload_hex": rebuilt.hex().upper(),
            "render": render,
            "terminator": f"{logical + extent:06X}",
        })
    if len(false_lead_reports) != EXPECTED_FALSE_LEADS:
        raise BuildError("false-lead cleanup target count drifted")

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    for row in prepared:
        before_rec = decode_record(parent, dictionary, tbl, row["logical"])
        after_rec = decode_record(result, d_result, tbl, row["logical"])
        reasons: list[str] = []
        if after_rec["terminator"] != before_rec["terminator"]:
            reasons.append("terminator_changed")
        if len(after_rec["payload"]) != len(before_rec["payload"]):
            reasons.append("record_extent_changed")
        if after_rec["prefix"] != before_rec["prefix"]:
            reasons.append("prefix_changed")
        if after_rec["text"] != row["after"]:
            reasons.append(f"render_mismatch:{after_rec['text']!r}")
        if len(after_rec["text"].replace("<E62F>", "")) > 20:
            reasons.append("over_20")
        if JP_RE.search(after_rec["text"]):
            reasons.append("japanese_remains")
        if reasons:
            failures.append({"abs": row["abs"], "reasons": reasons})

    for row in false_lead_reports:
        logical = int(row["abs"], 16)
        payload = bytes.fromhex(row["after_payload_hex"])
        at = sb + logical
        got = result[at:at + len(payload)]
        reasons: list[str] = []
        if got != payload:
            reasons.append("payload_mismatch")
        if not got.startswith(b"\xE5\x18"):
            reasons.append("ext3_not_at_byte0")
        if result[at + len(payload)] != 0:
            reasons.append("terminator_changed")
        try:
            render = d_result.expand(got, tbl).rstrip("　 \t")
        except Exception as exc:  # noqa: BLE001
            render = f"<decode:{type(exc).__name__}>"
            reasons.append("decode_failed")
        if render != row["render"]:
            reasons.append(f"render_mismatch:{render!r}")
        if JP_RE.search(render.replace("<E62F>", "")):
            reasons.append("japanese_remains")
        if max((len(part) for part in render.split("<E62F>")), default=0) > 20:
            reasons.append("over_20")
        if reasons:
            failures.append({"abs": row["abs"], "false_lead": True, "reasons": reasons})

    intervals = merge_intervals(allowed)
    unexpected = [
        off for off, (a, b) in enumerate(zip(parent, result))
        if a != b and not in_intervals(off, intervals)
    ]
    if unexpected:
        failures.append({
            "reason": "unexpected_diff_offsets",
            "count": len(unexpected),
            "sample": [f"{x:07X}" for x in unexpected[:30]],
        })
    if failures:
        raise BuildError(json.dumps(failures[:30], ensure_ascii=False, indent=2))

    tmp = OUT_ROM.with_name(f".{OUT_ROM.name}.{os.getpid()}.tmp")
    tmp.write_bytes(result)
    os.replace(tmp, OUT_ROM)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    for row in prepared:
        row["strategy"] = strategies[row["abs"]]
    counts = {
        "targets": len(prepared),
        "two_row_readability_records": len(original_readability_addresses),
        "singleton_source_rewrite_records": sum(r["classification"] == "singleton_source_rewrite" for r in prepared),
        "false_lead_cleanup_records": len(false_lead_reports),
        "semantic_rewrite_records": sum(r["classification"] == "semantic_rewrite_required" for r in prepared),
        "word_boundary_reflow_records": sum(r["classification"] == "word_boundary_reflow_only" for r in prepared),
        "unchanged_already_acceptable": noops,
        "reuse_existing_2byte_phrase": sum(v == "reuse_existing_2byte_phrase" for v in strategies.values()),
        "true_free_ext2_phrase_retarget": sum(v == "true_free_ext2_phrase_retarget" for v in strategies.values()),
        "true_free_alias_ext3_retarget": sum(v == "true_free_alias_ext3_retarget" for v in strategies.values()),
        "compact3_records": 0,
        "terminator_changes": 0,
        "prefix_changes": 0,
        "record_extent_changes": 0,
        "unexpected_diff_offsets": 0,
        "max_after_cells": max(len(r["after"].replace("<E62F>", "")) for r in prepared),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_readability_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_test_required",
        "policy": {
            "line_limit": 20,
            "two_row_capacity": 40,
            "semantic_rewrite_threshold_removed_spaces": 3,
            "word_boundary": "move whole word to row 2 when possible; do not split inside token",
            "singleton_reflow": "567 legacy one-row space-only reflows are source-grounded rewrites; normal spacing retained within 20 cells",
            "false_lead_guard": "264 proven visible Japanese sentence leads are removed and may never be restored as portrait metadata",
            "compact3": False,
        },
        "parent": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "size": len(parent),
            "sha256": sha(parent),
        },
        "candidate": {
            "path": "out/patch/dialogue_readability_candidate.wsc",
            "size": len(result),
            "sha256": sha(result),
            "ws_checksum": f"{checksum:04X}",
        },
        "candidate_save": {
            "path": "sram/dialogue_readability_candidate.sav",
            "size": OUT_SAVE.stat().st_size,
            "sha256": sha(OUT_SAVE.read_bytes()),
            "matches_main_save": sha(OUT_SAVE.read_bytes()) == sha(MAIN_SAVE.read_bytes()),
        },
        "counts": counts,
        "short_routes": short_routes.get("summary") or {},
        "dedicated_ext2": ext2_reports,
        "alias_write": alias_write,
        "targets": prepared,
        "false_lead_cleanup": false_lead_reports,
        "allowed_intervals": [[a, b] for a, b in intervals],
        "diff_runs": diff_runs(parent, result),
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate_save"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
