#!/usr/bin/env python3
"""Low-overhead render-only Hangul hook for the dual-use 75:B889 kana chart.

Unlike v1, normal glyph stores do not far-call a helper. The existing 7A:07A0
near-call is redirected to the legacy 14-byte 7A:4722 cave. That guard checks
only whether the current source cursor is in the B8xx offset page. Non-B8xx
sources immediately near-jump to the unchanged current 7A:FFCA store cave.
Only B8xx sources far-jump to 7D:FA37, which then validates segment=3000,
physical bank=F5 and an exact post-character source cursor before substituting
the displayed glyph. Raw 75:B889..B8BF bytes remain stock/pre-kana exact.
"""
from __future__ import annotations

import hashlib, json, shutil, struct, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import stock_base, update_ws_checksum

MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'
GOOD=ROOT/'out/patch/backup/20260815_012352_pre_encyclopedia_kana_index/monoeye_ko_expanded.wsc'
SAVE=ROOT/'sram/monoeye_ko_expanded.sav'
CATALOG=ROOT/'data/encyclopedia_kana_index_ko.json'
MAP_REPORT=ROOT/'out/patch/appreciation_bgm_kana_chart_render_hook_report.json'
OUT=ROOT/'out/patch/appreciation_bgm_kana_chart_render_hook_candidate_v2.wsc'
OUT_SAVE=ROOT/'sram/appreciation_bgm_kana_chart_render_hook_candidate_v2.sav'
REPORT=ROOT/'out/patch/appreciation_bgm_kana_chart_render_hook_v2_report.json'
EXPECTED_MAIN='d925ee07b4ea844a1bd89deff52bc88ec21e04bf8ab5a0df8d9fecdd651bd8b1'
STORE_SITE=0x7A07A0
STORE_SITE_EXPECT=bytes.fromhex('E827F890')
STORE_CAVE=0x7AFFCA
STORE_CAVE_EXPECT=bytes.fromhex('803EFF1901751781FE2008720C81FE640D730681CE0080EB05C606FF190089B76E1AC3')
GUARD=0x7A4722
HELPER=0x7DFA37
TAG_FLAG=0x19FF
GLYPH_BUFFER=0x1A6E
HANGUL_BASE=0x0820
HANGUL_END=0x0D64

def sha(b): return hashlib.sha256(bytes(b)).hexdigest()

def r8(buf,at,target):
    d=target-(at+2)
    if not -128<=d<=127: raise RuntimeError(d)
    buf[at+1]=d&0xff

def build_helper(entries):
    c=bytearray(); fallback_p=[]
    c+=b'\x50\x51\x57'                         # preserve ax,cx,di
    c+=b'\x81\x7E\xFA\x00\x30'               # cmp word [bp-6],3000
    p=len(c);c+=b'\x75\x00';fallback_p.append(p)
    c+=b'\xE4\xC3\x3C\xF5'                   # in al,C3 ; cmp al,F5
    p=len(c);c+=b'\x75\x00';fallback_p.append(p)
    c+=b'\x8B\x46\xF8'                       # mov ax,[bp-8]
    mdi=len(c);c+=b'\xBF\x00\x00'             # mov di,table
    c+=b'\xB9'+struct.pack('<H',len(entries))
    loop=len(c)
    c+=b'\x2E\x3B\x05'                       # cmp ax,cs:[di]
    jz=len(c);c+=b'\x74\x00'
    c+=b'\x83\xC7\x04'
    lp=len(c);c+=b'\xE2\x00'
    jf=len(c);c+=b'\xEB\x00'
    found=len(c)
    c+=b'\x2E\x8B\x75\x02'                  # mov si,cs:[di+2]
    c+=b'\xC6\x06'+struct.pack('<H',TAG_FLAG)+b'\x00'
    js=len(c);c+=b'\xEB\x00'
    fallback=len(c)
    c+=b'\x80\x3E'+struct.pack('<H',TAG_FLAG)+b'\x01'
    jne=len(c);c+=b'\x75\x00'
    c+=b'\x81\xFE'+struct.pack('<H',HANGUL_BASE)
    jb=len(c);c+=b'\x72\x00'
    c+=b'\x81\xFE'+struct.pack('<H',HANGUL_END)
    jae=len(c);c+=b'\x73\x00'
    c+=b'\x81\xCE\x00\x80'
    jj=len(c);c+=b'\xEB\x00'
    clear=len(c)
    c+=b'\xC6\x06'+struct.pack('<H',TAG_FLAG)+b'\x00'
    store=len(c)
    c+=b'\x89\xB7'+struct.pack('<H',GLYPH_BUFFER)
    c+=b'\x5F\x59\x58'                       # restore di,cx,ax
    c+=b'\x83\xC4\x02'                       # discard 07A3 near-call return IP
    c+=bytes.fromhex('EA A3 07 00 A0')           # far jmp A000:07A3
    table=(HELPER&0xffff)+len(c)
    struct.pack_into('<H',c,mdi+1,table)
    for p in fallback_p:r8(c,p,fallback)
    r8(c,jz,found);r8(c,lp,loop);r8(c,jf,fallback);r8(c,js,store)
    r8(c,jne,store);r8(c,jb,clear);r8(c,jae,clear);r8(c,jj,store)
    for ptr,g in entries:c+=struct.pack('<HH',ptr,g)
    return bytes(c),table

