#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,Tbl,token_from_dict_index
from apply_ext_dict_unit import make_dictionary_ext3
ROM=ROOT/'out/patch/monoeye_ko_expanded.wsc'; TBL=ROOT/'out/patch/hangul_patch_pad3.tbl'
rom=bytes(load_rom(ROM)); tbl=Tbl.load(TBL)
ext_meta={'stock_count':3831,'slot_count':265,'ext_ptr_off':'0000','ext_seg':'10','ext_in_expansion':True}; ext3_meta={'num_banks':16,'exp_seg0':'11'}
d=make_dictionary_ext3(rom,ext_meta,ext3_meta)
for i in range(int(d.stock_count)):
    try:s=d.expand_index(i,tbl).rstrip('　 \t')
    except Exception:continue
    if any(k in s for k in ('죄송','미안','송구')):
        print(f'{i:04X}',token_from_dict_index(i).hex().upper(),repr(s))
