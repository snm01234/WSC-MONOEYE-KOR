#!/usr/bin/env python3
from pathlib import Path
import hashlib,json,shutil,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import update_ws_checksum
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'
SAVE=ROOT/'sram/monoeye_ko_expanded.sav'
GOOD=ROOT/'out/patch/backup/20260815_012352_pre_encyclopedia_kana_index/monoeye_ko_expanded.wsc'
BAD=ROOT/'out/patch/backup/20260815_015949_pre_name_mapping_spirit_combined/monoeye_ko_expanded.wsc'
OUT1=ROOT/'out/patch/appreciation_bgm_kana_chart_records_restore_probe.wsc'
OUT2=ROOT/'out/patch/appreciation_bgm_pre_kana_boundary_rollback_probe.wsc'
SAV1=ROOT/'sram/appreciation_bgm_kana_chart_records_restore_probe.sav'
SAV2=ROOT/'sram/appreciation_bgm_pre_kana_boundary_rollback_probe.sav'
REPORT=ROOT/'out/patch/appreciation_bgm_kana_boundary_probes_report.json'
EXPECTED='d925ee07b4ea844a1bd89deff52bc88ec21e04bf8ab5a0df8d9fecdd651bd8b1'
ADDRS=[0x75B889,0x75B88F,0x75B896,0x75B89C,0x75B8A3,0x75B8AB,0x75B8B4,0x75B8BA,0x75B8BF]
LENS=[5,6,5,6,7,8,5,4,6]
def sha(b):return hashlib.sha256(bytes(b)).hexdigest()
def main():
 cur=MAIN.read_bytes(); good=GOOD.read_bytes(); bad=BAD.read_bytes(); save=SAVE.read_bytes()
 assert sha(cur)==EXPECTED
 # Prove all non-checksum boundary bytes remain exactly as bad in current.
 boundary=[i for i,(a,b) in enumerate(zip(good,bad)) if a!=b and i<len(cur)-2]
 assert len(boundary)==186
 assert all(cur[i]==bad[i] for i in boundary)
 # records-only restore.
 a=bytearray(cur); rows=[]
 for logical,n in zip(ADDRS,LENS):
  off=0x800000+logical; before=bytes(a[off:off+n]); after=good[off:off+n]; a[off:off+n]=after
  rows.append({'logical':f'{logical:06X}','len':n,'before':before.hex().upper(),'after':after.hex().upper()})
 c1=update_ws_checksum(a); OUT1.write_bytes(a); shutil.copy2(SAVE,SAV1)
 # full exact boundary rollback, excluding historical checksum then recalc current checksum.
 b=bytearray(cur)
 for i in boundary:b[i]=good[i]
 c2=update_ws_checksum(b); OUT2.write_bytes(b); shutil.copy2(SAVE,SAV2)
 rep={'ok':True,'parent_sha256':sha(cur),'good_boundary_sha256':sha(good),'bad_boundary_sha256':sha(bad),'boundary_nonchecksum_bytes':len(boundary),'records_only':{'path':str(OUT1.relative_to(ROOT)).replace('\\','/'),'sha256':sha(a),'checksum':f'{c1:04X}','restored_records':rows,'changed_nonchecksum_bytes':sum(i<len(a)-2 and x!=y for i,(x,y) in enumerate(zip(cur,a)))},'full_boundary_rollback':{'path':str(OUT2.relative_to(ROOT)).replace('\\','/'),'sha256':sha(b),'checksum':f'{c2:04X}','changed_nonchecksum_bytes':sum(i<len(b)-2 and x!=y for i,(x,y) in enumerate(zip(cur,b))),'meaning':'remove exact encyclopedia_kana_index promotion bytes while preserving all later changes'},'main_unchanged':MAIN.read_bytes()==cur,'live_saveram_unchanged':SAVE.read_bytes()==save,'promotion':'blocked_pending_cold_runtime'}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
