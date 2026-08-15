#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16,CS_OP_MEM,CS_AC_WRITE
r=bytes(load_rom(ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc'));sb=stock_base(r);md=Cs(CS_ARCH_X86,CS_MODE_16);md.detail=True
for bank,a,b in [(0x78,0x9000,0xA200),(0x79,0x0000,0x4000)]:
 data=r[sb+(bank<<16)+a:sb+(bank<<16)+b]
 print(f'\nBANK {bank:02X}')
 for ins in md.disasm(data,a):
  hit=False
  for op in ins.operands:
   if op.type==CS_OP_MEM and (op.mem.disp & 0xffff)==0x000A:
    hit=True
  if hit: print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():18s} {ins.mnemonic:8s} {ins.op_str}')
