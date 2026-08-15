#!/usr/bin/env python3
import hashlib,json,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'tools'))
from apply_ext_dict_unit import make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_remaining_dialogue_candidate import covered,diff_runs
from monoeye_rom import Dictionary,Tbl,read_encoded_z_safe,stock_base,token_from_dict_index,update_ws_checksum
from normalize_ko_text import normalize_ko_text,try_encode_ko_text
M=R/'out/patch/monoeye_ko_expanded.wsc';S=R/'sram/monoeye_ko_expanded.sav';O=R/'SD Gundam G Generation Mono-Eye Gundams.wsc';T=R/'out/patch/hangul_patch_pad3.tbl'
W=R/'out/patch/id_command_dynamic_labels_followup_candidate.wsc';WS=R/'sram/id_command_dynamic_labels_followup_candidate.sav';J=R/'out/patch/id_command_dynamic_labels_followup_candidate_report.json'
MH='2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483';SH='c395a8dbe2ecbebd7e3e7f55b8e58adb01f143749f82b2f26fde005f9d73b259';META={'stock_count':3831,'slot_count':265,'ext_ptr_off':'0000','ext_seg':'10','ext_in_expansion':True};M3={'num_banks':16,'exp_seg0':'11'}
def h(x):return hashlib.sha256(x).hexdigest()
def z(x,a):
 g=read_encoded_z_safe(x,stock_base(x)+a,64)
 if g is None:raise RuntimeError(hex(a))
 return bytes(g[0]),int(g[1])
def main():
 p=M.read_bytes();s=S.read_bytes();o=O.read_bytes();sb=stock_base(p);t=Tbl.load(T);d=make_dictionary_ext3(p,META,M3);q=Dictionary(p);qo=Dictionary(o)
 if h(p)!=MH or h(s)!=SH:raise RuntimeError('parent/save drift')
 a,at=z(p,0x5F38F0);b,bt=z(p,0x5F4A21)
 if (a,d.expand(a,t),at-sb)!=(bytes.fromhex('E02F7B'),'追撃',0x5F38F3):raise RuntimeError('追撃 drift')
 if (b,d.expand(b,t),bt-sb)!=(bytes.fromhex('E025E209'),'回避',0x5F4A25):raise RuntimeError('回避 drift')
 if d.expand_index(0x505,t)!='회피' or d.expand_index(0xA9,t)!='':raise RuntimeError('helper drift')
 if q.ptrs[0xAE]!=0x38F0 or q.ptrs[0xAF]!=0x38F4 or q.ptrs[0x93]!=0x388A:raise RuntimeError('pointer drift')
 if 0x93 not in set(current_strong_retired_slots(o,p,d)):raise RuntimeError('0093 not retired')
 if qo.ptrs[0x94]!=0x388E or q.ptrs[0x94]!=0xE03A or d.expand_index(0x94,t)!='수령':raise RuntimeError('0094 orphan drift')
 if any(0x388A<x<0x3893 for x in q.ptrs) or min(x for x in q.ptrs if x>0x388A)!=0x3893:raise RuntimeError('reclaim span busy')
 k=bytes(try_encode_ko_text(normalize_ko_text('추격'),t,hangul_marker_code=0xEC8D,hangul_marker_mode='run') or b'')
 if k!=bytes.fromhex('EC8DE88EE758'):raise RuntimeError('encoding drift')
 c=bytearray(p);base=sb+0x5F0000;c[base+0x388A:base+0x3893]=k+b'\0\0\0';pt=token_from_dict_index(0x93);c[sb+0x5F38F0:sb+0x5F38F3]=pt+b'\0';er=token_from_dict_index(0x505)+token_from_dict_index(0xA9);c[sb+0x5F4A21:sb+0x5F4A25]=er;ck=update_ws_checksum(c);r=bytes(c);rd=make_dictionary_ext3(r,META,M3);rq=Dictionary(r)
 aa,aat=z(r,0x5F38F0);bb,bbt=z(r,0x5F4A21)
 if rd.expand_index(0x93,t)!='추격' or rd.expand_index(0xAE,t)!='추격' or (aa,aat-sb)!=(pt,0x5F38F2) or r[sb+0x5F38F3]!=0:raise RuntimeError('추격 verify')
 if (bb,bbt-sb)!=(er,0x5F4A25) or rd.expand(bb,t)!='회피':raise RuntimeError('회피 verify')
 if rq.ptrs[0xAF]!=0x38F4 or rq.ptrs[0x94]!=0xE03A or rd.expand_index(0x94,t)!='수령':raise RuntimeError('collateral')
 al=[(base+0x388A,base+0x3893),(sb+0x5F38F0,sb+0x5F38F3),(sb+0x5F4A21,sb+0x5F4A25),(len(p)-2,len(p))];runs=diff_runs(p,r)
 if any(not covered(x,al) for x in runs):raise RuntimeError('diff outside allowlist')
 W.write_bytes(r);shutil.copy2(S,WS)
 if M.read_bytes()!=p or S.read_bytes()!=s or W.read_bytes()!=r or WS.read_bytes()!=s:raise RuntimeError('reread/mutation')
 rep={'ok':True,'status':'runtime_test_pending','candidate':str(W),'sha256':h(r),'save':str(WS),'save_sha256':h(s),'checksum':f'{ck:04X}','targets':{'5F4A21':'回避->회피','5F38F0':'追撃->추격'},'strategy':{'回避':'F505+F0A9; original terminator preserved','追撃':'00AE->native retired 0093; no E518/compact3; old 5F38F3 remains zero'},'reclaim':'0093 @ 5F388A-5F3892 (strong-retired; former 0094 bytes orphaned)','diff_runs':len(runs),'changed_bytes':sum(y-x for x,y in runs),'allowlist_clean':True,'main_unchanged':True,'save_unchanged':True}
 J.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
