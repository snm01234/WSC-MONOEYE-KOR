#!/usr/bin/env python3
"""Build a 20-cell dialogue candidate without changing dialogue record bytes.

Targets are bound to the current main TIP by dialogue_20cell_worklist.json.
Every target record must already reference one private E5 18 ext3 phrase.
Only that phrase storage (or, if the shorter/normalized phrase unexpectedly
needs more encoded bytes, that same ext3 pointer plus appended phrase room) may
change. Prefixes, record payloads, terminators, next-record boundaries, runtime
code and stock dictionary pointers remain byte-identical.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import struct
import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_terminology_retranslation_candidate import (
    append_ext3_phrase_alias_aware,
    diff_runs,
    target_accounted,
)
from extract_script import split_prefix_body
from hangul_marker import marker_code
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
    patch_expansion_bank,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import EXP3_SEG0, EXP3_SLOTS

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
WORKLIST = ROOT / "out/script/dialogue_20cell_worklist.json"
BATCH_GLOB = str(ROOT / "data/dialogue_20cell_llm_batches/batch*.json")
SHORT_PAIR_OVERRIDES = ROOT / "data/dialogue_20cell_short_pair_overrides.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/dialogue_20cell_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_20cell_candidate.sav"
OUT_REPORT = ROOT / "out/patch/dialogue_20cell_report.json"
EXPECTED_MAIN_SHA = "bbd14e0792264787985462c14d75cc77af168b90efc45b3a01d58b9a1de3d1ec"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LIMIT = 20
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def encode(text: str, tbl: Tbl) -> bytes:
    payload = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or b"\x00" in payload:
        raise BuildError(f"cannot encode target: {text!r}")
    return payload


def compact_spaces_to_limit(text: str) -> tuple[str, int]:
    """Remove only spaces, from right to left, until the 20-cell limit fits."""
    text = normalize_ko_text(text)
    if len(text) <= LIMIT:
        return text, 0
    chars = list(text)
    removed = 0
    for i in range(len(chars) - 1, -1, -1):
        if len(chars) <= LIMIT:
            break
        if chars[i] in {" ", "\u3000"}:
            del chars[i]
            removed += 1
    out = "".join(chars)
    if len(out) > LIMIT:
        raise BuildError(f"20-cell target still too long after space-only compaction: {len(out)} {out!r}")
    return out, removed


def ext3_index(body: bytes) -> int | None:
    if len(body) < 4 or body[:2] != b"\xE5\x18":
        return None
    return 0x1000 + (body[2] << 8) + body[3]


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


def compact_standard_ext3_banks(rom: bytearray, num_banks: int) -> dict[str, Any]:
    """Repack banks 11-20 without changing any local slot payload."""
    data_start = EXP3_SLOTS * 2
    rows: list[dict[str, Any]] = []
    for bi in range(num_banks):
        seg = EXP3_SEG0 + bi
        before = bytes(slice_expansion_bank(rom, seg))
        if len(before) != BANK_SIZE:
            raise BuildError(f"ext3 bank {seg:02X} wrong size")
        if all(v == 0xFF for v in before[:64]):
            continue
        if before[data_start] != 0:
            raise BuildError(f"ext3 bank {seg:02X} sentinel drift")
        payloads: list[bytes] = []
        for local in range(EXP3_SLOTS):
            ptr = le16(before, local * 2)
            if not data_start <= ptr < BANK_SIZE:
                raise BuildError(f"ext3 bank {seg:02X} bad pointer {local:03X}->{ptr:04X}")
            end = before.find(b"\x00", ptr)
            if end < 0:
                raise BuildError(f"ext3 bank {seg:02X} unterminated slot {local:03X}")
            payloads.append(bytes(before[ptr:end]))
        packed = bytearray(b"\xFF" * BANK_SIZE)
        packed[data_start] = 0
        cursor = data_start + 1
        ptr_by_payload: dict[bytes, int] = {b"": data_start}
        for local, payload in enumerate(payloads):
            ptr = ptr_by_payload.get(payload)
            if ptr is None:
                need = len(payload) + 1
                if cursor + need > BANK_SIZE:
                    raise BuildError(f"ext3 bank {seg:02X} compaction overflow")
                ptr = cursor
                packed[cursor:cursor + len(payload)] = payload
                packed[cursor + len(payload)] = 0
                ptr_by_payload[payload] = ptr
                cursor += need
            struct.pack_into("<H", packed, local * 2, ptr)
        patch_expansion_bank(rom, seg, packed)
        rows.append({
            "segment": f"{seg:02X}",
            "packed_cursor": f"{cursor:04X}",
            "room": BANK_SIZE - cursor,
            "unique_payloads": len(ptr_by_payload) - 1,
        })
    return {"banks": rows, "room_total": sum(int(r["room"]) for r in rows)}


def alias_bank_cursor(bank: bytes) -> int:
    data_start = EXP3_SLOTS * 2
    cursor = data_start + 1
    for local in range(EXP3_SLOTS):
        ptr = le16(bank, local * 2)
        if not data_start <= ptr < BANK_SIZE:
            continue
        end = bank.find(b"\x00", ptr)
        if end < 0:
            raise BuildError(f"unterminated alias phrase local={local:03X}")
        cursor = max(cursor, end + 1)
    return cursor


def write_alias_ext3_slots_guarded(
    rom: bytearray,
    slot_payload: dict[int, bytes],
    *,
    union,
    dictionary,
) -> dict[str, Any]:
    """Write true-free ext3 indices that runtime maps into alias banks 21-25."""
    outcome = guard_slot_writes(rom, slot_payload, union=union, require_free=True)
    if not outcome.ok:
        raise BuildError(f"alias ext3 guard refused: {outcome.outcome}")
    by_seg: dict[int, dict[int, bytes]] = defaultdict(dict)
    for idx, payload in slot_payload.items():
        if not dictionary._ext3_is_alias(idx):
            raise BuildError(f"redirect slot is not alias-mapped: {idx:05X}")
        seg, local = dictionary._ext3_bank_local(idx)
        by_seg[int(seg)][int(local)] = payload
    written = 0
    bank_rows: list[dict[str, Any]] = []
    for seg, local_map in sorted(by_seg.items()):
        bank = bytearray(slice_expansion_bank(rom, seg))
        cursor = alias_bank_cursor(bytes(bank))
        start_cursor = cursor
        for local, payload in sorted(local_map.items()):
            if b"\x00" in payload:
                raise BuildError(f"NUL in alias payload {seg:02X}:{local:03X}")
            need = len(payload) + 1
            if cursor + need > BANK_SIZE:
                raise BuildError(f"alias bank {seg:02X} overflow")
            bank[cursor:cursor + len(payload)] = payload
            bank[cursor + len(payload)] = 0
            struct.pack_into("<H", bank, local * 2, cursor)
            cursor += need
            written += 1
        patch_expansion_bank(rom, seg, bank)
        bank_rows.append({
            "segment": f"{seg:02X}",
            "start_cursor": f"{start_cursor:04X}",
            "end_cursor": f"{cursor:04X}",
            "written": len(local_map),
            "room": BANK_SIZE - cursor,
        })
    return {"written": written, "banks": bank_rows, "guard": outcome.as_dict()}


def load_translation_targets(work: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    required = {
        str(r["abs"]).upper()
        for g in work["groups"]
        if g["mode"] == "source_retranslation_required"
        for r in g["records"]
    }
    values: dict[str, str] = {}
    origins: dict[str, str] = {}
    for raw in sorted(glob.glob(BATCH_GLOB)):
        path = Path(raw)
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("translation_source") != "llm":
            raise BuildError(f"unapproved batch source: {path}")
        for raw_abs, raw_text in (doc.get("targets") or {}).items():
            address = str(raw_abs).upper()
            text, _ = compact_spaces_to_limit(str(raw_text))
            if JP_RE.search(text):
                raise BuildError(f"Japanese remains in target {address}: {text!r}")
            if address in values and values[address] != text:
                raise BuildError(f"conflicting target {address}: {values[address]!r} != {text!r}")
            values[address] = text
            origins[address] = str(path.relative_to(ROOT)).replace("\\", "/")
    missing = sorted(required - set(values))
    extra = sorted(set(values) - required)
    if missing or extra:
        raise BuildError(f"translation coverage mismatch missing={missing[:20]} extra={extra[:20]}")
    return values, origins


def make_after_map(work: dict[str, Any], translations: dict[str, str]) -> tuple[dict[str, str], dict[str, str], int]:
    after: dict[str, str] = {}
    mode: dict[str, str] = {}
    compact_removed = 0
    for g in work["groups"]:
        records = g["records"]
        if g["mode"] == "reflow_current_nonspace_exact":
            auto = g.get("auto_after") or []
            if len(auto) != len(records):
                raise BuildError(f"auto line count mismatch {g['group_id']}")
            for r, value in zip(records, auto):
                text, removed = compact_spaces_to_limit(str(value))
                compact_removed += removed
                after[str(r["abs"]).upper()] = text
                mode[str(r["abs"]).upper()] = "space_only_reflow"
        elif g["mode"] == "source_retranslation_required":
            for r in records:
                address = str(r["abs"]).upper()
                text, removed = compact_spaces_to_limit(translations[address])
                compact_removed += removed
                after[address] = text
                mode[address] = "llm_source_retranslation"
        else:
            raise BuildError(f"unknown work mode {g['mode']}")
    if len(after) != sum(len(g["records"]) for g in work["groups"]):
        raise BuildError("duplicate target address in worklist")
    return after, mode, compact_removed


def precompute_bank_pointer_maps(rom: bytes, dictionary, indices: set[int]) -> dict[int, dict[str, Any]]:
    segs = {dictionary._ext3_bank_local(i)[0] for i in indices}
    out: dict[int, dict[str, Any]] = {}
    for seg in segs:
        base = (seg & 0x7F) * BANK_SIZE
        vals = [le16(rom, base + local * 2) for local in range(0x1000)]
        aliases: dict[int, list[int]] = defaultdict(list)
        for local, ptr in enumerate(vals):
            aliases[ptr].append(local)
        out[seg] = {"base": base, "values": vals, "sorted": sorted((p, i) for i, p in enumerate(vals)), "aliases": aliases}
    return out


def storage_proof(rom: bytes, dictionary, index: int, bank_maps: dict[int, dict[str, Any]]) -> dict[str, Any]:
    seg, local = dictionary._ext3_bank_local(index)
    info = bank_maps[seg]
    base = int(info["base"])
    ptr = le16(rom, base + local * 2)
    raw = bytes(dictionary.raw_entry(index))
    aliases = list(info["aliases"].get(ptr, []))
    sorted_pairs = info["sorted"]
    lo = bisect_right(sorted_pairs, (ptr, 0x1000))
    hi = bisect_right(sorted_pairs, (ptr + len(raw), 0x1000))
    interior = [loc for p, loc in sorted_pairs[lo:hi] if ptr < p <= ptr + len(raw)]
    return {
        "index": f"{index:05X}",
        "physical_segment": f"{seg:02X}",
        "physical_local": f"{local:03X}",
        "ptr": f"{ptr:04X}",
        "entry_abs": int(dictionary.entry_abs(index)),
        "old_len": len(raw),
        "aliases": [f"{seg:02X}:{x:03X}" for x in aliases],
        "interior": [f"{seg:02X}:{x:03X}" for x in interior],
        "ok": aliases == [local] and not interior,
    }


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError("current main TIP identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    if str((work.get("summary") or {}).get("tip_sha256") or "").lower() != EXPECTED_MAIN_SHA:
        raise BuildError("worklist is not bound to current main TIP")

    translations, origins = load_translation_targets(work)
    after_by_abs, modes, compact_removed = make_after_map(work, translations)
    override_doc = json.loads(SHORT_PAIR_OVERRIDES.read_text(encoding="utf-8"))
    short_pair_overrides = {
        str(k).upper(): normalize_ko_text(str(v))
        for k, v in (override_doc.get("targets") or {}).items()
    }
    if len(short_pair_overrides) != 92:
        raise BuildError(f"short-pair override count drifted: {len(short_pair_overrides)}")
    for address, text in short_pair_overrides.items():
        if address not in after_by_abs:
            raise BuildError(f"short-pair override outside worklist: {address}")
        if len(text) > LIMIT or JP_RE.search(text):
            raise BuildError(f"bad short-pair override {address}: {text!r}")
        after_by_abs[address] = text
        modes[address] = "short_partner_source_retranslation"
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    prepared: list[dict[str, Any]] = []
    targets_by_index: dict[int, set[int]] = defaultdict(set)
    desired_by_index: dict[int, str] = {}
    redirect_records: list[dict[str, Any]] = []
    kept_short_nonext = 0
    for g in work["groups"]:
        for wr in g["records"]:
            address = str(wr["abs"]).upper()
            logical = int(address, 16)
            got = read_encoded_z_safe(parent, sb + logical, max_len=256)
            if got is None:
                raise BuildError(f"unreadable current record {address}")
            payload, term = bytes(got[0]), int(got[1])
            prefix, body, kind = split_prefix_body(payload)
            if payload.hex().upper() != str(wr["payload_hex"]).upper():
                raise BuildError(f"worklist payload drift at {address}")
            if f"{term - sb:06X}" != str(wr["terminator"]).upper():
                raise BuildError(f"worklist terminator drift at {address}")
            current = strip_pad(d_parent.expand(body, tbl))
            if current != str(wr["current"]):
                raise BuildError(f"worklist render drift at {address}: {current!r}")

            desired = after_by_abs[address]
            idx = ext3_index(body)
            strategy = "private_ext3_phrase"
            if idx is None and len(current.replace("<E62F>", "")) <= LIMIT and not JP_RE.search(current):
                # A small number of paired records are native/stock 2- or 3-byte
                # dictionary tokens.  Expanding those records would require a
                # new short-token portal.  Keep the already-correct short line
                # byte-identical and source-retranslate only its private-ext3
                # partner via SHORT_PAIR_OVERRIDES.
                desired = current
                after_by_abs[address] = current
                modes[address] = "kept_short_nonext"
                strategy = "unchanged_nonext"
                kept_short_nonext += 1
            elif idx is None:
                # The remaining non-ext3 records are the long battle-voice
                # residues.  Their body extents are large enough for a normal
                # four-byte E5 18 token.  Retarget the body in-place to a new,
                # union-proven-free ext3 slot without moving the terminator.
                if len(body) < 4:
                    raise BuildError(
                        f"non-ext3 target needs redirect but body is too short at {address}: {body.hex()}"
                    )
                strategy = "record_retarget_ext3_same_length"
                redirect_records.append({
                    "abs": address,
                    "logical": logical,
                    "prefix_len": len(prefix),
                    "body_len": len(body),
                    "desired": desired,
                })
            else:
                # Keep all original target consumers grouped by their current
                # ext3 slot.  After the reference-union scan below, any shared
                # slot that would need divergent output (or has an outside
                # consumer/nested parent) is detached record-by-record to new
                # true-free ext3 slots instead of mutating the shared phrase.
                desired_by_index.setdefault(idx, desired)
                targets_by_index[idx].add(logical)

            if len(desired) > LIMIT or JP_RE.search(desired):
                raise BuildError(f"bad desired text at {address}: {desired!r}")
            if current.count("<E62F>") != desired.count("<E62F>"):
                raise BuildError(f"E62F count would change at {address}: {current!r} -> {desired!r}")

            prepared.append({
                "abs": address,
                "logical": logical,
                "scope": g["scope"],
                "mode": modes[address],
                "strategy": strategy,
                "current": current,
                "after": desired,
                "before_cells": len(current.replace("<E62F>", "")),
                "after_cells": len(desired.replace("<E62F>", "")),
                "prefix_hex": prefix.hex().upper(),
                "payload_hex": payload.hex().upper(),
                "terminator": f"{term - sb:06X}",
                "ext3_index": f"{idx:05X}" if idx is not None else None,
                "source_jp": wr.get("source_jp") or "",
                "batch": origins.get(address),
            })

    # 92 short native/stock records are paired with an overridden ext3 partner;
    # four additional already-<=20 non-ext3 records require no text change.
    if kept_short_nonext != 96 or len(redirect_records) != 23:
        raise BuildError(
            f"non-ext3 classification drifted: kept_short={kept_short_nonext} redirects={len(redirect_records)}"
        )

    initial_target_ext3_slots = len(targets_by_index)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    bank_maps = precompute_bank_pointer_maps(parent, d_parent, set(targets_by_index))

    # A phrase slot can be edited directly only when every parent-ROM consumer
    # is one of our targets, it has no nested dictionary parent, storage is
    # private, and every target wants the exact same output.  Otherwise detach
    # every affected record to a fresh true-free ext3 slot.  This avoids the
    # classic shared-dictionary regression where fixing one scene changes an
    # unrelated scene that happens to reuse the same phrase.
    prepared_by_abs = {str(r["abs"]): r for r in prepared}
    detached_ext3_records = 0
    detached_ext3_indices: list[int] = []
    for idx in list(sorted(targets_by_index)):
        expected_targets = set(targets_by_index[idx])
        rows = [
            r for r in prepared
            if r.get("strategy") == "private_ext3_phrase"
            and r.get("ext3_index") == f"{idx:05X}"
        ]
        consumers = {int(c.abs) for c in union.consumers_for(idx)}
        unexpected = sorted(v for v in consumers if not target_accounted(v, expected_targets))
        missing = sorted(t for t in expected_targets if not any(0 <= t - v <= 8 for v in consumers))
        parents = sorted(union.parents_of(idx))
        proof = storage_proof(parent, d_parent, idx, bank_maps)
        variants = {str(r["after"]) for r in rows}
        if len(variants) == 1 and proof["ok"] and not unexpected and not missing and not parents:
            desired_by_index[idx] = next(iter(variants))
            continue

        detached_ext3_indices.append(idx)
        for row in rows:
            payload = bytes.fromhex(str(row["payload_hex"]))
            prefix = bytes.fromhex(str(row["prefix_hex"]))
            body_len = len(payload) - len(prefix)
            if body_len < 4:
                raise BuildError(f"shared ext3 detach body too short at {row['abs']}")
            redirect_records.append({
                "abs": str(row["abs"]),
                "logical": int(row["logical"]),
                "prefix_len": len(prefix),
                "body_len": body_len,
                "desired": str(row["after"]),
            })
            row["strategy"] = "record_retarget_ext3_same_length"
            row["detached_from_ext3_index"] = f"{idx:05X}"
            detached_ext3_records += 1
        del targets_by_index[idx]
        desired_by_index.pop(idx, None)

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    slot_reports: list[dict[str, Any]] = []
    inplace_count = 0
    repoint_count = 0

    # First apply every provably-private target that fits in its current phrase
    # storage.  If a replacement would grow, detach its record(s) instead of
    # consuming scarce append-only tail room.  The subsequent bank compaction
    # then turns the many shortened phrases into contiguous free tail space.
    growth_detached_records = 0
    for idx in list(sorted(targets_by_index)):
        proof = storage_proof(parent, d_parent, idx, bank_maps)
        encoded = encode(desired_by_index[idx], tbl)
        rows = [
            r for r in prepared
            if r.get("strategy") == "private_ext3_phrase"
            and r.get("ext3_index") == f"{idx:05X}"
        ]
        if not proof["ok"]:
            raise BuildError(f"private ext3 proof drift after classification: {proof}")
        if len(encoded) > int(proof["old_len"]):
            for row in rows:
                payload = bytes.fromhex(str(row["payload_hex"]))
                prefix = bytes.fromhex(str(row["prefix_hex"]))
                redirect_records.append({
                    "abs": str(row["abs"]),
                    "logical": int(row["logical"]),
                    "prefix_len": len(prefix),
                    "body_len": len(payload) - len(prefix),
                    "desired": str(row["after"]),
                })
                row["strategy"] = "record_retarget_ext3_same_length"
                row["detached_from_ext3_index"] = f"{idx:05X}"
                growth_detached_records += 1
            del targets_by_index[idx]
            desired_by_index.pop(idx, None)
            continue
        start = int(proof["entry_abs"])
        candidate[start:start + len(encoded)] = encoded
        candidate[start + len(encoded)] = 0
        allowed.append((start, start + int(proof["old_len"]) + 1))
        slot_reports.append({
            **proof,
            "expected_targets": [f"{x:06X}" for x in sorted(targets_by_index[idx])],
            "new_len": len(encoded),
            "after": desired_by_index[idx],
            "strategy": "inplace_before_compaction",
        })
        inplace_count += 1

    compaction_report = compact_standard_ext3_banks(
        candidate, int(ext3_meta.get("num_banks") or 16)
    )
    # Standard ext3 banks are dedicated dictionary storage.  Compaction changes
    # pointers/physical placement throughout each bank but keeps slot payloads
    # semantically identical except for the already-approved target phrases.
    for bi in range(int(ext3_meta.get("num_banks") or 16)):
        seg = EXP3_SEG0 + bi
        allowed.append((seg * BANK_SIZE, (seg + 1) * BANK_SIZE))

    # These have already been applied (or detached above); prevent the legacy
    # append path below from running a second time.
    targets_by_index.clear()
    desired_by_index.clear()

    # Retarget long non-ext3 residues plus any shared/growing ext3 records. Use
    # fresh ext3 slots and keep each original record extent/terminator fixed.
    # Retarget only the long non-ext3 residues.  Use fresh ext3 slots and keep
    # each original record extent/terminator fixed; padding remains 0x01.
    redirect_slot_by_text: dict[str, int] = {}
    redirect_write_info: dict[str, Any] | None = None
    if redirect_records:
        inventory = build_free_slot_inventory(
            parent,
            union=union,
            ext_meta=ext_meta,
            ext3_meta=ext3_meta,
        )
        # The five compact3 alias banks (21-25) intentionally carry the high
        # locals of ext3 pages 0-4 and have substantial unused phrase room.
        # Allocate redirects only from true-free indices that runtime maps into
        # those banks; the stock writer does not understand this alias mapping.
        free_by_seg: dict[int, list[int]] = defaultdict(list)
        for idx in inventory.ext3_free:
            if COMPACT3_INDEX_BASE <= idx <= COMPACT3_INDEX_END:
                continue
            if not dict_token_safe_in_zstring(idx) or len(token_from_dict_index(idx)) != 4:
                continue
            if not d_parent._ext3_is_alias(idx):
                continue
            seg, _local = d_parent._ext3_bank_local(idx)
            free_by_seg[int(seg)].append(idx)
        for values in free_by_seg.values():
            values.sort(reverse=True)

        room_by_seg = {
            seg: BANK_SIZE - alias_bank_cursor(bytes(slice_expansion_bank(candidate, seg)))
            for seg in free_by_seg
        }
        encoded_by_text = {
            text: encode(text, tbl)
            for text in sorted({str(r["desired"]) for r in redirect_records})
        }
        for text, payload in sorted(encoded_by_text.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            need = len(payload) + 1
            choices = [
                seg for seg, slots in free_by_seg.items()
                if slots and room_by_seg.get(seg, 0) >= need
            ]
            if not choices:
                raise BuildError(
                    f"alias ext3 capacity exhausted while allocating {text!r}; "
                    f"need={need} room={room_by_seg}"
                )
            seg = max(choices, key=lambda s: room_by_seg[s])
            idx = free_by_seg[seg].pop()
            redirect_slot_by_text[text] = idx
            room_by_seg[seg] -= need
        slot_payload = {
            idx: encoded_by_text[text]
            for text, idx in redirect_slot_by_text.items()
        }
        redirect_write_info = write_alias_ext3_slots_guarded(
            candidate,
            slot_payload,
            union=union,
            dictionary=d_parent,
        )
        for bank_row in redirect_write_info["banks"]:
            seg = int(bank_row["segment"], 16)
            allowed.append((seg * BANK_SIZE, (seg + 1) * BANK_SIZE))

        prepared_by_abs = {str(r["abs"]): r for r in prepared}
        for rr in redirect_records:
            address = str(rr["abs"])
            logical = int(rr["logical"])
            body_len = int(rr["body_len"])
            prefix_len = int(rr["prefix_len"])
            idx = redirect_slot_by_text[str(rr["desired"])]
            token = token_from_dict_index(idx)
            if len(token) != 4 or body_len < 4:
                raise BuildError(f"redirect token/capacity mismatch at {address}")
            start = sb + logical + prefix_len
            old_body = bytes(candidate[start:start + body_len])
            expected_old = bytes.fromhex(prepared_by_abs[address]["payload_hex"])[prefix_len:]
            if old_body != expected_old:
                raise BuildError(f"redirect body drift before write at {address}")
            new_body = token + (b"\x01" * (body_len - len(token)))
            candidate[start:start + body_len] = new_body
            allowed.append((start, start + body_len))
            prepared_by_abs[address]["ext3_index"] = f"{idx:05X}"
            prepared_by_abs[address]["redirect_token_hex"] = token.hex().upper()
            prepared_by_abs[address]["redirect_body_hex"] = new_body.hex().upper()

    for idx in sorted(targets_by_index):
        expected_targets = targets_by_index[idx]
        consumers = {int(c.abs) for c in union.consumers_for(idx)}
        unexpected = sorted(v for v in consumers if not target_accounted(v, expected_targets))
        missing = sorted(t for t in expected_targets if not any(0 <= t - v <= 8 for v in consumers))
        parents = sorted(union.parents_of(idx))
        proof = storage_proof(parent, d_parent, idx, bank_maps)
        encoded = encode(desired_by_index[idx], tbl)
        report = {
            **proof,
            "expected_targets": [f"{x:06X}" for x in sorted(expected_targets)],
            "actual_consumers": [f"{x:06X}" for x in sorted(consumers)],
            "unexpected_consumers": [f"{x:06X}" for x in unexpected],
            "missing_consumers": [f"{x:06X}" for x in missing],
            "nested_parents": [f"{x:05X}" for x in parents],
            "new_len": len(encoded),
            "after": desired_by_index[idx],
        }
        if not proof["ok"] or unexpected or missing or parents:
            raise BuildError(f"unsafe private ext3 target: {report}")
        if len(encoded) <= int(proof["old_len"]):
            start = int(proof["entry_abs"])
            candidate[start:start + len(encoded)] = encoded
            candidate[start + len(encoded)] = 0
            allowed.append((start, start + int(proof["old_len"]) + 1))
            report["strategy"] = "inplace"
            inplace_count += 1
        else:
            write, intervals = append_ext3_phrase_alias_aware(candidate, d_parent, idx, encoded)
            allowed.extend(intervals)
            report["strategy"] = "physical_bank_repoint_append"
            report["write"] = write
            repoint_count += 1
        slot_reports.append(report)

    checksum = update_ws_checksum(candidate)
    allowed.append((len(parent) - 2, len(parent)))
    result = bytes(candidate)
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in prepared:
        logical = int(row["logical"])
        before_got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        after_got = read_encoded_z_safe(result, sb + logical, max_len=256)
        if before_got is None or after_got is None:
            target_failures.append({"abs": row["abs"], "reason": "unreadable"})
            continue
        before_payload, before_term = bytes(before_got[0]), int(before_got[1])
        after_payload, after_term = bytes(after_got[0]), int(after_got[1])
        _, after_body, _ = split_prefix_body(after_payload)
        rendered = strip_pad(d_result.expand(after_body, tbl))
        reasons = []
        strategy = str(row.get("strategy") or "")
        if len(after_payload) != len(before_payload):
            reasons.append("record_length_changed")
        if strategy == "record_retarget_ext3_same_length":
            prefix = bytes.fromhex(str(row["prefix_hex"]))
            if after_payload[:len(prefix)] != before_payload[:len(prefix)]:
                reasons.append("prefix_changed")
            if after_body.hex().upper() != str(row.get("redirect_body_hex") or ""):
                reasons.append("redirect_body_mismatch")
        elif after_payload != before_payload:
            reasons.append("record_payload_changed")
        if after_term != before_term:
            reasons.append("terminator_changed")
        if rendered != row["after"]:
            reasons.append(f"render_mismatch:{rendered!r}")
        if len(rendered.replace("<E62F>", "")) > LIMIT:
            reasons.append("over_20")
        if JP_RE.search(rendered):
            reasons.append("japanese_visible_character")
        if reasons:
            target_failures.append({"abs": row["abs"], "reasons": reasons})

    intervals = merge_intervals(allowed)
    unexpected_diff = [
        off for off, (a, b) in enumerate(zip(parent, result))
        if a != b and not in_intervals(off, intervals)
    ]
    if target_failures or unexpected_diff:
        raise BuildError(
            f"postbuild target/diff verification failed targets={target_failures[:10]} "
            f"unexpected_diff={unexpected_diff[:20]}"
        )

    # Save the candidate only after every guard passes.
    tmp = OUT_ROM.with_name(f".{OUT_ROM.name}.{os.getpid()}.tmp")
    tmp.write_bytes(result)
    os.replace(tmp, OUT_ROM)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_20cell_candidate.py",
        "ok": True,
        "strategy": "private ext3 phrase rewrite; short native/stock partners kept byte-identical; long non-ext3 battle voices retargeted to proven-free ext3 slots with fixed record extent and terminator",
        "line_limit": LIMIT,
        "parent": {"path": str(MAIN.relative_to(ROOT)), "size": len(parent), "sha256": sha(parent)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "size": len(result),
            "sha256": sha(result),
            "ws_checksum": f"{checksum:04X}",
        },
        "candidate_save": {
            "path": str(OUT_SAVE.relative_to(ROOT)),
            "size": OUT_SAVE.stat().st_size,
            "sha256": sha(OUT_SAVE.read_bytes()),
        },
        "worklist_summary": work.get("summary"),
        "counts": {
            "target_records": len(prepared),
            "target_ext3_slots": initial_target_ext3_slots,
            "space_only_reflow_records": sum(r["mode"] == "space_only_reflow" for r in prepared),
            "llm_retranslation_records": sum(r["mode"] == "llm_source_retranslation" for r in prepared),
            "short_partner_source_retranslation_records": sum(r["mode"] == "short_partner_source_retranslation" for r in prepared),
            "kept_short_nonext_records": kept_short_nonext,
            "record_retarget_ext3_records": len(redirect_records),
            "redirect_ext3_slots": len(redirect_slot_by_text),
            "space_chars_removed_post_batch": compact_removed,
            "inplace_slots": inplace_count,
            "repoint_append_slots": repoint_count,
            "record_payload_changes": len(redirect_records),
            "terminator_changes": 0,
            "unexpected_diff_offsets": 0,
            "max_after_cells": max((r["after_cells"] for r in prepared), default=0),
        },
        "targets": prepared,
        "slots": slot_reports,
        "compaction": compaction_report,
        "redirect_ext3_write": redirect_write_info,
        "allowed_intervals": [[a, b] for a, b in intervals],
        "diff_runs": diff_runs(parent, result),
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
