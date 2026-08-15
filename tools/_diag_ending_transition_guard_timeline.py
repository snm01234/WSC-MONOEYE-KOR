from __future__ import annotations
import importlib.util, pathlib, struct, re, json, zlib
ROOT=pathlib.Path(r'D:\monoeye')
STATE_DIR=pathlib.Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan')
spec=importlib.util.spec_from_file_location('v',ROOT/'out/patch/_analyze_beetle_status_vram.py')
v=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(v)

def fields(path):
 raw=path.read_bytes(); blob=zlib.decompress(raw[24:]); memsz=struct.unpack_from('<I',blob,12)[0]; mem=blob[16:16+memsz]
 def top(buf,start=32):
  p=start; out=[]
  while p+36<=len(buf):
   nm=buf[p:p+32]
   if not all(b==0 or 32<=b<127 for b in nm): break
   name=nm.split(b'\0')[0].decode(); sz=struct.unpack_from('<I',buf,p+32)[0]; out.append((name,p+36,sz)); p+=36+sz
  return out
 def nested(buf,lo,hi):
  p=lo; d={}
  while p+5<=hi:
   sl=buf[p]
   if sl==0 or sl>40 or p+1+sl+4>hi: break
   nm=buf[p+1:p+1+sl]
   if not all(32<=b<127 for b in nm): break
   sz=struct.unpack_from('<I',buf,p+1+sl)[0]; do=p+1+sl+4
   if do+sz>hi: break
   d[nm.decode()]=buf[do:do+sz]; p=do+sz
  return d
 secs={n:(o,s) for n,o,s in top(mem)}; out={}
 for n in ('MEMR','V30','GFX'):
  if n in secs:
   o,s=secs[n]; out[n]=nested(mem,o,o+s)
 return out

def band(ram):
 out=[]
 for r,cs in ((9,range(4,28)),(10,range(28)),(11,range(28))):
  out += [struct.unpack_from('<H',ram,0x3000+2*(r*32+c))[0] for c in cs]
 return out
base='monoeye_ko_expanded_ending_credits_cinematic_transition_guard_test.state'
paths=[]
for p in STATE_DIR.glob(base+'*'):
 if p.suffix=='.png': continue
 m=re.fullmatch(re.escape(base)+r'(\d*)',p.name)
 if m: paths.append((int(m.group(1) or 0),p))
paths.sort()
prev=None
for slot,p in paths:
 ram,g=v.parse_beetle_ram(p); f=fields(p); b=band(ram); memr=f.get('MEMR',{}); gfx=f.get('GFX',{})
 changed=[] if prev is None else [i for i,(a,x) in enumerate(zip(prev,b)) if a!=x]
 print(json.dumps({
  'slot':slot,'scene':f'{ram[0x1a6c]:02X}','banks':memr.get('BankSelector',b'').hex().upper(),
  'wsLine': (gfx.get('wsLine',b'\0') or b'\0')[0], '0613':f'{ram[0x613]:02X}','0614':f'{ram[0x614]:02X}',
  'changed_from_prev':len(changed),'changed_idx_first':changed[:16],
  'head':[f'{x:04X}' for x in b[:10]],'tail':[f'{x:04X}' for x in b[-10:]]
 },ensure_ascii=False))
 prev=b
