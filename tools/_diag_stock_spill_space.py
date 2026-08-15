#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from monoeye_rom import BANK_SIZE, Dictionary, load_rom, slice_bank, SEG_DICT

rom = bytes(load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc"))
bank = slice_bank(rom, SEG_DICT)
cursor = _stock_phrase_cursor(rom)
print("spill_floor", hex(SPILL_FLOOR), "cursor", hex(cursor), "remain", BANK_SIZE - cursor)

# FF runs in whole bank
runs = []
i = 0
while i < BANK_SIZE:
    if bank[i] != 0xFF:
        i += 1
        continue
    j = i
    while j < BANK_SIZE and bank[j] == 0xFF:
        j += 1
    if j - i >= 16:
        runs.append((i, j, j - i))
    i = j
runs.sort(key=lambda r: -r[2])
print("ff_runs>=16", len(runs))
for a,b,n in runs[:15]:
    print(f"  {a:04X}-{b:04X} {n}")
