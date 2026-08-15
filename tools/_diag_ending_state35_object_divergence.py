#!/usr/bin/env python3
from __future__ import annotations
import importlib.util,struct,sys
from pathlib import Path
R=Path(r'D:\monoeye');sys.path.insert(0,str(R/'tools'))
s=importlib.util.spec_from_file_location('h',R/'tools/diag_ending_libretro_phase.py');h=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(h)
state=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state35');core=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
roms={'stage2':R/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc','current':R/'out/patch/monoeye_ko_expanded.wsc'}
def run(rom):
 hh=h.Harness(core,Path(r'C:\RetroArch-Win64\system'),state.parent);hh.load_game(rom);hh.unserialize(h.retroarch_state_payload(state));out=[]
 try:
  for f in range(7):
   ram=hh.ram(); objs=[]
   for i in range(0x74,0x80):
    o=0x846+i*0x20; w=struct.unpack_from('<16H',ram,o); objs.append(tuple(w))
   out.append(objs)
   if f<6:hh.run()
  return out
 finally:hh.close()
a={k:run(v) for k,v in roms.items()}
for f in (4,5,6):
 print('FRAME',f)
 for j,i in enumerate(range(0x74,0x80)):
  s0=a['stage2'][f][j]; c=a['current'][f][j]; dif=[x for x,(u,v) in enumerate(zip(s0,c)) if u!=v]
  if dif:
   print(f'obj{i:02X}',[(f'+{x*2:02X}',f'{s0[x]:04X}',f'{c[x]:04X}') for x in dif])
