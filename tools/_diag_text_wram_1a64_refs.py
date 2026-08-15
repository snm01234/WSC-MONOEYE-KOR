#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom, stock_base
from capstone import Cs, CS_ARCH_X86, CS_MODE_16, CS_OP_MEM

ROM=ROOT/'out/patch/monoeye_ko_expanded.wsc'
rom=bytes(load_rom(ROM)); sb=stock_base(rom)
md=Cs(CS_ARCH_X86,CS_MODE_16); md.detail=True
TARGETS={0x1A64,0x1A68,0x1A6E}

for bank in range(0x70,0x80):
    start=sb+(bank<<16); data=rom[start:start+0x10000]
    hits=[]
    for ins in md.disasm(data,0):
        vals=[]
        for op in ins.operands:
            if op.type==CS_OP_MEM:
                disp=op.mem.disp & 0xffff
                if disp in TARGETS:
                    vals.append(disp)
        if vals:
            hits.append((ins.address,ins.mnemonic,ins.op_str,vals))
    if hits:
        print(f'BANK {bank:02X}')
        for h in hits:
            print(f' {bank:02X}:{h[0]:04X} {h[1]:8s} {h[2]:24s} targets={",".join(f"{x:04X}" for x in h[3])}')

print('\nBANK7A window 0400-0900 references 1A50-1A90')
start=sb+(0x7A<<16); data=rom[start+0x0400:start+0x0900]
for ins in md.disasm(data,0x0400):
    refs=[]
    for op in ins.operands:
        if op.type==CS_OP_MEM:
            disp=op.mem.disp & 0xffff
            if 0x1A50<=disp<=0x1A90:
                refs.append(disp)
    if refs:
        print(f' 7A:{ins.address:04X} {ins.mnemonic:8s} {ins.op_str:28s} refs={",".join(f"{x:04X}" for x in refs)}')
