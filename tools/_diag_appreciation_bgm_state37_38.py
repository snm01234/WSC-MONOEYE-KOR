#!/usr/bin/env python3
from __future__ import annotations
import importlib.util, json, struct, zlib
from pathlib import Path
ROOT=Path(r'D:\monoeye')
STATE_DIR=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan')
spec=importlib.util.spec_from_file_location('v',ROOT/'out/patch/_analyze_beetle_status_vram.py')
v=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(v)

def nested(path:Path):
    raw=path.read_bytes(); blob=zlib.decompress(raw[24:]); memsz=struct.unpack_from('<I',blob,12)[0]; mem=blob[16:16+memsz]
    p=32; secs={}
    while p+36<=len(mem):
        name=mem[p:p+32]
        if not all(b==0 or 32<=b<127 for b in name): break
        n=name.split(b'\0')[0].decode('ascii'); sz=struct.unpack_from('<I',mem,p+32)[0]
        secs[n]=(p+36,sz); p+=36+sz
    out={}
    for sec,(lo,sz) in secs.items():
        q=lo; hi=lo+sz; d={}
        while q+5<=hi:
            sl=mem[q]
            if sl==0 or sl>48 or q+1+sl+4>hi: break
            nm=mem[q+1:q+1+sl]
            if not all(32<=b<127 for b in nm): break
            nsz=struct.unpack_from('<I',mem,q+1+sl)[0]; do=q+1+sl+4
            if do+nsz>hi: break
            d[nm.decode('ascii')]=mem[do:do+nsz]; q=do+nsz
        out[sec]=d
    return out

def u16(b,o): return struct.unpack_from('<H',b,o)[0]
def runs(a:bytes,b:bytes,lo=0,hi=None):
    if hi is None: hi=min(len(a),len(b))
    out=[]; s=None
    for i in range(lo,hi):
        if a[i]!=b[i] and s is None: s=i
        elif a[i]==b[i] and s is not None: out.append((s,i)); s=None
    if s is not None: out.append((s,hi))
    return out

def map_words(ram,base): return [u16(ram,base+2*(r*32+c)) for r in range(18) for c in range(28)]
def cpu(ns):
    d=ns.get('V30',{}); regs=d.get('regs',b''); sregs=d.get('sregs',b'');
    names=['AX','CX','DX','BX','SP','BP','SI','DI']; sn=['ES','CS','SS','DS']
    return {'IP': u16(d.get('IP',b'\0\0'),0) if len(d.get('IP',b''))>=2 else None,
            'regs':{n:u16(regs,i*2) for i,n in enumerate(names)} if len(regs)>=16 else {},
            'sregs':{n:u16(sregs,i*2) for i,n in enumerate(sn)} if len(sregs)>=8 else {},
            'PSW':u16(d.get('PSW',b'\0\0'),0) if len(d.get('PSW',b''))>=2 else None}
def gfx_summary(g):
    keys=['DispControl','FGBGLoc','VideoMode','BGXScroll','BGYScroll','FGXScroll','FGYScroll','SPRBase','SpriteStart','SpriteCount','LineCompare','LCDControl','BTimerControl']
    return {k:g[k].hex().upper() for k in keys if k in g}

def main():
    states=[]
    for slot in (37,38):
        p=STATE_DIR/f'monoeye_ko_expanded.state{slot}'
        ram,gfx=v.parse_beetle_ram(p); ns=nested(p); memr=ns['MEMR']
        loc=gfx['FGBGLoc'][0]; bg=(loc&7)<<11; fg=((loc>>4)&7)<<11
        states.append((slot,p,ram,gfx,ns,bg,fg))
    s37,p37,r37,g37,n37,bg37,fg37=states[0]; s38,p38,r38,g38,n38,bg38,fg38=states[1]
    rep={
      'state37':{'gfx':gfx_summary(g37),'bg_map':f'{bg37:04X}','fg_map':f'{fg37:04X}','banks':n37['MEMR'].get('BankSelector',b'').hex().upper(),'cpu':cpu(n37)},
      'state38':{'gfx':gfx_summary(g38),'bg_map':f'{bg38:04X}','fg_map':f'{fg38:04X}','banks':n38['MEMR'].get('BankSelector',b'').hex().upper(),'cpu':cpu(n38)},
    }
    rr=runs(r37,r38)
    rep['ram_diff']={'bytes':sum(b-a for a,b in rr),'run_count':len(rr),'first_runs':[{'range':f'{a:04X}-{b-1:04X}','len':b-a,'37':r37[a:b].hex().upper(),'38':r38[a:b].hex().upper()} for a,b in rr[:80]]}
    for name,base37,base38 in [('BG',bg37,bg38),('FG',fg37,fg38)]:
        w37=map_words(r37,base37); w38=map_words(r38,base38)
        dif=[i for i,(a,b) in enumerate(zip(w37,w38)) if a!=b]
        rep[name]={'changed_cells':len(dif),'first':[{'idx':i,'row':i//28,'col':i%28,'37':f'{w37[i]:04X}','38':f'{w38[i]:04X}'} for i in dif[:100]]}
    # Non-VRAM diffs are useful for menu state/cursors.
    non=[]
    for a,b in rr:
        for x,y in ((a,min(b,0x3000)),(max(a,0x8000),b)):
            if y>x: non.append((x,y))
    rep['non_vram_runs']=[{'range':f'{a:04X}-{b-1:04X}','len':b-a,'37':r37[a:b].hex().upper(),'38':r38[a:b].hex().upper()} for a,b in non[:120]]
    print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
