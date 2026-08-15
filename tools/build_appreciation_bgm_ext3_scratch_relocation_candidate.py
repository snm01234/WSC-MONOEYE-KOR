#!/usr/bin/env python3
"""Relocate ext3 temporary index scratch from WRAM 19F8 to 1B70.

Runtime proof from appreciation/BGM state37:
- WRAM 19F8 is the live BGM selection state. At the last valid item 0x4B,
  stock keeps 0x4B on Down while current main advances to invalid 0x4C.
- bank oracle shows the failure requires bank75 E518 UI data plus bank7A/7F
  ext3 hook code.
- the ext3 cave has six direct word accesses to 19F8 (4 stores, 2 loads).

This diagnostic candidate changes only those six 16-bit displacement operands,
19F8 -> 1B70, plus checksum. 1B70/1B71 are zero in all 39 available states and
have zero decoded direct MEM operand references in stock code banks 70-7F.
"""
from __future__ import annotations
import hashlib, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom, stock_base, update_ws_checksum
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; SAVE=ROOT/'sram/monoeye_ko_expanded.sav'
OUT=ROOT/'out/patch/appreciation_bgm_ext3_scratch_relocation_candidate.wsc'
OUT_SAVE=ROOT/'sram/appreciation_bgm_ext3_scratch_relocation_candidate.sav'
REPORT=ROOT/'out/patch/appreciation_bgm_ext3_scratch_relocation_report.json'
EXPECTED_MAIN='d925ee07b4ea844a1bd89deff52bc88ec21e04bf8ab5a0df8d9fecdd651bd8b1'
OLD=bytes.fromhex('F819'); NEW=bytes.fromhex('701B')
LOGICAL_HITS=(0x7FFD32,0x7FFD72,0x7FFDB1,0x7FFE1A,0x7FFE6C,0x7FFEBD)
def sha(b):return hashlib.sha256(bytes(b)).hexdigest()
def main():
 p=bytes(load_rom(MAIN)); sb=stock_base(p)
 if sha(p)!=EXPECTED_MAIN: raise RuntimeError(f'main drift {sha(p)}')
 before_save=SAVE.read_bytes(); out=bytearray(p); rows=[]
 for logical in LOGICAL_HITS:
  at=sb+logical
  if bytes(out[at:at+2])!=OLD: raise RuntimeError(f'{logical:06X} drift {out[at:at+2].hex()}')
  out[at:at+2]=NEW; rows.append({'logical':f'{logical>>16:02X}:{logical&0xffff:04X}','before':'F8 19','after':'70 1B'})
 update_ws_checksum(out)
 dif=[i for i,(a,b) in enumerate(zip(p,out)) if a!=b]
 allowed={sb+x for h in LOGICAL_HITS for x in (h,h+1)}|{len(out)-2,len(out)-1}
 if any(i not in allowed for i in dif): raise RuntimeError('unexpected diff')
 OUT.write_bytes(out); shutil.copyfile(SAVE,OUT_SAVE)
 rep={'ok':True,'generated_by':Path(__file__).name,'parent_sha256':sha(p),'candidate':str(OUT.relative_to(ROOT)).replace('\\','/'),'sha256':sha(out),'checksum':f'{out[-2]|out[-1]<<8:04X}','wram_old':'19F8','wram_new':'1B70','patches':rows,'non_checksum_changed_bytes':sum(i<len(out)-2 for i in dif),'candidate_saveram_sha256':sha(OUT_SAVE.read_bytes()),'main_tip_unchanged':sha(MAIN.read_bytes())==EXPECTED_MAIN,'live_saveram_unchanged':SAVE.read_bytes()==before_save,'promotion':'blocked_pending_runtime'}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
