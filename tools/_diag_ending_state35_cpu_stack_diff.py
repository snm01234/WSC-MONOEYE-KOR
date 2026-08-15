#!/usr/bin/env python3
from __future__ import annotations
import ctypes as C, importlib.util, json, struct, sys
from pathlib import Path
ROOT=Path(r'D:\monoeye'); sys.path.insert(0,str(ROOT/'tools'))
STATE=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state35')
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
spec=importlib.util.spec_from_file_location('h',ROOT/'tools/diag_ending_libretro_phase.py')
h=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(h)
ROMS={'stock':ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc','current':ROOT/'out/patch/monoeye_ko_expanded.wsc'}

def parse_nested(mem:bytes,secname:str):
 p=32
 while p+36<=len(mem):
  name=mem[p:p+32].split(b'\0')[0].decode('ascii'); sz=struct.unpack_from('<I',mem,p+32)[0]; off=p+36
  if name==secname:
   q=off; hi=off+sz; d={}
   while q+5<=hi:
    sl=mem[q]
    if sl==0 or sl>40 or q+1+sl+4>hi: break
    nm=mem[q+1:q+1+sl].decode('ascii'); ss=struct.unpack_from('<I',mem,q+1+sl)[0]; do=q+1+sl+4
    if do+ss>hi: break
    d[nm]=mem[do:do+ss]; q=do+ss
   return d
  p=off+sz
 return {}

def serialize(hh):
 n=int(hh.lib.retro_serialize_size()); buf=C.create_string_buffer(n)
 if not hh.lib.retro_serialize(buf,n): raise RuntimeError('serialize')
 return bytes(buf.raw[:n])

def state_fields(payload:bytes):
 # libretro serialize payload is MDFNSVST top-level directly
 mem=payload
 v=parse_nested(mem,'V30'); mr=parse_nested(mem,'MEMR'); gfx=parse_nested(mem,'GFX')
 ip=struct.unpack('<H',v['IP'])[0]; regs=list(struct.unpack('<8H',v['regs'])); sregs=list(struct.unpack('<4H',v['sregs']))
 names=['AX','CX','DX','BX','SP','BP','SI','DI']; rn=dict(zip(names,regs)); sn=dict(zip(['ES','CS','SS','DS'],sregs))
 return {'IP':f'{ip:04X}','regs':{k:f'{x:04X}' for k,x in rn.items()},'sregs':{k:f'{x:04X}' for k,x in sn.items()},'PSW':v.get('PSW',b'').hex().upper(),'banks':mr.get('BankSelector',b'').hex().upper(),'wsLine':int.from_bytes(gfx.get('wsLine',b'\0'),'little')}

def run(rom):
 payload=h.retroarch_state_payload(STATE); hh=h.Harness(CORE,Path(r'C:\RetroArch-Win64\system'),STATE.parent)
 try:
  hh.load_game(rom); hh.unserialize(payload); out=[]
  for f in range(7):
   ram=hh.ram(); sf=state_fields(serialize(hh)); words={f'{o:04X}':f'{struct.unpack_from("<H",ram,o)[0]:04X}' for o in range(0xF320,0xF392,2)}
   out.append({'frame':f,'cpu':sf,'stack':words})
   if f<6: hh.run()
  return out
 finally: hh.close()
A={k:run(v) for k,v in ROMS.items()}
for f in (4,5,6):
 s=A['stock'][f]; c=A['current'][f]; dif=[o for o in s['stack'] if s['stack'][o]!=c['stack'][o]]
 print(json.dumps({'frame':f,'stock_cpu':s['cpu'],'current_cpu':c['cpu'],'stack_diff':[{ 'off':o,'stock':s['stack'][o],'current':c['stack'][o]} for o in dif]},ensure_ascii=False,indent=2))
