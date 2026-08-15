#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(__file__).resolve().parents[1]
for name,path in [('hist',ROOT/'out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc'),('current',ROOT/'out/patch/monoeye_ko_expanded.wsc')]:
 b=path.read_bytes(); sb=0x800000; md=Cs(CS_ARCH_X86,CS_MODE_16)
 print('\n',name,'D580-D620')
 code=b[sb+0x7ED580:sb+0x7ED620]
 for i in md.disasm(code,0xD580): print(f'7E:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
 print(name,'FD1E-FD83')
 code=b[sb+0x7EFD1E:sb+0x7EFD83]
 for i in md.disasm(code,0xFD1E): print(f'7E:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
