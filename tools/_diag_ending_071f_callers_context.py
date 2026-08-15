from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom,stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
R=Path(__file__).resolve().parents[1];rom=load_rom(R/'out/patch/monoeye_ko_expanded.wsc');sb=stock_base(rom);md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank,addr in [(0x78,0x0c8e),(0x78,0x5b57),(0x78,0x5bc9),(0x7a,0xae1a),(0x7b,0x0ad1),(0x7b,0x0b26),(0x7c,0x1334),(0x7e,0xd832)]:
 a=max(0,addr-80);b=min(0x10000,addr+80);print(f'\nBANK {bank:02X} caller {addr:04X}')
 data=bytes(rom[sb+(bank<<16)+a:sb+(bank<<16)+b])
 for i in md.disasm(data,a):print(f'{bank:02X}:{i.address:04X} {i.bytes.hex().upper():<18} {i.mnemonic:<8} {i.op_str}')
