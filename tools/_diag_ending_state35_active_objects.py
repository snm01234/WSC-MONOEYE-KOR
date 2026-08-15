#!/usr/bin/env python3
from __future__ import annotations
import importlib.util,struct,sys
from pathlib import Path
R=Path(r'D:\monoeye');sys.path.insert(0,str(R/'tools'))
s=importlib.util.spec_from_file_location('h',R/'tools/diag_ending_libretro_phase.py');h=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(h)
state=Path(r'C:\RetroArch-Win64\states\Beetle WonderSwan\monoeye_ko_expanded.state35');rom=R/'out/patch/monoeye_ko_expanded.wsc';core=Path(r'C:\RetroArch-Win64\cores\mednafen_wswan_libretro.dll')
hh=h.Harness(core,Path(r'C:\RetroArch-Win64\system'),state.parent);hh.load_game(rom);hh.unserialize(h.retroarch_state_payload(state))
for _ in range(4):hh.run()
ram=hh.ram();
for i in range(128):
 o=0x846+i*0x20; w=struct.unpack_from('<16H',ram,o)
 if w[0]&1 or w[2]!=0 or w[5]!=0 or w[6]!=0 or w[8]!=0 or w[9]!=0:
  print(f'{i:02X} off={o:04X} flags={w[0]:04X} link={ram[o+2]:02X} x={w[3]:04X} y={w[4]:04X} +0A={w[5]:04X} +0C={w[6]:04X} +0E={w[7]:04X} res={w[9]:04X}:{w[8]:04X} +14={w[10]:04X} +16={w[11]:04X}')
hh.close()
