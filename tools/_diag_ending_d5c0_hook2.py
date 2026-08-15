#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(__file__).resolve().parents[1]; b=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); sb=0x800000; md=Cs(CS_ARCH_X86,CS_MODE_16)
for start,end in [(0x7EFD40,0x7EFD83),(0x7ED620,0x7ED700)]:
 print(f'\n{start:06X}-{end-1:06X}')
 for i in md.disasm(b[sb+start:sb+end], start&0xffff): print(f'7E:{i.address:04X} {i.bytes.hex().upper():<20} {i.mnemonic:<8} {i.op_str}')
