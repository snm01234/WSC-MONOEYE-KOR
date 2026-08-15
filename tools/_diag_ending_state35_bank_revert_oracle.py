#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, struct, sys, tempfile
from pathlib import Path
ROOT=Path(r'D:\monoeye'); sys.path.insert(0,str(ROOT/'tools'))
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state35')
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
CURRENT=ROOT/'out/patch/monoeye_ko_expanded.wsc'; CLEAN=ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc'
spec=importlib.util.spec_from_file_location('h',ROOT/'tools/diag_ending_libretro_phase.py'); h=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(h)
def band(r):
 o=[]
 for rr,cc in ((9,range(4,28)),(10,range(28)),(11,range(28))): o += [struct.unpack_from('<H',r,0x3000+2*(rr*32+c))[0] for c in cc]
 return tuple(o)
def run_rom(path):
 hh=h.Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
 try:
  hh.load_game(path); hh.unserialize(h.retroarch_state_payload(STATE))
  for _ in range(6): hh.run()
  return band(hh.ram())
 finally: hh.close()
def main():
 cur=CURRENT.read_bytes(); clean=CLEAN.read_bytes(); changed=[]
 for bk in range(256):
  a=cur[bk*0x10000:(bk+1)*0x10000]; b=clean[bk*0x10000:(bk+1)*0x10000]
  n=sum(x!=y for x,y in zip(a,b))
  if n: changed.append((bk,n))
 normal=run_rom(CLEAN); bad=run_rom(CURRENT); rows=[]
 with tempfile.TemporaryDirectory(prefix='ending_bank_oracle_') as td:
  p=Path(td)/'probe.wsc'
  for bk,n in changed:
   data=bytearray(cur); data[bk*0x10000:(bk+1)*0x10000]=clean[bk*0x10000:(bk+1)*0x10000]; p.write_bytes(data)
   try:
    got=run_rom(p); status='NORMAL' if got==normal else ('BAD' if got==bad else 'OTHER')
    rows.append({'physical_bank':f'{bk:02X}','diff_bytes':n,'status':status,'head':' '.join(f'{x:04X}' for x in got[:6]),'tail':' '.join(f'{x:04X}' for x in got[-4:])})
   except Exception as e: rows.append({'physical_bank':f'{bk:02X}','diff_bytes':n,'status':'ERROR','error':repr(e)})
 print(json.dumps({'changed_bank_count':len(changed),'normal_head':' '.join(f'{x:04X}' for x in normal[:6]),'bad_head':' '.join(f'{x:04X}' for x in bad[:6]),'rows':rows},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
