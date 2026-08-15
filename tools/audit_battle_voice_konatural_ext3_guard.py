#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from apply_ext_dict_unit import make_dictionary_ext3
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base

MAIN = ROOT/'out/patch/monoeye_ko_expanded.wsc'
ORIG = ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'
INV = ROOT/'out/script/battle_dialogue_speaker_portrait_metadata_inventory.csv'
TBL = ROOT/'out/patch/hangul_patch_pad3.tbl'
PARENT_SHA='2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483'
EXPECTED_PARENT_RISK=277
REPORTED={0x5D956C,0x5D9747}
SAME_METADATA_FAMILY={0x5D956C,0x5D9590,0x5D95AD,0x5D9747,0x5D976B,0x5D9788}
EXT={'stock_count':3831,'slot_count':265,'ext_ptr_off':'0000','ext_seg':'10','ext_in_expansion':True}
EXT3={'num_banks':16,'exp_seg0':'11'}

def sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()

def risks(rom:bytes, orig:bytes, tbl:Tbl):
    d=make_dictionary_ext3(rom,EXT,EXT3); od=Dictionary(orig); sb=stock_base(rom); osb=stock_base(orig); out=[]
    with INV.open(encoding='utf-8-sig',newline='') as f:
        for x in csv.DictReader(f):
            a=int(x['record_start'],16); struct=bytes.fromhex(x.get('authoritative_structure_hex') or '')
            if not struct: continue
            cg=read_encoded_z_safe(rom,sb+a,max_len=128); og=read_encoded_z_safe(orig,osb+a,max_len=128)
            if not cg or not og: continue
            cp,op=bytes(cg[0]),bytes(og[0])
            if not cp.startswith(struct) or not op.startswith(struct): continue
            cb,ob=cp[len(struct):],op[len(struct):]
            jp=od.expand(ob,tbl); ko=d.expand(cb,tbl).rstrip('　 ')
            if jp.startswith('こ') and cb.startswith(b'\xE5\x18'):
                out.append({'abs':f'{a:06X}','structure':struct.hex().upper(),'jp':jp,'ko':ko,'body_hex':cb.hex().upper()})
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--target',default=str(MAIN)); ap.add_argument('--out'); args=ap.parse_args()
    parent=MAIN.read_bytes(); target=Path(args.target).read_bytes(); orig=ORIG.read_bytes(); tbl=Tbl.load(TBL)
    if sha(parent)!=PARENT_SHA: raise SystemExit('bound parent TIP drifted')
    pr=risks(parent,orig,tbl); tr=risks(target,orig,tbl)
    pset={int(r['abs'],16) for r in pr}; tset={int(r['abs'],16) for r in tr}
    if len(pr)!=EXPECTED_PARENT_RISK: raise SystemExit(f'parent risk population drifted: {len(pr)}')
    new=sorted(tset-pset); reported_left=sorted(REPORTED&tset); same_family_left=sorted(SAME_METADATA_FAMILY&tset)
    parent_same=sorted(SAME_METADATA_FAMILY&pset)
    if parent_same!=sorted(SAME_METADATA_FAMILY): raise SystemExit('bound metadata-4A natural-こ family drifted')
    ok=not new and not reported_left and not same_family_left and len(tr)<=len(pr)-len(SAME_METADATA_FAMILY)
    report={'schema_version':1,'generated_by':'tools/audit_battle_voice_konatural_ext3_guard.py','ok':ok,
            'rule':'after authoritative battle-voice metadata, pristine visible Japanese beginning with こ must not gain a new E5 18 special-consumer body; the runtime-proven reported family is required native-only',
            'parent_sha256':sha(parent),'target_sha256':sha(target),
            'counts':{'parent_risk':len(pr),'target_risk':len(tr),'new_risk':len(new),'reported_family_remaining':len(reported_left),'metadata_4A_natural_ko_parent':len(parent_same),'metadata_4A_natural_ko_remaining':len(same_family_left)},
            'new_risk':[f'{a:06X}' for a in new],'reported_family_remaining':[f'{a:06X}' for a in reported_left],
            'metadata_4A_natural_ko_remaining':[f'{a:06X}' for a in same_family_left],
            'reported_family':[r for r in pr if int(r['abs'],16) in REPORTED],
            'metadata_4A_natural_ko_family':[r for r in pr if int(r['abs'],16) in SAME_METADATA_FAMILY]}
    if args.out: Path(args.out).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2)); return 0 if ok else 1
if __name__=='__main__': raise SystemExit(main())
