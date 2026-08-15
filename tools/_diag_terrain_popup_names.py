#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import NAME75_RANGES, _walk_zstring_range  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIG = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3 = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/_terrain_name_diag.json"

NEEDLES = (
    "宇宙",
    "ア・バオア",
    "아 바오아",
    "아・바오아",
    "우주",
    "森林",
    "砂漠",
    "クレーター",
    "クレータ",
    "市街地",
    "地上",
    "宙域",
)
RANGES = list(NAME75_RANGES) + [(0x75B000, 0x75C000)]


def dump_range(rom, dic, tbl):
    hits = []
    for lo, hi in RANGES:
        for logical, payload, kind in _walk_zstring_range(
            rom, lo, hi, region="name75", max_len=64
        ):
            text = dic.expand(payload, tbl)
            if any(needle in text for needle in NEEDLES):
                hits.append(
                    {"abs": f"{logical:06X}", "hex": payload.hex(), "text": text}
                )
    return hits


def main() -> int:
    tip = bytes(load_rom(TIP))
    orig = bytes(load_rom(ORIG))
    tbl = Tbl.load(TBL)
    d_tip = make_dictionary_ext3(tip, load_ext_meta(EXT), load_ext_meta(EXT3))
    d_orig = Dictionary(orig)
    orig_hits = dump_range(orig, d_orig, tbl)
    tip_hits = dump_range(tip, d_tip, tbl)
    sb = stock_base(tip)
    compared = []
    for hit in orig_hits:
        if len(hit["text"]) > 20:
            continue
        logical = int(hit["abs"], 16)
        got = read_encoded_z_safe(tip, sb + logical, max_len=64)
        if got is None:
            compared.append({**hit, "tip": None})
            continue
        payload, _term = got
        compared.append(
            {
                "abs": hit["abs"],
                "orig_text": hit["text"],
                "orig_hex": hit["hex"],
                "tip_hex": payload.hex(),
                "tip_text": d_tip.expand(payload, tbl),
            }
        )
    orig_slots = []
    for index in range(d_orig.stock_count):
        try:
            text = d_orig.expand_index(index, tbl)
        except Exception:
            continue
        if text and any(needle in text for needle in ("宇宙", "ア・バオア")) and len(text) <= 24:
            orig_slots.append({"idx": f"{index:04X}", "text": text})
    tip_slots = []
    for index in orig_slots:
        idx = int(index["idx"], 16)
        try:
            text = d_tip.expand_index(idx, tbl)
        except Exception:
            text = None
        tip_slots.append({"idx": index["idx"], "orig": index["text"], "tip": text})
    OUT.write_text(
        json.dumps(
            {
                "orig_hits": orig_hits,
                "tip_hits": tip_hits,
                "compared_short": compared,
                "orig_slots_short": orig_slots,
                "tip_same_idx": tip_slots,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"orig": len(orig_hits), "tip": len(tip_hits), "compared": len(compared), "slots": len(orig_slots)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
