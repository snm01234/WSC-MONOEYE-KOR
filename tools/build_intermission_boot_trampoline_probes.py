#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import stock_base, update_ws_checksum

MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'
OUT=ROOT/'out/patch/intermission_layout_fullstatic_candidate/boot_trampoline_probes'
HOOK=0x789C4D
TARGET=0x7DFA40
OLD=0x78FCD3

def far(target:int)->bytes:
    bank=target>>16
    if not 0x74<=bank<=0x7f: raise ValueError(target)
    seg=(bank-0x70)<<12
    return b'\x9a'+(target&0xffff).to_bytes(2,'little')+seg.to_bytes(2,'little')

def emit(name:str, code:bytes)->None:
    parent=MAIN.read_bytes(); base=stock_base(parent); out=bytearray(parent)
    if any(x!=0xff for x in parent[base+TARGET:base+TARGET+len(code)]): raise RuntimeError('probe cave not ff')
    out[base+TARGET:base+TARGET+len(code)]=code
    out[base+HOOK:base+HOOK+5]=far(TARGET)
    update_ws_checksum(out)
    p=OUT/(name+'.wsc'); p.write_bytes(out); print(name,code.hex(),p)

def main()->int:
    OUT.mkdir(parents=True,exist_ok=True)
    emit('old_call_retf',far(OLD)+b'\xcb')
    emit('old_call_pushf_retf',far(OLD)+bytes.fromhex('9c 9d cb'))
    emit('old_call_save_restore_retf',far(OLD)+bytes.fromhex('9c 50 51 56 57 1e 06 07 1f 5f 5e 59 58 9d cb'))
    return 0
if __name__=='__main__': raise SystemExit(main())
