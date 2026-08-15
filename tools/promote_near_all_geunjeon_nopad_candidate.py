#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import stock_base, read_encoded_z_safe
TIP=ROOT/'out/patch/monoeye_ko_expanded.wsc'; SAVE=ROOT/'sram/monoeye_ko_expanded.sav'
CAND=ROOT/'out/patch/near_all_geunjeon_nopad_candidate.wsc'
REPORT=ROOT/'out/patch/near_all_geunjeon_nopad_promotion_report.json'
POST=ROOT/'out/patch/near_all_geunjeon_nopad_postpromotion_audit.json'
OLD='b490dcbd87afa816475f3024d2d55d96fe77897afb82601b8939dce3e7321ed0'; NEW='6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052'

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def rec(rom:bytes,a:int):
 g=read_encoded_z_safe(rom,stock_base(rom)+a,max_len=16); return bytes(g[0]),int(g[1])-stock_base(rom)
def checksum(rom:bytes):
 s=int.from_bytes(rom[-2:],'little'); c=sum(rom[:-2])&0xffff; return {'stored':f'{s:04X}','computed':f'{c:04X}','valid':s==c}
def main():
 old=TIP.read_bytes(); cand=CAND.read_bytes(); save=SAVE.read_bytes()
 if sha(old)!=OLD or sha(cand)!=NEW: raise RuntimeError('identity drift')
 if rec(cand,0x75B3FD)!=(bytes.fromhex('FB68'),0x75B3FF): raise RuntimeError('target drift')
 if rec(cand,0x75B401)!=(bytes.fromhex('E08F86'),0x75B404): raise RuntimeError('neighbor drift')
 ci=checksum(cand)
 if not ci['valid'] or ci['stored']!='1DCD': raise RuntimeError(ci)
 stamp=datetime.now().astimezone().strftime('%Y%m%d_%H%M%S'); bdir=ROOT/'out/patch/backup'/f'{stamp}_pre_near_all_geunjeon_nopad'; bdir.mkdir(parents=True)
 backup=bdir/TIP.name; shutil.copy2(TIP,backup)
 tmp=TIP.with_name('.'+TIP.name+'.nopad.tmp'); shutil.copy2(CAND,tmp); os.replace(tmp,TIP)
 promoted=TIP.read_bytes()
 if sha(promoted)!=NEW: raise RuntimeError('promotion mismatch')
 env=os.environ.copy(); env['PYTHONIOENCODING']='utf-8'
 fs=ROOT/'out/patch/near_all_geunjeon_nopad_postpromotion_false_segptr.json'
 r=subprocess.run([sys.executable,str(ROOT/'tools/scan_false_segptr_writes.py'),'--target',str(TIP),'--out',str(fs)],cwd=ROOT,env=env,capture_output=True,text=True)
 if r.returncode: raise RuntimeError(r.stderr or r.stdout)
 f=json.loads(fs.read_text(encoding='utf-8'))
 if f.get('ok') is not True or int(f.get('sites_found',-1))!=0: raise RuntimeError('false segptr')
 x=subprocess.run([sys.executable,str(ROOT/'tools/make_main_tip_xdelta.py')],cwd=ROOT,env=env,capture_output=True,text=True)
 if x.returncode: raise RuntimeError(x.stderr or x.stdout)
 meta=json.loads((ROOT/'out/dist/monoeye_ko_expanded_xdelta.json').read_text(encoding='utf-8'))
 if meta.get('roundtrip_matches_main_tip') is not True or str((meta.get('main_tip') or {}).get('sha256','')).lower()!=NEW: raise RuntimeError('xdelta verify')
 if SAVE.read_bytes()!=save: raise RuntimeError('live save changed')
 post={'ok':True,'tip_sha256':NEW,'checksum':checksum(promoted),'target':{'abs':'75B3FD','payload_hex':'FB68','terminator':'75B3FF','visible_padding_removed':True},'neighbor_75B401_unchanged':True,'false_segptr_sites':0,'live_saveram_unchanged':True,'rollback':str(backup.relative_to(ROOT)).replace('\\','/')}
 POST.write_text(json.dumps(post,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 patch=ROOT/'out/dist/monoeye_ko_expanded.xdelta'
 report={'ok':True,'old_tip_sha256':OLD,'new_tip_sha256':NEW,'checksum':checksum(promoted),'rollback':post['rollback'],'xdelta':{'path':'out/dist/monoeye_ko_expanded.xdelta','sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),'roundtrip_matches_main_tip':True},'live_saveram_unchanged':True}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
