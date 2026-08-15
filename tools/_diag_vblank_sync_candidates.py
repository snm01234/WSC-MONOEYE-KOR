#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs, CS_ARCH_X86, CS_MODE_16
ROOT=Path(__file__).resolve().parents[1]
rom=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes()
SB=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank,start,end,label in [(0x78,0x0580,0x0670,'bank78 main/vblank helpers'),(0x7F,0x0B60,0x0C10,'F000 0BBC region'),(0x79,0x1600,0x17B0,'9000:1772 callers')]:
    print('\n'+label)
    off=SB+bank*0x10000+start
    data=rom[off:SB+bank*0x10000+end]
    for ins in md.disasm(data,start):
        print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():18s} {ins.mnemonic:8s} {ins.op_str}')
