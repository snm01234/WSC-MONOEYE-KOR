#!/usr/bin/env python3
"""Build the post-20cell runtime screenshot follow-up candidate.

Fixes four observed issue classes from the user-approved 20-cell main:
1) stale retired-slot overwrite at 6046E4,
2) unreadable no-space 20-cell pair at 604F41/604F51,
3) dialogue hidden behind 08 F0/FA 00 speaker-control dictionary-lead collisions,
4) remaining legacy `제장` typo rows.

The main TIP is never overwritten. Record extents and NUL terminators are fixed.
Existing private ext3 phrases are changed in place when provably safe; otherwise
records are retargeted to true-free alias ext3 slots. The 3-byte 6046E4 body must
stay on the ordinary 2-byte dictionary path: runtime testing proved the compact3
E5 19 portal is not decoded by this portrait-dialogue caller. A proven-unused
extended 2-byte slot is therefore dedicated to the user-approved `어이、` phrase.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_dialogue_20cell_candidate import (
    encode,
    ext3_index,
    precompute_bank_pointer_maps,
    storage_proof,
    write_alias_ext3_slots_guarded,
)
from build_terminology_retranslation_candidate import target_accounted
from extract_script import split_prefix_body
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
)
from monoeye_rom import (
    BANK_SIZE,
    COMPACT3_INDEX_BASE,
    COMPACT3_INDEX_END,
    Tbl,
    dict_token_safe_in_zstring,
    le16,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/dialogue_runtime_followup_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/dialogue_runtime_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_runtime_followup_candidate.sav"
OUT_REPORT = ROOT / "out/patch/dialogue_runtime_followup_report.json"
EXPECTED_MAIN = "8e80bc7e722652b9c6b31282c272966ae92f9d3c82975344c577556bf5b9145a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")
SHORT_TARGET = "6046E4"
DEDICATED_EXT2_SLOT = 0x0F4D
BATTLE_PREFIX_TARGETS = {"5D84F4": bytes.fromhex("40")}
HIDDEN_COLLISION_TARGETS = {"60497E", "62A603", "6377B9", "63B55E", "63B6EB"}


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def in_intervals(off: int, intervals: list[tuple[int, int]]) -> bool:
    return any(a <= off < b for a, b in intervals)


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


def decode_record(
    rom: bytes,
    dictionary,
    tbl: Tbl,
    logical: int,
    *,
    forced_prefix: bytes | None = None,
) -> dict[str, Any]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    payload, term = bytes(got[0]), int(got[1])
    if forced_prefix is not None:
        if not payload.startswith(forced_prefix):
            raise BuildError(
                f"forced runtime prefix drift at {logical:06X}: "
                f"need={forced_prefix.hex().upper()} got={payload[:len(forced_prefix)].hex().upper()}"
            )
        prefix, body, kind = forced_prefix, payload[len(forced_prefix):], "dialogue"
    else:
        prefix, body, kind = split_prefix_body(payload)
    return {
        "payload": payload,
        "terminator": term,
        "prefix": prefix,
        "body": body,
        "kind": kind,
        "text": dictionary.expand(body, tbl).rstrip("　 "),
    }


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN:
        raise BuildError("current main TIP identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_main_sha256") or "").lower() != EXPECTED_MAIN:
        raise BuildError("follow-up spec is not bound to current main")
    rows = list(spec.get("targets") or [])
    if len(rows) != 16:
        raise BuildError(f"target count drifted: {len(rows)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    sb = stock_base(parent)

    prepared: list[dict[str, Any]] = []
    existing_indices: set[int] = set()
    for raw in rows:
        address = str(raw["abs"]).upper()
        logical = int(address, 16)
        rec = decode_record(
            parent,
            d_parent,
            tbl,
            logical,
            forced_prefix=BATTLE_PREFIX_TARGETS.get(address),
        )
        if rec["text"] != raw["before"]:
            raise BuildError(f"current render drift {address}: {rec['text']!r} != {raw['before']!r}")
        after = str(raw["after"])
        if len(after) > 20 or JP_RE.search(after):
            raise BuildError(f"invalid target text {address}: {after!r}")
        idx = ext3_index(rec["body"])
        if idx is not None:
            existing_indices.add(idx)
        prepared.append({
            **raw,
            "abs": address,
            "logical": logical,
            "before_cells": len(str(raw["before"])),
            "after_cells": len(after),
            "payload_hex": rec["payload"].hex().upper(),
            "prefix_hex": rec["prefix"].hex().upper(),
            "body_hex": rec["body"].hex().upper(),
            "body_len": len(rec["body"]),
            "terminator": f"{rec['terminator'] - sb:06X}",
            "old_ext3_index": None if idx is None else f"{idx:05X}",
        })

    bank_maps = precompute_bank_pointer_maps(parent, d_parent, existing_indices)
    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    strategies: dict[str, str] = {}
    slot_reports: list[dict[str, Any]] = []
    fresh_rows: list[dict[str, Any]] = []

    # Existing ext3 targets: safest possible in-place replacement first.
    for row in prepared:
        address = row["abs"]
        if address == SHORT_TARGET or address in HIDDEN_COLLISION_TARGETS:
            fresh_rows.append(row)
            continue
        body = bytes.fromhex(row["body_hex"])
        idx = ext3_index(body)
        if idx is None:
            fresh_rows.append(row)
            continue
        logical = int(row["logical"])
        expected = {logical}
        consumers = {int(c.abs) for c in union.consumers_for(idx)}
        unexpected = sorted(v for v in consumers if not target_accounted(v, expected))
        missing = not any(target_accounted(v, expected) for v in consumers)
        parents = sorted(union.parents_of(idx))
        proof = storage_proof(parent, d_parent, idx, bank_maps)
        encoded = encode(str(row["after"]), tbl)
        proof.update({
            "record_abs": address,
            "actual_consumers": [f"{x:06X}" for x in sorted(consumers)],
            "unexpected_consumers": [f"{x:06X}" for x in unexpected],
            "nested_parents": [f"{x:05X}" for x in parents],
            "new_len": len(encoded),
        })
        if proof["ok"] and not unexpected and not missing and not parents and len(encoded) <= int(proof["old_len"]):
            start = int(proof["entry_abs"])
            candidate[start:start + len(encoded)] = encoded
            candidate[start + len(encoded)] = 0
            allowed.append((start, start + int(proof["old_len"]) + 1))
            strategies[address] = "private_ext3_inplace"
            proof["strategy"] = strategies[address]
            slot_reports.append(proof)
        else:
            fresh_rows.append(row)
            proof["strategy"] = "detach_required"
            slot_reports.append(proof)

    # 6046E4 has only three body bytes. compact3 failed at runtime and there is
    # no existing 2-byte phrase for the user-approved `어이、`. Use one extended
    # 2-byte slot that the Original+Working reference union proves unreachable.
    # The older Hangul guard reports 5C2858 only because the byte pair FF 4D
    # occurs *inside* the ext3 token E5 18 FF 4D; it is not a 0F4D consumer.
    short_row = next(r for r in fresh_rows if r["abs"] == SHORT_TARGET)
    if not union.is_true_free(DEDICATED_EXT2_SLOT):
        raise BuildError(f"dedicated ext2 slot {DEDICATED_EXT2_SLOT:04X} is not union-free")
    if union.consumers_for(DEDICATED_EXT2_SLOT) or union.parents_of(DEDICATED_EXT2_SLOT):
        raise BuildError(f"dedicated ext2 slot {DEDICATED_EXT2_SLOT:04X} has a real consumer/parent")
    short_payload = encode(str(short_row["after"]), tbl)
    short_token = token_from_dict_index(DEDICATED_EXT2_SLOT)
    if len(short_token) != 2 or not dict_token_safe_in_zstring(DEDICATED_EXT2_SLOT):
        raise BuildError("dedicated ext2 slot does not encode as a safe 2-byte token")
    # The ext bank is full at its append cursor, but 0F4D's existing dead phrase
    # is longer than `어이、`. Prove pointer uniqueness/no interior alias and
    # rewrite that exact phrase storage in place without moving any pointer.
    ext_seg = int(str(ext_meta.get("ext_seg") or "10"), 16)
    ext_base = ext_seg * BANK_SIZE
    ext_ptr_off = int(ext_meta.get("ext_ptr_off") or 0)
    stock_count = int(ext_meta.get("stock_count") or 3831)
    slot_count = int(ext_meta.get("slot_count") or 265)
    local = DEDICATED_EXT2_SLOT - stock_count
    if not 0 <= local < slot_count:
        raise BuildError("dedicated ext2 slot outside configured extended dictionary")
    ptrs = [le16(parent, ext_base + ext_ptr_off + i * 2) for i in range(slot_count)]
    ptr = ptrs[local]
    old_payload = bytes(d_parent.raw_entry(DEDICATED_EXT2_SLOT))
    aliases = [i for i, value in enumerate(ptrs) if value == ptr]
    interior = [i for i, value in enumerate(ptrs) if ptr < value <= ptr + len(old_payload)]
    if aliases != [local] or interior:
        raise BuildError(
            f"dedicated ext2 physical alias hazard aliases={aliases} interior={interior}"
        )
    if len(short_payload) > len(old_payload):
        raise BuildError(
            f"dedicated ext2 payload grew: {len(short_payload)} > {len(old_payload)}"
        )
    entry_abs = ext_base + ptr
    if entry_abs != int(d_parent.entry_abs(DEDICATED_EXT2_SLOT)):
        raise BuildError("dedicated ext2 physical entry address drifted")
    candidate[entry_abs:entry_abs + len(short_payload)] = short_payload
    candidate[entry_abs + len(short_payload)] = 0
    allowed.append((entry_abs, entry_abs + len(old_payload) + 1))
    ext2_write = {
        "strategy": "dead_extended_phrase_inplace",
        "entry_abs": f"{entry_abs:07X}",
        "old_len": len(old_payload),
        "new_len": len(short_payload),
        "pointer": f"{ptr:04X}",
        "pointer_unchanged": True,
        "aliases": [f"{stock_count + i:04X}" for i in aliases],
        "interior": [f"{stock_count + i:04X}" for i in interior],
    }
    if int(short_row["body_len"]) != 3:
        raise BuildError(f"6046E4 body no longer exactly 3 bytes: {short_row['body_len']}")
    start = sb + int(short_row["logical"]) + len(bytes.fromhex(short_row["prefix_hex"]))
    candidate[start:start + 3] = short_token + b"\x01"
    allowed.append((start, start + 3))
    short_row["new_index"] = f"{DEDICATED_EXT2_SLOT:04X}"
    short_row["new_token_hex"] = short_token.hex().upper()
    short_row["new_body_hex"] = (short_token + b"\x01").hex().upper()
    strategies[SHORT_TARGET] = "dedicated_ext_2byte_retarget"

    # Every remaining row gets its own true-free alias slot. This also safely
    # detaches any shared/aliased existing ext3 phrase.
    fresh_rows = [r for r in fresh_rows if r["abs"] != SHORT_TARGET]
    alias_free = [
        idx for idx in inventory.ext3_free
        if d_parent._ext3_is_alias(idx)
        and not (COMPACT3_INDEX_BASE <= idx <= COMPACT3_INDEX_END)
        and dict_token_safe_in_zstring(idx)
        and len(token_from_dict_index(idx)) == 4
    ]
    if len(alias_free) < len(fresh_rows):
        raise BuildError(f"not enough alias ext3 slots: need {len(fresh_rows)} have {len(alias_free)}")
    assigned: dict[str, int] = {r["abs"]: alias_free[n] for n, r in enumerate(fresh_rows)}
    alias_payloads = {assigned[r["abs"]]: encode(str(r["after"]), tbl) for r in fresh_rows}
    before_alias = bytes(candidate)
    alias_write = write_alias_ext3_slots_guarded(
        candidate,
        alias_payloads,
        union=union,
        dictionary=d_parent,
    )
    for off, (a, b) in enumerate(zip(before_alias, candidate)):
        if a != b:
            allowed.append((off, off + 1))

    for row in fresh_rows:
        address = row["abs"]
        idx = assigned[address]
        token = token_from_dict_index(idx)
        body_len = int(row["body_len"])
        if body_len < len(token):
            raise BuildError(f"target body too short for ext3 {address}: {body_len}")
        prefix_len = len(bytes.fromhex(row["prefix_hex"]))
        start = sb + int(row["logical"]) + prefix_len
        current_body = bytes(candidate[start:start + body_len])
        expected_body = bytes.fromhex(row["body_hex"])
        if current_body != expected_body:
            raise BuildError(f"record body drift before retarget {address}")
        new_body = token + (b"\x01" * (body_len - len(token)))
        candidate[start:start + body_len] = new_body
        allowed.append((start, start + body_len))
        row["new_index"] = f"{idx:05X}"
        row["new_token_hex"] = token.hex().upper()
        row["new_body_hex"] = new_body.hex().upper()
        strategies[address] = "true_free_alias_ext3_retarget"

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    for row in prepared:
        logical = int(row["logical"])
        forced_prefix = BATTLE_PREFIX_TARGETS.get(row["abs"])
        before_rec = decode_record(parent, d_parent, tbl, logical, forced_prefix=forced_prefix)
        after_rec = decode_record(result, d_result, tbl, logical, forced_prefix=forced_prefix)
        reasons: list[str] = []
        if after_rec["terminator"] != before_rec["terminator"]:
            reasons.append("terminator_changed")
        if len(after_rec["payload"]) != len(before_rec["payload"]):
            reasons.append("record_extent_changed")
        if after_rec["prefix"] != before_rec["prefix"]:
            reasons.append("prefix_changed")
        if after_rec["text"] != row["after"]:
            reasons.append(f"render_mismatch:{after_rec['text']!r}")
        if len(after_rec["text"]) > 20:
            reasons.append("over_20")
        if JP_RE.search(after_rec["text"]):
            reasons.append("japanese_remains")
        if reasons:
            failures.append({"abs": row["abs"], "reasons": reasons})

    # All quality-source rows that still spelled 젠장 as 제장 must now render clean.
    quality = json.loads((ROOT / "out/script/translations_quality_all.json").read_text(encoding="utf-8"))
    typo_addresses = [
        str(r["abs"]).upper() for r in quality.get("lines") or []
        if r.get("kind") == "dialogue" and "제장" in str(r.get("ko") or "")
    ]
    typo_residuals = []
    for address in typo_addresses:
        rec = decode_record(result, d_result, tbl, int(address, 16))
        if "제장" in rec["text"]:
            typo_residuals.append({"abs": address, "text": rec["text"]})
    if typo_residuals:
        failures.append({"reason": "제장_residuals", "rows": typo_residuals})

    intervals = merge_intervals(allowed)
    unexpected = [
        off for off, (a, b) in enumerate(zip(parent, result))
        if a != b and not in_intervals(off, intervals)
    ]
    if unexpected:
        failures.append({"reason": "unexpected_diff_offsets", "sample": [f"{x:07X}" for x in unexpected[:30]]})
    if failures:
        raise BuildError(json.dumps(failures[:20], ensure_ascii=False, indent=2))

    tmp = OUT_ROM.with_name(f".{OUT_ROM.name}.{os.getpid()}.tmp")
    tmp.write_bytes(result)
    os.replace(tmp, OUT_ROM)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    for row in prepared:
        row["strategy"] = strategies[row["abs"]]
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_runtime_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_test_required",
        "parent": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": len(parent), "sha256": sha(parent)},
        "candidate": {
            "path": "out/patch/dialogue_runtime_followup_candidate.wsc",
            "size": len(result),
            "sha256": sha(result),
            "ws_checksum": f"{checksum:04X}",
        },
        "candidate_save": {
            "path": "sram/dialogue_runtime_followup_candidate.sav",
            "size": OUT_SAVE.stat().st_size,
            "sha256": sha(OUT_SAVE.read_bytes()),
        },
        "counts": {
            "targets": len(prepared),
            "hidden_collision_targets": len(HIDDEN_COLLISION_TARGETS),
            "legacy_제장_source_rows": len(typo_addresses),
            "legacy_제장_remaining_after": len(typo_residuals),
            "dedicated_ext_2byte_retarget": sum(v == "dedicated_ext_2byte_retarget" for v in strategies.values()),
            "alias_ext3_retargets": sum(v == "true_free_alias_ext3_retarget" for v in strategies.values()),
            "private_ext3_inplace": sum(v == "private_ext3_inplace" for v in strategies.values()),
            "terminator_changes": 0,
            "unexpected_diff_offsets": 0,
            "max_after_cells": max(len(str(r["after"])) for r in prepared),
        },
        "short_ext2_slot": {
            "slot": f"{DEDICATED_EXT2_SLOT:04X}",
            "token_hex": short_token.hex().upper(),
            "phrase": str(short_row["after"]),
            "union_true_free": True,
            "guard_false_hit_explanation": "5C2858 contains E5 18 FF 4D, so its FF 4D bytes are ext3 index bytes rather than a 0F4D two-byte token consumer",
            "write": ext2_write,
            "runtime_reason": "compact3 E5 19 rendered as kanji in 6046E4 portrait dialogue; keep this caller on the ordinary two-byte dictionary path",
        },
        "alias_write": alias_write,
        "targets": prepared,
        "slot_proofs": slot_reports,
        "allowed_intervals": [[a, b] for a, b in intervals],
        "diff_runs": diff_runs(parent, result),
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
