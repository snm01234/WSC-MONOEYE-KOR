#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, struct, sys
from pathlib import Path
ROOT=Path(r'D:\monoeye'); sys.path.insert(0,str(ROOT/'tools'))
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state35')
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
spec=importlib.util.spec_from_file_location('h',ROOT/'tools/diag_ending_libretro_phase.py')
h=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(h)
ROMS={
 'stock':ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc',
 'current':ROOT/'out/patch/monoeye_ko_expanded.wsc',
 'stage2_clean':ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc',
 'hist_bad':ROOT/'out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc',
}
def band(ram):
 o=[]
 for r,cols in ((9,range(4,28)),(10,range(28)),(11,range(28))):
  o += [struct.unpack_from('<H',ram,0x3000+2*(r*32+c))[0] for c in cols]
 return o
def run(rom):
 payload=h.retroarch_state_payload(STATE); hh=h.Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
 try:
  info=hh.load_game(rom); hh.unserialize(payload); rows=[]
  for f in range(7):
   ram=hh.ram(); b=band(ram); rows.append({'frame':f,'nonblank':sum(x!=0x21F6 for x in b),'head':[f'{x:04X}' for x in b[:12]],'tail':[f'{x:04X}' for x in b[-12:]],'scene':f'{ram[0x1A6C]:02X}','banks_hint_0612_14':' '.join(f'{ram[x]:02X}' for x in range(0x612,0x615))})
   if f<6: hh.run()
  return {'ok':True,'info':info,'rows':rows}
 except Exception as e: return {'ok':False,'error':repr(e)}
 finally: hh.close()
print(json.dumps({k:run(v) for k,v in ROMS.items()},ensure_ascii=False,indent=2))
