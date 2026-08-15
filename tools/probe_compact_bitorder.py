#!/usr/bin/env python3
"""Find compact-font bit order that matches recognizable JP glyphs."""

from __future__ import annotations

from pathlib import Path

rom = Path("SD Gundam G Generation Mono-Eye Gundams.wsc").read_bytes()


def variants(record: bytes):
    assert len(record) == 16
    out = {}

    # A: 4px/byte, low-bit first, row-major (current)
    def a():
        pix = []
        for b in record:
            for s in (0, 2, 4, 6):
                pix.append((b >> s) & 3)
        return [pix[r * 8 : (r + 1) * 8] for r in range(8)]

    # B: 4px/byte, high-bit first
    def b():
        pix = []
        for b in record:
            for s in (6, 4, 2, 0):
                pix.append((b >> s) & 3)
        return [pix[r * 8 : (r + 1) * 8] for r in range(8)]

    # C: 2 bytes/row planar? try bytes as columns
    def c():
        grid = [[0] * 8 for _ in range(8)]
        for row in range(8):
            b0, b1 = record[row * 2], record[row * 2 + 1]
            for x in range(8):
                bit = 7 - x
                lo = (b0 >> bit) & 1
                hi = (b1 >> bit) & 1
                grid[row][x] = lo | (hi << 1)
        return grid

    # D: WS 2bpp chunky alternate - pairs of bits MSB
    def d():
        pix = []
        for b in record:
            for s in (0, 2, 4, 6):
                pix.append((b >> s) & 3)
        # column-major 8x8
        return [[pix[x * 8 + y] for x in range(8)] for y in range(8)]

    # E: like A but each pair of bytes is a row of 8 pixels (2bpp packed differently)
    def e():
        grid = [[0] * 8 for _ in range(8)]
        for row in range(8):
            w = record[row * 2] | (record[row * 2 + 1] << 8)
            for x in range(8):
                grid[row][x] = (w >> (x * 2)) & 3
        return grid

    # F: row words, MSB pixel first
    def f():
        grid = [[0] * 8 for _ in range(8)]
        for row in range(8):
            w = record[row * 2] | (record[row * 2 + 1] << 8)
            for x in range(8):
                grid[row][x] = (w >> ((7 - x) * 2)) & 3
        return grid

    for name, fn in [
        ("A_lo_row", a),
        ("B_hi_row", b),
        ("C_planar", c),
        ("D_lo_col", d),
        ("E_rowword_lo", e),
        ("F_rowword_hi", f),
    ]:
        out[name] = fn()
    return out


def show(grid):
    return "\n".join("".join(".#%@"[min(3, v)] for v in row) for row in grid)


samples = {
    "『": rom[0x400440 + 0x68 * 16 : 0x400440 + 0x68 * 16 + 16],
    "。": rom[0x400440 + 0x0A * 16 : 0x400440 + 0x0A * 16 + 16],
    "ア": rom[0x400440 + 0x26 * 16 : 0x400440 + 0x26 * 16 + 16],
    "３": rom[0x400440 + 0x151 * 16 : 0x400440 + 0x151 * 16 + 16]
    if False
    else None,
}

# find code for fullwidth ３ from tbl if needed — skip
for name, rec in samples.items():
    if rec is None:
        continue
    print("=" * 40, name, rec.hex())
    for vname, grid in variants(rec).items():
        print("---", vname)
        print(show(grid))
        print()
