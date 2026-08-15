#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
r=bytes(load_rom(ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc'));sb=stock_base(r)
md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank,a,b in [(0x7E,0xD380,0xD590),(0x10,0x1700,0x17C0),(0x78,0x9000,0x9200)]:
 print(f'\nBANK {bank:02X}:{a:04X}-{b:04X}')
 off=sb+(bank<<16)+a; data=r[off:off+(b-a)]
 for ins in md.disasm(data,a): print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():18s} {ins.mnemonic:7s} {ins.op_str}')
