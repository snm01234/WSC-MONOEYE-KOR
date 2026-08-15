#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(__file__).resolve().parents[1]
r=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); SB=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank in range(0x70,0x80):
    data=r[SB+bank*0x10000:SB+(bank+1)*0x10000]
    ins=list(md.disasm(data,0))
    for i,x in enumerate(ins):
        if x.mnemonic=='in' or x.mnemonic=='hlt':
            if x.mnemonic=='hlt' or any(p in x.op_str for p in ['0x2','0xa2','0xb0','0xb2','0xb4','0xb6']):
                lo=max(0,i-4); hi=min(len(ins),i+6)
                print(f'\nBANK {bank:02X} hit {x.address:04X} {x.mnemonic} {x.op_str}')
                for q in ins[lo:hi]: print(f'{bank:02X}:{q.address:04X} {q.bytes.hex().upper():14s} {q.mnemonic:8s} {q.op_str}')
