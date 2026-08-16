#!/usr/bin/env python3
"""Restore the misclassified bank60 control records used after Diana's STAGE18 line.

Parent is the cumulative stage18_event_terminology_ui_followup candidate.  Only
8 records in 60:3F33..3F9B are restored byte-for-byte from the pristine Japanese
ROM, plus WonderSwan checksum.  These records were historically misclassified
as dialogue even though their pristine payloads are event/control bytecode.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import stock_base, read_encoded_z_safe, update_ws_checksum  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "stage18_event_terminology_ui_followup_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT_TBL = PATCH / "stage18_event_terminology_ui_followup_candidate.tbl"
PARENT_SAVE = ROOT / "sram/stage18_event_terminology_ui_followup_candidate.sav"
OUT = PATCH / "diana_original_control_restore_candidate.wsc"
OUT_TBL = PATCH / "diana_original_control_restore_candidate.tbl"
OUT_SAVE = ROOT / "sram/diana_original_control_restore_candidate.sav"
REPORT = PATCH / "diana_original_control_restore_candidate_report.json"

EXPECTED_PARENT = "1ce84f3edfd4733d2f06f9679501561be36f51f09b3c947746ffd37f432106e8"
EXPECTED_ORIGINAL = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
TARGETS = [0x603F33, 0x603F3D, 0x603F45, 0x603F57, 0x603F72, 0x603F7C, 0x603F84, 0x603F91]

class BuildError(RuntimeError):
    pass

def sha(b: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(b)).hexdigest()

def rec(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=0x80)
    if got is None:
        raise BuildError(f"unreadable {logical:06X}")
    payload, end = got
    return bytes(payload), int(end) - sb

def atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)

def diff_runs(a: bytes, b: bytes):
    out=[]; i=0
    while i<len(a):
        if a[i]==b[i]: i+=1; continue
        s=i
        while i<len(a) and a[i]!=b[i]: i+=1
        out.append((s,i))
    return out

def main() -> int:
    parent = PARENT.read_bytes(); original = ORIGINAL.read_bytes()
    if sha(parent) != EXPECTED_PARENT: raise BuildError(f"parent drift {sha(parent)}")
    if sha(original) != EXPECTED_ORIGINAL: raise BuildError("original drift")
    psb=stock_base(parent); osb=stock_base(original)
    cand=bytearray(parent); rows=[]; allowed=[]
    for addr in TARGETS:
        op, ot = rec(original, addr); pp, pt = rec(parent, addr)
        if ot != pt:
            raise BuildError(f"terminator drift at {addr:06X}: orig {ot:06X} parent {pt:06X}")
        if len(op) != len(pp):
            raise BuildError(f"extent drift at {addr:06X}")
        start=psb+addr; end=start+len(op)
        cand[start:end]=op
        allowed.append((start,end))
        rows.append({"abs":f"{addr:06X}","original_hex":op.hex().upper(),"parent_hex":pp.hex().upper(),"terminator":f"{ot:06X}"})
    checksum=update_ws_checksum(cand)
    allowed.append((len(cand)-2,len(cand)))
    result=bytes(cand)
    # exact restoration checks
    for addr in TARGETS:
        op,ot=rec(original,addr); cp,ct=rec(result,addr)
        if cp!=op or ct!=ot: raise BuildError(f"restore verify failed {addr:06X}")
    # protect Diana line and branch control itself
    diana_parent=rec(parent,0x6019B7); diana_final=rec(result,0x6019B7)
    ctl_parent=parent[psb+0x6019C3:psb+0x6019CB]
    ctl_final=result[psb+0x6019C3:psb+0x6019CB]
    if diana_parent!=diana_final: raise BuildError("6019B7 collateral")
    if ctl_parent!=ctl_final: raise BuildError("6019C3 control collateral")
    runs=diff_runs(parent,result)
    def covered(run):
        a,b=run
        return any(x<=a and b<=y for x,y in allowed)
    unexpected=[r for r in runs if not covered(r)]
    if unexpected: raise BuildError(f"unexpected runs {unexpected}")
    atomic(OUT,result)
    shutil.copyfile(PARENT_TBL,OUT_TBL)
    shutil.copyfile(PARENT_SAVE,OUT_SAVE)
    report={
      "schema_version":1,
      "generated_by":"tools/build_diana_original_control_restore_candidate.py",
      "status":"runtime_test_pending",
      "parent":{"path":str(PARENT.relative_to(ROOT)).replace('\\','/'),"sha256":sha(parent)},
      "original":{"path":ORIGINAL.name,"sha256":sha(original)},
      "candidate":{"path":str(OUT.relative_to(ROOT)).replace('\\','/'),"sha256":sha(result),"size":len(result),"checksum":f"{checksum:04X}"},
      "tbl":{"path":str(OUT_TBL.relative_to(ROOT)).replace('\\','/'),"sha256":sha(OUT_TBL.read_bytes())},
      "saveram":{"path":str(OUT_SAVE.relative_to(ROOT)).replace('\\','/'),"sha256":sha(OUT_SAVE.read_bytes()),"size":OUT_SAVE.stat().st_size},
      "restored_records":rows,
      "guards":{"6019B7_byte_exact_parent":True,"6019C3_6019CA_byte_exact_parent":True,"all_8_records_exact_original":True,"unexpected_diff_runs":0},
      "diff":{"runs":len(runs),"bytes":sum(b-a for a,b in runs)}
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}",file=sys.stderr); raise SystemExit(1)
