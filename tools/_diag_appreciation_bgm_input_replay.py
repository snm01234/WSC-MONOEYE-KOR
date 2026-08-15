#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path
ROOT=Path(r'D:\monoeye')
sys.path.insert(0,str(ROOT/'tools'))
from diag_ending_libretro_phase import Harness, retroarch_state_payload
spec=importlib.util.spec_from_file_location('v',ROOT/'out/patch/_analyze_beetle_status_vram.py')
v=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(v)
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
STDIR=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan')
STATE37=STDIR/'monoeye_ko_expanded.state37'; STATE38=STDIR/'monoeye_ko_expanded.state38'
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; STOCK=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'
TARGET,_=v.parse_beetle_ram(STATE38)
# ranges dominated by menu/list state, BG map and dynamic tiles; avoid CPU stack timers.
RANGES=[(0x0840,0x0850),(0x1700,0x1840),(0x19F0,0x1B50),(0x2F80,0x3800),(0xBE00,0xC300),(0xFCB0,0xFDF0)]
def score(r:bytes):
    same=tot=0
    for a,b in RANGES:
        same+=sum(x==y for x,y in zip(r[a:b],TARGET[a:b])); tot+=b-a
    return same,tot

def snap(r:bytes,frame:int):
    s,t=score(r)
    return {'frame':frame,'score':s,'total':t,'ratio':round(s/t,6),'0842':r[0x842:0x844].hex().upper(),'19F8':r[0x19F8],'1B22':r[0x1B22],'1B34':r[0x1B34],'FCB0':r[0xFCB0],'bg3458_34B4':r[0x3458:0x34B4].hex().upper()}
def run(rom:Path,ident:int):
    h=Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STDIR)
    try:
        h.load_game(rom); h.unserialize(retroarch_state_payload(STATE37))
        out=[snap(h.ram(),0)]
        # one-frame button press, then release. also a second pressed frame because some games poll once/VBlank.
        h.set_pressed(ident); h.run(); out.append(snap(h.ram(),1))
        h.set_pressed();
        for f in range(2,21): h.run(); out.append(snap(h.ram(),f))
        best=max(out,key=lambda x:x['score'])
        return {'best':best,'frames':out}
    finally: h.close()
def main():
    rows={}
    for ident in (4,5,6,7):
        rows[f'main_id{ident}']=run(MAIN,ident)
    # only likely DOWN id5 on stock for causal comparison
    rows['stock_id5']=run(STOCK,5)
    print(json.dumps(rows,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
