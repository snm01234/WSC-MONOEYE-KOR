#!/usr/bin/env python3
"""Deeper analysis of glyph index conversion in program banks only."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom  # noqa: E402


def dump(rom: bytearray, abs_off: int, n: int = 96) -> None:
    print(f"--- @{abs_off:06X} ---")
    for i in range(0, n, 16):
        chunk = rom[abs_off + i : abs_off + i + 16]
        print(f"{abs_off+i:06X}: {chunk.hex(' ')}")


def main() -> None:
    rom = load_rom()

    print("=== cmp al,E0 / cmp al,F0 in 7A-7F ===")
    for seg in range(0x7A, 0x80):
        base = seg * 0x10000
        bank = bytes(rom[base : base + 0x10000])
        for needle, label in [(b"\x3c\xe0", "E0"), (b"\x3c\xf0", "F0"), (b"\x3c\xe7", "E7")]:
            start = 0
            while True:
                i = bank.find(needle, start)
                if i < 0:
                    break
                print(f"{base+i:06X} cmp al,{label}")
                dump(rom, base + i - 16, 64)
                start = i + 1

    print("\n=== search imm 16AE (RAM text buffer) LE ===")
    needle = b"\xae\x16"
    for seg in range(0x7A, 0x80):
        base = seg * 0x10000
        bank = bytes(rom[base : base + 0x10000])
        start = 0
        while True:
            i = bank.find(needle, start)
            if i < 0:
                break
            print(f"{base+i:06X}")
            dump(rom, base + i - 16, 48)
            start = i + 1

    print("\n=== search imm 0F47E / 001FE ===")
    for needle, name in [(b"\x7e\xf4", "0F47E-ish"), (b"\xfe\x01", "001FE")]:
        print(name)
        for seg in range(0x7A, 0x80):
            base = seg * 0x10000
            bank = bytes(rom[base : base + 0x10000])
            start = 0
            count = 0
            while True:
                i = bank.find(needle, start)
                if i < 0:
                    break
                if count < 3:
                    print(f"  {base+i:06X}: {rom[base+i-8:base+i+12].hex(' ')}")
                count += 1
                start = i + 1
            if count:
                print(f"  total in {seg:02X}: {count}")

    # Known length walker at 7D1677 — dump larger function
    print("\n=== function around 7D1650 (E0 length walk) ===")
    dump(rom, 0x7D1600, 0x100)

    # Look for mul by 128: 6B xx 80 / 69 xx 80 00 / C1 E0 07
    print("\n=== imul/shl *128 in 7A-7F ===")
    for seg in range(0x7A, 0x80):
        base = seg * 0x10000
        bank = bytes(rom[base : base + 0x10000])
        for opc, label in [
            (b"\xc1\xe0\x07", "shl ax,7"),
            (b"\xc1\xe1\x07", "shl cx,7"),
            (b"\xc1\xe2\x07", "shl dx,7"),
            (b"\xc1\xe3\x07", "shl bx,7"),
            (b"\xd1\xe0", "shl ax,1"),
        ]:
            start = 0
            while True:
                i = bank.find(opc, start)
                if i < 0:
                    break
                # only show if nearby has E0 compare within -64..+64? always print shl,7
                if opc.startswith(b"\xc1"):
                    abs_off = base + i
                    print(f"{abs_off:06X} {label}: {rom[abs_off-20:abs_off+12].hex(' ')}")
                start = i + 1


if __name__ == "__main__":
    main()
