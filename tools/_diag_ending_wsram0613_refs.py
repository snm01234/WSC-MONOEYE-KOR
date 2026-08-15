from pathlib import Path
from capstone import Cs,CS_ARCH_X86,CS_MODE_16
ROOT=Path(r'D:\monoeye')
ROM=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); SB=0x800000
md=Cs(CS_ARCH_X86,CS_MODE_16)
for bank in range(0x70,0x80):
    data=ROM[SB+(bank<<16):SB+((bank+1)<<16)]
    hits=[]
    # disassemble whole bank linearly; retain instructions mentioning 0x613/0x614/0x612/0x615
    for ins in md.disasm(data,0):
        s=ins.op_str.lower()
        if any(x in s for x in ('0x613','0x614','0x612','0x615')):
            hits.append(ins)
    if hits:
        print(f'BANK {bank:02X}')
        for ins in hits:
            print(f'{bank:02X}:{ins.address:04X} {ins.bytes.hex().upper():<18} {ins.mnemonic:<8} {ins.op_str}')
