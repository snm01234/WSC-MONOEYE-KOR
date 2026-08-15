#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import stock_base, read_encoded_z_safe, update_ws_checksum
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'
SAVE=ROOT/'sram/monoeye_ko_expanded.sav'
OUT=ROOT/'out/patch/near_all_geunjeon_nopad_candidate.wsc'
OUT_SAVE=ROOT/'sram/near_all_geunjeon_nopad_candidate.sav'
REPORT=ROOT/'out/patch/near_all_geunjeon_nopad_candidate_report.json'
EXPECTED='b490dcbd87afa816475f3024d2d55d96fe77897afb82601b8939dce3e7321ed0'
TARGET=0x75B3FD; NEXT=0x75B401

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()

def rec(rom:bytes,a:int):
 g=read_encoded_z_safe(rom,stock_base(rom)+a,max_len=16)
 if g is None: raise RuntimeError(f'unreadable {a:06X}')
 return bytes(g[0]), int(g[1])-stock_base(rom)

def main():
 parent=MAIN.read_bytes(); save=SAVE.read_bytes(); sb=stock_base(parent)
 if sha(parent)!=EXPECTED: raise RuntimeError('main identity drift')
 before,term=rec(parent,TARGET); nxt,nterm=rec(parent,NEXT)
 if before!=bytes.fromhex('FB6801') or term!=0x75B400: raise RuntimeError((before.hex(),hex(term)))
 if nxt!=bytes.fromhex('E08F86') or nterm!=0x75B404: raise RuntimeError('neighbor drift')
 if parent[sb+0x75B400]!=0 or parent[sb+0x75B401:sb+0x75B404]!=bytes.fromhex('E08F86'):
  raise RuntimeError('boundary drift')
 rom=bytearray(parent)
 # Remove the visible 01 space. The label is directly addressed; move its NUL one byte left.
 rom[sb+0x75B3FF]=0
 checksum=update_ws_checksum(rom); result=bytes(rom)
 after,aterm=rec(result,TARGET); nxt2,nterm2=rec(result,NEXT)
 if after!=bytes.fromhex('FB68') or aterm!=0x75B3FF: raise RuntimeError('target nopad failed')
 if nxt2!=nxt or nterm2!=nterm: raise RuntimeError('neighbor changed')
 # B400 remains NUL, so the following directly-addressed record still starts byte-exact at B401.
 if result[sb+0x75B3FF:sb+0x75B402] != bytes.fromhex('0000E0'):
  raise RuntimeError('expected double-NUL boundary absent')
 OUT.write_bytes(result); shutil.copy2(SAVE,OUT_SAVE)
 report={'ok':True,'parent_sha256':EXPECTED,'candidate_sha256':sha(result),'checksum':f'{checksum:04X}',
 'target':{'abs':'75B3FD','before_hex':before.hex().upper(),'after_hex':after.hex().upper(),'terminator_before':f'{term:06X}','terminator_after':f'{aterm:06X}','visible_padding_removed':True},
 'neighbor':{'abs':'75B401','payload_hex':nxt2.hex().upper(),'terminator':f'{nterm2:06X}','byte_exact_unchanged':True},
 'changed_byte':{'logical':'75B3FF','before':'01','after':'00'},'main_unchanged':MAIN.read_bytes()==parent,'live_save_unchanged':SAVE.read_bytes()==save}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
