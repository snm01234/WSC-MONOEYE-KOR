from pathlib import Path
from capstone import Cs, CS_ARCH_X86, CS_MODE_16
p=Path('out/patch/monoeye_ko_expanded.wsc').read_bytes(); sb=0x800000; md=Cs(CS_ARCH_X86,CS_MODE_16)
for s,e in [(0x7EFF00,0x7EFF80),(0x7ED5A0,0x7ED5F0)]:
 print(f'\n{s:06X}-{e:06X}')
 code=p[sb+s:sb+e]
 for ins in md.disasm(code,s&0xffff):
  print(f'7E:{ins.address:04X} {ins.bytes.hex().upper():18} {ins.mnemonic:8} {ins.op_str}')
