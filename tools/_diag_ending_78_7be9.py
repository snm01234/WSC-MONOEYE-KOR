from pathlib import Path
ROOT=Path(r'D:\monoeye'); ROM=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes(); SB=0x800000
bank=0x78; lo=0x7B80; hi=0x7C40; data=ROM[SB+(bank<<16)+lo:SB+(bank<<16)+hi]
for i in range(0,len(data),16):
    print(f'{bank:02X}:{lo+i:04X}',data[i:i+16].hex(' '))
