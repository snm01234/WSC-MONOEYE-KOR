#!/usr/bin/env python3
"""Locate separately composed 40x16 ID-command result badges from screen captures.

Capture-derived 4bpp bodies are quantized to the measured live OBJ palette.
JPEG/scaler noise can alter a handful of pixels, so every byte phase in stock
bank 4C is ranked by nibble Hamming distance. Analysis only; no ROM writes.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STOCK=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'; TIP=ROOT/'out/patch/monoeye_ko_expanded.wsc'; OUT=ROOT/'out/patch/id_command_result_badge_analysis.json'; BLOCK=0x140
CAPTURE={
'evade_bang':bytes.fromhex('ECEECBFFBCCFFBCCCCFBCCCECFBCCCCECFCCCCCEBBCCCCCEFBCCCCCEFBCCCCCEFFFFFFFFCCCCCCCCEEEEEEEEFFFFFFFFFCEEEEEEFEEEEEEEFEEFFFFFFEEFEEEFFFFFFFFFCCCCCCCCEEEECCCEFFFECCCEECFECCCEEEFECCCCEEFECCCCEEFECCEEFFFFFFFFCCCCCCCCEEEEEEEEFCEFFFFEEFEFEEFEEEEFEEFECCEFFFFEEEEFEEECFFFFFFFFEEECCCCCECECCCCEEFEEECCEFFFCECCEFEFEECCEFEFEECCEFFFCECCEFBCCCCCEFBCCCCCEBBCCCCCECFCCCCCE0FBCCCCE0CFBCCCE00CFFBCE0000CBFEFEEFECEFFEEFEEEFFEEFFFFFFEECEEECFDEEEEEEFFFFFFFFCEEEEEEEEEDFFFFFEEFECCEFEEFECCEEEEFECCCCEEFECCCCEDFECCCEFFFECCCEEECECCCEDEEEFFFFFFEFFFFEEFECFEFEEFFEFEFCEFEEFEFEEFEEFFFEFFFCDEEEEEEFFFFFFDEEEEEEEFEEECCEEFEEECCEFFFCECCDEFEEECCCECECCCCDEEEEECCEFFFCECCEEEEEEFFD'),
'pursuit_bang':bytes.fromhex('ECEECBFFBCCFFBCCCCFBCCCECFBCCCCECFCCCCCEBBCCCCCCFBCCCCCCFBCCCCCEFFFFFFFFCCCCCCEEEECCCEFEFEECEFEEEFEEFFFFEFEEFEEEEEEEFEEEFFEEFFFFFFFFFFFFCCCCCCCCCCCCCEEEEECCCECFFCECCEECEFECCCEFEFECCCEFFFECCCEEFFFFFFFFFFFFFFFFEEEFFEEFEEEEEEEFFEEFFEEFEEEEEEEFEEFEEEEFEFFFFEEFFFFFFFFFEECCCCCCFECCCCEEFEEECCECCFCECCEFEEEECCEFFFECCCEFCEECCCEFFBCCCCCEFBCCCCCCBBCCCCCCCFCCCCCCEFBCCCCCECFBCCCEEECFFBCEEEECCBFEEFEEFEEEEFEEFFFFEFEEFEEEEFEEFEEEEFEEFFFFFEFEEEEECEEFFFFFEEEEEEEEEEECCECFFFECCEEEEFECCCCEEFECCCCEFFECCCEEEEEEECECFFCDECEEEEEEEFFFFFFFFFFFEEEEEEEFEEEEEEEFFFFFFEEFCCCCFEEFCCCCFFFFCCCCCCCCFFFFFFFFFFECCCEFEEECCCECEECCCCDECECCCCCDEEEECCDEFFCECCEFEEEECCECFFFFFFDE')}
def h(b):return hashlib.sha256(b).hexdigest()
def nd(a,b):return sum(((x>>4)!=(y>>4))+((x&15)!=(y&15)) for x,y in zip(a,b))
def best(bank,t,n=16):
 r=[]
 for p in range(len(bank)-len(t)+1):
  d=nd(bank[p:p+len(t)],t)
  if len(r)<n:r.append((d,p));r.sort()
  elif d<r[-1][0]:r[-1]=(d,p);r.sort()
 return r
def main():
 s=STOCK.read_bytes(); tip=TIP.read_bytes(); base=len(tip)-len(s); bank=s[0x4C0000:0x4D0000]
 rep={'schema_version':1,'generated_by':'tools/analyze_id_command_result_badges.py','stock_sha256':h(s),'tip_sha256':h(tip),'captures':{}}
 for name,t in CAPTURE.items():
  rows=[]
  for d,p in best(bank,t):
   logical=0x4C0000+p; raw=s[logical:logical+BLOCK]; cur=tip[base+logical:base+logical+BLOCK]
   rows.append({'distance_nibbles':d,'logical':f'{logical:06X}','tip_equals_stock':cur==raw,'stock_block_sha256':h(raw)})
  rep['captures'][name]=rows
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
