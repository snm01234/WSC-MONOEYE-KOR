#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, sys, tempfile
from pathlib import Path
ROOT=Path(r'D:\monoeye'); sys.path.insert(0,str(ROOT/'tools'))
from diag_ending_libretro_phase import Harness, retroarch_state_payload
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
STDIR=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan'); STATE=STDIR/'monoeye_ko_expanded.state37'
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; STOCK=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'

def run_bytes(data:bytes)->dict:
    # temp file is required only as libretro content path; deleted immediately after the run.
    tmp=Path(tempfile.gettempdir())/('monoeye_bgm_oracle_'+hashlib.sha256(data).hexdigest()[:16]+'.wsc')
    tmp.write_bytes(data)
    h=Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STDIR)
    try:
        h.load_game(tmp); h.unserialize(retroarch_state_payload(STATE))
        h.set_pressed(5); h.run(); r1=h.ram()
        h.set_pressed(); h.run(); r2=h.ram(); h.run(); r3=h.ram()
        return {'idx1':r1[0x19F8],'idx2':r2[0x19F8],'idx3':r3[0x19F8],
                'bg_row17':r3[0x3000+17*64:0x3000+18*64].hex().upper(),
                'dyn_sha':hashlib.sha256(r3[0xBE00:0xC300]).hexdigest()[:16]}
    finally:
        h.close(); tmp.unlink(missing_ok=True)

def main():
    main=MAIN.read_bytes(); stock=STOCK.read_bytes(); sb=len(main)-len(stock)
    base=run_bytes(main); normal=run_bytes(stock)
    changed=[]
    for bank in range(0x80):
        ms=sb+bank*0x10000; ss=bank*0x10000
        if main[ms:ms+0x10000]!=stock[ss:ss+0x10000]: changed.append(bank)
    rows=[]
    for bank in changed:
        out=bytearray(main); ms=sb+bank*0x10000; ss=bank*0x10000; out[ms:ms+0x10000]=stock[ss:ss+0x10000]
        got=run_bytes(bytes(out)); status='BLOCKED_AT_4B' if got['idx1']==0x4B else 'BAD'
        rows.append({'logical_bank':f'{bank:02X}','diff_bytes':sum(a!=b for a,b in zip(main[ms:ms+0x10000],stock[ss:ss+0x10000])),'status':status,**got})
    print(json.dumps({'current':base,'stock':normal,'changed_banks':len(changed),'rows':rows},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
