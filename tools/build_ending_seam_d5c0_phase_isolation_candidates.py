#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom, stock_base, update_ws_checksum

MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'
SAVE=ROOT/'sram/monoeye_ko_expanded.sav'
REPORT=ROOT/'out/patch/ending_seam_d5c0_phase_isolation_report.json'

ENTRY_SITE=0x7ED3AA
PREUPDATE_SITE=0x7ED51C
D5C0=0x7ED5C0
FD40=0x7EFD40
CAVE=0x7EFD83
SEG=0xE000
ENTRY_EXPECT=bytes.fromhex('9A83910080')
PRE_EXPECT=bytes.fromhex('26810F2000')
D5C0_CURRENT=bytes.fromhex('E97D2790')
D5C0_HIST=bytes.fromhex('32D2EB1B')
FD40_EXPECT=bytes.fromhex('B0159A30FF00F032D2E993D8')
# preupdate wrapper: preserve AX, execute original OR, sync to next frame boundary, return
PRE_WRAPPER=bytes.fromhex('5026810F2000E4023C9072FAE4023C9073FA58CB')
# page21 wrapper replacement in current FD40: perform overlay, then sync, then original xor dl; jmp D5DF
POST_PAGE21_WRAPPER=bytes.fromhex('B0159A30FF00F0E4023C9072FAE4023C9073FA32D2E987D8')
# Final near-jump begins at FD55 and returns from FD58; D5DF-FD58 = D887 (little-endian 87 D8).

def sha(b:bytes|bytearray)->str:return hashlib.sha256(bytes(b)).hexdigest()

def phys(sb,logical):return sb+logical

def save_out(out:bytearray, stem:str, parent:bytes, allowed:list[tuple[int,int]], meta:dict)->dict:
    update_ws_checksum(out)
    op=ROOT/'out/patch'/f'{stem}.wsc'; sp=ROOT/'sram'/f'{stem}.sav'
    op.write_bytes(out); shutil.copyfile(SAVE,sp)
    diffs=[i for i,(a,b) in enumerate(zip(parent,out)) if a!=b]
    non=[i for i in diffs if i not in (len(out)-2,len(out)-1)]
    aset=set()
    for a,b in allowed: aset.update(range(a,b))
    extra=set(non)-aset
    if extra: raise RuntimeError(f'unexpected diff {sorted(extra)[:8]}')
    return {**meta,'rom':str(op.relative_to(ROOT)).replace('\\','/'),'sha256':sha(out),'checksum':f'{out[-2]|out[-1]<<8:04X}','saveram':str(sp.relative_to(ROOT)).replace('\\','/'),'saveram_sha256':sha(sp.read_bytes()),'nonchecksum_changed_bytes':len(non)}

def main():
    parent=bytes(load_rom(MAIN)); sb=stock_base(parent)
    for logical,expected,name in [(ENTRY_SITE,ENTRY_EXPECT,'entry'),(PREUPDATE_SITE,PRE_EXPECT,'pre'),(D5C0,D5C0_CURRENT,'d5c0'),(FD40,FD40_EXPECT,'fd40')]:
        got=parent[phys(sb,logical):phys(sb,logical)+len(expected)]
        if got!=expected: raise RuntimeError(f'{name} drift {got.hex().upper()}')
    cave=phys(sb,CAVE)
    if parent[cave:cave+len(PRE_WRAPPER)] != b'\xff'*len(PRE_WRAPPER): raise RuntimeError('cave occupied')

    rows=[]
    # A: reproduce the already-tested successful historical strategy on current: late preupdate sync + remove current-only page21 overlay.
    a=bytearray(parent)
    ps=phys(sb,PREUPDATE_SITE); ds=phys(sb,D5C0)
    a[ps:ps+5]=bytes([0x9A,CAVE&0xff,(CAVE>>8)&0xff,SEG&0xff,(SEG>>8)&0xff])
    a[cave:cave+len(PRE_WRAPPER)]=PRE_WRAPPER
    a[ds:ds+4]=D5C0_HIST
    rows.append(save_out(a,'ending_seam_current_preupdate_sync_plus_page21_bypass_probe',parent,[(ps,ps+5),(cave,cave+len(PRE_WRAPPER)),(ds,ds+4)],{
        'mode':'preupdate_sync_plus_page21_bypass','page20_overlay_preserved':True,'page21_overlay_preserved':False,
        'purpose':'prove current-only D5C0/page21 hook is the remaining difference after preupdate sync'}))

    # B: retain current page21 overlay, but synchronize immediately after it before returning to D5DF loop.
    b=bytearray(parent)
    fs=phys(sb,FD40)
    b[fs:fs+len(POST_PAGE21_WRAPPER)]=POST_PAGE21_WRAPPER
    rows.append(save_out(b,'ending_seam_current_page21_postblit_frame_sync_candidate',parent,[(fs,fs+len(POST_PAGE21_WRAPPER))],{
        'mode':'page21_postblit_sync_only','page20_overlay_preserved':True,'page21_overlay_preserved':True,
        'sync_point':'immediately after page21 F000:FF30 blit and before original xor dl / D5DF loop'}))

    # C: belt-and-suspenders diagnostic: preupdate sync plus page21 post-blit sync, both overlays preserved.
    c=bytearray(parent)
    ps=phys(sb,PREUPDATE_SITE); fs=phys(sb,FD40)
    c[ps:ps+5]=bytes([0x9A,CAVE&0xff,(CAVE>>8)&0xff,SEG&0xff,(SEG>>8)&0xff])
    c[cave:cave+len(PRE_WRAPPER)]=PRE_WRAPPER
    c[fs:fs+len(POST_PAGE21_WRAPPER)]=POST_PAGE21_WRAPPER
    rows.append(save_out(c,'ending_seam_current_dual_phase_sync_candidate',parent,[(ps,ps+5),(cave,cave+len(PRE_WRAPPER)),(fs,fs+len(POST_PAGE21_WRAPPER))],{
        'mode':'preupdate_plus_page21_postblit_sync','page20_overlay_preserved':True,'page21_overlay_preserved':True,
        'purpose':'determinize both animation phase boundaries without removing translated overlays'}))

    rep={'schema_version':1,'generated_by':'tools/build_ending_seam_d5c0_phase_isolation_candidates.py','ok':True,
         'evidence':{'historical_preupdate_sync':'user clean','current_preupdate_sync':'user bad','current_entry_sync_plus_page20_bypass':'user bad','hist_vs_current_object_renderer':'byte-exact','remaining_current_only_ending_difference_after_D523':'7E:D5C0 page21 hook'},
         'candidates':rows,'recommended_order':[rows[0]['rom'],rows[1]['rom'],rows[2]['rom']],
         'main_tip_unchanged':sha(MAIN.read_bytes())==sha(parent),'live_saveram_unchanged':True,'promotion':'blocked_pending_user_runtime_validation'}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
