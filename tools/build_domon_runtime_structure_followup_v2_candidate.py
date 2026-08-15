#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from analyze_p2_duplicate_detachment import external_occurrence_map,nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import make_dictionary_ext3
from build_remaining_dialogue_candidate import diff_runs,covered
from build_scenario_page_boundary_guard_candidate import safe_unreachable_slots
from normalize_ko_text import normalize_ko_text,try_encode_ko_text
from monoeye_rom import Tbl,read_encoded_z_safe,stock_base,token_from_dict_index,update_ws_checksum

MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; SAVE=ROOT/'sram/monoeye_ko_expanded.sav'; ORIG=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'; TBLP=ROOT/'out/patch/hangul_patch_pad3.tbl'
OUT=ROOT/'out/patch/domon_runtime_structure_followup_v2_candidate.wsc'; OUTS=ROOT/'sram/domon_runtime_structure_followup_v2_candidate.sav'; REPORT=ROOT/'out/patch/domon_runtime_structure_followup_v2_candidate_report.json'
MAIN_SHA='2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483'; ORIG_SHA='376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0'
EXT={'stock_count':3831,'slot_count':265,'ext_ptr_off':'0000','ext_seg':'10','ext_in_expansion':True}; EXT3={'num_banks':16,'exp_seg0':'11'}
SLOT_OU=0x00FD; SLOT_FOOL=0x024B; SLOT_YAK=0x00CF; SLOT_PPAR=0x013E; SLOT_GUNA=0x0143
FULL='이　멍청한　놈이！！'; QUICK='약삭빠르구나！！'; SHORT='이……'
TARGETS={
 0x5D956C:bytes.fromhex('4AE518378701010101010101010101'),
 0x5D9590:bytes.fromhex('4AE51828D7010101010101'),
 0x5D95AD:bytes.fromhex('4AE518382C'),
 0x5D9747:bytes.fromhex('4AE518378701010101010101010101'),
 0x5D976B:bytes.fromhex('4AE51828D7010101010101'),
 0x5D9788:bytes.fromhex('4AE518382C'),
 0x62663E:bytes.fromhex('173418E5181CF8')}
TERMS={0x5D956C:0x5D957B,0x5D9590:0x5D959B,0x5D95AD:0x5D95B2,0x5D9747:0x5D9756,0x5D976B:0x5D9776,0x5D9788:0x5D978D,0x62663E:0x626645}
def sha(b): return hashlib.sha256(bytes(b)).hexdigest()
def rr(rom,a):
 sb=stock_base(rom); g=read_encoded_z_safe(rom,sb+a,max_len=128)
 if not g: raise RuntimeError(f'unreadable {a:06X}')
 return bytes(g[0]),g[1]-sb
def enc(tbl,s):
 e=try_encode_ko_text(normalize_ko_text(s),tbl,hangul_marker_code=0xEC8D,hangul_marker_mode='run')
 if not e or b'\0' in e: raise RuntimeError(f'encode failed {s!r}')
 return bytes(e)
