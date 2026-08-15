#!/usr/bin/env python3
from __future__ import annotations
import hashlib,sys
from pathlib import Path
ROOT=Path(r'D:\monoeye'); sys.path.insert(0,str(ROOT/'tools'))
from diag_ending_libretro_phase import Harness,retroarch_state_payload
CORE=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll'); SD=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan'); STATE=SD/'monoeye_ko_expanded.state37'
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'; CAND=ROOT/'out/patch/appreciation_bgm_ext3_scratch_relocation_candidate.wsc'; STOCK=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'
def run(rom,down):
 h=Harness(CORE,Path(r'C:\RetroArch-Win64\system'),SD)
 try:
  h.load_game(rom); h.unserialize(retroarch_state_payload(STATE))
  if down:h.set_pressed(5)
  h.run(); r1=h.ram(); h.set_pressed(); h.run(); r2=h.ram(); h.run(); r3=h.ram()
  return {'idx1':r1[0x19f8],'idx_word1':int.from_bytes(r1[0x19f8:0x19fa],'little'),'new_scratch1':int.from_bytes(r1[0x1b70:0x1b72],'little'),
          'idx3':r3[0x19f8],'row17':r3[0x3000+17*64:0x3000+18*64],'dyn':r3[0xbe00:0xc300],
          'map':r3[0x3000:0x3800],'tiles':r3[0x4000:0xc300]}
 finally:h.close()
def main():
 m0=run(MAIN,False); c0=run(CAND,False); md=run(MAIN,True); cd=run(CAND,True); sd=run(STOCK,True)
 print('NO_INPUT screen_equal_main',c0['map']==m0['map'],'dyn_equal',c0['dyn']==m0['dyn'],'tiles_equal',c0['tiles']==m0['tiles'],'idx',m0['idx3'],c0['idx3'])
 for n,x in [('main_down',md),('cand_down',cd),('stock_down',sd)]:
  print(n,'idx1',x['idx1'],'idxword',hex(x['idx_word1']),'scratch',hex(x['new_scratch1']),'idx3',x['idx3'],'row17sha',hashlib.sha256(x['row17']).hexdigest()[:12],'dynsha',hashlib.sha256(x['dyn']).hexdigest()[:12])
 print('cand_vs_stock_down_map',cd['map']==sd['map'],'row17',cd['row17']==sd['row17'])
if __name__=='__main__':main()
