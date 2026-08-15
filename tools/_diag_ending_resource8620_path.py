from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from monoeye_rom import load_rom, stock_base
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(__file__).resolve().parents[1]
ROM=load_rom(ROOT/'out/patch/monoeye_ko_expanded.wsc'); sb=stock_base(ROM)
md=Cs(CS_ARCH_X86,CS_MODE_16);md.detail=False

def dis(bank,a,b):
 data=bytes(ROM[sb+(bank<<16)+a:sb+(bank<<16)+b])
 print(f'BANK {bank:02X}:{a:04X}-{b:04X}')
 for ins in md.disasm(data,a): print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():<18} {ins.mnemonic:<8} {ins.op_str}')

dis(0x7C,0x0E80,0x10A0)
# raw direct call patterns commonly used for dynamic C000 bank window and same-bank near calls
for pat,name in [(bytes.fromhex('9A650F00C0'),'lcall C000:0F65'),(bytes.fromhex('E8'),'near-call opcode')]:
 if len(pat)>1:
  hits=[];s=0
  while True:
   i=bytes(ROM).find(pat,s)
   if i<0: break
   hits.append(i);s=i+1
  print(name,[hex(x) for x in hits[:100]],'count',len(hits))
# exact 8620 words in logical code/data banks 70-7F with local context
raw=bytes(ROM)
for bank in range(0x70,0x80):
 lo=sb+(bank<<16); hi=lo+0x10000; s=lo
 while True:
  i=raw.find(bytes.fromhex('2086'),s,hi)
  if i<0: break
  off=i-lo
  print(f'8620 word bank {bank:02X}:{off:04X} context',raw[max(lo,i-12):min(hi,i+14)].hex().upper())
  s=i+1
