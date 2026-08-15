#!/usr/bin/env python3
"""Dump metadata-5D E5 18 battle records: slot payload, Korean, unique phrases."""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_battle_dialogue_runtime_integrated_cleanup_candidate import clean, visible_japanese
from monoeye_rom import (
    dict_index_from_ext3_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
INV = ROOT / "out/script/battle_dialogue_speaker_portrait_metadata_inventory.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/_heero_battle_5d_slots.json"

from monoeye_rom import Tbl


def main() -> int:
    parent = bytes(load_rom(MAIN))
    tbl = Tbl.load(TBL_PATH)
    ext3 = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(parent)
    print("alias_pages", ext3.ext3_alias_page_count, "ext3_count", ext3.ext3_count, "ext3_banks", ext3.ext3_banks)

    targets = []
    skipped = Counter()
    with INV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("metadata_hex") or "").upper() != "5D":
                continue
            if row.get("classification") != "battle_voice_structured":
                skipped["not_structured"] += 1
                continue
            if row.get("safe_structure_exact") != "yes":
                skipped["unsafe"] += 1
                continue
            logical = int(row["record_start"], 16)
            rec = read_encoded_z_safe(parent, sb + logical, max_len=128)
            if rec is None:
                skipped["no_record"] += 1
                continue
            live, term = rec
            live_b = bytes(live)
            if live_b.startswith(b"\x5D\xE5\x18"):
                body = live_b[1:]
                kind = "meta_then_e518"
            elif live_b.startswith(b"\xE5\x18"):
                body = live_b
                kind = "body_only"
            else:
                skipped["not_e518"] += 1
                continue
            if len(body) < 4 or any(value != 0x01 for value in body[4:]):
                skipped["not_token_pad"] += 1
                continue
            index = dict_index_from_ext3_token(*body[:4])
            try:
                raw = bytes(ext3.raw_entry(index))
                text = clean(ext3.expand(raw, tbl))
            except Exception as exc:
                raw = b""
                text = f"<ERR:{type(exc).__name__}:{exc}>"
            targets.append({
                "abs": f"{logical:06X}",
                "kind": kind,
                "live_hex": live_b.hex().upper(),
                "body_len": len(body),
                "index": f"{index:05X}",
                "raw_hex": raw.hex().upper()[:80],
                "raw_len": len(raw),
                "text": text,
                "jp_visible": visible_japanese(text),
                "has_e518_in_raw": b"\xE5\x18" in raw,
            })

    phrases = Counter(r["text"] for r in targets)
    print("targets", len(targets), "unique", len(phrases), "skipped", dict(skipped))
    print("kinds", Counter(r["kind"] for r in targets))
    empty = [r["abs"] for r in targets if not r["text"] or r["text"].startswith("<")]
    print("empty_or_err", len(empty), empty[:20])
    print("sample_phrases")
    for text, n in phrases.most_common(15):
        print(f"  {n:3d}  {text!r}")

    inv_text: dict[str, str] = {}
    struct_inv = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
    with struct_inv.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            inv_text[row["record_start"].upper()] = (row.get("current_render") or "").rstrip("\u3000 \t")
    for row in targets:
        row["inventory_ko"] = inv_text.get(row["abs"], "")
    inv_phrases = Counter(r["inventory_ko"] for r in targets)
    print("inventory_unique", len(inv_phrases), "inventory_empty", sum(1 for r in targets if not r["inventory_ko"]))
    for text, n in inv_phrases.most_common():
        print(f"  {n:3d}  {text}")

    OUT.write_text(json.dumps({
        "alias_pages": ext3.ext3_alias_page_count,
        "ext3_count": ext3.ext3_count,
        "skipped": dict(skipped),
        "target_count": len(targets),
        "unique_count": len(phrases),
        "empty_or_err": empty,
        "inventory_unique": len(inv_phrases),
        "phrases": [{"text": t, "n": n} for t, n in phrases.most_common()],
        "inventory_phrases": [{"text": t, "n": n} for t, n in inv_phrases.most_common()],
        "targets": targets,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
