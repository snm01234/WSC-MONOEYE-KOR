#!/usr/bin/env python3
"""Diagnostic: remove timing-sensitive hardware scroll reads in renderer path.

State35 cross-ROM replay proves current Main first diverges from stock at frame 5,
while the IRQ return frame is 8000:9FD4 in Main vs 8000:9FCA in stock.  The two
addresses straddle renderer reads from ports 10h/11h.  Scene 2B resets both BG
scroll registers to zero before this transition, so this probe replaces only
those two IN instructions with XOR AL,AL.  It is diagnostic-only and must never
be promoted as a generic renderer fix.
"""
from __future__ import annotations
import hashlib, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base,update_ws_checksum
PARENT=ROOT/'out/patch/monoeye_ko_expanded.wsc'
LIVE=ROOT/'sram/monoeye_ko_expanded.sav'
OUT=ROOT/'out/patch/ending_seam_current_renderer_scroll_zero_probe.wsc'
OUTSAVE=ROOT/'sram/ending_seam_current_renderer_scroll_zero_probe.sav'
REPORT=ROOT/'out/patch/ending_seam_current_renderer_scroll_zero_probe_report.json'
PATCHES=[(0x789FBF,bytes.fromhex('E410'),bytes.fromhex('30C0')),(0x789FD2,bytes.fromhex('E411'),bytes.fromhex('30C0'))]
def sha(x): return hashlib.sha256(bytes(x)).hexdigest()
def main():
 parent=bytes(load_rom(PARENT)); before=sha(parent); save_before=sha(LIVE.read_bytes()); sb=stock_base(parent); out=bytearray(parent)
 for logical,exp,new in PATCHES:
  off=sb+logical
  if out[off:off+len(exp)]!=exp: raise RuntimeError(f'site drift {logical:06X}: {out[off:off+len(exp)].hex()}')
  out[off:off+len(new)]=new
 update_ws_checksum(out); OUT.write_bytes(out); shutil.copyfile(LIVE,OUTSAVE)
 rep={'ok':True,'generated_by':'tools/build_ending_seam_renderer_scroll_zero_probe.py','parent_sha256':before,'rom':str(OUT.relative_to(ROOT)).replace('\\','/'),'sha256':sha(out),'checksum':f'{out[-2]|(out[-1]<<8):04X}','patches':[{'logical':f'{x:06X}','before':a.hex().upper(),'after':b.hex().upper()} for x,a,b in PATCHES],'purpose':'diagnostic only: test whether IRQ timing around port 10h/11h reads is causal','main_tip_unchanged':sha(PARENT.read_bytes())==before,'live_saveram_unchanged':sha(LIVE.read_bytes())==save_before,'promotion':'blocked'}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
