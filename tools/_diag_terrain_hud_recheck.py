#!/usr/bin/env python3
"""Re-check the failed terrain candidate against current main and original encodings."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from mixed_residual_reference_union import _reference_scopes
from monoeye_rom import (
    Dictionary,
    Tbl,
    compact_font_file_offset,
    decode_compact_font_record,
    is_compact3_magic,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    load_rom,
    logical_bank_offset,
    read_encoded_z_safe,
    stock_base,
    text_code_to_glyph_index,
    token_from_dict_index,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CAND = ROOT / "out/patch/terrain_space_abaoaqu_compact_glyph_candidate.wsc"
ORIG = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/_terrain_hud_recheck.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def compact_off(rom: bytes, code: int) -> int:
    index = text_code_to_glyph_index(code)
    return (
        stock_base(rom)
        + logical_bank_offset(0x40, 0x440)
        + index * 16
    )


def rec(rom: bytes, logical: int) -> dict:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=32)
    if got is None:
        return {"abs": f"{logical:06X}", "unreadable": True}
    payload, term = got
    return {
        "abs": f"{logical:06X}",
        "hex": payload.hex().upper(),
        "len": len(payload),
        "term": f"{term - sb:06X}",
        "around": rom[sb + logical : sb + logical + 16].hex().upper(),
    }


def walk_units(payload: bytes, ext3_aware: bool):
    cursor = 0
    while cursor < len(payload):
        lead = payload[cursor]
        if ext3_aware and cursor + 2 < len(payload) and is_compact3_magic(lead, payload[cursor + 1]):
            cursor += 3
            continue
        if ext3_aware and cursor + 3 < len(payload) and is_ext3_magic(lead, payload[cursor + 1]):
            cursor += 4
            continue
        if is_dict_token(lead) and cursor + 1 < len(payload):
            yield ("dict", (lead << 8) | payload[cursor + 1], cursor)
            cursor += 2
            continue
        if is_kanji_lead(lead) and cursor + 1 < len(payload):
            yield ("kanji", (lead << 8) | payload[cursor + 1], cursor)
            cursor += 2
            continue
        yield ("one", lead, cursor)
        cursor += 1


def scan_kanji_and_one(rom, dic, ext3_aware, wanted_kanji, wanted_one):
    k = Counter()
    o = Counter()
    k_s = {c: [] for c in wanted_kanji}
    o_s = {c: [] for c in wanted_one}
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(rom, lo, hi, region=region, max_len=max_len):
            for typ, val, off in walk_units(payload, ext3_aware):
                if typ == "kanji":
                    k[val] += 1
                    if val in k_s and len(k_s[val]) < 4:
                        k_s[val].append(f"{logical + off:06X}/{region}")
                elif typ == "one":
                    o[val] += 1
                    if val in o_s and len(o_s[val]) < 4:
                        o_s[val].append(f"{logical + off:06X}/{region}")
    for idx in range(min(dic.count, dic.stock_count if hasattr(dic, "stock_count") else dic.count)):
        try:
            raw = bytes(dic.raw_entry(idx))
        except Exception:
            continue
        for typ, val, off in walk_units(raw, ext3_aware):
            loc = f"dict{idx:04X}+{off}"
            if typ == "kanji":
                k[val] += 1
                if val in k_s and len(k_s[val]) < 4:
                    k_s[val].append(loc)
            elif typ == "one":
                o[val] += 1
                if val in o_s and len(o_s[val]) < 4:
                    o_s[val].append(loc)
    return k, o, k_s, o_s


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    cand = bytes(load_rom(CAND)) if CAND.is_file() else b""
    orig = bytes(load_rom(ORIG))
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
    d_main = make_dictionary_ext3(
        main_rom,
        load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )
    d_orig = Dictionary(orig)
    addrs = [0x75B3CA, 0x75B3CE, 0x75B3D1, 0x75B457, 0x75E58C, 0x75E597, 0x75E59A, 0x75BD77, 0x75BD74]
    out = {
        "main_sha": hashlib.sha256(main_rom).hexdigest(),
        "main_size": len(main_rom),
        "cand_sha": hashlib.sha256(cand).hexdigest() if cand else None,
        "orig_records": {f"{a:06X}": rec(orig, a) for a in addrs},
        "main_records": {f"{a:06X}": rec(main_rom, a) for a in addrs},
        "cand_records": {f"{a:06X}": rec(cand, a) for a in addrs} if cand else {},
        "slot_008F_main": bytes(d_main.raw_entry(0x008F)).hex().upper(),
        "slot_008F_orig": bytes(d_orig.raw_entry(0x008F)).hex().upper(),
        "slot_0652_orig": bytes(d_orig.raw_entry(0x0652)).hex().upper() if 0x0652 < d_orig.stock_count else None,
        "slot_047F_orig": bytes(d_orig.raw_entry(0x047F)).hex().upper() if 0x047F < d_orig.stock_count else None,
    }
    if cand:
        d_cand = make_dictionary_ext3(
            cand,
            load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
            load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
        )
        out["slot_0050_cand"] = bytes(d_cand.raw_entry(0x0050)).hex().upper()
        out["slot_0050_ptr"] = f"{d_cand.ptrs[0x0050]:04X}"
        out["slot_008F_cand"] = bytes(d_cand.raw_entry(0x008F)).hex().upper()
        steals = (0xE511, 0xE51B, 0xE51C, 0xE51D, 0xE520, 0xE521, 0xE078, 0xE046, 0xC6, 0xDF)
        glyphs = {}
        for code in steals:
            off_c = compact_off(cand, code)
            off_m = compact_off(main_rom, code)
            rec_c = bytes(cand[off_c : off_c + 16])
            rec_m = bytes(main_rom[off_m : off_m + 16])
            glyphs[f"{code:04X}"] = {
                "cand_off": f"{off_c:06X}",
                "main_off": f"{off_m:06X}",
                "cand_hex": rec_c.hex().upper(),
                "main_hex": rec_m.hex().upper(),
                "changed": rec_c != rec_m,
                "tbl": tbl.decode_char(code),
            }
        out["glyphs"] = glyphs
        # F050 occurrences in 75B000-75E800
        sb = stock_base(cand)
        needle = bytes.fromhex("F050")
        hits = []
        for logical in range(0x75B000, 0x75E800):
            if cand[sb + logical : sb + logical + 2] == needle:
                hits.append(f"{logical:06X}")
        out["F050_hits_75B_75E"] = hits

    wanted_k = {0xE078, 0xE046, 0xE511, 0xE51C}
    wanted_o = {0x26, 0x2A, 0xC6, 0xDF, 0x1F, 0x18}
    k, o, ks, os_ = scan_kanji_and_one(orig, d_orig, False, wanted_k, wanted_o)
    k2, o2, ks2, os2 = scan_kanji_and_one(main_rom, d_main, True, wanted_k, wanted_o)
    out["orig_kanji"] = {f"{c:04X}": {"n": k[c], "s": ks[c]} for c in wanted_k}
    out["main_kanji"] = {f"{c:04X}": {"n": k2[c], "s": ks2[c]} for c in wanted_k}
    out["orig_one"] = {f"{c:02X}": {"n": o[c], "s": os_[c]} for c in wanted_o}
    out["main_one"] = {f"{c:02X}": {"n": o2[c], "s": os2[c]} for c in wanted_o}

    # low-usage 1-byte in orig+main union, dialogue+name75+aux only (skip 64-69)
    one_union = Counter()
    for rom, dic, aware in ((orig, d_orig, False), (main_rom, d_main, True)):
        for region, lo, hi, max_len in _reference_scopes():
            bank = lo >> 16
            if region == "script" and 0x64 <= bank <= 0x69:
                continue
            for logical, payload, kind in _walk_zstring_range(rom, lo, hi, region=region, max_len=max_len):
                for typ, val, off in walk_units(payload, aware):
                    if typ == "one":
                        one_union[val] += 1
        for idx in range(dic.stock_count if hasattr(dic, "stock_count") else min(dic.count, 0xF00)):
            try:
                raw = bytes(dic.raw_entry(idx))
            except Exception:
                continue
            for typ, val, off in walk_units(raw, aware):
                if typ == "one":
                    one_union[val] += 1
    rare = []
    for code in range(0x02, 0xE0):
        if code in {0x01, 0xC6, 0xDF}:
            continue
        n = one_union[code]
        if n <= 8:
            off = compact_off(main_rom, code)
            recb = bytes(main_rom[off : off + 16])
            ink = sum(sum(row) for row in decode_compact_font_record(recb)) if len(recb) == 16 else -1
            rare.append({"code": f"{code:02X}", "n": n, "ch": tbl.decode_char(code), "ink": ink})
    out["rare_onebyte_excl_6469"] = rare[:40]
    out["rare_onebyte_count"] = len(rare)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"main": out["main_sha"][:16], "cand": (out["cand_sha"] or "")[:16], "rare": len(rare)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
