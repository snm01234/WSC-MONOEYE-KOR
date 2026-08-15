#!/usr/bin/env python3
"""Disassemble program banks and list likely text/font routines."""

from __future__ import annotations

import sys
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_16, Cs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import load_rom  # noqa: E402


def main() -> None:
    rom = load_rom()
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    md.skipdata = True

    targets = (
        "0x4000",
        "0x16ae",
        "0x1a6e",
        "0xf47e",
        "0x1fe",
        "0x2000",
        "0xdf20",
    )
    matches: list[tuple[int, str, str]] = []
    for segment in range(0x7A, 0x80):
        base = segment * 0x10000
        code = bytes(rom[base : base + 0x10000])
        for insn in md.disasm(code, base):
            operands = insn.op_str.lower()
            if any(target in operands for target in targets):
                matches.append((insn.address, insn.mnemonic, insn.op_str))

    print(f"Matches: {len(matches)}")
    for address, mnemonic, operands in matches:
        print(f"{address:06X}: {mnemonic:<8} {operands}")

    # Focused, linear disassembly of known text decoder.
    for start, size in ((0x7A0510, 0x30), (0x7F05B0, 0xC0), (0x7A06D0, 0x100), (0x7D1640, 0x80)):
        print(f"\n=== {start:06X} ===")
        for insn in md.disasm(bytes(rom[start : start + size]), start):
            print(f"{insn.address:06X}: {insn.mnemonic:<8} {insn.op_str}")


if __name__ == "__main__":
    main()
