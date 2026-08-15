#!/usr/bin/env python3
from pathlib import Path
import hashlib, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from diag_ending_libretro_phase import Harness, retroarch_state_payload
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
STATEDIR=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan')
BASE=ROOT/'out/patch/appreciation_bgm_kana_chart_records_restore_probe.wsc'
CAND=ROOT/'out/patch/appreciation_bgm_kana_chart_render_hook_candidate_v2.wsc'
DOWN=5

def run(rom,state,down=False,frames=2):
    h=Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATEDIR)
    try:
        h.load_game(rom); h.unserialize(retroarch_state_payload(state))
        if down:
            h.set_pressed(DOWN); h.run(); h.set_pressed(); frames-=1
        for _ in range(max(0,frames)): h.run()
        r=h.ram()
        return r
    finally: h.close()

def sh(b): return hashlib.sha256(b).hexdigest()[:16]

def main():
    s37=STATEDIR/'monoeye_ko_expanded.state37'
    a=run(BASE,s37,True,7); b=run(CAND,s37,True,7)
    print('state37_down ram_equal',a==b,'bgfg',a[0x3000:0x4000]==b[0x3000:0x4000],'tiles',a[0x4000:0xC200]==b[0x4000:0xC200],'sha',sh(a),sh(b))
    for n in range(17,25):
        s=STATEDIR/f'monoeye_ko_expanded.state{n}'
        if not s.exists(): continue
        a=run(BASE,s,False,2); b=run(CAND,s,False,2)
        dif=[i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
        print('state',n,'idle diff',len(dif),'glyph',a[0x1A6E:0x1ACE]!=b[0x1A6E:0x1ACE],'tiles',a[0x4000:0xC200]!=b[0x4000:0xC200])
        for button in (4,5,6,7,8,0,1):
            def rb(rom):
                h=Harness(CORE,Path(r'C:\\RetroArch-Win64\\system'),STATEDIR)
                try:
                    h.load_game(rom); h.unserialize(retroarch_state_payload(s)); h.set_pressed(button); h.run(); h.set_pressed(); h.run(); h.run(); return h.ram()
                finally: h.close()
            aa=rb(BASE); bb=rb(CAND)
            if aa[0x1A6E:0x1ACE]!=bb[0x1A6E:0x1ACE] or aa[0x4000:0xC200]!=bb[0x4000:0xC200]:
                print('  trigger button',button,'glyph',aa[0x1A6E:0x1ACE]!=bb[0x1A6E:0x1ACE],'tiles',aa[0x4000:0xC200]!=bb[0x4000:0xC200])
if __name__=='__main__': main()
