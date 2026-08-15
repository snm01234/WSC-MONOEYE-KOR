#!/usr/bin/env python3
"""Restore false-positive 'dialogue' bytes at 69:3D54 back to resource data.

State35 cross-ROM replay isolates current Main's ending seam to the only three
bytes changed in physical bank E9 (logical bank69): 69:3D54-3D56.  The bytes
sit inside an active 4-byte graphics/animation resource table used while bank69
is mapped into the 3000h ROM window.  A historical raw duplicate-text audit
mistook F2 44 03 for a dialogue token/terminator sequence and rewrote it.

This candidate restores only those three stock/resource bytes.  Diagnostic
candidate; promotion requires user runtime validation.
"""
from __future__ import annotations
import hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base,update_ws_checksum
PARENT=ROOT/'out/patch/monoeye_ko_expanded.wsc'; LIVE=ROOT/'sram/monoeye_ko_expanded.sav'
OUT=ROOT/'out/patch/ending_seam_693d54_resource_restore_candidate.wsc'; OUTSAVE=ROOT/'sram/ending_seam_693d54_resource_restore_candidate.sav'; REPORT=ROOT/'out/patch/ending_seam_693d54_resource_restore_candidate_report.json'
LOGICAL=0x693D54; CURRENT=bytes.fromhex('F33F01'); RESTORE=bytes.fromhex('F24403')
def sha(x):return hashlib.sha256(bytes(x)).hexdigest()
def main():
 p=bytes(load_rom(PARENT)); sb=stock_base(p); off=sb+LOGICAL
 if p[off:off+3]!=CURRENT: raise RuntimeError(f'site drift: {p[off:off+3].hex().upper()}')
 main_sha=sha(p); save_sha=sha(LIVE.read_bytes()); out=bytearray(p); out[off:off+3]=RESTORE; update_ws_checksum(out); OUT.write_bytes(out); shutil.copyfile(LIVE,OUTSAVE)
 rep={'ok':True,'generated_by':'tools/build_ending_seam_693d54_resource_restore_candidate.py','parent_sha256':main_sha,'rom':str(OUT.relative_to(ROOT)).replace('\\','/'),'sha256':sha(out),'checksum':f'{out[-2]|(out[-1]<<8):04X}','restore':{'logical':'69:3D54','before':CURRENT.hex().upper(),'after':RESTORE.hex().upper(),'reason':'false-positive raw text duplicate overlapped active graphics/animation resource 4-byte entry'},'main_tip_unchanged':sha(PARENT.read_bytes())==main_sha,'live_saveram_unchanged':sha(LIVE.read_bytes())==save_sha,'promotion':'blocked_pending_user_runtime'}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
