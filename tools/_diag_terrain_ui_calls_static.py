#!/usr/bin/env python3
"""Static comparison of the UI helper calls surrounding the terrain leak."""
from __future__ import annotations

from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_16, CS_OP_MEM, Cs


ROOT = Path(__file__).resolve().parents[1]
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
STOCK_BASE = 0x800000


def disasm(rom: bytes, logical: int, size: int) -> None:
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    md.skipdata = True
    bank_base = logical & 0xFF0000
    origin = logical & 0xFFFF
    code = rom[STOCK_BASE + logical : STOCK_BASE + logical + size]
    print(f"\n=== {logical >> 16:02X}:{origin:04X} +{size:04X} ===")
    for insn in md.disasm(code, origin):
        print(
            f"{logical >> 16:02X}:{insn.address:04X} "
            f"{insn.bytes.hex().upper():<18} {insn.mnemonic:<8} {insn.op_str}"
        )


def call_hits(rom: bytes, target: bytes) -> list[int]:
    hits: list[int] = []
    start = STOCK_BASE + 0x740000
    end = STOCK_BASE + 0x800000
    while True:
        pos = rom.find(target, start, end)
        if pos < 0:
            break
        hits.append(pos - STOCK_BASE)
        start = pos + 1
    return hits


def main() -> None:
    rom = ROM.read_bytes()
    for logical, size in (
        (0x7A05A0, 0x500),
        (0x74E680, 0x280),
        (0x78B240, 0x180),
        (0x786100, 0x600),
        (0x7C0800, 0x600),
        (0x7C0F20, 0x280),
        (0x7B5D00, 0x180),
        (0x7E8D60, 0x280),
        (0x7EB1C0, 0x240),
        (0x7D5D80, 0x100),
        (0x7D1500, 0x480),
        (0x7D1B00, 0x400),
        (0x7D6010, 0x2C0),
        (0x7D6400, 0x120),
        (0x7D68E0, 0x120),
    ):
        disasm(rom, logical, size)

    for label, target in (
        ("EB88:0517", bytes.fromhex("9A170588EB")),
        ("EB88:0591", bytes.fromhex("9A910588EB")),
    ):
        hits = call_hits(rom, target)
        print(f"\n{label} hits={len(hits)}")
        print(" ".join(f"{x >> 16:02X}:{x & 0xFFFF:04X}" for x in hits))

    print("\nA000:07AC calls near immediate screen coordinates/field widths")
    text_call = bytes.fromhex("9AAC0700A0")
    hits = call_hits(rom, text_call)
    selected = []
    coordinate_immediates = (
        bytes.fromhex("B90C00"),  # mov cx,12
        bytes.fromhex("BA0600"),  # mov dx,6
        bytes.fromhex("B90A00"),  # mov cx,10 / push candidates
        bytes.fromhex("B80A00"),
        bytes.fromhex("6A0A"),
    )
    for hit in hits:
        at = STOCK_BASE + hit
        before = rom[max(STOCK_BASE, at - 72) : at]
        score = sum(pattern in before for pattern in coordinate_immediates)
        if score >= 2:
            selected.append(hit)
            print(
                f"{hit >> 16:02X}:{hit & 0xFFFF:04X} score={score} "
                f"ctx={before.hex().upper()}"
            )
    print(f"selected={len(selected)} total={len(hits)}")

    print("\n4C6B:25AA window calls carrying 16/6 immediates")
    for hit in call_hits(rom, bytes.fromhex("9AAA256B4C")):
        at = STOCK_BASE + hit
        before = rom[max(STOCK_BASE, at - 56) : at]
        has_16 = any(bytes([opcode, 0x10, 0x00]) in before for opcode in range(0xB8, 0xBC))
        has_6 = any(bytes([opcode, 0x06, 0x00]) in before for opcode in range(0xB8, 0xBC))
        if has_16 and has_6:
            print(f"WINDOW {hit >> 16:02X}:{hit & 0xFFFF:04X} {before.hex().upper()}")

    print("\nWRAM 19F8..19FF decoded direct references (linear bank disassembly)")
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    md.detail = True
    md.skipdata = True
    for bank in range(0x74, 0x80):
        code = rom[STOCK_BASE + (bank << 16) : STOCK_BASE + ((bank + 1) << 16)]
        for insn in md.disasm(code, 0):
            if insn.mnemonic == ".byte":
                continue
            refs = []
            for operand in insn.operands:
                if operand.type == CS_OP_MEM and 0x19F8 <= operand.mem.disp <= 0x19FF:
                    refs.append(operand.mem.disp)
            if refs:
                print(
                    f"{bank:02X}:{insn.address:04X} {insn.bytes.hex().upper():<18} "
                    f"{insn.mnemonic:<8} {insn.op_str} refs="
                    + ",".join(f"{x:04X}" for x in refs)
                )

if __name__ == "__main__":
    main()
