from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(__file__).resolve().parents[1]; rom=load_rom(ROOT/'out/patch/monoeye_ko_expanded.wsc');sb=stock_base(rom)
md=Cs(CS_ARCH_X86,CS_MODE_16)
def d(a,b):
 data=bytes(rom[sb+(0x7c<<16)+a:sb+(0x7c<<16)+b]);print(f'\n7C:{a:04X}-{b:04X}')
 for i in md.disasm(data,a): print(f'7C:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
for a,b in [(0x0740,0x0860),(0x0B50,0x0BE0),(0x0F40,0x0F70),(0x1060,0x10E0)]:d(a,b)
