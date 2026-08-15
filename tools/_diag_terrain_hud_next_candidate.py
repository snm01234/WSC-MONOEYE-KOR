#!/usr/bin/env python3
"""Pick E0xx + 1-byte steals for the next terrain HUD candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import NAME75_RANGES, _walk_zstring_range
from mixed_residual_reference_union import (
    NAME75_UI_TABLE_RANGES,
    _reference_scopes,
    build_reference_union,
)
from monoeye_rom import (
    Dictionary,
    Tbl,
    decode_compact_font_record,
    dict_token_safe_in_zstring,
    is_compact3_magic,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    load_rom,
    logical_bank_offset,
    read_encoded_z_safe,
    stock_base,
    text_code_to_glyph_index,
)
from analyze_p2_duplicate_detachment import (
    external_occurrence_map,
    nested_occurrence_map,
)
from analyze_p2_retired_slot_reclaim import _raw_pair_hits

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIG = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CAND = ROOT / "out/patch/terrain_space_abaoaqu_compact_glyph_candidate.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3 = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/_terrain_hud_next_pick.json"

HUD_RANGES = list(NAME75_RANGES) + list(NAME75_UI_TABLE_RANGES)
TAKEN_ONE = {0x00, 0x01, 0x18, 0x19, 0x1F, 0x26, 0x2A, 0xC6, 0xDF}
UNSAFE_TRAILS = {0x00, 0x18, 0x19}


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
    }


def walk_units(payload: bytes, ext3_aware: bool):
    cursor = 0
    size = len(payload)
    while cursor < size:
        lead = payload[cursor]
        if ext3_aware and cursor + 2 < size and is_compact3_magic(lead, payload[cursor + 1]):
            cursor += 3
            continue
        if ext3_aware and cursor + 3 < size and is_ext3_magic(lead, payload[cursor + 1]):
            cursor += 4
            continue
        if is_dict_token(lead) and cursor + 1 < size:
            yield ("dict", (lead << 8) | payload[cursor + 1], cursor)
            cursor += 2
            continue
        if is_kanji_lead(lead) and cursor + 1 < size:
            yield ("kanji", (lead << 8) | payload[cursor + 1], cursor)
            cursor += 2
            continue
        yield ("one", lead, cursor)
        cursor += 1


def glyph_ok(rom: bytes, code: int) -> tuple[bool, int, str]:
    offset = compact_off(rom, code)
    record = bytes(rom[offset : offset + 16])
    if len(record) != 16 or record == b"\xFF" * 16:
        return False, 0, f"{offset:06X}"
    ink = sum(sum(row) for row in decode_compact_font_record(record))
    return ink > 0, ink, f"{offset:06X}"


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIG))
    failed = bytes(load_rom(CAND)) if CAND.is_file() else b""
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT)
    ext3_meta = load_ext_meta(EXT3)
    d = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d0 = Dictionary(original)
    sb = stock_base(parent)

    sites = (0x75B3CE, 0x75E59A, 0x75E58C, 0x75BD77, 0x75B3CA, 0x75B457, 0x75B75E, 0x75BE2B)
    records = {
        "main": {f"{a:06X}": rec(parent, a) for a in sites},
        "failed": {f"{a:06X}": rec(failed, a) for a in sites} if failed else {},
        "orig": {f"{a:06X}": rec(original, a) for a in sites},
    }

    slot008f = {
        "main": bytes(d.raw_entry(0x008F)).hex().upper(),
        "orig": bytes(d0.raw_entry(0x008F)).hex().upper(),
        "text_main": d.expand_index(0x008F, tbl),
        "text_orig": d0.expand_index(0x008F, tbl),
    }

    aux = read_encoded_z_safe(parent, sb + 0x5ED6A1, max_len=80)
    leftover_e078 = None
    if aux:
        leftover_e078 = {
            "hex": aux[0].hex().upper(),
            "jp": d0.expand(aux[0], tbl),
            "ko": d.expand(aux[0], tbl),
        }

    kanji_counts: Counter[int] = Counter()
    for rom, dic, aware in ((parent, d, True), (original, d0, False)):
        for region, lo, hi, max_len in _reference_scopes():
            for _logical, payload, _kind in _walk_zstring_range(
                rom, lo, hi, region=region, max_len=max_len
            ):
                for kind, value, _off in walk_units(payload, aware):
                    if kind == "kanji":
                        kanji_counts[value] += 1
        for index in range(dic.count):
            try:
                raw = bytes(dic.raw_entry(index))
            except Exception:
                continue
            for kind, value, _off in walk_units(raw, aware):
                if kind == "kanji":
                    kanji_counts[value] += 1

    e0_unused = []
    for code in range(0xE000, 0xE500):
        if (code & 0xFF) in UNSAFE_TRAILS:
            continue
        if kanji_counts[code]:
            continue
        ok, ink, offset = glyph_ok(parent, code)
        if not ok:
            continue
        e0_unused.append(
            {
                "code": f"{code:04X}",
                "char": tbl.decode_char(code),
                "ink": ink,
                "offset": offset,
            }
        )
        if len(e0_unused) >= 12:
            break

    dict_one: Counter[int] = Counter()
    hud_one: Counter[int] = Counter()
    hud_sites: dict[int, list[str]] = {i: [] for i in range(256)}
    script_one: Counter[int] = Counter()
    for index in range(d.count):
        try:
            raw = bytes(d.raw_entry(index))
        except Exception:
            continue
        for kind, value, off in walk_units(raw, True):
            if kind == "one":
                dict_one[value] += 1
    for lo, hi in HUD_RANGES:
        for logical, payload, _kind in _walk_zstring_range(
            parent, lo, hi, region="name75", max_len=64
        ):
            for kind, value, off in walk_units(payload, True):
                if kind == "one":
                    hud_one[value] += 1
                    if len(hud_sites[value]) < 4:
                        hud_sites[value].append(f"{logical + off:06X}")
    for region, lo, hi, max_len in _reference_scopes():
        if region != "script":
            continue
        for logical, payload, _kind in _walk_zstring_range(
            parent, lo, hi, region=region, max_len=max_len
        ):
            for kind, value, _off in walk_units(payload, True):
                if kind == "one":
                    script_one[value] += 1

    one_candidates = []
    for code in range(0x02, 0xE0):
        if code in TAKEN_ONE:
            continue
        if dict_one[code] or hud_one[code]:
            continue
        ok, ink, offset = glyph_ok(parent, code)
        if not ok:
            continue
        one_candidates.append(
            {
                "code": f"{code:02X}",
                "char": tbl.decode_char(code),
                "ink": ink,
                "offset": offset,
                "script": script_one[code],
            }
        )
    one_candidates.sort(key=lambda row: (row["script"], int(row["code"], 16)))

    wanted = {
        index
        for index in range(min(d0.stock_count, 0xF00))
        if dict_token_safe_in_zstring(index)
    }
    orig_ext = external_occurrence_map(original, ext3_aware=False, wanted=wanted)
    cur_ext = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    orig_nested = nested_occurrence_map(d0, wanted=wanted, ext3_aware=False)
    cur_nested = nested_occurrence_map(d, wanted=wanted, ext3_aware=True)

    true_free = []
    retired = []
    for index in sorted(wanted):
        if cur_ext.get(index) or cur_nested.get(index) or orig_nested.get(index):
            continue
        try:
            vanilla = bytes(d0.raw_entry(index))
            current = bytes(d.raw_entry(index))
        except Exception:
            continue
        if d0.ptrs[index] != d.ptrs[index] or vanilla != current:
            continue
        if index == 0x00A9:
            continue
        cap = len(current)
        row = {
            "index": f"{index:04X}",
            "cap": cap,
            "payload": current.hex().upper(),
            "historical": len(orig_ext.get(index) or []),
        }
        if not orig_ext.get(index):
            if cap >= 4:
                true_free.append(row)
        else:
            if cap >= 4:
                retired.append(row)
        if len(true_free) >= 8 and len(retired) >= 8:
            break

    raw_hits = _raw_pair_hits(parent, [int(r["index"], 16) for r in retired[:20]])
    retired_strong = [r for r in retired if not raw_hits.get(int(r["index"], 16))]

    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    true_free_union = []
    for row in true_free[:8]:
        index = int(row["index"], 16)
        reason = union.refuse_reason(index, require_free=True)
        true_free_union.append({**row, "refuse_free": reason})

    c6 = compact_off(parent, 0xC6)
    df = compact_off(parent, 0xDF)
    e078 = compact_off(parent, 0xE078)
    e046 = compact_off(parent, 0xE046)
    e511 = compact_off(failed, 0xE511) if failed else 0

    out = {
        "main_sha": sha(MAIN),
        "failed_sha": sha(CAND) if CAND.is_file() else None,
        "records": records,
        "slot008f": slot008f,
        "leftover_e078_5ED6A1": leftover_e078,
        "kanji_E078": kanji_counts[0xE078],
        "kanji_E046": kanji_counts[0xE046],
        "kanji_E511": kanji_counts[0xE511],
        "e0_unused_first": e0_unused,
        "onebyte_hud_dict_zero": one_candidates[:20],
        "onebyte_zero_count": len(one_candidates),
        "true_free_cap4": true_free[:8],
        "true_free_union": true_free_union,
        "retired_strong_cap4": retired_strong[:8],
        "c6_offset": f"{c6:06X}",
        "df_offset": f"{df:06X}",
        "e078_offset": f"{e078:06X}",
        "e046_offset": f"{e046:06X}",
        "failed_e511_offset": f"{e511:06X}" if failed else None,
        "failed_e511_eq_hangul_u": (
            bytes(failed[e511 : e511 + 16]) == bytes(parent[compact_off(parent, 0xE761) : compact_off(parent, 0xE761) + 16])
            if failed
            else None
        ),
        "c6_glyph_hex": parent[c6 : c6 + 16].hex().upper(),
        "df_glyph_hex": parent[df : df + 16].hex().upper(),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(OUT), "onebyte": len(one_candidates), "e0": len(e0_unused), "true_free": len(true_free)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
