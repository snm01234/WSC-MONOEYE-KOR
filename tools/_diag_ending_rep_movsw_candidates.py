from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(r'D:\monoeye'); ROM=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); SB=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank in range(0x70,0x80):
    data=ROM[SB+(bank<<16):SB+((bank+1)<<16)]
    hits=[]
    for i in range(len(data)-1):
        if data[i:i+2] in (b'\xF3\xA5',b'\xF2\xA5'):
            hits.append(i)
    if not hits: continue
    print(f'BANK {bank:02X}')
    for off in hits:
        lo=max(0,off-40); hi=min(0x10000,off+40)
        insns=list(md.disasm(data[lo:hi],lo))
        print(f'-- around {bank:02X}:{off:04X} --')
        for ins in insns:
            if off-24 <= ins.address <= off+20:
                print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():<16} {ins.mnemonic:<8} {ins.op_str}')
