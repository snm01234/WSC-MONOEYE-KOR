#!/usr/bin/env python3
"""Independent static audit for id_command_result_badges_ko_candidate."""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import stock_base
from build_id_command_plaques_ko_candidate import decode_grid
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; CAND=ROOT/'out/patch/id_command_result_badges_ko_candidate.wsc'; SAVE=ROOT/'sram/id_command_result_badges_ko_candidate.sav'; LIVE_SAVE=ROOT/'sram/monoeye_ko_expanded.sav'; STOCK=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'; SPEC24=ROOT/'data/id_command_plaque_translations_ko.json'; BUILD=ROOT/'out/patch/id_command_result_badges_ko_candidate_report.json'; OUT=ROOT/'out/patch/id_command_result_badges_ko_candidate_audit.json'
PARENT_SHA='984a0f2cfa1d932abc2ba2bdc2a7e76489c54ba0ef57804933fd9d60ad1170d5'; CAND_SHA='b128e2adc05246859d557c08e2195b3f60d78d456a413eb9bb1f634051a360ae'; TARGETS={0x4C53B4,0x4CC32A}; BLOCK=0x140
def h(x):return hashlib.sha256(bytes(x)).hexdigest()
def runs(a,b):
 r=[];s=None
 for i,(x,y) in enumerate(zip(a,b)):
  if x!=y and s is None:s=i
  elif x==y and s is not None:r.append((s,i));s=None
 if s is not None:r.append((s,len(a)))
 return r
def main():
 p=MAIN.read_bytes();c=CAND.read_bytes();s=STOCK.read_bytes();sv=SAVE.read_bytes();ls=LIVE_SAVE.read_bytes();build=json.loads(BUILD.read_text(encoding='utf-8'));base=stock_base(p)
 failures=[]
 if h(p)!=PARENT_SHA:failures.append('parent_sha')
 if h(c)!=CAND_SHA:failures.append('candidate_sha')
 if sv!=ls:failures.append('save_pair')
 if build.get('ok') is not True:failures.append('build_report')
 allowed=[(base+x,base+x+BLOCK) for x in sorted(TARGETS)]+[(len(c)-2,len(c))]
 rr=runs(p,c); outside=[(a,b) for a,b in rr if not any(lo<=a and b<=hi for lo,hi in allowed)]
 if outside:failures.append('diff_outside')
 if (sum(c[:-2])&0xffff)!=int.from_bytes(c[-2:],'little'):failures.append('checksum')
 target=[]
 for logical in sorted(TARGETS):
  pp=base+logical; before=p[pp:pp+BLOCK]; after=c[pp:pp+BLOCK]
  src=decode_grid(before,5,2); dst=decode_grid(after,5,2)
  stock_exact=before==s[logical:logical+BLOCK]
  border=(src[0]==dst[0] and src[15]==dst[15] and all(src[y][:5]==dst[y][:5] and src[y][35:]==dst[y][35:] for y in range(16)))
  changed=before!=after
  if not stock_exact:failures.append(f'{logical:06X}_source')
  if not border:failures.append(f'{logical:06X}_border')
  if not changed:failures.append(f'{logical:06X}_nochange')
  target.append({'logical':f'{logical:06X}','stock_exact':stock_exact,'outer_rows_and_x0_4_x35_39_preserved':border,'changed':changed,'before_sha256':h(before),'after_sha256':h(after)})
 spec24=json.loads(SPEC24.read_text(encoding='utf-8')); existing=[]
 for row in spec24['plaques']:
  logical=int(row['logical'],16); size=0x140 if row['storage']=='body_plus_shared_cap' else 0x180; pp=base+logical; exact=p[pp:pp+size]==c[pp:pp+size]
  if not exact:failures.append(f'existing24_{logical:06X}')
  existing.append({'logical':f'{logical:06X}','exact':exact})
 report={'schema_version':1,'generated_by':'tools/audit_id_command_result_badges_ko_candidate.py','ok':not failures,'status':'static_audit_passed_pending_user_runtime_test' if not failures else 'failed','parent_sha256':h(p),'candidate_sha256':h(c),'failures':failures,'targets':target,'existing_24_plaques_all_exact':all(x['exact'] for x in existing),'diff_allowlist_clean':not outside,'diff_runs':len(rr),'changed_bytes':sum(b-a for a,b in rr),'paired_saveram_exact':sv==ls,'checksum_valid':(sum(c[:-2])&0xffff)==int.from_bytes(c[-2:],'little'),'promotion':'blocked_pending_user_visual_verification'}
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=True,indent=2));return 0 if not failures else 2
if __name__=='__main__':raise SystemExit(main())
