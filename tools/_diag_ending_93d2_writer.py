from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
R=Path(__file__).resolve().parents[1];rom=load_rom(R/'out/patch/monoeye_ko_expanded.wsc');sb=stock_base(rom);md=Cs(CS_ARCH_X86,CS_MODE_16)
for a,b in [(0x935E,0x9450),(0x9450,0x9560)]:
 print(f'\n78:{a:04X}-{b:04X}')
 for i in md.disasm(bytes(rom[sb+(0x78<<16)+a:sb+(0x78<<16)+b]),a):print(f'78:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
