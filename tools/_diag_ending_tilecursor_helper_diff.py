from pathlib import Path
import hashlib
ROOT=Path(__file__).resolve().parents[1]
H=(ROOT/'out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc').read_bytes()
C=(ROOT/'out/patch/monoeye_ko_expanded.wsc').read_bytes()
SB=0x800000
ranges=[
 (0x7A,0xAE80,0xAF80,'AA94:058C logical'),
 (0x7A,0xB900,0xBB00,'AA94:0FF9 logical'),
 (0x7A,0xBE80,0xC220,'BECA/BF61'),
 (0x77,0x99E0,0xA180,'7042 helper trio'),
 (0x78,0xB900,0xBA80,'8000:B9BB helper'),
 (0x40,0x8620,0x8B00,'resource 3000:8620'),
]
for bank,a,b,label in ranges:
 lo=SB+(bank<<16)+a;hi=SB+(bank<<16)+b
 h=H[lo:hi];c=C[lo:hi]
 dif=[i for i,(x,y) in enumerate(zip(h,c)) if x!=y]
 print(f'{label}: diffbytes={len(dif)} shaH={hashlib.sha256(h).hexdigest()[:12]} shaC={hashlib.sha256(c).hexdigest()[:12]}')
 if dif:
  runs=[];s=p=dif[0]
  for x in dif[1:]:
   if x==p+1:p=x;continue
   runs.append((s,p));s=p=x
  runs.append((s,p))
  for s,e in runs[:30]:print(f'  {bank:02X}:{a+s:04X}-{a+e:04X} H={h[s:e+1].hex().upper()} C={c[s:e+1].hex().upper()}')
