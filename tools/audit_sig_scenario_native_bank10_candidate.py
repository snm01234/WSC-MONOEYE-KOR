#!/usr/bin/env python3
"""Read-only independent audit for sig_scenario_native_bank10_candidate.wsc."""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base,read_encoded_z_safe,Tbl,token_from_dict_index,ws_header
from apply_ext_dict_unit import load_ext_meta,make_dictionary_ext3
from extract_script import split_prefix_body

MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'
CAND=ROOT/'out/patch/sig_scenario_native_bank10_candidate.wsc'
TBL=ROOT/'out/patch/hangul_patch_pad3.tbl'
EXT=ROOT/'out/patch/exp_dictionary_meta.json'
EXT3=ROOT/'out/patch/ext3_dictionary_meta.json'
EXPECTED='eee70bec18014f0fb04ec0ec66ac3faf33177f2b8ec1c7561c21f7a25b66e2b5'
TARGETS={0x611DF0:(0x0F4D,b'\x17\x34\x18','장난치지　마라！'),0x611DF8:(0x0FA3,b'\x18','세라를　죽여놓고선、'),0x611E05:(0x0FB9,b'','뻔뻔하게　잘도　살아　숨　쉬는구나！！')}
NEIGHBORS=(0x611E10,0x611E13,0x611E20,0x611E2D,0x611E32,0x611E3C,0x611E3F)

def sha(b): return hashlib.sha256(bytes(b)).hexdigest()
def rec(rom,a):
 r=read_encoded_z_safe(rom,stock_base(rom)+a,max_len=128); return bytes(r[0]),int(r[1])

def main():
 p=bytes(load_rom(MAIN)); c=bytes(load_rom(CAND)); tbl=Tbl.load(TBL); d=make_dictionary_ext3(c,load_ext_meta(EXT),load_ext_meta(EXT3)); checks={}; details={}
 checks['candidate_sha']=sha(c)==EXPECTED
 for a,(slot,prefix,text) in TARGETS.items():
  pp,pt=rec(p,a); cp,ct=rec(c,a); token=token_from_dict_index(slot); body=cp[len(prefix):]; rendered=d.expand(token,tbl).rstrip('　')
  ok=cp.startswith(prefix+token) and b'\xE5\x18' not in body and len(cp)==len(pp) and ct==pt and rendered==text
  checks[f'{a:06X}']=ok; details[f'{a:06X}']={'payload':cp.hex().upper(),'rendered':rendered,'terminator':f'{ct-stock_base(c):06X}'}
 for a in NEIGHBORS:
  pp,pt=rec(p,a); cp,ct=rec(c,a); checks[f'neighbor_{a:06X}']=pp==cp and pt==ct
 # Explicitly prove original speaker flow: 08 07 selects Ain before 611E13; 08 02 comes back before 611E32.
 checks['ain_control_611E10_exact']=rec(c,0x611E10)[0]==bytes.fromhex('0807')
 checks['sig_control_611E2D_exact']=rec(c,0x611E2D)[0]==bytes.fromhex('17280802')
 p13,_=rec(c,0x611E13); pre13,b13,_=split_prefix_body(p13); details['611E13_render']=d.expand(b13,tbl).rstrip('　'); checks['611E13_render_ok']=details['611E13_render']=='에？……　죽였다고요？'
 # Header checksum is checked by re-summing all bytes except the checksum field.
 stored=int.from_bytes(c[-2:],'little'); calc=sum(c[:-2]) & 0xFFFF; checks['checksum']=stored==calc
 print(json.dumps({'ok':all(checks.values()),'sha256':sha(c),'checks':checks,'details':details,'header':ws_header(c)},ensure_ascii=False,indent=2))
 return 0 if all(checks.values()) else 1
if __name__=='__main__': raise SystemExit(main())
