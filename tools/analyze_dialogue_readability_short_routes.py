#!/usr/bin/env python3
"""Analyze safe routes for readability targets whose body is <4 bytes.

Read-only. The accepted runtime keeps compact3 disabled, so these records must
stay on ordinary 2-byte dictionary tokens (optionally plus 0x01 padding).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_dialogue_20cell_candidate import encode, ext3_index
from extract_script import split_prefix_body
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    iter_token_refs_with_offsets,
)
from monoeye_rom import BANK_SIZE, Tbl, dict_token_safe_in_zstring, le16, token_from_dict_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
WORKLIST = ROOT / "out/script/dialogue_readability_worklist.json"
CATALOG = ROOT / "out/script/dialogue_readability_changes.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/script/dialogue_readability_short_routes.json"


def strip(text: str) -> str:
    return text.rstrip("　 \t")


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)

    source_groups = {
        str(g["group_id"]): g
        for key in ("semantic_rewrite_groups", "word_boundary_reflow_only_groups")
        for g in work.get(key) or []
    }
    after = {
        (str(g["group_id"]), i): text
        for g in catalog.get("groups") or []
        for i, text in enumerate(g["after_rows"])
    }

    two_byte_exact: dict[str, list[int]] = defaultdict(list)
    for index in range(min(dictionary.count, 0x1000)):
        if not dict_token_safe_in_zstring(index):
            continue
        token = token_from_dict_index(index)
        if len(token) != 2:
            continue
        try:
            text = strip(dictionary.expand(dictionary.raw_entry(index), tbl))
        except Exception:
            continue
        two_byte_exact[text].append(index)

    ext_seg = int(str(ext_meta.get("ext_seg") or "10"), 16)
    ext_base = ext_seg * BANK_SIZE
    ext_ptr_off = int(ext_meta.get("ext_ptr_off") or 0)
    stock_count = int(ext_meta.get("stock_count") or 3831)
    slot_count = int(ext_meta.get("slot_count") or 265)
    ext_ptrs = [le16(parent, ext_base + ext_ptr_off + i * 2) for i in range(slot_count)]

    rows = []
    changed = 0
    for gid, group in source_groups.items():
        records = group.get("records") or []
        for i, record in enumerate(records):
            payload = bytes.fromhex(str(record["current_payload_hex"]))
            _prefix, body, _kind = split_prefix_body(payload)
            if ext3_index(body) is not None:
                continue
            desired = str(after[(gid, i)])
            current = str(record["current_main"])
            if desired == current:
                continue
            changed += 1
            refs = list(iter_token_refs_with_offsets(body))
            if len(refs) != 1 or refs[0][1] != 2:
                rows.append({
                    "group_id": gid,
                    "row": i,
                    "abs": str(record["abs"]).upper(),
                    "current": current,
                    "desired": desired,
                    "body_hex": body.hex().upper(),
                    "route": "unsupported_non_single_2byte_token",
                })
                continue
            index, _length, token_offset = refs[0]
            consumers = sorted(int(c.abs) for c in union.consumers_for(index))
            parents = sorted(union.parents_of(index))
            encoded = encode(desired, tbl)
            exact = [
                idx for idx in two_byte_exact.get(desired, [])
                if dict_token_safe_in_zstring(idx)
            ]

            physical = None
            if stock_count <= index < stock_count + slot_count:
                local = index - stock_count
                ptr = ext_ptrs[local]
                old_payload = bytes(dictionary.raw_entry(index))
                aliases = [stock_count + n for n, value in enumerate(ext_ptrs) if value == ptr]
                interior = [
                    stock_count + n for n, value in enumerate(ext_ptrs)
                    if ptr < value <= ptr + len(old_payload)
                ]
                physical = {
                    "entry_abs": f"{int(dictionary.entry_abs(index)):07X}",
                    "pointer": f"{ptr:04X}",
                    "old_len": len(old_payload),
                    "new_len": len(encoded),
                    "aliases": [f"{x:04X}" for x in aliases],
                    "interior": [f"{x:04X}" for x in interior],
                    "unique_storage": aliases == [index] and not interior,
                    "fits_inplace": len(encoded) <= len(old_payload),
                }

            route = "needs_new_or_keep_current"
            if exact:
                route = "reuse_existing_2byte_phrase"
            elif (
                physical
                and physical["unique_storage"]
                and physical["fits_inplace"]
                and not parents
                and consumers == [int(str(record["abs"]), 16)]
            ):
                route = "private_ext2_inplace"

            rows.append({
                "group_id": gid,
                "row": i,
                "abs": str(record["abs"]).upper(),
                "current": current,
                "desired": desired,
                "body_hex": body.hex().upper(),
                "body_len": len(body),
                "token_offset": token_offset,
                "current_index": f"{index:04X}",
                "current_index_is_extended": index >= stock_count,
                "consumers": [f"{x:06X}" for x in consumers],
                "nested_parents": [f"{x:04X}" for x in parents],
                "desired_encoded_len": len(encoded),
                "exact_existing_indices": [f"{x:04X}" for x in exact],
                "physical": physical,
                "route": route,
            })

    summary = {
        "changed_short_records": changed,
        "reuse_existing_2byte_phrase": sum(r.get("route") == "reuse_existing_2byte_phrase" for r in rows),
        "private_ext2_inplace": sum(r.get("route") == "private_ext2_inplace" for r in rows),
        "needs_new_or_keep_current": sum(r.get("route") == "needs_new_or_keep_current" for r in rows),
        "unsupported": sum(str(r.get("route", "")).startswith("unsupported") for r in rows),
        "true_free_extended_slots": [f"{x:04X}" for x in inventory.ext_free],
    }
    OUT.write_text(json.dumps({"schema_version": 1, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in rows:
        print(r["abs"], r["route"], r.get("current_index"), r["current"], "->", r["desired"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
