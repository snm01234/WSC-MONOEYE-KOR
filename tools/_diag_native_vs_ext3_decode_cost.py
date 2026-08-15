#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import sys, json
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import (load_rom,stock_base,read_encoded_z_safe,is_dict_token,is_kanji_lead,is_ext3_magic,is_compact3_magic,dict_index_from_token,dict_index_from_ext3_token,dict_index_from_compact3_token,EXT3_INDEX_BASE)
from apply_ext_dict_unit import make_dictionary_ext3,load_ext_meta

FILES={
 'stage2_ext3': ROOT/'out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc',
 'bad_native': ROOT/'out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc',
 'clean_native_alt': ROOT/'out/patch/ending_seam_native_interact_c_five_no_retired.wsc',
}
ADDRS=[0x63E6E4,0x63EB4A,0x63F0BD,0x63F483,0x63F67C]
em=load_ext_meta(ROOT/'out/patch/exp_dictionary_meta.json'); e3=load_ext_meta(ROOT/'out/patch/ext3_dictionary_meta.json')

def count_stream(d,data,depth=0):
    c={'dict_calls':0,'marker_codes':0,'glyph_codes':0,'onebyte':0,'raw_bytes':len(data),'max_depth':depth}
    i=0
    while i<len(data):
        b=data[i]
        if b==0: break
        if is_dict_token(b) and i+1<len(data):
            idx=dict_index_from_token(b,data[i+1]); c['dict_calls']+=1
            sub=count_stream(d,d.raw_entry(idx),depth+1)
            for k in ('dict_calls','marker_codes','glyph_codes','onebyte'): c[k]+=sub[k]
            c['max_depth']=max(c['max_depth'],sub['max_depth']); i+=2; continue
        if is_kanji_lead(b) and i+1<len(data):
            if is_compact3_magic(b,data[i+1]) and i+2<len(data):
                idx=dict_index_from_compact3_token(b,data[i+1],data[i+2]); c['dict_calls']+=1; sub=count_stream(d,d.raw_entry(idx),depth+1)
                for k in ('dict_calls','marker_codes','glyph_codes','onebyte'): c[k]+=sub[k]
                c['max_depth']=max(c['max_depth'],sub['max_depth']); i+=3; continue
            if is_ext3_magic(b,data[i+1]) and i+3<len(data):
                idx=dict_index_from_ext3_token(b,data[i+1],data[i+2],data[i+3]); c['dict_calls']+=1; sub=count_stream(d,d.raw_entry(idx),depth+1)
                for k in ('dict_calls','marker_codes','glyph_codes','onebyte'): c[k]+=sub[k]
                c['max_depth']=max(c['max_depth'],sub['max_depth']); i+=4; continue
            code=(b<<8)|data[i+1]
            if code==0xEC8D: c['marker_codes']+=1
            else: c['glyph_codes']+=1
            i+=2; continue
        c['onebyte']+=1; i+=1
    return c

out={}
for tag,path in FILES.items():
    r=bytes(load_rom(path)); d=make_dictionary_ext3(r,em,e3); sb=stock_base(r); rows=[]; total={k:0 for k in ('dict_calls','marker_codes','glyph_codes','onebyte')}
    for a in ADDRS:
        p,_=read_encoded_z_safe(r,sb+a,max_len=32); body=bytes(p[3:]); c=count_stream(d,body)
        for k in total: total[k]+=c[k]
        rows.append({'address':f'{a:06X}','body':body.hex().upper(),**c})
    out[tag]={'rows':rows,'total':total}
print(json.dumps(out,indent=2))
