#!/usr/bin/env python3
"""Independent static audit for sig_scenario_ext3free_probe_candidate.wsc."""
from __future__ import annotations
import hashlib,json,struct,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import Tbl,load_rom,read_encoded_z_safe,stock_base,token_from_dict_index
from apply_ext_dict_unit import load_ext_meta,make_dictionary_ext3
from extract_script import split_prefix_body
from expand_dictionary import iter_dict_indices
from mixed_residual_reference_union import _working_two_byte_external_refs

MAIN=ROOT/'sram/monoeye_ko_expanded.wsc'; CAND=ROOT/'out/patch/sig_scenario_ext3free_probe_candidate.wsc'; SAV=ROOT/'sram/sig_scenario_ext3free_probe_candidate.sav'; LIVE=ROOT/'sram/monoeye_ko_expanded.sav'
REPORT=ROOT/'out/patch/sig_scenario_ext3free_probe_audit.json'; TARGETS=ROOT/'out/script/sig_scenario_ext3free_probe_targets.json'
TBL=ROOT/'out/patch/hangul_patch_pad3.tbl'; EXT=ROOT/'out/patch/exp_dictionary_meta.json'; EXT3=ROOT/'out/patch/ext3_dictionary_meta.json'
EXPECTED_MAIN='b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a'; EXPECTED_CAND='e39edfbe4c49f63e7352062fb598ea9919a22a23ad1a60a27d4e4ee2753c99d0'
RANGES=((0x0D4C,0x0D52),(0x0E81,0x0E87),(0x0DB3,0x0DB8),(0x0EA2,0x0EA7),(0x0C38,0x0C3C),(0x0C86,0x0C8A),(0x0D63,0x0D66)); RESERVED=tuple(i for lo,hi in RANGES for i in range(lo,hi+1)); RS=set(RESERVED)
BLOCK=(0x611DED,0x612100); EXPBANK=0x26; TRAMP=0x7AFFED; WRAP=0x7FFF18; OLD=0x7FFC8C; OLDHEX=bytes.fromhex('81FEEE1D7213B0109AB5DE00805681EEEE1D268B8400005ECB268B84CC7BCB')
CANNON={0x75C3D3:'메가　캐논　포',0x75C7B2:'배부　빔　캐논',0x75C7E5:'빔　캐논',0x75CBC7:'메가　캐논'}

def sha(b): return hashlib.sha256(bytes(b)).hexdigest()
def fa(r,a): return stock_base(r)+a
def rec(r,a):
 g=read_encoded_z_safe(r,fa(r,a),max_len=160)
 if not g: raise RuntimeError(f'unreadable {a:06X}')
 return bytes(g[0]),int(g[1])-stock_base(r)
def nested_native(d):
 out={}
 for p in range(min(int(d.count),0x1000)):
  try: raw=bytes(d.raw_entry(p))
  except Exception: continue
  for c in iter_dict_indices(raw):
   c=int(c)
   if c in RS and c!=p: out.setdefault(c,[]).append(p)
 return out
def nested_ext3(d):
 out={}
 for off in range(int(d.ext3_banks)*0x1000):
  p=0x1000+off
  try: raw=bytes(d.raw_entry(p))
  except Exception: continue
  for c in iter_dict_indices(raw):
   c=int(c)
   if c in RS: out.setdefault(c,[]).append(p)
 return out

