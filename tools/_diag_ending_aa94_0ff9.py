from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
R=Path(__file__).resolve().parents[1];rom=load_rom(R/'out/patch/monoeye_ko_expanded.wsc');sb=stock_base(rom);md=Cs(CS_ARCH_X86,CS_MODE_16)
# AA94:0FF9 => CPU ((AA94<<4)+0FF9)&FFFFF = AB939; cart logical = 70:0000 + CPU => 7A:B939
for a,b in [(0xB880,0xBA40),(0xB900,0xB9C0)]:
 print(f'\n7A:{a:04X}-{b:04X}')
 data=bytes(rom[sb+(0x7a<<16)+a:sb+(0x7a<<16)+b])
 for i in md.disasm(data,a):print(f'7A:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
