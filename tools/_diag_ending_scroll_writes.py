#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
R=Path(r'D:\monoeye'); rom=(R/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); sb=len(rom)-0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16); md.skipdata=True
for a,b in ((0xD430,0xD540),(0xD680,0xD720),(0xD780,0xD820)):
 d=rom[sb+0x7E0000+a:sb+0x7E0000+b]; print(f'---7E:{a:04X}-{b:04X}---')
 for i in md.disasm(d,a): print(f'7E:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
