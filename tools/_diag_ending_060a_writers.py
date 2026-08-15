from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(r'D:\monoeye'); ROM=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); SB=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank,lo,hi in [(0x7A,0x0E80,0x0F40),(0x7C,0x0DF0,0x0EC0)]:
 print(f'\nBANK {bank:02X}:{lo:04X}-{hi:04X}')
 data=ROM[SB+(bank<<16)+lo:SB+(bank<<16)+hi]
 for ins in md.disasm(data,lo): print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():<18} {ins.mnemonic:<8} {ins.op_str}')
