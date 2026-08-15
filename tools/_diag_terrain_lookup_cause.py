#!/usr/bin/env python3
"""Compare original vs TIP map-name tables and hunt the terrain-info lookup."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from monoeye_rom import Dictionary, Tbl, load_rom, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIG = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CAND = ROOT / "out/patch/terrain_space_abaoaqu_e0_onebyte_candidate.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3 = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/_terrain_lookup_cause.json"

RANGES = ((0x75E400, 0x75E640), (0x75BD40, 0x75BE40), (0x75B3C0, 0x75B3E0))
NEEDLES = (0x75E58C, 0x75E594, 0x75E597, 0x75E59A, 0x75BD77, 0x75B3CE, 0x75B457)


def dump_walk(rom, dic, tbl, lo, hi):
    rows = []
    for i, (logical, payload, kind) in enumerate(
        _walk_zstring_range(rom, lo, hi, region="name75", max_len=64)
    ):
        rows.append(
            {
                "i": i,
                "abs": f"{logical:06X}",
                "len": len(payload),
                "hex": payload.hex().upper(),
                "text": dic.expand(payload, tbl),
                "kind": kind,
            }
        )
    return rows


def find_le16_hits(rom, sb, logical):
    off = logical & 0xFFFF
    pair = bytes([off & 0xFF, off >> 8])
    hits = []
    data = rom[sb:]
    start = 0
    while True:
        at = data.find(pair, start)
        if at < 0:
            break
        abs_off = sb + at
        bank = (abs_off - sb) >> 16
        if 0x50 <= bank <= 0x7F:
            ctx = rom[abs_off - 4 : abs_off + 6].hex().upper()
            hits.append({"file": f"{abs_off:06X}", "logical": f"{abs_off - sb:06X}", "ctx": ctx})
            if len(hits) >= 12:
                break
        start = at + 1
    return hits


def main() -> int:
    orig = bytes(load_rom(ORIG))
    tip = bytes(load_rom(TIP))
    cand = bytes(load_rom(CAND)) if CAND.is_file() else None
    tbl = Tbl.load(TBL)
    d0 = Dictionary(orig)
    d1 = make_dictionary_ext3(tip, load_ext_meta(EXT), load_ext_meta(EXT3))
    d2 = (
        make_dictionary_ext3(cand, load_ext_meta(EXT), load_ext_meta(EXT3))
        if cand
        else None
    )
    sb = stock_base(tip)
    tables = {}
    for lo, hi in RANGES:
        key = f"{lo:06X}-{hi:06X}"
        o = dump_walk(orig, d0, tbl, lo, hi)
        t = dump_walk(tip, d1, tbl, lo, hi)
        aligned = [a["abs"] for a in o] == [a["abs"] for a in t]
        shifted = []
        for a, b in zip(o, t):
            if a["abs"] != b["abs"]:
                shifted.append({"orig": a, "tip": b})
                if len(shifted) >= 8:
                    break
        extra = []
        if len(o) != len(t):
            extra.append({"orig_n": len(o), "tip_n": len(t)})
        tables[key] = {
            "orig_n": len(o),
            "tip_n": len(t),
            "start_addrs_equal": aligned,
            "orig": o,
            "tip": t,
            "first_shifted": shifted,
        }
        if d2 is not None:
            tables[key]["cand"] = dump_walk(cand, d2, tbl, lo, hi)

    ptrs = {f"{n:06X}": find_le16_hits(tip, sb, n) for n in NEEDLES}
    orig_ptrs = {f"{n:06X}": find_le16_hits(orig, 0, n) for n in NEEDLES}

    # Local hex around A Baoa Qu / space
    windows = {}
    for label, rom, base in (
        ("orig", orig, 0),
        ("tip", tip, sb),
        ("cand", cand, sb if cand is not None else None),
    ):
        if rom is None or base is None:
            continue
        windows[label] = {
            "75E580": rom[base + 0x75E580 : base + 0x75E5C0].hex().upper(),
            "75BD70": rom[base + 0x75BD70 : base + 0x75BD90].hex().upper(),
            "75B3C8": rom[base + 0x75B3C8 : base + 0x75B3D8].hex().upper(),
        }

    OUT.write_text(
        json.dumps(
            {
                "tables": {
                    k: {
                        **{sk: v[sk] for sk in v if sk not in ("orig", "tip", "cand")},
                        "orig_sample": v["orig"][:40],
                        "tip_sample": v["tip"][:40],
                        "cand_abaoa": [
                            row
                            for row in (v.get("cand") or [])
                            if row["abs"] in {"75E58C", "75E594", "75E597", "75E59A", "75BD77", "75B3CE"}
                        ],
                        "orig_abaoa": [
                            row
                            for row in v["orig"]
                            if row["abs"] in {"75E58C", "75E594", "75E597", "75E59A", "75BD77", "75B3CE"}
                        ],
                        "tip_abaoa": [
                            row
                            for row in v["tip"]
                            if row["abs"] in {"75E58C", "75E594", "75E597", "75E59A", "75BD77", "75B3CE"}
                        ],
                        "first_shifted": v.get("first_shifted"),
                    }
                    for k, v in tables.items()
                },
                "ptr_tip": ptrs,
                "ptr_orig": orig_ptrs,
                "windows": windows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    summary = {
        k: {
            "orig_n": v["orig_n"],
            "tip_n": v["tip_n"],
            "aligned": v["start_addrs_equal"],
            "shifted": len(v.get("first_shifted") or []),
        }
        for k, v in tables.items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
