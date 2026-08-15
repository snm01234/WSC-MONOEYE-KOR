#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16,CS_OP_MEM
ROOT=Path(r'D:\monoeye'); rom=(ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc').read_bytes(); target=0x1B70
md=Cs(CS_ARCH_X86,CS_MODE_16); md.detail=True; md.skipdata=True
refs=[]
for bank in range(0x80):
 code=rom[bank*0x10000:(bank+1)*0x10000]
 for ins in md.disasm(code,0):
  if ins.mnemonic=='.byte': continue
  if any(op.type==CS_OP_MEM and op.mem.disp==target for op in ins.operands): refs.append(f'{bank:02X}:{ins.address:04X} {ins.mnemonic} {ins.op_str}')
print('target',hex(target),'decoded_direct_refs',len(refs)); print('\n'.join(refs[:100]))
