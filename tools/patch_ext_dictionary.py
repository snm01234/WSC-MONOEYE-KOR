#!/usr/bin/env python3
"""
Extended dictionary hook PoC.

Stock F0–FE tokens index bank 5F pointer table at 7BCC (3831 entries).
This patch redirects index >= 3831 to a pointer table + phrases in bank 5E
trailing FF padding, via a hook at the stock load site 7A:0700.

Layout (defaults):
  5E:E22B  LE16 pointer table (N entries)
  5E:E22B+2N  null-terminated phrase payloads

Runtime (at 7A:0700, SI=index*2, ES=3000, bank already 5F/DF):
  if SI < 3831*2: AX = ES:[SI+7BCC]   (stock)
  else: map bank 5E/DE, AX = ES:[SI-3831*2 + EXT_PTR_OFF]
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_END,
    DICT_PTR_START,
    load_rom,
    patch_bank,
    slice_bank,
    update_ws_checksum,
)
from patch_font_hangul_hook import (  # noqa: E402
    EXT_CAVE,
    EXT_CAVE_MAX,
    EXT_CAVE_SEG,
    MAIN_CAVE,
    MAIN_CAVE_MAX,
    far_jmp,
    near_call,
    near_jmp,
    rel16,
)

# Stock dictionary
STOCK_DICT_COUNT = (DICT_PTR_END - DICT_PTR_START + 1) // 2  # 3831
STOCK_LOAD_SITE = 0x7A0700
STOCK_LOAD_EXPECT = bytes.fromhex("268b84cc7b")  # mov ax, es:[si+7BCC]
STOCK_LOAD_RETURN = 0x7A0705  # next insn after load (lcall FAD0)

# Bank map helpers in CS=8000
BANK_MAP_SEG = 0x8000
BANK_MAP_OFF = 0xDEB5  # OUT C3h, AL

# Extended dict in bank 5E
EXT_SEG = 0x5E
EXT_BANK_AL = 0xDE  # 5E | 0x80
EXT_PTR_OFF_DEFAULT = 0xE22B

# The Hangul primary occupies 7F:FC4E-FC8B. Keep the dictionary helper at
# its historical fixed address so moving the primary does not shift ABI targets.
DICT_HELPER_OFF = 0x7FFC8C
DICT_HELPER_FILE = DICT_HELPER_OFF

# Near trampoline in leftover 7A main cave (after sticky Hangul store).
DICT_TRAMP_FILE = MAIN_CAVE + 56  # 7A:FFED


def build_ext_dict_helper(*, ext_ptr_off: int, stock_count: int = STOCK_DICT_COUNT) -> bytes:
    """
    Far-callable helper. Entry: SI=index*2, ES=3000. Exit: AX=phrase offset, retf.
    """
    stock_lim = stock_count * 2  # 0x1DEE
    out = bytearray()
    out += b"\x81\xFE" + struct.pack("<H", stock_lim)  # cmp si, stock_lim
    jb_stock_at = len(out)
    out += b"\x72\x00"  # jb stock

    # Map bank 5E
    out += b"\xB0" + bytes([EXT_BANK_AL])  # mov al, DE
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)  # lcall 8000:DEB5
    out += b"\x56"  # push si
    out += b"\x81\xEE" + struct.pack("<H", stock_lim)  # sub si, stock_lim
    out += b"\x26\x8B\x84" + struct.pack("<H", ext_ptr_off)  # mov ax, es:[si+EXT]
    out += b"\x5E"  # pop si
    out += b"\xCB"  # retf

    stock_off = len(out)
    out += b"\x26\x8B\x84\xCC\x7B"  # mov ax, es:[si+7BCC]
    out += b"\xCB"  # retf

    # patch jb
    disp = stock_off - (jb_stock_at + 2)
    if not -128 <= disp <= 127:
        raise RuntimeError("jb stock out of range")
    out[jb_stock_at + 1] = disp & 0xFF
    return bytes(out)


def build_near_trampoline(helper_off: int) -> bytes:
    """Near-callable stub: far-call helper, then near ret to 7A:0705."""
    out = bytearray()
    out += b"\x9A" + struct.pack("<HH", helper_off & 0xFFFF, EXT_CAVE_SEG)
    out += b"\xC3"
    return bytes(out)


def install_ext_dict_hook(
    rom: bytearray,
    *,
    ext_ptr_off: int = EXT_PTR_OFF_DEFAULT,
    stock_count: int = STOCK_DICT_COUNT,
    slot_count: int = 64,
    force_format: bool = False,
) -> dict:
    if bytes(rom[STOCK_LOAD_SITE : STOCK_LOAD_SITE + 5]) not in {
        STOCK_LOAD_EXPECT,
        # already hooked: near call + nops
    }:
        cur = bytes(rom[STOCK_LOAD_SITE : STOCK_LOAD_SITE + 5])
        if cur[0] != 0xE8:
            raise RuntimeError(f"Unexpected stock load site: {cur.hex()}")

    helper = build_ext_dict_helper(ext_ptr_off=ext_ptr_off, stock_count=stock_count)
    helper_abs = DICT_HELPER_FILE
    tramp_abs = DICT_TRAMP_FILE
    tramp = build_near_trampoline(helper_abs & 0xFFFF)

    # Fit checks
    if helper_abs + len(helper) > EXT_CAVE + EXT_CAVE_MAX:
        raise RuntimeError(
            f"Dict helper overflows Hangul ext cave budget: "
            f"{helper_abs:06X}+{len(helper)}"
        )
    if tramp_abs + len(tramp) > MAIN_CAVE + MAIN_CAVE_MAX:
        raise RuntimeError("Dict trampoline overflows main cave")

    # Ensure helper region is free (FF) unless re-installing same hook
    region = bytes(rom[helper_abs : helper_abs + len(helper)])
    if not all(b == 0xFF for b in region):
        # allow overwrite if it looks like our previous helper (starts with cmp si)
        if region[:2] != b"\x81\xFE":
            raise RuntimeError(
                f"Dict helper region {helper_abs:06X} not free: {region[:8].hex()}"
            )

    tramp_region = bytes(rom[tramp_abs : tramp_abs + len(tramp)])
    if not all(b == 0xFF for b in tramp_region) and tramp_region[0] not in (0x9A, 0xE8, 0xEA):
        raise RuntimeError(f"Dict trampoline region {tramp_abs:06X} not free")

    rom[helper_abs : helper_abs + len(helper)] = helper
    rom[tramp_abs : tramp_abs + len(tramp)] = tramp

    # 7A:0700 → near call trampoline + NOP NOP
    call = near_call(STOCK_LOAD_SITE, tramp_abs)
    rom[STOCK_LOAD_SITE : STOCK_LOAD_SITE + 5] = call + b"\x90\x90"

    # Initialize empty extended table (pointers → empty zstrings) if blank
    # or when growing/rebuilding slot count (force_format).
    bank = bytearray(slice_bank(rom, EXT_SEG))
    phrase_at = ext_ptr_off + slot_count * 2
    if phrase_at >= BANK_SIZE:
        raise RuntimeError("Extended ptr table exceeds bank 5E")
    ptr_region = bank[ext_ptr_off : ext_ptr_off + slot_count * 2]
    need_format = force_format or all(b == 0xFF for b in ptr_region)
    if need_format:
        # Wipe ptr+payload region so a smaller prior table cannot poison
        # a larger slot_count (pointers must not land inside old phrases).
        bank[ext_ptr_off:] = b"\xFF" * (BANK_SIZE - ext_ptr_off)
        if phrase_at >= BANK_SIZE:
            raise RuntimeError("No room for empty phrase")
        bank[phrase_at] = 0x00
        empty_off = phrase_at
        for i in range(slot_count):
            off = ext_ptr_off + i * 2
            bank[off] = empty_off & 0xFF
            bank[off + 1] = (empty_off >> 8) & 0xFF
        patch_bank(rom, EXT_SEG, bank)

    return {
        "stock_load_site": f"{STOCK_LOAD_SITE:06X}",
        "trampoline": f"{tramp_abs:06X}",
        "helper": f"{helper_abs:06X}",
        "helper_len": len(helper),
        "ext_seg": f"{EXT_SEG:02X}",
        "ext_ptr_off": f"{ext_ptr_off:04X}",
        "stock_count": stock_count,
        "slot_count": slot_count,
        "index_base": stock_count,
        "index_end": stock_count + slot_count - 1,
    }


def write_ext_dictionary_slots(
    rom: bytearray,
    slot_payload: Dict[int, bytes],
    *,
    ext_ptr_off: int = EXT_PTR_OFF_DEFAULT,
    stock_count: int = STOCK_DICT_COUNT,
    slot_count: int = 64,
    allow_aux_consumers: bool = False,
) -> dict:
    """
    Write payloads for absolute dict indices in [stock_count, stock_count+slot_count).

    Hangul on aux/name75-live slots refused unless ``allow_aux_consumers``.
    """
    if not slot_payload:
        return {"written": 0, "phrase_end": ext_ptr_off + slot_count * 2}

    from expand_dictionary import guard_hangul_slot_writes

    guard_hangul_slot_writes(
        rom, slot_payload, allow_aux_consumers=allow_aux_consumers
    )

    bank = bytearray(slice_bank(rom, EXT_SEG))
    # Discover current phrase cursor: max end of existing non-empty entries
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
                f"Extended dict overflow writing index {index} "
                f"(need {cursor + need:#x})"
            )
        bank[cursor : cursor + len(encoded)] = encoded
        bank[cursor + len(encoded)] = 0
        ptr_at = ext_ptr_off + local * 2
        bank[ptr_at] = cursor & 0xFF
        bank[ptr_at + 1] = (cursor >> 8) & 0xFF
        cursor += need
        written += 1

    patch_bank(rom, EXT_SEG, bank)
    return {"written": written, "phrase_end": cursor, "bytes_used": cursor - ext_ptr_off}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--slots", type=int, default=64)
    ap.add_argument("--ext-ptr-off", default=f"{EXT_PTR_OFF_DEFAULT:X}")
    ap.add_argument(
        "--force-format",
        action="store_true",
        help="Wipe 5E:E22B+ and rebuild empty ptr table (required when growing slots)",
    )
    ap.add_argument(
        "--out-meta",
        type=Path,
        default=ROOT / "out" / "patch" / "ext_dictionary_meta.json",
    )
    args = ap.parse_args()

    rom = bytearray(load_rom(args.rom))
    ext_ptr_off = int(args.ext_ptr_off, 16)
    report = install_ext_dict_hook(
        rom,
        ext_ptr_off=ext_ptr_off,
        slot_count=args.slots,
        force_format=args.force_format,
    )
    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    args.out.write_bytes(rom)
    args.out_meta.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Installed ext dict hook: slots={report['slot_count']} "
        f"idx={report['index_base']}..{report['index_end']} "
        f"helper={report['helper']} checksum={report['checksum']}"
    )
    print(f"Wrote {args.out}")
    print(f"Wrote {args.out_meta}")


if __name__ == "__main__":
    main()
