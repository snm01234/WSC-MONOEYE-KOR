#!/usr/bin/env python3
"""Reverse user-visible ぉ / で人メあ ぇ備何 against ROM payloads and compact glyphs."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from hangul_allocator import HANGUL_PRIMARY_START
from monoeye_rom import (
    COMPACT_FONT_RECORD_SIZE,
    COMPACT_FONT_SEGMENT,
    COMPACT_FONT_TABLE,
    Dictionary,
    Tbl,
    decode_compact_font_record,
    dict_index_from_ext3_token,
    dict_index_from_token,
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

TBL = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
EXT = load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json")
EXT3 = load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json")
OUT = ROOT / "out/patch/_terrain_display_mismatch.json"

ROMS = {
    "original": ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    "live_main": ROOT / "out/patch/monoeye_ko_expanded.wsc",
    "vanilla_cand": ROOT / "out/patch/terrain_space_abaoaqu_vanilla_font_candidate.wsc",
    "e0_cand": ROOT / "out/patch/terrain_space_abaoaqu_e0_onebyte_candidate.wsc",
    "parent_backup": ROOT
    / "out/patch/backup/20260815_015949_pre_name_mapping_spirit_combined/monoeye_ko_expanded.wsc",
}

SITES = {
    "space_75B3CE": 0x75B3CE,
    "space_75E59A": 0x75E59A,
    "abaoa_75E58C": 0x75E58C,
    "abaoa_75BD77": 0x75BD77,
    "hit_75B457": 0x75B457,
}

USER_SPACE = "ぉ"
USER_ABAOA = "で人メあ　ぇ備何"
USER_ABAOA_NOSPACE = "で人メあぇ備何"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def compact_off(rom: bytes, code: int) -> int:
    return (
        stock_base(rom)
        + logical_bank_offset(COMPACT_FONT_SEGMENT, COMPACT_FONT_TABLE)
        + text_code_to_glyph_index(code) * COMPACT_FONT_RECORD_SIZE
    )


def glyph_bytes(rom: bytes, code: int) -> bytes:
    off = compact_off(rom, code)
    return bytes(rom[off : off + 16])


def ink(record: bytes) -> int:
    if len(record) != 16:
        return -1
    return sum(sum(row) for row in decode_compact_font_record(record))


def payload_at(rom: bytes, logical: int) -> dict:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=64)
    if got is None:
        return {"abs": f"{logical:06X}", "unreadable": True}
    raw, term = got
    return {
        "abs": f"{logical:06X}",
        "hex": bytes(raw).hex().upper(),
        "len": len(raw),
        "term": f"{term - sb:06X}",
        "around": bytes(rom[sb + logical : sb + logical + 16]).hex().upper(),
    }


def dictionary_for(name: str, rom: bytes):
    if name == "original":
        return Dictionary(rom)
    return make_dictionary_ext3(rom, EXT, EXT3)


def expand_once(payload: bytes, dic, *, ext3: bool, dict_tokens: bool, kanji: bool) -> bytes:
    out = bytearray()
    i = 0
    while i < len(payload):
        b = payload[i]
        if ext3 and i + 2 < len(payload) and is_compact3_magic(b, payload[i + 1]):
            i += 3
            continue
        if ext3 and i + 3 < len(payload) and is_ext3_magic(b, payload[i + 1]):
            idx = dict_index_from_ext3_token(b, payload[i + 1], payload[i + 2], payload[i + 3])
            try:
                out.extend(bytes(dic.raw_entry(idx)))
            except Exception:
                out.extend(payload[i : i + 4])
            i += 4
            continue
        if dict_tokens and is_dict_token(b) and i + 1 < len(payload):
            idx = dict_index_from_token(b, payload[i + 1])
            try:
                out.extend(bytes(dic.raw_entry(idx)))
            except Exception:
                out.extend(payload[i : i + 2])
            i += 2
            continue
        if kanji and is_kanji_lead(b) and i + 1 < len(payload):
            out.extend(payload[i : i + 2])
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def expand_full(payload: bytes, dic, *, ext3: bool, dict_tokens: bool, kanji: bool) -> bytes:
    cur = bytes(payload)
    for _ in range(8):
        nxt = expand_once(cur, dic, ext3=ext3, dict_tokens=dict_tokens, kanji=kanji)
        if nxt == cur:
            return cur
        cur = nxt
    return cur


def codes_of(payload: bytes, *, kanji: bool) -> list[int]:
    codes: list[int] = []
    i = 0
    while i < len(payload):
        b = payload[i]
        if kanji and is_kanji_lead(b) and i + 1 < len(payload):
            codes.append((b << 8) | payload[i + 1])
            i += 2
            continue
        codes.append(b)
        i += 1
    return codes


def decode_codes(codes: list[int]) -> str:
    parts: list[str] = []
    for code in codes:
        ch = TBL.decode_char(code)
        parts.append(ch if ch else f"[{code:04X}]")
    return "".join(parts)


def tbl_bytes(text: str) -> list[bytes]:
    """Encode with 1-byte TBL only (ignore CJK that need 2-byte)."""
    out = bytearray()
    missing = []
    for ch in text:
        if ch == " ":
            ch = "　"
        found = None
        for code, glyph in TBL.code_to_char.items():
            if glyph == ch and code <= 0xDF:
                found = code
                break
        if found is None:
            missing.append(ch)
        else:
            out.append(found)
    return [bytes(out), "".join(missing)]


def find_bytes(rom: bytes, needle: bytes, limit: int = 12) -> list[str]:
    hits = []
    start = 0
    while len(hits) < limit:
        pos = rom.find(needle, start)
        if pos < 0:
            break
        logical = pos - stock_base(rom)
        hits.append(f"{pos:06X}/log={logical:06X}")
        start = pos + 1
    return hits


def slot_info(dic, idx: int) -> dict:
    try:
        raw = bytes(dic.raw_entry(idx))
        ptr = int(dic.ptrs[idx]) if idx < len(dic.ptrs) else None
        return {
            "index": f"{idx:04X}",
            "ptr": None if ptr is None else f"{ptr:04X}",
            "hex": raw.hex().upper(),
            "text": dic.expand_index(idx, TBL) if hasattr(dic, "expand_index") else TBL.decode(raw),
        }
    except Exception as exc:
        return {"index": f"{idx:04X}", "error": str(exc)}


def main() -> int:
    report: dict = {
        "user": {
            "space": USER_SPACE,
            "abaoa": USER_ABAOA,
            "tbl_space": "BA",
            "tbl_abaoa_1byte": None,
        },
        "roms": {},
        "glyph_compare": {},
        "byte_search": {},
        "walker": {},
    }
    abaoa_enc, missing = tbl_bytes(USER_ABAOA)
    abaoa_ns, missing_ns = tbl_bytes(USER_ABAOA_NOSPACE)
    report["user"]["tbl_abaoa_1byte"] = {
        "with_space_hex": abaoa_enc.hex().upper(),
        "with_space_missing": missing,
        "no_space_hex": abaoa_ns.hex().upper(),
        "no_space_missing": missing_ns,
        "chars": [
            {"ch": ch, "code": f"{code:02X}" if (code := next((c for c, g in TBL.code_to_char.items() if g == (ch if ch != " " else "　") and c <= 0xDF), None)) else None}
            for ch in USER_ABAOA
        ],
    }

    loaded = {}
    for name, path in ROMS.items():
        if not path.is_file():
            report["roms"][name] = {"missing": True, "path": str(path)}
            continue
        data = bytes(load_rom(path))
        loaded[name] = data
        report["roms"][name] = {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(data),
            "size": len(data),
            "sites": {key: payload_at(data, abs_) for key, abs_ in SITES.items()},
        }
        dic = dictionary_for(name, data)
        report["roms"][name]["slot_008F"] = slot_info(dic, 0x8F)
        report["roms"][name]["slot_0050"] = slot_info(dic, 0x50)
        report["roms"][name]["slot_0632"] = slot_info(dic, 0x632)
        report["roms"][name]["slot_0652"] = slot_info(dic, 0x652)
        report["roms"][name]["slot_047F"] = slot_info(dic, 0x47F)

    # Glyph: does EC8D compact equal ぉ (BA)? あ (1F)?
    ref = loaded.get("live_main") or loaded.get("vanilla_cand")
    if ref:
        pairs = {
            "EC8D": 0xEC8D,
            "BA_ぉ": 0xBA,
            "1F_あ": 0x1F,
            "E078_宇": 0xE078,
            "E046_宙": 0xE046,
            "E761_우": 0xE761,
            "E764_주": 0xE764,
            "E009_受": 0xE009,
        }
        g = {}
        for label, code in pairs.items():
            rec = glyph_bytes(ref, code)
            g[label] = {
                "code": f"{code:04X}" if code > 0xFF else f"{code:02X}",
                "index": f"{text_code_to_glyph_index(code):04X}",
                "offset": f"{compact_off(ref, code):06X}",
                "hex": rec.hex().upper(),
                "ink": ink(rec),
            }
        g["EC8D_eq_BA"] = g["EC8D"]["hex"] == g["BA_ぉ"]["hex"]
        g["EC8D_eq_1F"] = g["EC8D"]["hex"] == g["1F_あ"]["hex"]
        if "vanilla_cand" in loaded:
            vc = loaded["vanilla_cand"]
            g["vanilla_E078_eq_E761"] = glyph_bytes(vc, 0xE078) == glyph_bytes(vc, 0xE761)
            g["vanilla_E046_eq_E764"] = glyph_bytes(vc, 0xE046) == glyph_bytes(vc, 0xE764)
            g["vanilla_E078_hex"] = glyph_bytes(vc, 0xE078).hex().upper()
            g["vanilla_E761_hex"] = glyph_bytes(vc, 0xE761).hex().upper()
            g["live_E078_hex"] = glyph_bytes(ref, 0xE078).hex().upper()
        report["glyph_compare"] = g

    needles = {
        "BA": bytes.fromhex("BA"),
        "abaoa_space": abaoa_enc,
        "abaoa_nospace": abaoa_ns,
        "1650B91F": bytes.fromhex("1650B91F"),
        "B8C59A": bytes.fromhex("B8C59A"),
        "F08F": bytes.fromhex("F08F"),
        "EC8DE761E764": bytes.fromhex("EC8DE761E764"),
        "E078E046": bytes.fromhex("E078E046"),
        "9001F63201F050": bytes.fromhex("9001F63201F050"),
        "E518EC41010101": bytes.fromhex("E518EC41010101"),
        "E518B445F0A901": bytes.fromhex("E518B445F0A901"),
    }
    for name, data in loaded.items():
        report["byte_search"][name] = {
            key: find_bytes(data, needle) for key, needle in needles.items() if needle
        }

    modes = [
        ("raw_1byte", False, False, False),
        ("kanji_only", False, False, True),
        ("dict_kanji", False, True, True),
        ("ext3_dict_kanji", True, True, True),
        ("dict_as_1byte_after_expand", False, True, False),
        ("ext3_then_1byte", True, True, False),
    ]
    for rom_name in ("live_main", "vanilla_cand", "original", "e0_cand"):
        if rom_name not in loaded:
            continue
        data = loaded[rom_name]
        dic = dictionary_for(rom_name, data)
        rom_walk = {}
        for site_name, abs_ in SITES.items():
            rec = payload_at(data, abs_)
            if rec.get("unreadable"):
                continue
            raw = bytes.fromhex(rec["hex"])
            site_walk = {"raw": rec["hex"]}
            for mode_name, ext3, dict_tokens, kanji in modes:
                expanded = expand_full(
                    raw, dic, ext3=ext3, dict_tokens=dict_tokens, kanji=kanji
                )
                # After expansion, tokenize for display
                if kanji:
                    codes = codes_of(expanded, kanji=True)
                else:
                    codes = list(expanded)
                text = decode_codes(codes)
                site_walk[mode_name] = {
                    "expanded_hex": expanded.hex().upper(),
                    "codes": [f"{c:04X}" if c > 0xFF else f"{c:02X}" for c in codes],
                    "text": text,
                    "match_space": text.replace(" ", "　") in (USER_SPACE, f"　{USER_SPACE}", USER_SPACE),
                    "match_abaoa": USER_ABAOA.replace(" ", "　") in text.replace(" ", "　")
                    or USER_ABAOA_NOSPACE in text.replace("　", "").replace(" ", ""),
                }
            rom_walk[site_name] = site_walk
        # Also expand known ext3 indices if present
        for label, blob in (
            ("ext_EC41", bytes.fromhex("E518EC41")),
            ("ext_B445", bytes.fromhex("E518B445")),
        ):
            try:
                idx = dict_index_from_ext3_token(*blob)
                rom_walk[label] = slot_info(dic, idx)
            except Exception as exc:
                rom_walk[label] = {"error": str(exc)}
        report["walker"][rom_name] = rom_walk

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print("user 1byte", report["user"]["tbl_abaoa_1byte"]["with_space_hex"])
    if "glyph_compare" in report:
        print("EC8D_eq_BA", report["glyph_compare"].get("EC8D_eq_BA"))
        print("EC8D_eq_1F", report["glyph_compare"].get("EC8D_eq_1F"))
        print("vanilla_E078_eq_E761", report["glyph_compare"].get("vanilla_E078_eq_E761"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
