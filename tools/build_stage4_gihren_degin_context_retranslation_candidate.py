#!/usr/bin/env python3
"""Build Stage 4 Gihren-Degwin Solar Ray conversation retranslation candidate."""
from __future__ import annotations
import json
from pathlib import Path
import build_global_dialogue_boundary_retranslation_candidate as builder
ROOT=Path(__file__).resolve().parents[1]
builder.SPEC=ROOT/'data/stage4_gihren_degin_context_retranslation_ko.json'
builder.OUT_ROM=ROOT/'out/patch/stage4_gihren_degin_context_retranslation_candidate.wsc'
builder.OUT_SAVE=ROOT/'sram/stage4_gihren_degin_context_retranslation_candidate.sav'
builder.OUT_REPORT=ROOT/'out/patch/stage4_gihren_degin_context_retranslation_report.json'
builder.EXPECTED_PARENT_SHA='9003cbe4333ac16059afaf0995c98a6aa56711dcf71411cc1b750ec2f7a8e6aa'
def main():
 rc=builder.main()
 rep=json.loads(builder.OUT_REPORT.read_text(encoding='utf-8'))
 rep['generated_by']='tools/build_stage4_gihren_degin_context_retranslation_candidate.py'
 rep['scope']='Stage 4 Gihren-Degwin Solar Ray conversation full context retranslation'
 rep['promotion']='blocked pending user runtime validation'
 builder.OUT_REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return rc
if __name__=='__main__': raise SystemExit(main())
