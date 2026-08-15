#!/usr/bin/env python3
"""Analyze 7A:0610 font loader and locate code caves."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monoeye_rom import find_rom, load_rom


def dump(rom: bytes, addr: int, n: int) -> None:
    print(f"==== {addr:06X}")
    data = rom[addr : addr + n]
    for i in range(0, len(data), 16):
        hx = data[i : i + 16].hex(" ")
        print(f"{addr + i:06X}: {hx}")


def main() -> None:
    rom = load_rom(find_rom(Path(".")))
    dump(rom, 0x7A05F0, 0xC0)
    dump(rom, 0x7A0760, 0x60)

    # Disassemble key bytes at 7A0610 manually
    # 3D 00 E0        cmp ax, 0E000h
    # 72 03           jb +3
    # 2D 20 DF        sub ax, 0DF20h
    # B9 40 04        mov cx, 0440h
    # BB 00 30        mov bx, 3000h
    # D1 E0 x4        shl ax,1 four times = *16
    # 03 C8           add cx, ax
    print("\nParsed 7A0610:")
    print("  cmp ax, E000; jb skip; sub ax, DF20")
    print("  mov cx, 0440; mov bx, 3000; shl ax,4; add cx, ax")
    print("  => far ptr BX:CX = 3000:(0440+index*16)  [bank40 window?]")

    b = rom[0x7A0000:0x7B0000]
    runs = []
    i = 0
    while i < len(b):
        if b[i] == 0xFF:
            j = i
            while j < len(b) and b[j] == 0xFF:
                j += 1
            if j - i >= 24:
                runs.append((i, j - i))
            i = j
        else:
            i += 1
    runs.sort(key=lambda x: -x[1])
    print("\nLargest 7A FF caves:")
    for off, n in runs[:15]:
        print(f"  7A:{off:04X} len={n}")

    # Also check right after the font routine for spare bytes
    dump(rom, 0x7A06B0, 0x40)


if __name__ == "__main__":
    main()
