#!/usr/bin/env python3
"""Independent static audit for the page-20 overlay bypass diagnostic."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CAND = ROOT / "out/patch/ending_scene_page20_overlay_bypass_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CSAVE = ROOT / "sram/ending_scene_page20_overlay_bypass_candidate.sav"
BUILD = ROOT / "out/patch/ending_scene_page20_overlay_bypass_candidate_report.json"
OUT = ROOT / "out/patch/ending_scene_page20_overlay_bypass_candidate_audit.json"
MAIN_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
SITE = 0x7ED4F1
STOCK = bytes.fromhex("B90000BB0000")
MAIN_HOOK = bytes.fromhex("9A24FF00F090")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out=[]; i=0
    while i < len(a):
        if a[i] == b[i]: i += 1; continue
        s=i
        while i < len(a) and a[i] != b[i]: i += 1
        out.append((s,i))
    return out


def main() -> int:
    parent=MAIN.read_bytes(); cand=CAND.read_bytes(); save=SAVE.read_bytes(); csave=CSAVE.read_bytes()
    build=json.loads(BUILD.read_text(encoding="utf-8")); sb=stock_base(parent); csb=stock_base(cand)
    dr=runs(parent,cand)
    checks={
        "main_identity": sha(parent)==MAIN_SHA,
        "builder_ok": build.get("ok") is True,
        "main_hook_exact": parent[sb+SITE:sb+SITE+6]==MAIN_HOOK,
        "candidate_stock_D4F1_exact": cand[csb+SITE:csb+SITE+6]==STOCK,
        "paired_save_exact": csave==save,
        "checksum_valid": int.from_bytes(cand[-2:],"little")==sum(cand[:-2])&0xFFFF,
        "only_D4F1_plus_checksum": all((csb+SITE<=a and b<=csb+SITE+6) or a>=len(cand)-2 for a,b in dr),
        "main_unchanged": sha(MAIN.read_bytes())==MAIN_SHA,
    }
    report={"schema_version":1,"generated_by":"tools/audit_ending_scene_page20_overlay_bypass_candidate.py","ok":all(checks.values()),"checks":checks,"candidate_sha256":sha(cand),"diff_runs":[[f"{a:08X}",f"{b:08X}"] for a,b in dr],"runtime_validation_required":True}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["ok"] else 1

if __name__ == "__main__": raise SystemExit(main())
