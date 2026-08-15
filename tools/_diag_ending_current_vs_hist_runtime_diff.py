#!/usr/bin/env python3
from pathlib import Path
from capstone import Cs, CS_ARCH_X86, CS_MODE_16
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc'
C=ROOT/'out/patch/monoeye_ko_expanded.wsc'
h=H.read_bytes(); c=C.read_bytes(); SB=0x800000

def runs(lo,hi):
    out=[]; s=None
    for i in range(lo,hi):
        d=h[SB+i]!=c[SB+i]
        if d and s is None:s=i
        if not d and s is not None:out.append((s,i));s=None
    if s is not None:out.append((s,hi))
    return out

print('DIFF RUNS selected logical banks')
for bank in [0x3F,0x70,0x71,0x72,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7A,0x7B,0x7C,0x7D,0x7E,0x7F]:
    rs=runs(bank<<16,(bank+1)<<16)
    if rs:
        print(f'bank {bank:02X}: {len(rs)} runs, {sum(e-s for s,e in rs)} bytes')
        for s,e in rs[:80]:
            print(f' {s:06X}-{e-1:06X} len={e-s} H={h[SB+s:SB+min(e,s+16)].hex().upper()} C={c[SB+s:SB+min(e,s+16)].hex().upper()}')
        if len(rs)>80: print(' ...')

# focused exact ranges used each frame / scene setup
focus=[
(0x7ED380,0x7ED600,'ending scene'),
(0x789000,0x78A130,'object create/update/render'),
(0x790000,0x791900,'frame helper/callees local'),
(0x7BE000,0x7BE100,'wait helper'),
(0x7F0A00,0x7F0C00,'irq helper'),
(0x7FFF00,0x800000,'overlay helper tail'),
]
print('\nFOCUSED')
for lo,hi,label in focus:
    rs=runs(lo,hi)
    print(label, f'{lo:06X}-{hi-1:06X}: runs={len(rs)} bytes={sum(e-s for s,e in rs)}')
    for s,e in rs:
        print(f' {s:06X}-{e-1:06X} H={h[SB+s:SB+e].hex().upper()} C={c[SB+s:SB+e].hex().upper()}')
