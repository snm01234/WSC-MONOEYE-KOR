#!/usr/bin/env python3
"""Read-only audit for the shield duplicate-overlap fix candidate."""
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import build_id_command_plaques_ko_candidate as base
PARENT=ROOT/'out/patch/id_command_residual_plaques_ko_followup_v2_candidate.wsc'
CAND=ROOT/'out/patch/id_command_shield_dup_bang_v2_candidate.wsc'
LIVE=ROOT/'sram/monoeye_ko_expanded.sav'; SAV=ROOT/'sram/id_command_shield_dup_bang_v2_candidate.sav'
REPORT=ROOT/'out/patch/id_command_shield_dup_bang_v2_candidate_audit.json'
EP='93b1b0222a672c5ee8e059f567380985f55c29ef558f6af5b981d5d5edecbf30'
EC='cdd272d6c701562ae8d2495d8df0f4247c8a0aed622d6b780b8de7200e9746ee'
BASE=0x800000; SHIELD=0x4C4BB4; SUCCESS=0x4C4654; BG=0xC; INK=0xE; OUTLINE=0xF
def sha(b): return hashlib.sha256(bytes(b)).hexdigest()
def main():
 p=PARENT.read_bytes(); c=CAND.read_bytes(); live=LIVE.read_bytes(); sav=SAV.read_bytes()
 body=base.decode_grid(c[BASE+SHIELD:BASE+SHIELD+256],4,2)
 success=base.decode_grid(c[BASE+SUCCESS:BASE+SUCCESS+384],6,2); right=[r[40:48] for r in success]
 display=[body[y][:32]+body[y][24:32]+right[y] for y in range(16)]
 runs=base.diff_runs(p,c); allowed=[(BASE+SHIELD,BASE+SHIELD+256),(len(c)-2,len(c))]
 unexpected=[(s,e) for s,e in runs if not any(lo<=s and e<=hi for lo,hi in allowed)]
 checks={
  'parent_sha':sha(p)==EP,'candidate_sha':sha(c)==EC,'size_16mib':len(c)==16_777_216,
  'checksum_valid':(sum(c[:-2])&0xffff)==int.from_bytes(c[-2:],'little'),
  'shield_only_plus_checksum':not unexpected,'display_width_48':all(len(r)==48 for r in display),
  'duplicated_tile_is_identical':all(display[y][24:32]==display[y][32:40] for y in range(16)),
  'trailing_half_clean':all(body[y][27:32]==[BG]*5 for y in range(1,15)),
  'minimal_gap':body[12][24:27]==[BG]*3,'minimal_dot':body[13][24:27]==[INK,OUTLINE,BG],
  'paired_save_exact':sav==live,
 }
 rep={'schema_version':1,'generated_by':'tools/audit_id_command_shield_dup_bang_v2_candidate.py','read_only':True,'ok':all(checks.values()),
      'parent_sha256':sha(p),'candidate_sha256':sha(c),'checks':checks,
      'diff':{'changed_bytes_including_checksum':sum(e-s for s,e in runs),'run_count':len(runs),'unexpected':unexpected},
      'runtime_model':'user capture: col0,col1,col2,col3,col3,right-cap','promotion':'blocked_pending_user_runtime_visual_test'}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(rep,ensure_ascii=True,indent=2)); return 0 if rep['ok'] else 1
if __name__=='__main__': raise SystemExit(main())
