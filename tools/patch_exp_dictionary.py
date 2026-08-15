#!/usr/bin/env python3
"""
Move extended dictionary phrase storage to 16MB expansion bank 0x10.

Token indices stay 3831–4095 (FF page, hard cap 0xFFF). Only the *payload*
bank changes: stock 5E trailing pad (~7KB) → expand bank10 (~64KB).

Runtime helper (7F:FC8C) maps ROM1 with AL=0x10 instead of AL=DE (5E).
Requires prepended 16MB ROM (pad3 base). Does not touch pad_hi at 7F:FCAB+.

Layout (bank 0x10):
  0000  LE16 pointer table (N entries)
  0000+2N  null-terminated phrases
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    bank_al_expansion,
    is_expanded_rom,
    load_rom,
    patch_expansion_bank,
    slice_bank,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
    ws_header,
)
from patch_ext_dictionary import (  # noqa: E402
    DICT_HELPER_OFF,
    DICT_TRAMP_FILE,
    STOCK_DICT_COUNT,
    STOCK_LOAD_EXPECT,
    STOCK_LOAD_SITE,
    build_near_trampoline,
)
from patch_font_hangul_hook import (  # noqa: E402
    EXT_CAVE,
    EXT_CAVE_MAX,
    EXT_CAVE_SEG,
    MAIN_CAVE,
    MAIN_CAVE_MAX,
    near_call,
)

# Keep helper ≤31B so it ends before pad_hi at 7FFCAB.
PAD_HI_FILE = 0x7FFCAB
EXP_SEG = 0x10
EXP_BANK_AL = bank_al_expansion(EXP_SEG)  # 0x10
EXP_PTR_OFF_DEFAULT = 0x0000
MAX_EXT_SLOTS = 0x1000 - STOCK_DICT_COUNT  # 265 (indices 3831..4095)

BANK_MAP_SEG = 0x8000
BANK_MAP_OFF = 0xDEB5

# Legacy 5E table (migrate source)
LEGACY_EXT_SEG = 0x5E
LEGACY_EXT_PTR = 0xE22B


def sab(rom: bytes | bytearray, off: int) -> int:
    return stock_base(rom) + off


def build_exp_dict_helper(
    *,
    ext_ptr_off: int,
    stock_count: int = STOCK_DICT_COUNT,
    bank_al: int = EXP_BANK_AL,
) -> bytes:
    """Far-callable. Entry: SI=index*2, ES=3000. Exit: AX=phrase offset, retf."""
    stock_lim = stock_count * 2
    out = bytearray()
    out += b"\x81\xFE" + struct.pack("<H", stock_lim)
    jb_stock_at = len(out)
    out += b"\x72\x00"
    out += b"\xB0" + bytes([bank_al & 0xFF])
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\x56"
    out += b"\x81\xEE" + struct.pack("<H", stock_lim)
    out += b"\x26\x8B\x84" + struct.pack("<H", ext_ptr_off & 0xFFFF)
    out += b"\x5E"
    out += b"\xCB"
    stock_off = len(out)
    out += b"\x26\x8B\x84\xCC\x7B"
    out += b"\xCB"
    disp = stock_off - (jb_stock_at + 2)
    if not -128 <= disp <= 127:
        raise RuntimeError("jb stock out of range")
    out[jb_stock_at + 1] = disp & 0xFF
    return bytes(out)


def install_exp_dict_hook(
    rom: bytearray,
    *,
    ext_ptr_off: int = EXP_PTR_OFF_DEFAULT,
    stock_count: int = STOCK_DICT_COUNT,
    slot_count: int = MAX_EXT_SLOTS,
    force_format: bool = False,
    migrate_from_5e: bool = True,
) -> dict:
    if not is_expanded_rom(rom):
        raise RuntimeError("expansion dictionary requires a 16MB prepended ROM")
    if not 1 <= slot_count <= MAX_EXT_SLOTS:
        raise RuntimeError(f"slot_count must be 1..{MAX_EXT_SLOTS}, got {slot_count}")

    helper = build_exp_dict_helper(ext_ptr_off=ext_ptr_off, stock_count=stock_count)
    if sab(rom, DICT_HELPER_OFF) + len(helper) > sab(rom, PAD_HI_FILE):
        raise RuntimeError(
            f"helper {len(helper)}B would overwrite pad_hi at {PAD_HI_FILE:06X}"
        )
    if DICT_HELPER_OFF + len(helper) > EXT_CAVE + EXT_CAVE_MAX:
        raise RuntimeError("helper exceeds EXT_CAVE_MAX")

    tramp = build_near_trampoline(DICT_HELPER_OFF & 0xFFFF)
    if DICT_TRAMP_FILE + len(tramp) > MAIN_CAVE + MAIN_CAVE_MAX:
        raise RuntimeError("trampoline overflows main cave")

    # Preserve pad_hi: only write helper bytes.
    rom[sab(rom, DICT_HELPER_OFF) : sab(rom, DICT_HELPER_OFF) + len(helper)] = helper
    rom[sab(rom, DICT_TRAMP_FILE) : sab(rom, DICT_TRAMP_FILE) + len(tramp)] = tramp

    site = sab(rom, STOCK_LOAD_SITE)
    cur = bytes(rom[site : site + 5])
    if cur != STOCK_LOAD_EXPECT and cur[0] != 0xE8:
        raise RuntimeError(f"Unexpected stock load site: {cur.hex()}")
    call = near_call(STOCK_LOAD_SITE, DICT_TRAMP_FILE)
    rom[site : site + 5] = call + b"\x90\x90"

    migrated = 0
    legacy_payloads: Dict[int, bytes] = {}
    if migrate_from_5e:
        legacy_payloads = _read_legacy_5e_payloads(
            rom, stock_count=stock_count, slot_count=slot_count
        )

    bank = bytearray(slice_expansion_bank(rom, EXP_SEG))
    phrase_at = ext_ptr_off + slot_count * 2
    if phrase_at >= BANK_SIZE:
        raise RuntimeError("ptr table exceeds expansion bank")
    ptr_region = bank[ext_ptr_off : ext_ptr_off + slot_count * 2]
    need_format = force_format or all(b == 0xFF for b in ptr_region) or bool(
        legacy_payloads
    )
    if need_format:
        bank[:] = b"\xFF" * BANK_SIZE
        bank[phrase_at] = 0x00
        empty_off = phrase_at
        for i in range(slot_count):
            struct.pack_into("<H", bank, ext_ptr_off + i * 2, empty_off)
        patch_expansion_bank(rom, EXP_SEG, bank)
        if legacy_payloads:
            # Relocate existing 5E phrases as-is. Aux "consumers" of FF-page
            # tokens are byte collisions in UI banks, not ownership of these
            # payloads — blocking migrate would break cold rebuild.
            wr = write_exp_dictionary_slots(
                rom,
                legacy_payloads,
                ext_ptr_off=ext_ptr_off,
                stock_count=stock_count,
                slot_count=slot_count,
                allow_aux_consumers=True,
            )
            migrated = wr["written"]

    return {
        "stock_load_site": f"{STOCK_LOAD_SITE:06X}",
        "trampoline": f"{DICT_TRAMP_FILE:06X}",
        "helper": f"{DICT_HELPER_OFF:06X}",
        "helper_len": len(helper),
        "ext_seg": f"{EXP_SEG:02X}",
        "ext_bank_al": f"{EXP_BANK_AL:02X}",
        "ext_ptr_off": f"{ext_ptr_off:04X}",
        "ext_in_expansion": True,
        "stock_count": stock_count,
        "slot_count": slot_count,
        "index_base": stock_count,
        "index_end": stock_count + slot_count - 1,
        "max_ext_slots": MAX_EXT_SLOTS,
        "migrated_from_5e": migrated,
        "phrase_budget_bytes": BANK_SIZE - phrase_at,
    }


def _read_legacy_5e_payloads(
    rom: bytes | bytearray,
    *,
    stock_count: int,
    slot_count: int,
) -> Dict[int, bytes]:
    """Pull non-empty phrases from the old bank5E ext table if present."""
    bank = slice_bank(rom, LEGACY_EXT_SEG)
    region = bank[LEGACY_EXT_PTR : LEGACY_EXT_PTR + 4]
    if all(b == 0xFF for b in region):
        return {}
    d = Dictionary(
        rom,
        count=stock_count + slot_count,
        ext_ptr_off=LEGACY_EXT_PTR,
        ext_seg=LEGACY_EXT_SEG,
        stock_count=stock_count,
        ext_in_expansion=False,
    )
    out: Dict[int, bytes] = {}
    for index in range(stock_count, stock_count + slot_count):
        raw = d.raw_entry(index, max_len=512)
        if raw:
            out[index] = raw
    return out


def write_exp_dictionary_slots(
    rom: bytearray,
    slot_payload: Dict[int, bytes],
    *,
    ext_ptr_off: int = EXP_PTR_OFF_DEFAULT,
    stock_count: int = STOCK_DICT_COUNT,
    slot_count: int = MAX_EXT_SLOTS,
    allow_aux_consumers: bool = False,
    locs: dict | None = None,
) -> dict:
    if not is_expanded_rom(rom):
        raise RuntimeError("write_exp_dictionary_slots requires 16MB ROM")
    if not slot_payload:
        return {"written": 0, "phrase_end": ext_ptr_off + slot_count * 2}

    from expand_dictionary import guard_hangul_slot_writes

    guard_hangul_slot_writes(
        rom,
        slot_payload,
        allow_aux_consumers=allow_aux_consumers,
        locs=locs,
    )

    bank = bytearray(slice_expansion_bank(rom, EXP_SEG))
    cursor = ext_ptr_off + slot_count * 2
    for i in range(slot_count):
        poff = bank[ext_ptr_off + i * 2] | (bank[ext_ptr_off + i * 2 + 1] << 8)
        if poff < cursor or poff >= BANK_SIZE:
            continue
        end = poff
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        end += 1
        cursor = max(cursor, end)

    written = 0
    for index, encoded in sorted(slot_payload.items()):
        local = index - stock_count
        if not 0 <= local < slot_count:
            raise RuntimeError(f"Index {index} outside extended range")
        need = len(encoded) + 1
        if cursor + need > BANK_SIZE:
            raise RuntimeError(
                f"Expansion dict overflow at index {index} "
                f"(need {cursor + need:#x} > {BANK_SIZE:#x})"
            )
        bank[cursor : cursor + len(encoded)] = encoded
        bank[cursor + len(encoded)] = 0
        struct.pack_into("<H", bank, ext_ptr_off + local * 2, cursor)
        cursor += need
        written += 1

    patch_expansion_bank(rom, EXP_SEG, bank)
    return {
        "written": written,
        "phrase_end": cursor,
        "bytes_used": cursor - ext_ptr_off,
        "bytes_free": BANK_SIZE - cursor,
    }


def make_exp_dictionary(rom: bytes | bytearray, meta: dict) -> Dictionary:
    stock = int(meta.get("stock_count", STOCK_DICT_COUNT))
    slots = int(meta.get("slot_count", 0))
    ext_off = int(meta.get("ext_ptr_off", f"{EXP_PTR_OFF_DEFAULT:04X}"), 16)
    ext_seg = int(meta.get("ext_seg", f"{EXP_SEG:02X}"), 16)
    if slots <= 0:
        return Dictionary(rom)
    return Dictionary(
        rom,
        count=stock + slots,
        ext_ptr_off=ext_off,
        ext_seg=ext_seg,
        stock_count=stock,
        ext_in_expansion=bool(meta.get("ext_in_expansion", True)),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "-i",
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
        help="16MB tip (or a fresh pad3 image). force_format rebuilds bank10.",
    )
    ap.add_argument(
        "-o",
        "--out",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument("--slots", type=int, default=MAX_EXT_SLOTS)
    ap.add_argument("--ext-ptr-off", default=f"{EXP_PTR_OFF_DEFAULT:X}")
    ap.add_argument("--no-migrate", action="store_true")
    ap.add_argument(
        "--out-meta",
        type=Path,
        default=ROOT / "out" / "patch" / "exp_dictionary_meta.json",
    )
    args = ap.parse_args()

    rom = load_rom(args.rom)
    report = install_exp_dict_hook(
        rom,
        ext_ptr_off=int(args.ext_ptr_off, 16),
        slot_count=args.slots,
        force_format=True,
        migrate_from_5e=not args.no_migrate,
    )
    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    report["header"] = ws_header(rom)

    # Verify helper AL and a migrated probe
    helper = bytes(
        rom[sab(rom, DICT_HELPER_OFF) : sab(rom, DICT_HELPER_OFF) + report["helper_len"]]
    )
    assert bytes([0xB0, EXP_BANK_AL]) in helper
    assert b"\xb0\xde" not in helper  # no longer maps stock 5E
    # pad_hi intact
    assert rom[sab(rom, PAD_HI_FILE) : sab(rom, PAD_HI_FILE) + 2] == bytes.fromhex(
        "81fb"
    )

    d = make_exp_dictionary(rom, report)
    sample = {}
    for idx in (report["index_base"], report["index_end"]):
        if idx < d.count:
            raw = d.raw_entry(idx)
            sample[f"{idx:04X}"] = {"len": len(raw), "head": raw[:8].hex()}
    report["sample_entries"] = sample

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(rom)
    args.out_meta.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
