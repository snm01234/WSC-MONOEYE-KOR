from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(r'D:\monoeye'); ROM=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); SB=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16); bank=0x78; lo=0xDE70; hi=0xDF30
for ins in md.disasm(ROM[SB+(bank<<16)+lo:SB+(bank<<16)+hi],lo): print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():<18} {ins.mnemonic:<8} {ins.op_str}')
