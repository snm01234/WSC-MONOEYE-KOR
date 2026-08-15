#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from monoeye_rom import load_rom, Tbl, token_from_dict_index
from apply_ext_dict_unit import make_dictionary_ext3
GOOD=Path(r'D:\legacy_260814\out\patch\backup\20260809_224743_pre_runtime_measured_followup_structural\monoeye_ko_expanded.wsc')
TBL=ROOT/'out/patch/hangul_patch_pad3.tbl'
rom=bytes(load_rom(GOOD)); tbl=Tbl.load(TBL)
ext_meta={'stock_count':3831,'slot_count':265,'ext_ptr_off':'0000','ext_seg':'10','ext_in_expansion':True}; ext3_meta={'num_banks':16,'exp_seg0':'11'}
d=make_dictionary_ext3(rom,ext_meta,ext3_meta)
texts={}
for i in range(int(d.stock_count)):
    try:s=d.expand_index(i,tbl).rstrip('　 \t')
    except Exception:continue
    texts.setdefault(s,[]).append(i)

def decomps(target,max_tokens=4):
    memo={}
    def rec(pos,n):
        k=(pos,n)
        if k in memo:return memo[k]
        if pos==len(target):return [[]]
        if n==0:return []
        out=[]
        for end in range(pos+1,len(target)+1):
            s=target[pos:end]
            if s not in texts:continue
            for idx in texts[s][:8]:
                for tail in rec(end,n-1):
                    out.append([(s,idx)]+tail)
                    if len(out)>=100:return out
        memo[k]=out; return out
    allout=[]
    for n in range(1,max_tokens+1):
        for row in rec(0,n):
            if len(row)==n:allout.append(row)
    return allout

for target in ['최종오의','최종오의！！','최종오의이이','최종오의이이！！','죄송해요……','죄송합니다……']:
    rows=decomps(target,4)
    print('\nTARGET',repr(target),'solutions',len(rows))
    for row in rows[:50]:
        print(' ', ' + '.join(f"{s!r}[{i:04X}/{token_from_dict_index(i).hex().upper()}]" for s,i in row))

print('\nPARTIAL final/secret-technique entries')
for s,inds in sorted(texts.items(),key=lambda kv:(len(kv[0]),kv[0])):
    if s and any(k in s for k in ('최종','오의')) and len(s)<=24:
        print(repr(s),[f'{i:04X}' for i in inds[:12]])
