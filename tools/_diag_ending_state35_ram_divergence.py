#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, struct, sys
from pathlib import Path
ROOT=Path(r'D:\monoeye'); sys.path.insert(0,str(ROOT/'tools'))
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state35')
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
spec=importlib.util.spec_from_file_location('h',ROOT/'tools/diag_ending_libretro_phase.py')
h=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(h)
ROMS={'stock':ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc','stage2':ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc','current':ROOT/'out/patch/monoeye_ko_expanded.wsc'}
def frames(rom):
 p=h.retroarch_state_payload(STATE); hh=h.Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
 try:
  hh.load_game(rom); hh.unserialize(p); out=[]
  for f in range(7):
   out.append(hh.ram())
   if f<6: hh.run()
  return out
 finally: hh.close()
def ranges(idxs):
 if not idxs:return []
 out=[]; a=b=idxs[0]
 for x in idxs[1:]:
  if x==b+1:b=x
  else:out.append((a,b));a=b=x
 out.append((a,b));return out
def fmt(ram,a,b): return ' '.join(f'{x:02X}' for x in ram[a:b+1])
allf={k:frames(v) for k,v in ROMS.items()}; out={}
for other in ('stock','stage2'):
 rows=[]
 for f in range(7):
  a=allf[other][f]; b=allf['current'][f]; idx=[i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
  nonv=[i for i in idx if not (0x3000<=i<0x8000)]; vr=[i for i in idx if 0x3000<=i<0x8000]
  rr=[]
  for lo,hi in ranges(nonv)[:40]: rr.append({'range':f'{lo:04X}-{hi:04X}','other':fmt(a,lo,hi),'current':fmt(b,lo,hi)})
  rows.append({'frame':f,'diff_total':len(idx),'diff_nonvram':len(nonv),'diff_vram':len(vr),'first_nonvram_ranges':rr,'vram_first':[f'{x:04X}' for x in vr[:40]],'state_fields':{'other_0600_0620':fmt(a,0x600,0x620),'current_0600_0620':fmt(b,0x600,0x620),'other_1A60_1A80':fmt(a,0x1A60,0x1A80),'current_1A60_1A80':fmt(b,0x1A60,0x1A80)}})
 out[f'{other}_vs_current']=rows
print(json.dumps(out,ensure_ascii=False,indent=2))
