from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(r'D:\monoeye'); ROM=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); SB=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16); bank=0x78; lo=0x8F1E; hi=0x9027
for ins in md.disasm(ROM[SB+(bank<<16)+lo:SB+(bank<<16)+hi],lo): print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():<18} {ins.mnemonic:<8} {ins.op_str}')
# raw far-call refs to likely function starts in this window
for off in range(0x8F1E,0x8F80):
    pat=bytes([0x9A,off&0xff,(off>>8)&0xff,0x00,0x80])
    hits=[]; pos=SB
    while True:
        pos=ROM.find(pat,pos)
        if pos<0: break
        hits.append(pos-SB); pos+=1
    if hits: print('CALL',f'{off:04X}',[f'{x>>16:02X}:{x&0xffff:04X}' for x in hits])