def main():
    p=bytearray(MAIN.read_bytes()); good=GOOD.read_bytes(); live=SAVE.read_bytes()
    if sha(p)!=EXPECTED_MAIN: raise RuntimeError('main identity drifted')
    sb=stock_base(p);gsb=stock_base(good)
    cat=json.loads(CATALOG.read_text(encoding='utf-8'))
    # Restore only raw chart records.
    restored=[]
    for row in cat['records']:
        a=int(row['abs'],16);n=int(row['payload_len']); raw=good[gsb+a:gsb+a+n]
        restored.append({'abs':row['abs'],'hex':raw.hex().upper()})
        p[sb+a:sb+a+n]=raw
    # Reuse the already statically verified source-cursor -> tagged-glyph mapping.
    mr=json.loads(MAP_REPORT.read_text(encoding='utf-8'))
    entries=[]
    for rec in mr['records']:
        for s in rec['substitutions']:
            entries.append((int(s['after_ptr'],16),int(s['glyph'],16)|0x8000))
    if len(entries)!=41 or len({x for x,_ in entries})!=41: raise RuntimeError('mapping drift')
    if bytes(p[sb+STORE_SITE:sb+STORE_SITE+4])!=STORE_SITE_EXPECT: raise RuntimeError('store site drift')
    if bytes(p[sb+STORE_CAVE:sb+STORE_CAVE+len(STORE_CAVE_EXPECT)])!=STORE_CAVE_EXPECT: raise RuntimeError('store cave drift')
    if bytes(p[sb+GUARD:sb+GUARD+14])!=b'\x00'*14: raise RuntimeError('legacy guard cave occupied')
    # Existing near call now targets 7A:4722, preserving the same call/return shape.
    disp=(GUARD&0xffff)-((STORE_SITE&0xffff)+3)
    p[sb+STORE_SITE:sb+STORE_SITE+4]=b'\xE8'+struct.pack('<H',disp&0xffff)+b'\x90'
    # 14-byte guard: only B8xx source offsets take the far helper path.
    normal=GUARD+11
    guard=bytearray(bytes.fromhex('80 7E F9 B8 75 05 EA 37 FA 00 D0 E9 00 00'))
    nd=(STORE_CAVE&0xffff)-((normal&0xffff)+3)
    struct.pack_into('<H',guard,12,nd&0xffff)
    p[sb+GUARD:sb+GUARD+14]=guard
    helper,table=build_helper(entries)
    if any(x!=0xff for x in p[sb+HELPER:sb+HELPER+len(helper)]): raise RuntimeError('helper cave occupied')
    p[sb+HELPER:sb+HELPER+len(helper)]=helper
    update_ws_checksum(p)
    OUT.write_bytes(p);shutil.copyfile(SAVE,OUT_SAVE)
    changed=[i for i,(a,b) in enumerate(zip(MAIN.read_bytes(),p)) if a!=b]
    rep={'ok':True,'candidate':str(OUT.relative_to(ROOT)).replace('\\','/'),'sha256':sha(p),'checksum':f'{p[-2]|p[-1]<<8:04X}','strategy':'raw_chart_preserved_low_overhead_B8xx_render_guard','raw_chart_preserved':True,'records':restored,'guard':{'logical':'7A:4722','hex':guard.hex().upper()},'store_site':{'logical':'7A:07A0','hex':p[sb+STORE_SITE:sb+STORE_SITE+4].hex().upper()},'helper':{'logical':'7D:FA37','bytes':len(helper),'table':f'7D:{table:04X}','entries':len(entries)},'non_checksum_changed_bytes':sum(i<len(p)-2 for i in changed),'main_tip_unchanged':sha(MAIN.read_bytes())==EXPECTED_MAIN,'live_save_unchanged':SAVE.read_bytes()==live,'save_sha256':sha(OUT_SAVE.read_bytes()),'promotion':'blocked_pending_runtime'}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
