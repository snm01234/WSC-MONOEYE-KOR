#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16,CS_OP_MEM
r=bytes(load_rom(ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc'));sb=stock_base(r);md=Cs(CS_ARCH_X86,CS_MODE_16);md.detail=True
bank=0x78;a=0x9C5A;b=0xA130;data=r[sb+(bank<<16)+a:sb+(bank<<16)+b]
for ins in md.disasm(data,a):
    interesting=False
    s=ins.op_str.lower()
    if any(x in s for x in ('[bp - 4]','[bp-4]','[bp - 2]','[bp-2]','es:[bx]','es:[di]','es:[si]')): interesting=True
    if ins.mnemonic in ('stosw','stosb','movsw','movsb'): interesting=True
    if interesting: print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():18s} {ins.mnemonic:8s} {ins.op_str}')
