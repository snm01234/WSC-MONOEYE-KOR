#!/usr/bin/env python3
from pathlib import Path
import sys,tempfile,os,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from diag_ending_libretro_phase import Harness,retroarch_state_payload
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state37')
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll'); DOWN=5
GOOD=ROOT/'out/patch/backup/20260815_012352_pre_encyclopedia_kana_index/monoeye_ko_expanded.wsc'
BAD=ROOT/'out/patch/backup/20260815_015949_pre_name_mapping_spirit_combined/monoeye_ko_expanded.wsc'
ADDRS=[0x75B889,0x75B88F,0x75B896,0x75B89C,0x75B8A3,0x75B8AB,0x75B8B4,0x75B8BA,0x75B8BF]
# lengths include payload only; determined by next record/NUL from boundary diff
LENS=[5,6,5,6,7,8,5,4,6]

def run(raw,frames=4):
 fd,p=tempfile.mkstemp(suffix='.wsc');os.close(fd);p=Path(p);p.write_bytes(raw);h=Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
 try:
  h.load_game(p);h.unserialize(retroarch_state_payload(STATE));h.set_pressed(DOWN);h.run();h.set_pressed()
  for _ in range(frames-1):h.run()
  r=h.ram();return r
 finally:h.close();p.unlink(missing_ok=True)

def sig(r):
 regions={'bgfg':r[0x3000:0x4000],'tiles':r[0x4000:0xC200],'glyph':r[0x1A60:0x1B00],'menu':r[0x1800:0x1C00]}
 return {k:hashlib.sha256(v).hexdigest()[:12] for k,v in regions.items()}
def difcount(a,b,lo,hi):return sum(x!=y for x,y in zip(a[lo:hi],b[lo:hi]))
def main():
 g=GOOD.read_bytes();b=BAD.read_bytes();ref=run(g);bad=run(b);print('GOOD',sig(ref));print('BAD ',sig(bad));print('bad diff', {k:difcount(ref,bad,*v) for k,v in {'bgfg':(0x3000,0x4000),'tiles':(0x4000,0xC200),'glyph':(0x1A60,0x1B00),'menu':(0x1800,0x1C00)}.items()})
 for a,n in zip(ADDRS,LENS):
  x=bytearray(b);off=0x800000+a;x[off:off+n]=g[off:off+n];r=run(bytes(x));print(f'{a:06X}',sig(r),{k:difcount(ref,r,*v) for k,v in {'bgfg':(0x3000,0x4000),'tiles':(0x4000,0xC200),'glyph':(0x1A60,0x1B00),'menu':(0x1800,0x1C00)}.items()})
 x=bytearray(b)
 for a,n in zip(ADDRS,LENS):off=0x800000+a;x[off:off+n]=g[off:off+n]
 r=run(bytes(x));print('ALL9',sig(r),{k:difcount(ref,r,*v) for k,v in {'bgfg':(0x3000,0x4000),'tiles':(0x4000,0xC200),'glyph':(0x1A60,0x1B00),'menu':(0x1800,0x1C00)}.items()})
if __name__=='__main__':main()
