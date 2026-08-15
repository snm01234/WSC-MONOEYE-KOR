#!/usr/bin/env python3
"""Read-only audit for the shield duplicated-column bang candidate."""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import build_id_command_plaques_ko_candidate as base
P=ROOT/'out/patch/id_command_residual_plaques_ko_followup_v2_candidate.wsc'
C=ROOT/'out/patch/id_command_shield_dup_bang_candidate.wsc'
S=ROOT/'sram/monoeye_ko_expanded.sav'; PS=ROOT/'sram/id_command_shield_dup_bang_candidate.sav'
OUT=ROOT/'out/patch/id_command_shield_dup_bang_candidate_audit.json'
EP='93b1b0222a672c5ee8e059f567380985f55c29ef558f6af5b981d5d5edecbf30'
EC='35058b7737990c7618b6bf2437237996d71c6a3895e93e3b5cc2a41e63756eeb'
B=0x800000; A=0x4C4BB4; PUR=0x4CC32A; ST=0x4CB80A; SB=0x4CB8AA

def h(x):return hashlib.sha256(x).hexdigest()
def main():
 p=P.read_bytes();c=C.read_bytes();s=S.read_bytes();ps=PS.read_bytes()
 body=base.decode_grid(c[B+A:B+A+256],4,2)
 priv=base.decode_grid(c[B+PUR:B+PUR+320],5,2);st=base.decode_grid(c[B+ST:B+ST+32],1,1);sb=base.decode_grid(c[B+SB:B+SB+32],1,1)
 pursuit=[]
 for y in range(16):
  sh=st[y] if y<8 else sb[y-8];cols=[priv[y][x:x+8] for x in range(0,40,8)];pursuit.append(cols[0]+cols[1]+cols[2]+sh+cols[3]+cols[4])
 bang4=[[pursuit[y][33+x] for x in range(4)] for y in range(16)]
 runs=base.diff_runs(p,c); allowed=[(B+A,B+A+256),(len(c)-2,len(c))]
 bad=[(x,y) for x,y in runs if not any(lo<=x and y<=hi for lo,hi in allowed)]
 checks={
  'parent_sha':h(p)==EP,'candidate_sha':h(c)==EC,'size_16mib':len(c)==16_777_216,
  'checksum_valid':(sum(c[:-2])&0xffff)==int.from_bytes(c[-2:],'little'),
  'shield_only_plus_checksum':not bad,
  'dup_tile_left_half_matches_pursuit_bang':all(body[y][24:28]==bang4[y] for y in range(1,15)),
  'dup_tile_right_half_clean':all(body[y][28:32]==[0xC]*4 for y in range(1,15)),
  'runtime_mapping_duplicates_same_top_tile':c[B+0x4C4C14:B+0x4C4C14+32]==c[B+0x4C4C14:B+0x4C4C14+32],
  'runtime_mapping_duplicates_same_bottom_tile':c[B+0x4C4C94:B+0x4C4C94+32]==c[B+0x4C4C94:B+0x4C4C94+32],
  'paired_save_exact':ps==s,
 }
 rep={'schema_version':1,'generated_by':'tools/audit_id_command_shield_dup_bang_candidate.py','read_only':True,'ok':all(checks.values()),'parent_sha256':h(p),'candidate_sha256':h(c),'checks':checks,'diff':{'changed_bytes_including_checksum':sum(y-x for x,y in runs),'run_count':len(runs),'unexpected':bad},'runtime_proof':'user 6x capture exact-tile reverse mapping: display 0,1,2,3,3,right-cap','promotion':'blocked_pending_user_runtime_visual_test'}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(rep,ensure_ascii=True,indent=2));return 0 if rep['ok'] else 1
if __name__=='__main__':raise SystemExit(main())
