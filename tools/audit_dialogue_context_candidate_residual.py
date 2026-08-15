#!/usr/bin/env python3
"""Audit residual high-confidence translation anomalies in reviewed neighborhoods.

Uses the candidate runtime render as truth.  All batch targets are excluded from
residual review because they are verified separately by the literal-candidate audit.
"""
from __future__ import annotations
import glob, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

SRC=ROOT/'out/script/dialogue_context_neighborhood_worklist.json'
CAND=ROOT/'out/patch/dialogue_legacy_mt_literal_candidate.wsc'
TBL=ROOT/'out/patch/hangul_patch_pad3.tbl'
EXT_META=ROOT/'out/patch/exp_dictionary_meta.json'
EXT3_META=ROOT/'out/patch/ext3_dictionary_meta.json'
OUT=ROOT/'out/script/dialogue_context_candidate_residual.json'
BAD_TERMS=("우역","커틀릿","오마에","키사마","아이츠","코이츠","치쿠쇼","독수리의","악마 건담","밝은 중령","밝은 함장","제리도","르스","양해입니다","양해했다","비단입니다","비단이라고","미리샤","캡틴","모빌스－츠","인신 공공","인신어공","옛썰","라져")
REPEAT_RE=re.compile(r"([가-힣]{2,6})\1")

def norm(s): return str(s or '').replace(' ','　').strip('　 \t')

def main():
    ledger=json.loads(SRC.read_text(encoding='utf-8'))
    targets={}
    jp2desired=defaultdict(Counter)
    jp_by_abs={r['abs']:r['jp'] for r in ledger['records']}
    for p in sorted(glob.glob(str(ROOT/'data/dialogue_legacy_mt_literal_batch*.json'))):
        d=json.loads(Path(p).read_text(encoding='utf-8'))
        for a,v in (d.get('targets') or {}).items():
            a=a.upper(); targets[a]=norm(v)
            jp=jp_by_abs.get(a)
            if jp: jp2desired[jp][norm(v)]+=1
    rom=CAND.read_bytes(); tbl=Tbl.load(TBL); dic=make_dictionary_ext3(rom,load_ext_meta(EXT_META),load_ext_meta(EXT3_META)); sb=stock_base(rom)
    out=[]; counts=Counter()
    for r in ledger['records']:
        a=r['abs'].upper()
        if a in targets: continue
        got=read_encoded_z_safe(rom,sb+int(a,16),max_len=256)
        if got is None: current=''; kind='unreadable'
        else:
            _p,b,kind=split_prefix_body(bytes(got[0]))
            try: current=dic.expand(b,tbl).rstrip('　 \t')
            except Exception: current=''
        jp=r['jp']; reasons=[]
        variants=jp2desired.get(jp)
        if variants:
            best,n=variants.most_common(1)[0]
            if norm(current)!=best: reasons.append('same_jp_as_corrected_target_differs')
        if any(x in current for x in BAD_TERMS): reasons.append('known_mt_lexical_residue')
        jp_len=max(1,len(jp.replace('　','').replace(' ',''))); ko_len=len(current.replace('　','').replace(' ','')); ratio=ko_len/jp_len
        if jp_len>=5 and ratio>=1.65: reasons.append('strong_expansion')
        elif jp_len>=8 and ratio<=0.38: reasons.append('strong_undertranslation')
        if REPEAT_RE.search(current) and not any(x in jp for x in ('ふふ','はは','くく','ぉぉ','ぁぁ')): reasons.append('repeated_korean_chunk')
        if reasons:
            for x in reasons: counts[x]+=1
            out.append({'cluster':r['cluster'],'abs':a,'jp':jp,'candidate_render':current,'kind':kind,'reasons':reasons,'forensic_route':r.get('forensic_route','')})
    report={'schema_version':1,'candidate':str(CAND.relative_to(ROOT)).replace('\\','/'),'batch_targets':len(targets),'neighborhood_records':len(ledger['records']),'residual_flagged':len(out),'reason_counts':dict(counts),'rows':out}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('batch_targets','neighborhood_records','residual_flagged','reason_counts')},ensure_ascii=False,indent=2)); print(OUT); return 0
if __name__=='__main__': raise SystemExit(main())
