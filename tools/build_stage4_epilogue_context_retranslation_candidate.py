#!/usr/bin/env python3
"""Build the Stage 4 epilogue context/register retranslation candidate.

This is a narrow wrapper around the proven current-main ext3/portal retarget
builder.  It changes only the rows listed in
``data/stage4_epilogue_context_retranslation_ko.json`` and keeps the promoted
main TIP and live SaveRAM untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import build_global_dialogue_boundary_retranslation_candidate as builder

ROOT = Path(__file__).resolve().parents[1]

builder.SPEC = ROOT / "data/stage4_epilogue_context_retranslation_ko.json"
builder.OUT_ROM = ROOT / "out/patch/stage4_epilogue_context_retranslation_candidate.wsc"
builder.OUT_SAVE = ROOT / "sram/stage4_epilogue_context_retranslation_candidate.sav"
builder.OUT_REPORT = ROOT / "out/patch/stage4_epilogue_context_retranslation_report.json"
builder.EXPECTED_PARENT_SHA = "63ccb2dc173bbd65ebfa64f6ef2fd531233b558d543e769cab5e1b8147abb70c"


def main() -> int:
    rc = builder.main()
    report = json.loads(builder.OUT_REPORT.read_text(encoding="utf-8"))
    report["generated_by"] = "tools/build_stage4_epilogue_context_retranslation_candidate.py"
    report["scope"] = "Stage 4 epilogue context + speaker/addressee register consistency"
    report["promotion"] = "blocked pending user runtime validation"
    builder.OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
