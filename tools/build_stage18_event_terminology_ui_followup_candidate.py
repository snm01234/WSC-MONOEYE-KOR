#!/usr/bin/env python3
"""Build cumulative STAGE18 event/terminology/UI follow-up candidate.

Parent: phil_communication_page_boundary_followup_candidate.wsc
Changes are deliberately narrow:
- Phil line grammar: 달의 -> 달을 in the existing private ext3 phrase.
- Four/Dianna event 6019B7: restore pristine-style [dict,dict,dict,char1]
  native grammar while keeping Korean text, preventing post-line control leakage.
- Hangelg Evin / Gim Ginganam terminology cleanup in ordinary/ext3 + five-page aliases.
- Restore ambiguous raw MA icon tile identity E736 on the three entries flattened
  to E6C5 by the previous terminology re-encode.
- Translate 75:B401 射全 -> 사전 through the already-proven short-UI compact-glyph
  path: one new unused compact glyph for 사 + existing E51B=전, stored in a
  runtime-unreachable stock dictionary slot.
"""
from __future__ import annotations

import hashlib, json, os, shutil, struct, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta
from build_dialogue_20cell_candidate import encode
from build_remaining_dialogue_candidate import covered, diff_runs
from build_scenario_page_boundary_guard_candidate import safe_unreachable_slots
from build_stage17t_global_20cell_followup_candidate import active_dictionary
from build_terrain_space_abaoaqu_compact_glyph_candidate import (
    compact_glyph_offset, hangul_glyph_offset, read_glyph, select_steal_codes,
)
from monoeye_rom import (
    BANK_SIZE, Dictionary, Tbl, read_encoded_z_safe, stock_base,
    token_from_dict_index, update_ws_checksum,
)

PATCH = ROOT / "out/patch"
PARENT = PATCH / "phil_communication_page_boundary_followup_candidate.wsc"
PARENT_TBL = PATCH / "phil_communication_page_boundary_followup_candidate.tbl"
PARENT_SAVE = ROOT / "sram/phil_communication_page_boundary_followup_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/stage18_event_terminology_ui_followup_ko.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT = PATCH / "stage18_event_terminology_ui_followup_candidate.wsc"
OUT_TBL = PATCH / "stage18_event_terminology_ui_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/stage18_event_terminology_ui_followup_candidate.sav"
REPORT = PATCH / "stage18_event_terminology_ui_followup_candidate_report.json"
EXPECTED_PARENT = "62ec37d288cbd85e06dad96af2fd6f4d40db23dbf3f5484007679c61f54eadd4"
EXPECTED_TBL = "9d31d1fd1a5022b54f83866d285f4d656461b30d4046ef2b6261a9441abca914"
EXPECTED_ORIGINAL = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

class BuildError(RuntimeError): pass

def sha(b: bytes | bytearray) -> str: return hashlib.sha256(bytes(b)).hexdigest()
def trim(s: str) -> str: return s.rstrip("\u3000 \t")
def rel(p: Path) -> str: return str(p.resolve().relative_to(ROOT.resolve())).replace("\\", "/")

def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); tmp=path.with_name(f".{path.name}.{os.getpid()}.tmp"); tmp.write_bytes(data); os.replace(tmp,path)
def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); tmp=path.with_name(f".{path.name}.{os.getpid()}.tmp"); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n"); os.replace(tmp,path)

def record_at(rom: bytes|bytearray, logical: int) -> tuple[bytes,int]:
    sb=stock_base(rom); got=read_encoded_z_safe(rom,sb+logical,max_len=512)
    if got is None: raise BuildError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1])-sb

