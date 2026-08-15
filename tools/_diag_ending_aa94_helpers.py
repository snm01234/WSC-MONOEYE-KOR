from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
R=Path(__file__).resolve().parents[1];rom=load_rom(R/'out/patch/monoeye_ko_expanded.wsc');sb=stock_base(rom);md=Cs(CS_ARCH_X86,CS_MODE_16)
# AA94:058C => logical 7A:AECC; AA94:0B40 => 7A:B480
for a,b in [(0xAEA0,0xAF60),(0xB450,0xB590),(0xB590,0xB700)]:
 print(f'\n7A:{a:04X}-{b:04X}')
 for i in md.disasm(bytes(rom[sb+(0x7A<<16)+a:sb+(0x7A<<16)+b]),a):print(f'7A:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
