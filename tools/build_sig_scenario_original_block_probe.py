#!/usr/bin/env python3
"""Build a pure A/B probe by restoring the broken Sig event block to JP original.

No runtime hook, dictionary table, pointer, or SaveRAM is changed.  Only logical
bank61 bytes [611DED, 611E62) are copied byte-for-byte from the original JP ROM
onto the current main TIP.  This isolates whether the user's こ / early-event-end
symptom is caused by the localized bytes in this exact block or by a different
runtime/address path.
"""
from __future__ import annotations
import hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from monoeye_rom import load_rom,stock_base,update_ws_checksum
MAIN=ROOT/'sram/monoeye_ko_expanded.wsc'; LIVE=ROOT/'sram/monoeye_ko_expanded.sav'; JP=ROOT/'SD Gundam G Generation Mono-Eye Gundams.wsc'
OUT=ROOT/'sram/sig_scenario_original_block_probe_candidate.wsc'; OUTS=ROOT/'sram/sig_scenario_original_block_probe_candidate.sav'; REPORT=ROOT/'out/patch/sig_scenario_original_block_probe_report.json'
EXPECTED='b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a'
LO=0x611DED; HI=0x611E62

def sha(b): return hashlib.sha256(bytes(b)).hexdigest()
def main():
 p=bytes(load_rom(MAIN)); j=bytes(load_rom(JP)); sav=LIVE.read_bytes();
 if sha(p)!=EXPECTED: raise SystemExit('main TIP identity drifted')
 if len(p)!=16777216 or len(j)!=8388608: raise SystemExit('ROM geometry drifted')
 sb=stock_base(p); c=bytearray(p); before=p[sb+LO:sb+HI]; src=j[LO:HI]
 if len(before)!=len(src): raise SystemExit('range length mismatch')
 c[sb+LO:sb+HI]=src; checksum=update_ws_checksum(c); result=bytes(c)
 # Proof only the selected stock-relative block + checksum changed.
 changed=[i for i,(a,b) in enumerate(zip(p,result)) if a!=b]
 bad=[i for i in changed if not (sb+LO<=i<sb+HI or i>=len(p)-2)]
 if bad: raise SystemExit(f'unaccounted changes: {bad[:10]}')
 if result[sb+LO:sb+HI]!=src: raise SystemExit('restored block mismatch')
 OUT.write_bytes(result); shutil.copyfile(LIVE,OUTS)
 if OUTS.read_bytes()!=sav: raise SystemExit('SaveRAM mismatch')
 rep={'ok':True,'status':'diagnostic_runtime_probe_only_not_for_promotion','generated_by':'tools/build_sig_scenario_original_block_probe.py','main_tip_modified':False,'inputs':{'main_sha256':sha(p),'jp_sha256':sha(j),'live_sav_sha256':sha(sav)},'range':{'lo':f'{LO:06X}','hi_exclusive':f'{HI:06X}','length':HI-LO,'candidate_equals_jp_in_range':True},'outputs':{'rom':str(OUT),'rom_sha256':sha(result),'sav':str(OUTS),'sav_sha256':sha(sav)},'changed_bytes':len(changed),'unaccounted_changed_bytes':len(bad),'checksum':f'{checksum:04X}','purpose':'If this JP-original block runs through normally, the active runtime address binding is confirmed and the localization of this block is causal. If the same こ/early-end remains, discard the 611Dxx binding.'}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(rep,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
