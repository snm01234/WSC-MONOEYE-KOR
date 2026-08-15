#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
import build_id_command_plaques_ko_candidate as base
P=ROOT/'out/patch/id_command_residual_plaques_ko_followup_v2_candidate.wsc'; C=ROOT/'out/patch/id_command_shield_centered_candidate.wsc'; S=ROOT/'sram/monoeye_ko_expanded.sav'; PS=ROOT/'sram/id_command_shield_centered_candidate.sav'
EP='93b1b0222a672c5ee8e059f567380985f55c29ef558f6af5b981d5d5edecbf30'; EC='0baef831de707e038ad690a1d25ef4ca5b13b297528789c74b204ccce90a27a7'; B=0x800000; A=0x4C4BB4; BG=0xC; F=0xF
def sha(x): return hashlib.sha256(x).hexdigest()
def main():
 p=P.read_bytes(); c=C.read_bytes(); s=S.read_bytes(); ps=PS.read_bytes(); body=base.decode_grid(c[B+A:B+A+256],4,2); runs=base.diff_runs(p,c); allow=[(B+A,B+A+256),(len(c)-2,len(c))]; bad=[(a,b) for a,b in runs if not any(x<=a and b<=y for x,y in allow)]
 checks={'parent_sha':sha(p)==EP,'candidate_sha':sha(c)==EC,'checksum_valid':(sum(c[:-2])&0xffff)==int.from_bytes(c[-2:],'little'),'shield_only_plus_checksum':not bad,'duplicated_col3_text_free':all(body[y][24:32]==([F]*8 if y in (0,15) else [BG]*8) for y in range(16)),'paired_save_exact':ps==s}
 rep={'schema_version':1,'generated_by':'tools/audit_id_command_shield_centered_candidate.py','read_only':True,'ok':all(checks.values()),'parent_sha256':sha(p),'candidate_sha256':sha(c),'checks':checks,'diff':{'changed_bytes_including_checksum':sum(b-a for a,b in runs),'run_count':len(runs),'unexpected':bad},'runtime_model':'col0,col1,col2,col3,col3,right-cap; duplicated col3 carries background only','promotion':'blocked_pending_user_runtime_visual_test'}
 out=ROOT/'out/patch/id_command_shield_centered_candidate_audit.json'; out.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(rep,ensure_ascii=True,indent=2)); return 0 if rep['ok'] else 1
if __name__=='__main__': raise SystemExit(main())