def main():
 parent=bytes(load_rom(MAIN)); cand=bytes(load_rom(CAND)); tbl=Tbl.load(TBL); d0=make_dictionary_ext3(parent,load_ext_meta(EXT),load_ext_meta(EXT3))
 tdata=json.loads(TARGETS.read_text(encoding='utf-8'))['targets']; failures=[]; checks={}
 checks['main_identity']=sha(parent)==EXPECTED_MAIN; checks['candidate_identity']=sha(cand)==EXPECTED_CAND; checks['save_equals_live']=SAV.read_bytes()==LIVE.read_bytes()
 checks['old_helper_exact']=cand[fa(cand,OLD):fa(cand,OLD)+len(OLDHEX)]==OLDHEX
 # Reserved were unreachable on parent, including ext3 nested.
 ext=_working_two_byte_external_refs(parent); external={i:[r.abs for r in ext.get(i,[])] for i in RESERVED if ext.get(i)}; nn=nested_native(d0); en=nested_ext3(d0)
 checks['reserved_parent_unreachable']=not external and not nn and not en
 # Candidate pointer table: exactly 40 non-FFFF entries.
 bb=EXPBANK*0x10000; nonff=[i for i in range(0x1000) if struct.unpack_from('<H',cand,bb+i*2)[0]!=0xFFFF]
 checks['bank26_exact_reserved']=set(nonff)==RS and len(nonff)==len(RESERVED)
 # Target records exact shape and phrase round-trip.
 target_fail=[]
 for idx,row in zip(RESERVED,tdata):
  a=int(row['abs'],16); p0,t0=rec(parent,a); p1,t1=rec(cand,a); pre=bytes.fromhex(row['prefix_hex']); tok=token_from_dict_index(idx); body=p1[len(pre):]
  ptr=struct.unpack_from('<H',cand,bb+idx*2)[0]; q=bb+ptr; z=cand.find(b'\x00',q,bb+0x10000); raw=cand[q:z]
  try: oldidx=int(row['old_ext3_index'],16); oldraw=bytes(d0.raw_entry(oldidx))
  except Exception: oldraw=b'!BAD!'
  ok=(p1.startswith(pre) and len(p1)==len(p0) and t1==t0 and body.startswith(tok) and b'\xE5\x18' not in body and raw==oldraw)
  if not ok: target_fail.append({'abs':row['abs'],'idx':f'{idx:04X}'})
 checks['targets_40_exact']=not target_fail and len(tdata)==40
 # Every target in the block removed E5 18. Non-target records in block unchanged.
 targetset={int(r['abs'],16) for r in tdata}; risk=[]; non_target=[]; a=BLOCK[0]
 while a<BLOCK[1]:
  p0,t0=rec(parent,a); p1,t1=rec(cand,a)
  pre1,b1,_=split_prefix_body(p1)
  if b1.startswith(b'\xE5\x18'): risk.append(f'{a:06X}')
  if a not in targetset and (p0!=p1 or t0!=t1): non_target.append(f'{a:06X}')
  a=t0+1
 checks['block_ext3_zero']=not risk; checks['block_non_targets_unchanged']=not non_target
 # Reported sequence specifically has no ext3 starting with 611DF0.
 reported={}
 for a in (0x611DF0,0x611DF8,0x611E05,0x611E13,0x611E20):
  p,t=rec(cand,a); pre,b,_=split_prefix_body(p); reported[f'{a:06X}']={'payload_hex':p.hex().upper(),'prefix_hex':pre.hex().upper(),'body_hex':b.hex().upper(),'has_ext3':b.startswith(b'\xE5\x18'),'terminator':f'{t:06X}'}
 checks['reported_ext3_zero']=all(not x['has_ext3'] for x in reported.values())
 # Cannon output exact; records are unchanged from parent.
 cannon=[]
 d1=make_dictionary_ext3(cand,load_ext_meta(EXT),load_ext_meta(EXT3))
 for a,exp in CANNON.items():
  p0,t0=rec(parent,a);p1,t1=rec(cand,a);pre,b,_=split_prefix_body(p1);rend=d1.expand(b,tbl).rstrip('　');ok=(p0==p1 and t0==t1 and rend==exp);cannon.append({'abs':f'{a:06X}','rendered':rend,'ok':ok})
 checks['cannon_4_exact']=all(x['ok'] for x in cannon)
 stored=int.from_bytes(cand[-2:],'little'); calc=sum(cand[:-2])&0xFFFF; checks['checksum_exact']=stored==calc
 if not all(checks.values()): failures=[k for k,v in checks.items() if not v]
 rep={'ok':not failures,'checks':checks,'failures':failures,'candidate_sha256':sha(cand),'target_failures':target_fail,'risk_residual':risk,'non_target_changes':non_target,'reserved_external':external,'reserved_native_nested':nn,'reserved_ext3_nested':{f'{k:04X}':[f'{x:05X}' for x in v[:20]] for k,v in en.items()},'reported':reported,'cannon':cannon,'checksum':{'stored':f'{stored:04X}','calculated':f'{calc:04X}'}}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'ok':rep['ok'],'candidate_sha256':rep['candidate_sha256'],'checks':checks,'reported':reported},ensure_ascii=False,indent=2));return 0 if rep['ok'] else 1
if __name__=='__main__': raise SystemExit(main())
