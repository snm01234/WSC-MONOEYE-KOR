#!/usr/bin/env python3
"""Stem/space-repeat14 POC for Galmuri11Bitmap Condensed dialogue glyphs.

The prior adaptive stem-repeat strategy could still duplicate a long horizontal
bar as a fallback when three well-spaced stem rows were unavailable. That makes
syllables such as 브/드/령 look locally bolder than neighbors.

This variant makes long horizontal rows ineligible for duplication. It prefers
short vertical-stem cross sections; if a third height-insertion row is needed it
uses an interior blank row instead. Thus height can grow to 14 rows without
thickening ㅡ/ㅂ/ㄹ horizontal bars. Every inserted row is still an exact source
row, so no new x-position pixels can be synthesized.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import build_dialogue_galmuri11bitmap_condensed_8x16_exactlut_poc as base

OUT=ROOT/'out/patch/dialogue_galmuri11bitmap_condensed_stemspace14_exactlut_poc_candidate.wsc'
OUT_SAVE=ROOT/'sram/dialogue_galmuri11bitmap_condensed_stemspace14_exactlut_poc_candidate.sav'
REPORT=ROOT/'out/patch/dialogue_galmuri11bitmap_condensed_stemspace14_exactlut_poc_report.json'
PREVIEW=ROOT/'out/patch/dialogue_galmuri11bitmap_condensed_stemspace14_exactlut_poc_preview'


def native_metric(ch,font):
    im=Image.new('L',(8,16),0); d=ImageDraw.Draw(im); bb=d.textbbox((0,0),ch,font=font)
    w,h=bb[2]-bb[0],bb[3]-bb[1]
    if w>8: raise base.BuildError(f'{ch!r} exceeds 8px width: {w}')
    x=(8-w)//2-bb[0]; y=(16-h)//2-bb[1]
    d.text((x,y),ch,font=font,fill=255)
    return [[1 if im.getpixel((x,y))>=128 else 0 for x in range(8)] for y in range(16)]


def maxrun(row):
    best=cur=0
    for v in row:
        if v:
            cur+=1; best=max(best,cur)
        else:
            cur=0
    return best


def hpairs(row):
    return sum(1 for a,b in zip(row,row[1:]) if a and b)


def overlap(a,b):
    return sum(1 for x,y in zip(a,b) if x and y)


def row_cost(src,i):
    row=src[i]; ink=sum(row)
    if ink==0:
        # Interior whitespace is a safe place to gain vertical height: it does
        # not alter any stroke weight. Trailing/leading whitespace is allowed
        # only as a late fallback so vertical centering stays stable.
        before=any(any(r) for r in src[:i])
        after=any(any(r) for r in src[i+1:])
        return ((1.0 if before and after else 18.0)+0.1*abs(i-5), i)

    prev=src[i-1] if i>0 else [0]*8
    nxt=src[i+1] if i+1<len(src) else [0]*8
    vert=max(overlap(row,prev),overlap(row,nxt))
    hp=hpairs(row); mr=maxrun(row)

    # Never prefer a long horizontal bar (ㅡ, ㅂ/ㄹ/ㄷ bar etc.). Such rows are
    # the exact source of the locally-bold 브/드/령 artifacts.
    if hp>=2 or mr>=3:
        return (100.0 + 10.0*hp + 3.0*mr + ink, i)

    # Short stem cross sections get the best scores. Mild center preference
    # avoids pushing the whole glyph vertically when several rows are equal.
    cost=2.0*hp + 1.5*mr + 0.3*ink - 3.0*vert + 0.1*abs(i-5)
    return (cost,i)


def choose_three(src):
    ranked=sorted(row_cost(src,i) for i in range(len(src)))
    picks=[]
    for _,i in ranked:
        if all(abs(i-j)>=2 for j in picks):
            picks.append(i)
            if len(picks)==3:
                break
    if len(picks)<3:
        # Keep horizontal-bar prohibition even in fallback: blank rows are safer
        # than making a bar two pixels thick.
        for _,i in ranked:
            row=src[i]
            if i in picks:
                continue
            if any(row) and (hpairs(row)>=2 or maxrun(row)>=3):
                continue
            picks.append(i)
            if len(picks)==3:
                break
    if len(picks)<3:
        raise base.BuildError('cannot choose three safe stem/space rows')
    return sorted(picks)


def render_stemspace14(ch,font,target_h=14):
    metric=native_metric(ch,font)
    src=metric[2:13]
    picks=set(choose_three(src))
    out=[]
    for i,row in enumerate(src):
        out.append(row[:])
        if i in picks:
            out.append(row[:])
    if len(out)!=14:
        raise base.BuildError(f'stemspace14 height drift: {len(out)}')
    return [[0]*8]+out+[[0]*8]


def main():
    base.OUT=OUT; base.OUT_SAVE=OUT_SAVE; base.REPORT=REPORT; base.PREVIEW=PREVIEW
    base.render_condensed=render_stemspace14
    rc=base.main()
    rep=json.loads(REPORT.read_text(encoding='utf-8'))
    rep['weight_strategy']={
        'name':'adaptive_stem_or_space_repeat14',
        'source_metric_window':'fixed rows 2..12 (11 rows)',
        'output_content_height':14,
        'insert_count':3,
        'insert_rule':'duplicate short vertical-stem rows; use interior blank rows when needed; never duplicate long horizontal bars',
        'new_pixel_policy':'forbidden: every output row is an exact original Condensed row',
        'target_artifacts':['브 horizontal bar','드 horizontal bar','령 thick local strokes'],
        'purpose':'remove residual locally-bold syllables without reintroducing hooks or palette drift',
    }
    rep['candidate']['path']=str(OUT.relative_to(ROOT)).replace('\\','/')
    rep['save']['path']=str(OUT_SAVE.relative_to(ROOT)).replace('\\','/')
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return rc

if __name__=='__main__': raise SystemExit(main())
