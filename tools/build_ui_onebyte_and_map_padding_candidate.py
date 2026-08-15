#!/usr/bin/env python3
"""Build a candidate for standalone 攻/分 UI labels and long map-name padding.

1. Unit-info 攻 (75B3EF, plus bank5C twins) cannot hold a 2-byte Hangul token.
   Dictionary payloads contain no 1-byte C6, so the stock C6 glyph is copied
   from Hangul 공. 攻撃 dictionary tokens are unchanged.
2. Save-time 分 (75B559) cannot remap A3 globally: live dictionary phrases
   気分 / 五分五分 still use 1-byte A3.  The proven-UI-unused 1-byte code DF
   (了) receives the 분 bitmap and 75B559 is rewritten to that code.
3. Map location names in 75BD4E–75BE34 with trailing 01 padding ≥ 3 are
   shortened with the already-approved zero-width stock token F0A9, leaving
   at most two visible spaces (matching 달 항로 / 다카르 교외).

The live main TIP and SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import nested_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs
from expand_dictionary import DEFAULT_REF_REGIONS, build_dict_token_locs
from hangul_allocator import HANGUL_PRIMARY_START
from monoeye_rom import (
    Tbl,
    compact_font_file_offset,
    decode_compact_font_record,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_font_hangul_hook import PAD1_FILE, PAD1_SLOTS, PAD2_FILE
from patch_pad3_expansion import PAD12_SLOTS, pad3_file_offset

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ui_onebyte_and_map_padding_candidate.wsc"
OUT_SAVE = ROOT / "sram/ui_onebyte_and_map_padding_candidate.sav"
OUT_REPORT = ROOT / "out/patch/ui_onebyte_and_map_padding_candidate_report.json"

EXPECTED_MAIN = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EMPTY_STOCK = 0x00A9
ATTACK_CODE = 0xC6
MINUTE_STEAL = 0xDF  # 了; unused in dictionary and proven UI table
HANGUL_GONG = 0xE746
HANGUL_BUN = 0xE77C
ATTACK_ABS = 0x75B3EF
MINUTE_ABS = 0x75B559
KIBUN_INDEX = 0x01EC
GOBUN_INDEX = 0x09F8
LOCATION_LONG_PAD = (
    0x75BD4E,
    0x75BD77,
    0x75BD8C,
    0x75BDBB,
    0x75BDD0,
    0x75BE03,
)
MAX_VISIBLE_PAD = 2


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=64)
    if got is None:
        raise BuildError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - stock_base(rom)


def hangul_pad_slot(code: int) -> int:
    slot = code - HANGUL_PRIMARY_START
    if slot < 0:
        raise BuildError(f"not a primary Hangul code: {code:04X}")
    return slot


def hangul_glyph_offset(rom: bytes | bytearray, code: int) -> int:
    slot = hangul_pad_slot(code)
    base = stock_base(rom)
    if slot < PAD1_SLOTS:
        return base + PAD1_FILE + slot * 16
    if slot < PAD12_SLOTS:
        return base + PAD2_FILE + (slot - PAD1_SLOTS) * 16
    return pad3_file_offset(rom, slot)


def read_glyph(rom: bytes | bytearray, offset: int) -> bytes:
    record = bytes(rom[offset : offset + 16])
    if len(record) != 16:
        raise BuildError(f"truncated glyph at {offset:06X}")
    ink = sum(sum(row) for row in decode_compact_font_record(record))
    if ink <= 0 or record == b"\xFF" * 16:
        raise BuildError(f"empty glyph at {offset:06X}")
    return record


def shorten_trailing_01(payload: bytes, empty: bytes) -> bytes:
    if not payload.startswith(bytes.fromhex("E518")) or len(payload) < 5:
        raise BuildError(f"expected ext3+padding, got {payload.hex().upper()}")
    head, pad = payload[:4], payload[4:]
    if not pad or set(pad) != {0x01}:
        raise BuildError(f"non-01 tail {payload.hex().upper()}")
    n = len(pad)
    if n <= MAX_VISIBLE_PAD:
        return payload
    keep = 1 if n % 2 else 2
    fill = n - keep
    if fill % 2:
        raise BuildError(f"cannot pack zero-width filler into {n} pad bytes")
    return head + empty * (fill // 2) + bytes([0x01] * keep)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN:
        raise BuildError("parent ROM drift")
    if len(save) != SAVE_SIZE:
        raise BuildError("SaveRAM size drift")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)
    empty = token_from_dict_index(EMPTY_STOCK)
    if empty != bytes.fromhex("F0A9"):
        raise BuildError("00A9 token drifted")
    if dictionary.expand_index(EMPTY_STOCK, tbl) != "":
        raise BuildError("00A9 is no longer zero-width")

    attack_payload, attack_term = payload_at(parent, ATTACK_ABS)
    minute_payload, minute_term = payload_at(parent, MINUTE_ABS)
    if attack_payload != bytes([ATTACK_CODE]) or attack_term != ATTACK_ABS + 1:
        raise BuildError("75B3EF is no longer standalone 攻")
    if minute_payload != bytes([0xA3]) or minute_term != MINUTE_ABS + 1:
        raise BuildError("75B559 is no longer standalone 分")
    if dictionary.expand(attack_payload, tbl) != "攻":
        raise BuildError("75B3EF decode drift")
    if dictionary.expand(minute_payload, tbl) != "分":
        raise BuildError("75B559 decode drift")

    kibun = dictionary.expand_index(KIBUN_INDEX, tbl)
    gobun = dictionary.expand_index(GOBUN_INDEX, tbl)
    if kibun != "気分" or bytes(dictionary.raw_entry(KIBUN_INDEX)) != bytes.fromhex("A0A3"):
        raise BuildError("気分 slot drifted; refusing A3 isolation")
    if gobun != "五分五分" or b"\xa3" not in bytes(dictionary.raw_entry(GOBUN_INDEX)):
        raise BuildError("五分五分 slot drifted; refusing A3 isolation")
    refs = build_dict_token_locs(parent, regions=DEFAULT_REF_REGIONS)
    nested = nested_occurrence_map(
        dictionary, wanted={KIBUN_INDEX, GOBUN_INDEX}, ext3_aware=True
    )
    if refs.get(GOBUN_INDEX) is None:
        raise BuildError("五分五分 lost its live consumer")

    gong = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_GONG))
    bun = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_BUN))
    c6_off = compact_font_file_offset(ATTACK_CODE)
    df_off = compact_font_file_offset(MINUTE_STEAL)
    if bytes(parent[c6_off : c6_off + 16]) == gong:
        raise BuildError("C6 glyph already matches 공")
    if bytes(parent[df_off : df_off + 16]) == bun:
        raise BuildError("DF glyph already matches 분")

    rom = bytearray(parent)
    allow: list[tuple[int, int]] = []

    rom[c6_off : c6_off + 16] = gong
    allow.append((c6_off, c6_off + 16))
    rom[df_off : df_off + 16] = bun
    allow.append((df_off, df_off + 16))

    minute_file = sb + MINUTE_ABS
    if rom[minute_file] != 0xA3:
        raise BuildError("minute label byte drifted before write")
    rom[minute_file] = MINUTE_STEAL
    allow.append((minute_file, minute_file + 1))

    location_rows: list[dict[str, Any]] = []
    for logical in LOCATION_LONG_PAD:
        old, term = payload_at(parent, logical)
        new = shorten_trailing_01(old, empty)
        if len(new) != len(old):
            raise BuildError(f"{logical:06X} length changed")
        start = sb + logical
        rom[start : start + len(new)] = new
        allow.append((start, start + len(new)))
        location_rows.append(
            {
                "abs": f"{logical:06X}",
                "before_hex": old.hex().upper(),
                "after_hex": new.hex().upper(),
                "before_text": dictionary.expand(old, tbl),
                "after_text": dictionary.expand(new, tbl),
                "before_pad": len(old) - len(old.rstrip(b"\x01")),
                "after_pad": len(new) - len(new.rstrip(b"\x01")),
            }
        )

    checksum = update_ws_checksum(rom)
    allow.append((len(rom) - 2, len(rom)))
    result = bytes(rom)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allow)]
    if unexpected:
        raise BuildError(
            "diff outside allowlist: "
            + ", ".join(f"{lo:08X}-{hi:08X}" for lo, hi in unexpected)
        )

    candidate_dict = make_dictionary_ext3(result, ext_meta, ext3_meta)
    if bytes(result[c6_off : c6_off + 16]) != gong:
        raise BuildError("C6 glyph write failed")
    if bytes(result[df_off : df_off + 16]) != bun:
        raise BuildError("DF glyph write failed")
    if payload_at(result, ATTACK_ABS)[0] != bytes([ATTACK_CODE]):
        raise BuildError("75B3EF body changed")
    if payload_at(result, MINUTE_ABS)[0] != bytes([MINUTE_STEAL]):
        raise BuildError("75B559 steal failed")
    if candidate_dict.expand_index(KIBUN_INDEX, tbl) != "気分":
        raise BuildError("気分 collateral")
    if candidate_dict.expand_index(GOBUN_INDEX, tbl) != "五分五分":
        raise BuildError("五分五分 collateral")
    if candidate_dict.expand_index(EMPTY_STOCK, tbl) != "":
        raise BuildError("00A9 collateral")
    for row in location_rows:
        if int(row["after_pad"]) > MAX_VISIBLE_PAD:
            raise BuildError(f"{row['abs']} still has long padding")
        if row["after_text"].rstrip("　 \t") != row["before_text"].rstrip("　 \t"):
            raise BuildError(f"{row['abs']} visible text changed")

    earth = payload_at(result, 0x75BDD0)[0]
    if candidate_dict.expand(earth, tbl).rstrip("　 \t") != "지구　궤도　항로":
        raise BuildError("지구 궤도 항로 text drift")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != save:
        raise BuildError("live main mutated")
    if OUT_ROM.read_bytes() != result or OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate reread mismatch")

    report = {
        "ok": True,
        "status": "candidate_static_verified_pending_user_runtime_test",
        "parent": identity(MAIN, parent),
        "candidate": {**identity(OUT_ROM, result), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(OUT_SAVE),
        "live_main_unchanged": True,
        "live_saveram_unchanged": True,
        "targets": {
            "75B3EF": "攻 glyph C6 -> 공 (record byte unchanged)",
            "75B559": "分 A3 -> DF(了) glyph 분",
            "map_locations": [f"{abs_:06X}" for abs_ in LOCATION_LONG_PAD],
        },
        "guards": {
            "kibun": kibun,
            "gobun": gobun,
            "gobun_external_refs": len(refs.get(GOBUN_INDEX) or []),
            "kibun_nested_parents": len(nested.get(KIBUN_INDEX) or []),
            "gobun_nested_parents": len(nested.get(GOBUN_INDEX) or []),
            "empty_stock_zero_width": True,
        },
        "glyphs": {
            "C6_from": f"{HANGUL_GONG:04X}",
            "DF_from": f"{HANGUL_BUN:04X}",
            "C6_offset": f"{c6_off:08X}",
            "DF_offset": f"{df_off:08X}",
        },
        "locations": location_rows,
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "allowlist_clean": True,
        },
    }
    atomic_json(OUT_REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate_sha256": report["candidate"]["sha256"],
                "checksum": report["candidate"]["ws_checksum"],
                "diff_bytes": report["diff"]["changed_bytes"],
                "locations": [
                    {
                        "abs": row["abs"],
                        "before_pad": row["before_pad"],
                        "after_pad": row["after_pad"],
                    }
                    for row in location_rows
                ],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
