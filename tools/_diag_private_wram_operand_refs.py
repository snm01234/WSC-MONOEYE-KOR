#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16,CS_OP_MEM
ROOT=Path(r'D:\monoeye'); rom=(ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc').read_bytes()
CANDS=list(range(0x2DE0,0x2E00))+list(range(0x1B55,0x1B80))
refs={a:[] for a in CANDS}
md=Cs(CS_ARCH_X86,CS_MODE_16); md.detail=True; md.skipdata=True
for bank in range(0x70,0x80):
 code=rom[bank*0x10000:(bank+1)*0x10000]
 for ins in md.disasm(code,0):
  if ins.mnemonic=='.byte': continue
  for op in ins.operands:
   if op.type==CS_OP_MEM and op.mem.disp in refs:
    refs[op.mem.disp].append(f'{bank:02X}:{ins.address:04X} {ins.mnemonic} {ins.op_str}')
for a in CANDS:
 if not refs[a]: print(f'{a:04X} clear')
 else: print(f'{a:04X} refs={refs[a]}')
