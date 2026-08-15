#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
r=bytes(load_rom(ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc'));sb=stock_base(r);md=Cs(CS_ARCH_X86,CS_MODE_16)
for a,b in [(0x9180,0x9360)]:
 data=r[sb+(0x78<<16)+a:sb+(0x78<<16)+b]
 for ins in md.disasm(data,a): print(f'78:{ins.address:04X} {ins.bytes.hex().upper():18s} {ins.mnemonic:8s} {ins.op_str}')
