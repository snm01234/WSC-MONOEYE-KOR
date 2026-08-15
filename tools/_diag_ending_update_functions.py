#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
r=bytes(load_rom(ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc'));sb=stock_base(r);md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank,a,b,label in [(0x78,0x05E0,0x06C0,'8000:0631 common update'),(0x79,0x1720,0x17D0,'9000:1772 loop helper'),(0x78,0x9900,0x9C90,'object update/render')]:
 print(f'\n{label} logical bank {bank:02X}:{a:04X}-{b:04X}')
 data=r[sb+(bank<<16)+a:sb+(bank<<16)+b]
 for ins in md.disasm(data,a): print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():18s} {ins.mnemonic:7s} {ins.op_str}')
