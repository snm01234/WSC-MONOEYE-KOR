from __future__ import annotations
import hashlib, importlib.util, json, re, struct, zlib
from pathlib import Path
from PIL import Image, ImageChops, ImageStat
ROOT=Path(r'D:\monoeye')
STATE_DIR=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan')
spec=importlib.util.spec_from_file_location('v',ROOT/'out/patch/_analyze_beetle_status_vram.py')
v=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(v)

def nested_from_raw(path:Path):
 raw=path.read_bytes(); blob=zlib.decompress(raw[24:]); memsz=struct.unpack_from('<I',blob,12)[0]; mem=blob[16:16+memsz]
 def top(buf,start=32):
  p=start; out=[]
  while p+36<=len(buf):
   name=buf[p:p+32]
   if not all(b==0 or 32<=b<127 for b in name): break
   n=name.split(b'\0')[0].decode('ascii'); sz=struct.unpack_from('<I',buf,p+32)[0]; out.append((n,p+36,sz)); p+=36+sz
  return out
 def nested(buf,lo,hi):
  p=lo; d={}
  while p+5<=hi:
   sl=buf[p]
   if sl==0 or sl>40 or p+1+sl+4>hi: break
   name=buf[p+1:p+1+sl]
   if not all(32<=b<127 for b in name): break
   sz=struct.unpack_from('<I',buf,p+1+sl)[0]; do=p+1+sl+4
   if do+sz>hi: break
   d[name.decode('ascii')]=buf[do:do+sz]; p=do+sz
  return d
 secs={n:(o,s) for n,o,s in top(mem)}; out={}
 for sec in ('MEMR','V30','GFX'):
  if sec in secs:
   o,s=secs[sec]; out[sec]=nested(mem,o,o+s)
 return out

def band(ram):
 vals=[]
 for r,cols in ((9,range(4,28)),(10,range(28)),(11,range(28))):
  vals += [struct.unpack_from('<H',ram,0x3000+2*(r*32+c))[0] for c in cols]
 return vals

def img_metric(p:Path,ref:Image.Image):
 if not p.exists(): return None
 im=Image.open(p).convert('RGB').resize(ref.size)
 st=ImageStat.Stat(ImageChops.difference(im,ref)); return round(sum(st.mean)/3,3)
refpng=STATE_DIR/'monoeye_ko_expanded.state31.png'; ref=Image.open(refpng).convert('RGB')
refstate=STATE_DIR/'monoeye_ko_expanded.state31'; refram,_=v.parse_beetle_ram(refstate); refband=band(refram)
rows=[]
for p in STATE_DIR.glob('monoeye_ko_expanded.state*'):
 if p.suffix.lower()=='.png': continue
 m=re.fullmatch(r'monoeye_ko_expanded\.state(\d*)',p.name)
 if not m: continue
 slot=int(m.group(1) or 0)
 try:
  ram,gfx=v.parse_beetle_ram(p); ns=nested_from_raw(p); b=band(ram); banks=ns.get('MEMR',{}).get('BankSelector',b'')
  direct=sum(x==y for x,y in zip(b,refband)); shifted=sum(b[i]==refband[i+1] for i in range(79))
  rows.append(dict(slot=slot,path=str(p),mtime=p.stat().st_mtime,scene=f'{ram[0x1a6c]:02X}',banks=banks.hex().upper(),band_sha=hashlib.sha256(struct.pack('<80H',*b)).hexdigest()[:16],band_eq_state31=direct,band_self_next_eq_state31=shifted,img_mae_to_state31=img_metric(Path(str(p)+'.png'),ref),r9c4_7=' '.join(f'{struct.unpack_from("<H",ram,0x3000+2*(9*32+c))[0]:04X}' for c in range(4,8)),r10c0_4=' '.join(f'{struct.unpack_from("<H",ram,0x3000+2*(10*32+c))[0]:04X}' for c in range(5)),ws0613=f'{ram[0x613]:02X}',ws0614=f'{ram[0x614]:02X}'))
 except Exception as e: rows.append(dict(slot=slot,path=str(p),error=str(e)))
rows.sort(key=lambda x:x['slot'])
print(json.dumps(rows,ensure_ascii=False,indent=2))
