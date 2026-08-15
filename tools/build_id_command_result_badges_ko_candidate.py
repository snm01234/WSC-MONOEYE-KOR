#!/usr/bin/env python3
"""Build a test ROM for the screen-proven 40x16 回避!/追撃! result badges.

These two assets are *not* the previously localized 48x16/40+cap ID plaques and
not bank5F text.  The user capture identifies a separate full 5x2-tile graphic
body in stock bank4C.  Only the interior glyph zone is redrawn; the outer
silhouette, runtime code, all other plaque assets, and SaveRAM stay byte-exact.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from build_id_command_plaques_ko_candidate import decode_grid, encode_grid, make_masks  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/id_command_result_badges_ko.json"
OUT = ROOT / "out/patch/id_command_result_badges_ko_candidate.wsc"
OUT_SAVE = ROOT / "sram/id_command_result_badges_ko_candidate.sav"
REPORT = ROOT / "out/patch/id_command_result_badges_ko_candidate_report.json"
PREVIEW = ROOT / "out/patch/id_command_result_badges_ko_candidate_previews"

EXPECTED_MAIN_SHA256 = "984a0f2cfa1d932abc2ba2bdc2a7e76489c54ba0ef57804933fd9d60ad1170d5"
EXPECTED_SAVE_SHA256 = "c395a8dbe2ecbebd7e3e7f55b8e58adb01f143749f82b2f26fde005f9d73b259"
EXPECTED_STOCK_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
BLOCK = 0x140
TARGETS = {0x4C53B4, 0x4CC32A}
LIVE = {0x0:(0,0,0),0xB:(170,255,187),0xC:(68,255,68),0xD:(0,204,0),0xE:(0,68,0),0xF:(255,255,255)}

class BuildError(RuntimeError):
    pass

def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()

def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}

def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data); os.replace(tmp, path)

def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)

def diff_runs(a: bytes, b: bytes) -> list[tuple[int,int]]:
    out=[]; start=None
    for i,(x,y) in enumerate(zip(a,b)):
        if x!=y and start is None:start=i
        elif x==y and start is not None:out.append((start,i));start=None
    if start is not None:out.append((start,len(a)))
    return out

def render(pixels: list[list[int]], scale: int=8) -> Image.Image:
    image=Image.new("RGB",(40,16)); dst=image.load()
    for y,row in enumerate(pixels):
        for x,v in enumerate(row): dst[x,y]=LIVE.get(v,(v*17,v*17,v*17))
    return image.resize((40*scale,16*scale),Image.Resampling.NEAREST)

def localize(source: list[list[int]], text: str, font: ImageFont.FreeTypeFont, stroke: int, zone: tuple[int,int]):
    x0,x1=zone
    if not (0<=x0<x1<=40): raise BuildError("bad zone")
    pixels=[row[:] for row in source]
    # Preserve the complete top/bottom rows and both side silhouettes. Only the
    # label interior is cleared/repainted.
    for y in range(1,15):
        for x in range(x0,x1): pixels[y][x]=0xC
    outer,inner=make_masks(text,font,stroke)
    if outer.width>x1-x0 or outer.height>14:
        raise BuildError(f"text does not fit: {text} mask={outer.width}x{outer.height} zone={x1-x0}x14")
    dx=x0+((x1-x0)-outer.width)//2; dy=1+(14-outer.height)//2
    op,ip=outer.load(),inner.load()
    for y in range(outer.height):
        for x in range(outer.width):
            if op[x,y]: pixels[dy+y][dx+x]=0xF
            if ip[x,y]: pixels[dy+y][dx+x]=0xE
    changed=[(x,y) for y in range(16) for x in range(40) if pixels[y][x]!=source[y][x]]
    if not changed: raise BuildError("no visual change")
    if any(x<x0 or x>=x1 or y in {0,15} for x,y in changed): raise BuildError("border/silhouette changed")
    return pixels,{"glyph_mask":[outer.width,outer.height],"draw_origin":[dx,dy],"changed_pixels":len(changed),"changed_bbox":[min(x for x,y in changed),min(y for x,y in changed),max(x for x,y in changed)+1,max(y for x,y in changed)+1]}

def main() -> int:
    parent=MAIN.read_bytes(); save=SAVE.read_bytes(); stock=STOCK.read_bytes()
    if len(parent)!=ROM_SIZE or sha256(parent)!=EXPECTED_MAIN_SHA256: raise BuildError(f"main drift {sha256(parent)}")
    if len(save)!=SAVE_SIZE or sha256(save)!=EXPECTED_SAVE_SHA256: raise BuildError("SaveRAM drift")
    if sha256(stock)!=EXPECTED_STOCK_SHA256: raise BuildError("stock drift")
    base=stock_base(parent)
    if base!=0x800000: raise BuildError(f"unexpected stock base {base:#x}")
    spec=json.loads(SPEC.read_text(encoding="utf-8")); rows=list(spec.get("badges") or [])
    if {int(r["logical"],16) for r in rows}!=TARGETS or len(rows)!=2: raise BuildError("target set drift")
    font_path=ROOT/spec["font"]["path"]
    if not font_path.is_file(): raise BuildError("font missing")
    font=ImageFont.truetype(str(font_path),int(spec["font"]["size"])); stroke=int(spec["font"]["stroke_width"])
    candidate=bytearray(parent); allowed=[]; manifest=[]; previews=[]
    for row in rows:
        logical=int(row["logical"],16); physical=base+logical
        raw=parent[physical:physical+BLOCK]; stock_raw=stock[logical:logical+BLOCK]
        if raw!=stock_raw: raise BuildError(f"target source no longer stock-exact at {logical:06X}")
        source=decode_grid(raw,5,2)
        target,layout=localize(source,str(row["text"]),font,stroke,tuple(int(x) for x in row["zone"]))
        encoded=encode_grid(target,5,2)
        if encoded==raw: raise BuildError(f"encoded no-op at {logical:06X}")
        candidate[physical:physical+BLOCK]=encoded; allowed.append((physical,physical+BLOCK))
        manifest.append({"logical":f"{logical:06X}","physical":f"{physical:08X}-{physical+BLOCK-1:08X}","jp":row["jp"],"ko":row["ko"],"storage":"full_40x16","source_sha256":sha256(raw),"target_sha256":sha256(encoded),"outer_rows_preserved":target[0]==source[0] and target[15]==source[15],"left_side_preserved":all(target[y][:int(row['zone'][0])]==source[y][:int(row['zone'][0])] for y in range(16)),"right_side_preserved":all(target[y][int(row['zone'][1]):]==source[y][int(row['zone'][1]):] for y in range(16)),**layout})
        previews.append((row,source,target))
    checksum=update_ws_checksum(candidate); allowed.append((len(candidate)-2,len(candidate)))
    result=bytes(candidate); runs=diff_runs(parent,result)
    unexpected=[(a,b) for a,b in runs if not any(lo<=a and b<=hi for lo,hi in allowed)]
    if unexpected: raise BuildError(f"diff outside allowlist {unexpected}")
    if (sum(result[:-2])&0xFFFF)!=int.from_bytes(result[-2:],"little"): raise BuildError("checksum invalid")
    # Existing localized 24-plaque regions are outside the two new targets and
    # therefore byte-exact by the global allowlist check. Runtime banks are too.
    runtime7a=parent[0x7A0000:0x7B0000]==result[0x7A0000:0x7B0000]
    runtime7f=parent[0x7F0000:0x7FFFFE]==result[0x7F0000:0x7FFFFE]
    if not runtime7a or not runtime7f: raise BuildError("runtime bank changed")
    PREVIEW.mkdir(parents=True,exist_ok=True)
    for i,(row,before,after) in enumerate(previews,1):
        pair=Image.new("RGB",(640,128)); pair.paste(render(before),(0,0)); pair.paste(render(after),(320,0)); pair.save(PREVIEW/f"{i:02d}_{row['logical']}_before_after.png")
    sheet=Image.new("RGB",(640,256));
    for i,(row,before,after) in enumerate(previews): sheet.paste(render(before),(0,i*128)); sheet.paste(render(after),(320,i*128))
    sheet.save(PREVIEW/'all_2_before_after.png')
    atomic_bytes(OUT,result); shutil.copy2(SAVE,OUT_SAVE)
    if MAIN.read_bytes()!=parent or SAVE.read_bytes()!=save: raise BuildError("main or live SaveRAM changed on disk")
    report={"schema_version":1,"generated_by":"tools/build_id_command_result_badges_ko_candidate.py","ok":True,"status":"candidate_static_verified_pending_user_runtime_test","finding":"回避!/追撃! are separate full_40x16 bank4C graphics, not the bank5F dictionary path","supersedes":"id_command_dynamic_labels_followup_candidate for these screenshots","parent":identity(MAIN,parent),"stock":identity(STOCK,stock),"candidate":{**identity(OUT,result),"ws_checksum":f"{checksum:04X}"},"paired_saveram":identity(OUT_SAVE),"targets":manifest,"diff":{"runs":len(runs),"changed_bytes":sum(b-a for a,b in runs),"ranges":[[f"{a:08X}",f"{b:08X}"] for a,b in runs],"allowlist_clean":not unexpected},"guards":{"main_unchanged":MAIN.read_bytes()==parent,"live_saveram_unchanged":SAVE.read_bytes()==save,"runtime_bank_7a_exact":runtime7a,"runtime_bank_7f_exact_except_checksum":runtime7f,"target_sources_stock_exact":True,"outer_badge_silhouettes_preserved":all(m['outer_rows_preserved'] and m['left_side_preserved'] and m['right_side_preserved'] for m in manifest)},"preview":str((PREVIEW/'all_2_before_after.png').resolve()),"promotion":"blocked_pending_user_visual_verification"}
    atomic_json(REPORT,report); print(json.dumps(report,ensure_ascii=True,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
