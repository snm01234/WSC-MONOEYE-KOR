#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(r'D:\monoeye'); rom=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); sb=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16); md.skipdata=True
for a,b in ((0x7FFD10,0x7FFF10),(0x7A06B0,0x7A0840)):
 print(f'---{a:06X}-{b:06X}---')
 blob=rom[sb+a:sb+b]
 for i in md.disasm(blob,a):
  note=''
  if any(x in i.op_str.lower() for x in ('0x19f8','0x19fa','0x19ff')): note=' ***'
  print(f'{i.address>>16:02X}:{i.address&0xffff:04X} {i.bytes.hex().upper():<20} {i.mnemonic:<8} {i.op_str}{note}')
