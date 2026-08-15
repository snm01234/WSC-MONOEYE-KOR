#!/usr/bin/env python3
"""Locate character-code → glyph-offset conversion logic."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom  # noqa: E402


def main() -> None:
    rom = load_rom()
    print("=== cmp al, 0xE0 sites ===")
    for seg in range(0x00, 0x80):
        base = seg * 0x10000
        bank = bytes(rom[base : base + 0x10000])
        start = 0
        while True:
            i = bank.find(b"\x3c\xe0", start)
            if i < 0:
                break
            h = base + i
            ctx = rom[h - 8 : h + 28]
            print(f"{h:06X}: {ctx.hex(' ')}")
            start = i + 1

    print("\n=== shl reg,7 (x128) in program banks ===")
    for seg in range(0x7A, 0x80):
        base = seg * 0x10000
        bank = bytes(rom[base : base + 0x10000])
        for name, opc in [
            ("ax", b"\xc1\xe0\x07"),
            ("cx", b"\xc1\xe1\x07"),
            ("dx", b"\xc1\xe2\x07"),
            ("bx", b"\xc1\xe3\x07"),
        ]:
            start = 0
            while True:
                i = bank.find(opc, start)
                if i < 0:
                    break
                abs_off = base + i
                pre = rom[abs_off - 32 : abs_off + 8]
                print(f"{abs_off:06X} shl {name},7  {pre.hex(' ')}")
                start = i + 1

    # Look for far call targets that mention bank 40 / offset math
    print("\n=== imm16 0x0040 near program (possible font seg) ===")
    for seg in range(0x7A, 0x80):
        base = seg * 0x10000
        bank = bytes(rom[base : base + 0x10000])
        start = 0
        while True:
            i = bank.find(b"\x40\x00", start)  # little-endian 0x0040
            if i < 0:
                break
            # filter: preceding opcode looks like mov xx, imm16 (B8-BF) or push
            if i >= 1 and rom[base + i - 1] in range(0xB8, 0xC0):
                abs_off = base + i - 1
                print(f"{abs_off:06X}: {rom[abs_off:abs_off+12].hex(' ')}")
            start = i + 1


if __name__ == "__main__":
    main()
