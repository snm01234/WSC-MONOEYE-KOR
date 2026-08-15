#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,update_ws_checksum,stock_base,Tbl
from apply_ext_dict_unit import make_dictionary_ext3,load_ext_meta

BAD=ROOT/'out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc'
LIVE=ROOT/'sram/monoeye_ko_expanded.sav'
OUT=ROOT/'out/patch'
REPORT=OUT/'ending_seam_invisible_marker_timing_probes_report.json'
BAD_SHA='1e2b9b23c8f8d82e50c0f11c142e5ee655e090d18178107960661fa94d52e31b'
SLOT=0x09C9; LOGICAL=0x5FE690; MARK=bytes.fromhex('EC8D'); EMPTY_TOKEN=bytes.fromhex('F0A9')
EXPECTED_RAW=bytes.fromhex('EC8DE8FEE918E7ECE75BE74D')

def sha(x): return hashlib.sha256(bytes(x)).hexdigest()

def build(mode:str):
    base=bytes(load_rom(BAD)); out=bytearray(base); off=stock_base(out)+LOGICAL
    # 14-byte historical storage capacity before the original NUL.
    if bytes(out[off:off+12])!=EXPECTED_RAW or out[off+12]!=0:
        raise RuntimeError('09C9 raw drift')
    if mode=='double_start': raw=MARK+EXPECTED_RAW
    elif mode=='extra_end': raw=EXPECTED_RAW+MARK
    elif mode=='empty_token_end': raw=EXPECTED_RAW+EMPTY_TOKEN
    else: raise RuntimeError(mode)
    if len(raw)!=14: raise RuntimeError('timing raw must be 14 bytes')
    out[off:off+14]=raw; out[off+14]=0
    update_ws_checksum(out)
    return out

def main():
    base=bytes(load_rom(BAD))
    if sha(base)!=BAD_SHA: raise RuntimeError(f'bad base sha drift {sha(base)}')
    em=load_ext_meta(ROOT/'out/patch/exp_dictionary_meta.json'); e3=load_ext_meta(ROOT/'out/patch/ext3_dictionary_meta.json')
    tbl=Tbl.load(ROOT/'out/patch/hangul_patch_pad3.tbl')
    db=make_dictionary_ext3(base,em,e3)
    before=db.expand_index(SLOT,tbl)
    rows=[]
    if db.raw_entry(0x00A9) != b'': raise RuntimeError('F0A9 is no longer an empty stock token')
    for mode,name in [('double_start','ending_seam_bad_plus_invisible_marker_start_probe'),('extra_end','ending_seam_bad_plus_invisible_marker_end_probe'),('empty_token_end','ending_seam_bad_plus_empty_dict_call_probe')]: 
        rom=build(mode); d=make_dictionary_ext3(rom,em,e3); after=d.expand_index(SLOT,tbl)
        if after!=before: raise RuntimeError(f'visible text changed {mode}: {before!r}->{after!r}')
        rp=OUT/(name+'.wsc'); sp=ROOT/'sram'/(name+'.sav'); rp.write_bytes(rom); shutil.copyfile(LIVE,sp)
        rows.append({'mode':mode,'rom':str(rp.relative_to(ROOT)).replace('\\','/'),'sha256':sha(rom),'checksum':f'{rom[-2]|rom[-1]<<8:04X}','saveram':str(sp.relative_to(ROOT)).replace('\\','/'),'saveram_sha256':sha(sp.read_bytes()),'slot09C9_visible_text_unchanged':True,'slot09C9_raw':d.raw_entry(SLOT).hex().upper(),'extra_invisible_marker_count':1 if mode!='empty_token_end' else 0,'extra_empty_dictionary_calls':1 if mode=='empty_token_end' else 0})
    rep={'schema_version':1,'generated_by':'tools/build_ending_seam_invisible_marker_timing_probes.py','ok':True,'known_bad':{'path':str(BAD.relative_to(ROOT)).replace('\\','/'),'sha256':BAD_SHA},'proof':{'record_payloads_unchanged':True,'visible_dictionary_text_unchanged':True,'only_nonchecksum_change':'the two spare bytes in private 09C9 are filled with either one no-glyph EC8D marker or the existing empty stock token F0A9; record payloads and visible text stay unchanged','interpretation':'If any probe becomes seam-clean, ROM overlap/content corruption is rejected and parser/VBlank timing sensitivity is strongly supported. The F0A9 probe adds one full dictionary recursion with zero visible glyphs.'},'probes':rows,'promotion':'blocked_pending_runtime_validation'}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
