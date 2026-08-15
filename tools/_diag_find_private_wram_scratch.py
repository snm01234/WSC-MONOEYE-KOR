#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, re
from pathlib import Path
ROOT=Path(r'D:\monoeye'); SD=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan')
spec=importlib.util.spec_from_file_location('v',ROOT/'out/patch/_analyze_beetle_status_vram.py'); v=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(v)
rams=[]
for s in range(39):
 p=SD/('monoeye_ko_expanded.state' if s==0 else f'monoeye_ko_expanded.state{s}')
 if p.exists():
  try:r,_=v.parse_beetle_ram(p); rams.append((s,r))
  except Exception:pass
stock=(ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc').read_bytes()
# all-zero across every available state, only ordinary WRAM 0000-2FFF.
zero=[all(r[a]==0 for _,r in rams) for a in range(0x3000)]
runs=[]; st=None
for a,z in enumerate(zero+[False]):
 if z and st is None:st=a
 elif not z and st is not None:
  if a-st>=4:runs.append((st,a))
  st=None
print('states',len(rams),'zero runs >=4')
for a,b in runs:
 # Binary pair count in stock as a conservative prefilter; operand scan comes later.
 pair=a.to_bytes(2,'little'); hits=stock.count(pair)
 if b-a>=8 or hits==0:
  print(f'{a:04X}-{b-1:04X} len={b-a} raw_pair_hits_at_start={hits}')