def main() -> int:
    parent=PARENT.read_bytes(); tbl_bytes=PARENT_TBL.read_bytes(); save=PARENT_SAVE.read_bytes(); original=ORIGINAL.read_bytes()
    if len(parent)!=ROM_SIZE or sha(parent)!=EXPECTED_PARENT: raise BuildError(f"parent drift {sha(parent)}")
    if sha(tbl_bytes)!=EXPECTED_TBL: raise BuildError("TBL drift")
    if len(save)!=SAVE_SIZE: raise BuildError("SaveRAM drift")
    if sha(original)!=EXPECTED_ORIGINAL: raise BuildError("Original drift")
    spec=json.loads(SPEC.read_text(encoding="utf-8")); tbl=Tbl.load(PARENT_TBL)
    ext=load_ext_meta(EXT_META); ext3=load_ext_meta(EXT3_META); pd=active_dictionary(parent,ext,ext3)
    sb=stock_base(parent); cand=bytearray(parent); allowed=[]; rows=[]

    # 1. Existing ext3 phrase wording correction (same byte length).
    for r in spec["dialogue_phrase_rewrites"]:
        idx=int(r["index"],16); old=bytes(pd.raw_entry(idx)); old_text=trim(pd.expand_index(idx,tbl)); new=encode(r["after"],tbl)
        if old_text!=r["before"] or len(new)!=len(old): raise BuildError(f"dialogue phrase drift {idx:05X}")
        a=int(pd.entry_abs(idx)); cand[a:a+len(old)]=new; allowed.append((a,a+len(old)))
        rows.append({"kind":"dialogue_phrase","index":r["index"],"before":r["before"],"after":r["after"]})

    # 2. 6019B7: exact pristine unit-count grammar, Korean stock tokens already exist.
    for r in spec["native_page_repairs"]:
        logical=int(r["abs"],16); before,term=record_at(parent,logical); after=bytes.fromhex(r["after_payload_hex"])
        if before!=bytes.fromhex(r["before_payload_hex"]) or term!=int(r["terminator"],16) or len(after)!=len(before): raise BuildError("6019B7 drift")
        if after[:3]!=bytes.fromhex("173418") or after[3:]!=bytes.fromhex("F8E7F2D6F60C03"): raise BuildError("6019B7 grammar mismatch")
        if trim(pd.expand(after[3:],tbl))!=r["render"]: raise BuildError("6019B7 render mismatch")
        cand[sb+logical:sb+logical+len(after)]=after; allowed.append((sb+logical,sb+logical+len(after)))
        rows.append({"kind":"native_page_repair","abs":r["abs"],"before":before.hex().upper(),"after":after.hex().upper(),"render":r["render"]})

    # 3. Same-or-shorter ordinary/ext3 terminology phrases.
    for r in spec["dictionary_rewrites"]:
        idx=int(r["index"],16); old=bytes(pd.raw_entry(idx)); old_text=trim(pd.expand_index(idx,tbl)); new=encode(r["after"],tbl)
        if old_text!=r["before"] or len(new)>len(old): raise BuildError(f"dict drift/grow {idx:05X}")
        a=int(pd.entry_abs(idx)); span=len(old)+1; cand[a:a+span]=new+b"\0"*(span-len(new)); allowed.append((a,a+span))
        rows.append({"kind":"dictionary","index":r["index"],"before":r["before"],"after":r["after"]})

    # 4. 김・깅가남 -> 김　깅가남: raw 2A -> 01 only, preserving markers/storage.
    for r in spec["dictionary_dot_to_space"]:
        idx=int(r["index"],16); old=bytearray(pd.raw_entry(idx)); old_text=trim(pd.expand_index(idx,tbl))
        if old_text!=r["before"] or old.count(0x2A)!=1: raise BuildError(f"dot dict drift {idx:05X}")
        pos=old.index(0x2A); old[pos]=0x01; a=int(pd.entry_abs(idx)); cand[a:a+len(old)]=old; allowed.append((a,a+len(old)))
        rows.append({"kind":"dictionary_dot","index":r["index"],"before":r["before"],"after":r["after"]})

    # 5. Five-page alias phrases.
    for mode in ("alias_rewrites","alias_dot_to_space"):
        for r in spec[mode]:
            seg=int(r["segment"],16); local=int(r["local"],16); bank0=seg*BANK_SIZE; ptr=struct.unpack_from('<H',cand,bank0+local*2)[0]
            if ptr!=int(r["expected_pointer"],16): raise BuildError(f"alias ptr drift {seg:02X}:{local:04X}")
            end=bytes(cand[bank0:bank0+BANK_SIZE]).find(b"\0",ptr); old=bytes(cand[bank0+ptr:bank0+end]); old_text=trim(pd.expand(old,tbl))
            if old_text!=r["before"]: raise BuildError(f"alias text drift {seg:02X}:{local:04X} {old_text!r}")
            if mode=="alias_dot_to_space":
                new=bytearray(old)
                if new.count(0x2A)!=1: raise BuildError("alias dot count drift")
                new[new.index(0x2A)]=0x01; new=bytes(new)
            else: new=encode(r["after"],tbl)
            if len(new)>len(old): raise BuildError("alias grows")
            span=len(old)+1; cand[bank0+ptr:bank0+ptr+span]=new+b"\0"*(span-len(new)); allowed.append((bank0+ptr,bank0+ptr+span))
            rows.append({"kind":mode,"segment":r["segment"],"local":r["local"],"before":r["before"],"after":r["after"]})

    # 6. Direct bank5C name record: preserve both dictionary tokens and change
    # only the middle separator from ・(2A) to a full-width-space code (01).
    for r in spec.get("direct_name_dot_to_space") or []:
        logical=int(r["abs"],16); before,term=record_at(parent,logical); after=bytes.fromhex(r["after_payload_hex"])
        if before!=bytes.fromhex(r["before_payload_hex"]) or term!=int(r["terminator"],16): raise BuildError(f"direct name drift {logical:06X}")
        if len(after)!=len(before) or before[2:3]!=b"\x2A" or after[2:3]!=b"\x01" or before[:2]!=after[:2] or before[3:]!=after[3:]: raise BuildError(f"direct name rewrite shape {logical:06X}")
        if trim(pd.expand(before,tbl))!=r["before"] or trim(pd.expand(after,tbl))!=r["after"]: raise BuildError(f"direct name render {logical:06X}")
        cand[sb+logical:sb+logical+len(after)]=after; allowed.append((sb+logical,sb+logical+len(after)))
        rows.append({"kind":"direct_name_dot","abs":r["abs"],"before":r["before"],"after":r["after"]})

    # 7. Restore raw MA/icon tile identity lost by decode/re-encode flattening.
    for r in spec["raw_tile_restores"]:
        idx=int(r["index"],16); raw=bytearray(pd.raw_entry(idx)); off=int(r["offset"]); before=bytes.fromhex(r["before"]); after=bytes.fromhex(r["after"])
        if bytes(raw[off:off+2])!=before: raise BuildError(f"raw tile drift {idx:05X}")
        raw[off:off+2]=after; a=int(pd.entry_abs(idx)); cand[a:a+len(raw)]=raw; allowed.append((a+off,a+off+2))
        rows.append({"kind":"raw_tile_restore","index":r["index"],"offset":off,"before":r["before"],"after":r["after"]})

    # 8. 75:B401 射全 -> 사전 through compact-glyph short-UI path.
    u=spec["short_ui"]; logical=int(u["abs"],16); before,term=record_at(parent,logical)
    if before!=bytes.fromhex(u["before_payload_hex"]) or term!=0x75B404: raise BuildError("75B401 drift")
    slot=int(u["retired_slot"],16); safe={int(x["index"]):x for x in safe_unreachable_slots(parent,pd)}
    if slot not in safe or bytes(pd.raw_entry(slot))!=bytes.fromhex(u["retired_slot_before"]): raise BuildError("short UI retired slot unsafe")
    # prove selected compact code remains unused, and the prior E51B=전 steal is intact
    od=Dictionary(original); chosen=select_steal_codes(parent,original,pd,od,tbl); new_code=int(u["new_compact_code"],16)
    if not chosen or int(chosen[0]["code"])!=new_code: raise BuildError("compact unused-code drift")
    reuse=int(u["reuse_compact_code"],16); src_sa=int(u["source_hangul_code"],16)
    if read_glyph(parent,compact_glyph_offset(parent,reuse))!=read_glyph(parent,hangul_glyph_offset(parent,0xE745)): raise BuildError("E51B no longer equals 전")
    sa_glyph=read_glyph(parent,hangul_glyph_offset(parent,src_sa)); goff=compact_glyph_offset(parent,new_code); cand[goff:goff+16]=sa_glyph; allowed.append((goff,goff+16))
    entry=int(pd.entry_abs(slot)); oldraw=bytes(pd.raw_entry(slot)); phrase=new_code.to_bytes(2,'big')+reuse.to_bytes(2,'big')
    cand[entry:entry+len(oldraw)+1]=phrase+b"\0"+b"\xFF"*(len(oldraw)-len(phrase)); allowed.append((entry,entry+len(oldraw)+1))
    token=token_from_dict_index(slot); cand[sb+logical:sb+logical+2]=token; cand[sb+logical+2]=0; allowed.append((sb+logical,sb+logical+3))
    rows.append({"kind":"short_ui","abs":u["abs"],"before":"射全","after":"사전","slot":u["retired_slot"],"token":token.hex().upper(),"glyph_sa":u["new_compact_code"],"glyph_jeon":u["reuse_compact_code"]})

    checksum=update_ws_checksum(cand); allowed.append((len(cand)-2,len(cand))); result=bytes(cand)
    if (sum(result[:-2])&0xFFFF)!=int.from_bytes(result[-2:],'little'): raise BuildError("checksum")
    unexpected=[x for x in diff_runs(parent,result) if not covered(x,allowed)]
    if unexpected: raise BuildError(f"unexpected diffs {unexpected[:8]}")

    fd=active_dictionary(result,ext,ext3)
    # final semantic checks
    expected={0xC1FE:'저희들은　지구만을　생각하고、　달을',0x00FE5:'김　깅가남　네놈！！',0x033CB:'한겔그　에빈이라고　합니다。',0x033CD:'한겔그　에빈……',0x033DC:'한겔그　씨。',0x0C20D:'김　깅가남은　턴　엑스를　들고나가、',0x0C2F6:'저것은……　김　깅가남인가！！',0x10073:'이　김　깅가남이',0x10B9E:'김　깅가남과　손을　잡기로　했다。',0x10BB9:'김　깅가남을　어떻게든　하려면'}
    for idx,text in expected.items():
        got=trim(fd.expand_index(idx,tbl))
        if got!=text: raise BuildError(f"final dict render {idx:05X}: {got!r}")
    p19,t19=record_at(result,0x6019B7)
    if p19!=bytes.fromhex('173418F8E7F2D6F60C03') or t19!=0x6019C1: raise BuildError("6019B7 final")
    # double NUL + following 17/28 control is the runtime-sensitive boundary.
    if result[sb+0x6019C1:sb+0x6019CB]!=bytes.fromhex('00001728010600082600'): raise BuildError("6019B7 post-boundary drift")
    # 75B401 becomes 2-byte token + NUL; old NUL at B404 remains as second boundary NUL.
    pui,tui=record_at(result,0x75B401)
    if pui!=token or tui!=0x75B403 or result[sb+0x75B404]!=0: raise BuildError("75B401 final")
    if bytes(fd.raw_entry(slot))!=phrase: raise BuildError("short UI phrase")
    if read_glyph(result,goff)!=sa_glyph: raise BuildError("사 glyph")
    for r in spec["raw_tile_restores"]:
        idx=int(r["index"],16); raw=bytes(fd.raw_entry(idx)); off=int(r["offset"])
        if raw[off:off+2]!=bytes.fromhex('E736'): raise BuildError("icon restore final")
    pname,tname=record_at(result,0x5C09C1)
    if pname!=bytes.fromhex('F1B801FCD5') or tname!=0x5C09C6 or trim(fd.expand(pname,tbl))!='김　깅가남': raise BuildError("5C09C1 final")

    atomic_bytes(OUT,result); atomic_bytes(OUT_TBL,tbl_bytes); atomic_bytes(OUT_SAVE,save)
    report={"schema_version":1,"generated_by":"tools/build_stage18_event_terminology_ui_followup_candidate.py","status":"runtime_test_pending","parent":{"path":rel(PARENT),"sha256":sha(parent)},"candidate":{"path":rel(OUT),"sha256":sha(result),"size":len(result),"checksum":f"{checksum:04X}"},"tbl":{"path":rel(OUT_TBL),"sha256":sha(tbl_bytes)},"saveram":{"path":rel(OUT_SAVE),"sha256":sha(save),"size":len(save)},"changes":rows,"guards":{"6019B7_native_unit_grammar":True,"6019C1_double_nul_and_1728_control_preserved":True,"v2_and_hamma_raw_icon_E736_restored":True,"75B401_short_ui_compact_path":True,"unexpected_diff_runs":0},"diff":{"runs":len(diff_runs(parent,result)),"bytes":sum(b-a for a,b in diff_runs(parent,result))}}
    atomic_json(REPORT,report); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except BuildError as e: print(f"BUILD FAILED: {e}",file=sys.stderr); raise SystemExit(1)
