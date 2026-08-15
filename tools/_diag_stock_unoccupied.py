#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import BANK_SIZE, DICT_PTR_START, Dictionary, load_rom, slice_bank, SEG_DICT

rom = bytes(load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc"))
d = Dictionary(rom)
bank = bytearray(slice_bank(rom, SEG_DICT))
occ = bytearray(BANK_SIZE)
for i in range(d.stock_count):
    p = d.ptrs[i]
    if p == 0 or p >= BANK_SIZE:
        continue
    end = p
    while end < BANK_SIZE and bank[end] != 0:
        end += 1
    end = min(BANK_SIZE, end + 1)
    for j in range(p, end):
        occ[j] = 1
# pointer table is occupied
for j in range(DICT_PTR_START, min(BANK_SIZE, DICT_PTR_START + d.stock_count * 2)):
    occ[j] = 1
holes = []
i = 0
while i < BANK_SIZE:
    if occ[i]:
        i += 1
        continue
    j = i
    while j < BANK_SIZE and not occ[j]:
        j += 1
    if j - i >= 8:
        holes.append((i, j, j - i))
    i = j
holes.sort(key=lambda r: -r[2])
print("holes", len(holes), "total_free", sum(h[2] for h in holes))
for a,b,n in holes[:20]:
    print(f"  {a:04X}-{b:04X} {n}")
