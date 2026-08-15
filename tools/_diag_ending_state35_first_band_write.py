#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, struct, sys, zlib
from pathlib import Path
ROOT=Path(r'D:\monoeye')
sys.path.insert(0,str(ROOT/'tools'))
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state35')
ROM=ROOT/'out/patch/monoeye_ko_expanded.wsc'
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
# reuse known-good libretro harness
spec=importlib.util.spec_from_file_location('h',ROOT/'tools/diag_ending_libretro_phase.py')
h=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(h)

def band(ram:bytes):
 out=[]
 for r,cols in ((9,range(4,28)),(10,range(28)),(11,range(28))):
  out.extend(struct.unpack_from('<H',ram,0x3000+2*(r*32+c))[0] for c in cols)
 return out

def snap(ram:bytes,frame:int):
 b=band(ram)
 return {
  'frame':frame,
  'nonblank':sum(x!=0x21F6 for x in b),
  'head':[f'{x:04X}' for x in b[:12]],
  'tail':[f'{x:04X}' for x in b[-12:]],
  'scene':f'{ram[0x1A6C]:02X}',
  '0612_0614':' '.join(f'{ram[x]:02X}' for x in range(0x612,0x615)),
  '060A_0610':' '.join(f'{ram[x]:02X}' for x in range(0x60A,0x611)),
 }

def main():
 payload=h.retroarch_state_payload(STATE)
 hh=h.Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
 try:
  info=hh.load_game(ROM); hh.unserialize(payload)
  prev=band(hh.ram()); events=[]; first=None
  for f in range(0,2401):
   ram=hh.ram(); cur=band(ram)
   if cur!=prev:
    changed=[i for i,(a,b) in enumerate(zip(prev,cur)) if a!=b]
    events.append({'frame':f,'changed_count':len(changed),'changed_first':changed[:20],'snapshot':snap(ram,f)})
    if first is None and any(x!=0x21F6 for x in cur): first=f
    prev=cur
   if first is not None and f>=first+8: break
   hh.run()
  print(json.dumps({'ok':True,'system':info,'first_nonblank_frame':first,'events':events},ensure_ascii=False,indent=2))
 finally:
  hh.close()
if __name__=='__main__': main()
