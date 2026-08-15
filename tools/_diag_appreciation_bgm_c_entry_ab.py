#!/usr/bin/env python3
from pathlib import Path
import sys, hashlib, tempfile, os
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from diag_ending_libretro_phase import Harness,retroarch_state_payload
from monoeye_rom import stock_base
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state37')
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; STOCK=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'
DOWN=5

def run_bytes(name, raw):
    fd,tmpname=tempfile.mkstemp(suffix='.wsc'); os.close(fd); tmp=Path(tmpname); tmp.write_bytes(raw)
    h=Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
    try:
        h.load_game(tmp);h.unserialize(retroarch_state_payload(STATE)); h.set_pressed(DOWN);h.run();h.set_pressed()
        for _ in range(5): h.run()
        r=h.ram();
        return {'name':name,'19F8':r[0x19F8:0x19FA].hex(),'bgys':r[0x3458:0x34B4].hex(),'dyn':hashlib.sha256(r[0xBE80:0xC200]).hexdigest()[:16],'wram':hashlib.sha256(r).hexdigest()[:16]}
    finally:
        h.close(); tmp.unlink(missing_ok=True)

def main():
    m=bytearray(MAIN.read_bytes()); s=stock_base(m); st=STOCK.read_bytes(); ss=stock_base(st)
    cases=[]
    cases.append(('main',bytes(m)))
    x=bytearray(m); x[s+0x75B9AB:s+0x75B9AF]=bytes.fromhex('E518B64B'); cases.append(('c_record_uses_b_token',bytes(x)))
    x=bytearray(m); x[s+0x75B9AB:s+0x75B9AB+11]=st[ss+0x75B9AB:ss+0x75B9AB+11]; cases.append(('c_record_stock_body',bytes(x)))
    # ext3 C64C phrase: replace its payload bytes with exact C64B phrase if same/fits, preserving pointer table.
    # Resolve ext3 index C64B/C64C in expansion bank 1C: local slot 64B/64C.
    def slot_payload(buf, idx):
        seg=0x11+((idx-0x1000)>>12); local=(idx-0x1000)&0xFFF; base=seg*0x10000
        off=int.from_bytes(buf[base+local*2:base+local*2+2],'little'); end=buf.index(0,base+off); return base+off,end+1
    a0,a1=slot_payload(m,0xC64B); c0,c1=slot_payload(m,0xC64C)
    print('payload lens B,C',a1-a0,c1-c0,'B',m[a0:a1].hex(),'C',m[c0:c1].hex())
    if a1-a0 <= c1-c0:
        x=bytearray(m); pay=bytes(m[a0:a1]); x[c0:c1]=pay+b'\x00'*(c1-c0-len(pay)); cases.append(('c64c_payload_equals_b',bytes(x)))
    for n,b in cases: print(run_bytes(n,b))
if __name__=='__main__': main()
