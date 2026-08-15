#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(r'D:\monoeye'); rom=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); sb=len(rom)-0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16); md.skipdata=True
for a,b in ((0x9F60,0xA020),(0x9E40,0x9F80)):
 data=rom[sb+0x78*0x10000+a:sb+0x78*0x10000+b]
 print(f'---78:{a:04X}-{b:04X}---')
 for i in md.disasm(data,a): print(f'78:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
