#!/usr/bin/env python3
"""Read-only audit for shield fifth-column runtime blank candidates."""
from __future__ import annotations
import hashlib, json, struct, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import build_id_command_plaques_ko_candidate as base
import build_id_command_shield_runtime_blank_guard_candidate as g

PARENT=g.PARENT
A=g.OUT
B=ROOT/'out/patch/id_command_shield_runtime_blank_destonly_probe.wsc'
SAVE=g.LIVE_SAVE
ASAVE=g.OUT_SAVE
BSAVE=ROOT/'sram/id_command_shield_runtime_blank_destonly_probe.sav'
OUT=ROOT/'out/patch/id_command_shield_runtime_blank_candidates_audit.json'
EA='3bddb4a6b2367999c1a8d1db93d47f4ffa623e5cad56872d8fa757fc0036c12c'
EB='7d6248ac6cce2a2510359b1538b2092ce999b1bb419753754878d2a4bed7e31a'

def h(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def reljmp_target(data:bytes,phys:int)->int:
 if data[phys]!=0xE9: raise RuntimeError('not near jmp')
 d=struct.unpack_from('<h',data,phys+1)[0]
 return ((phys&0xffff)+3+d)&0xffff

def audit(path:Path,expected:str,save_path:Path)->dict:
 p=PARENT.read_bytes();c=path.read_bytes();s=SAVE.read_bytes();ps=save_path.read_bytes()
 runs=base.diff_runs(p,c)
 shield=p[g.SHIELD_BODY_PHYS:g.SHIELD_BODY_PHYS+g.SHIELD_BODY_BYTES]
 cave_changed=any(c[g.CAVE_PHYS:g.CAVE_PHYS+0x80][i]!=p[g.CAVE_PHYS:g.CAVE_PHYS+0x80][i] for i in range(0x80))
 checks={
  'sha_expected':h(c)==expected,
  'size_16mib':len(c)==16_777_216,
  'checksum_valid':(sum(c[:-2])&0xffff)==int.from_bytes(c[-2:],'little'),
  'v2_shield_body_byte_exact':c[g.SHIELD_BODY_PHYS:g.SHIELD_BODY_PHYS+g.SHIELD_BODY_BYTES]==shield,
  'hook_is_near_jump':c[g.HOOK_PHYS]==0xE9,
  'hook_targets_cave':reljmp_target(c,g.HOOK_PHYS)==g.CAVE_PC,
  'cave_changed':cave_changed,
  'blank_top_exact':c[g.BLANK_TOP_PHYS:g.BLANK_TOP_PHYS+0x20]==g.blank_tile(True),
  'blank_bottom_exact':c[g.BLANK_BOTTOM_PHYS:g.BLANK_BOTTOM_PHYS+0x20]==g.blank_tile(False),
  'paired_save_latest_live_exact':ps==s,
 }
 allowed=[(g.HOOK_PHYS,g.HOOK_PHYS+3),(g.CAVE_PHYS,g.CAVE_PHYS+0x80),(g.BLANK_TOP_PHYS,g.BLANK_BOTTOM_PHYS+0x20),(len(c)-2,len(c))]
 bad=[(x,y) for x,y in runs if not any(a<=x and y<=b for a,b in allowed)]
 checks['diff_allowlist_clean']=not bad
 return {'path':str(path.relative_to(ROOT)).replace('\\','/'),'sha256':h(c),'ok':all(checks.values()),'checks':checks,'diff':{'bytes':sum(y-x for x,y in runs),'runs':len(runs),'unexpected':[{'start':f'{x:08X}','end_exclusive':f'{y:08X}'} for x,y in bad]}}

def main()->int:
 parent=PARENT.read_bytes()
 rep={'schema_version':1,'generated_by':'tools/audit_id_command_shield_runtime_blank_candidates.py','read_only':True,
      'parent_sha256':h(parent),'safe_A':audit(A,EA,ASAVE),'diagnostic_B':audit(B,EB,BSAVE),
      'interpretation':{'A':'source-range + destination-slot guard; promotion still blocked until shield runtime test','B':'destination-slot-only diagnostic; never promote'}}
 rep['ok']=rep['safe_A']['ok'] and rep['diagnostic_B']['ok']
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(rep,ensure_ascii=True,indent=2));return 0 if rep['ok'] else 1
if __name__=='__main__':raise SystemExit(main())