def main():
 p=MAIN.read_bytes(); o=ORIG.read_bytes(); s=SAVE.read_bytes(); tbl=Tbl.load(TBLP)
 if sha(p)!=MAIN_SHA or sha(o)!=ORIG_SHA or len(s)!=32768: raise RuntimeError('input identity/SaveRAM size drift')
 d=make_dictionary_ext3(p,EXT,EXT3); sb=stock_base(p)
 for a,b in TARGETS.items():
  got,t=rr(p,a)
  if got!=b or t!=TERMS[a]: raise RuntimeError(f'target drift {a:06X}')
 if d.expand(TARGETS[0x5D9747][1:],tbl).rstrip('　 ')!=FULL: raise RuntimeError('reported Korean phrase drift')
 safe={int(r['index']):r for r in safe_unreachable_slots(p,d)}
 selected_slots=(SLOT_OU,SLOT_FOOL,SLOT_YAK,SLOT_PPAR,SLOT_GUNA)
 for idx in selected_slots:
  if idx not in safe: raise RuntimeError(f'slot {idx:04X} no longer safe')
 payloads={SLOT_OU:enc(tbl,'오우'),SLOT_FOOL:enc(tbl,FULL),SLOT_YAK:enc(tbl,'약삭'),SLOT_PPAR:enc(tbl,'빠르'),SLOT_GUNA:enc(tbl,'구나')}
 for idx,e in payloads.items():
  if len(e)>int(safe[idx]['old_len']): raise RuntimeError(f'slot capacity {idx:04X}')
 c=bytearray(p); allowed=[]; slots=[]
 labels={SLOT_OU:'오우',SLOT_FOOL:FULL,SLOT_YAK:'약삭',SLOT_PPAR:'빠르',SLOT_GUNA:'구나'}
 for idx in selected_slots:
  r=safe[idx]; st=int(r['entry_abs']); old=int(r['old_len']); before=d.expand_index(idx,tbl); e=payloads[idx]
  c[st:st+len(e)]=e; c[st+len(e)]=0; allowed.append((st,st+old+1)); slots.append({'index':f'{idx:04X}','before':before,'after':labels[idx],'raw':e.hex().upper(),'entry_abs':st,'old_len':old})
 fooltok=token_from_dict_index(SLOT_FOOL); outok=token_from_dict_index(SLOT_OU); yaktok=token_from_dict_index(SLOT_YAK); ppartok=token_from_dict_index(SLOT_PPAR); gunatok=token_from_dict_index(SLOT_GUNA); exclam=token_from_dict_index(0x0044); itok=token_from_dict_index(0x0053); ell=token_from_dict_index(0x0191)
 quickbody=yaktok+ppartok+gunatok+exclam+b'\x01'*2
 shortbody=itok+ell
 afters={
  0x5D956C:b'\x4A'+fooltok+b'\x01'*12,
  0x5D9590:b'\x4A'+quickbody,
  0x5D95AD:b'\x4A'+shortbody,
  0x5D9747:b'\x4A'+fooltok+b'\x01'*12,
  0x5D976B:b'\x4A'+quickbody,
  0x5D9788:b'\x4A'+shortbody,
  0x62663E:bytes.fromhex('173418')+outok+exclam,
 }
 patches=[]
 for a,new in afters.items():
  old=TARGETS[a]
  if len(new)!=len(old): raise RuntimeError(f'extent {a:06X}')
  c[sb+a:sb+a+len(new)]=new; allowed.append((sb+a,sb+a+len(new))); patches.append({'abs':f'{a:06X}','before':old.hex().upper(),'after':new.hex().upper(),'terminator':f'{TERMS[a]:06X}'})
 chk=update_ws_checksum(c); allowed.append((len(c)-2,len(c))); cb=bytes(c); fd=make_dictionary_ext3(cb,EXT,EXT3)
 if fd.expand(fooltok,tbl)!=FULL or fd.expand(outok+exclam,tbl)!='오우！！' or fd.expand(quickbody,tbl).rstrip('　 ')!=QUICK or fd.expand(shortbody,tbl)!=SHORT: raise RuntimeError('native render mismatch')
 expected_render={0x5D956C:FULL,0x5D9590:QUICK,0x5D95AD:SHORT,0x5D9747:FULL,0x5D976B:QUICK,0x5D9788:SHORT}
 for a,want in expected_render.items():
  pl,t=rr(cb,a)
  if t!=TERMS[a] or pl[0]!=0x4A or b'\xE5\x18' in pl[1:] or fd.expand(pl[1:],tbl).rstrip('　 ')!=want: raise RuntimeError(f'voice verify {a:06X}')
 pl,t=rr(cb,0x62663E)
 if t!=TERMS[0x62663E] or b'\xE5\x18' in pl[3:] or fd.expand(pl[3:],tbl)!='오우！！': raise RuntimeError('ou verify')
 # 626102 was a prior misidentification and must stay byte-exact to main in v2.
 a=0x626102; old,t=rr(p,a); new,nt=rr(cb,a)
 if old!=new or t!=nt: raise RuntimeError('626102 speculative v1 change leaked into v2')
 selected=set(selected_slots); ext=external_occurrence_map(cb,ext3_aware=True,wanted=selected); nested=nested_occurrence_map(fd,wanted=selected,ext3_aware=True); raw=_raw_pair_hits(cb,sorted(selected))
 expected={SLOT_OU:[0x626641],SLOT_FOOL:[0x5D956D,0x5D9748],SLOT_YAK:[0x5D9591,0x5D976C],SLOT_PPAR:[0x5D9593,0x5D976E],SLOT_GUNA:[0x5D9595,0x5D9770]}
 refs=[]
 for idx in sorted(selected):
  ea=sorted(int(str(x['token_abs']),16) for x in ext.get(idx,[])); ra=sorted(int(str(x['token_abs']),16) for x in raw.get(idx,[])); ne=nested.get(idx,[])
  if ea!=expected[idx] or ra!=expected[idx] or ne: raise RuntimeError(f'reference proof {idx:04X}: {ea} {ra} {ne}')
  refs.append({'index':f'{idx:04X}','expected':[f'{x:06X}' for x in expected[idx]],'external':[f'{x:06X}' for x in ea],'raw':[f'{x:06X}' for x in ra],'nested':[]})
 runs=diff_runs(p,cb); outside=[r for r in runs if not covered(r,allowed)]
 if outside: raise RuntimeError(f'diff escape {outside[:4]}')
 OUT.write_bytes(cb); shutil.copy2(SAVE,OUTS)
 rep={'schema_version':1,'generated_by':'tools/build_domon_runtime_structure_followup_v2_candidate.py','status':'pending_user_runtime_validation','parent_sha256':sha(p),'candidate_sha256':sha(cb),'checksum':f'{chk:04X}','saveram_sha256':sha(OUTS.read_bytes()),'scope':{'validated_keep':'62663E 오우！！ native two-token fix','corrected_misdiagnosis':'626102 is unchanged; actual こ+이 멍청한 놈이!! address is 5D9747 with duplicate 5D956C','natural_ko_same_metadata_family':'all six authoritative metadata 4A records whose pristine visible sentence begins with こ are converted from E5 18 to ordinary native stock tokens','new_targets':['5D956C','5D9590','5D95AD','5D9747','5D976B','5D9788']},'slots':slots,'patches':patches,'renders':{'5D956C':FULL,'5D9590':QUICK,'5D95AD':SHORT,'5D9747':FULL,'5D976B':QUICK,'5D9788':SHORT,'62663E':'오우！！'},'reference_proof':refs,'unexpected_diff_runs':0}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(rep,ensure_ascii=True,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
