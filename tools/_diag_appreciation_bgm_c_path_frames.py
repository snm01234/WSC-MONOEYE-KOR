#!/usr/bin/env python3
from pathlib import Path
import sys,tempfile,os,hashlib,struct
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from diag_ending_libretro_phase import Harness,retroarch_state_payload
from monoeye_rom import stock_base
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state37')
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll'); MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; STOCK=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'; DOWN=5

def run(name,raw):
 fd,p=tempfile.mkstemp(suffix='.wsc');os.close(fd);p=Path(p);p.write_bytes(raw);h=Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
 try:
  h.load_game(p);h.unserialize(retroarch_state_payload(STATE)); rows=[]
  for f in range(9):
   if f==1:h.set_pressed(DOWN)
   elif f==2:h.set_pressed()
   if f:h.run()
   r=h.ram(); rows.append({'f':f,'19f8':r[0x19F8:0x19FA].hex(),'bgys':r[0x3458:0x34B4].hex(),'dyn':hashlib.sha256(r[0xBE80:0xC200]).hexdigest()[:12],'glyph':r[0x1A60:0x1AB0].hex(),'stack':r[0xF280:0xF3C0].hex(),'ram':hashlib.sha256(r).hexdigest()[:12]})
  return rows
 finally:h.close();p.unlink(missing_ok=True)

def main():
 m=bytearray(MAIN.read_bytes());s=stock_base(m); st=STOCK.read_bytes();ss=stock_base(st)
 variants={'main':bytes(m)}
 x=bytearray(m);x[s+0x75B9AB:s+0x75B9AF]=bytes.fromhex('E518B64B');variants['c_uses_b_ext3']=bytes(x)
 x=bytearray(m);x[s+0x75B9AB:s+0x75B9AB+11]=st[ss+0x75B9AB:ss+0x75B9AB+11];variants['c_stock']=bytes(x)
 out={k:run(k,v) for k,v in variants.items()}
 for f in range(9):
  print('FRAME',f)
  for k in variants:
   q=out[k][f]; print(k,'19f8',q['19f8'],'dyn',q['dyn'],'ram',q['ram'],'glyphsha',hashlib.sha256(bytes.fromhex(q['glyph'])).hexdigest()[:12])
  a=bytes.fromhex(out['main'][f]['glyph']);
  for k in ('c_uses_b_ext3','c_stock'):
   b=bytes.fromhex(out[k][f]['glyph']); print(' diff main',k,sum(x!=y for x,y in zip(a,b)))
if __name__=='__main__':main()
