#!/usr/bin/env python3
"""
Apply overflow KO lines onto the tip 16MB ROM via expansion script spill (bank 30+).

Uses hangul_patch_pad3.tbl and overflow_mode=exp_spill. Only far-pointer-backed
records relocate; sequential-scan lines stay skipped_no_pointer (safe).

Writes the tip ROM in place.

Prefer the hybrid compiler `tools/build_script_ko.py` (exp_spill + seq_dict +
separator guards). This script remains a thin single-phase entry point.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_translations_expanded import (  # noqa: E402
    apply_translations_expanded,
    load_translation_lines,
)
from monoeye_rom import Tbl, load_rom, update_ws_checksum  # noqa: E402


def main() -> int:
    tip = ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc"
    tbl_path = ROOT / "out" / "patch" / "hangul_patch_pad3.tbl"
    sheet = ROOT / "out" / "script" / "translations_full.json"
    if not sheet.exists():
        sheet = ROOT / "data" / "translations_seed_hook96.json"
    if not tip.exists():
        print(f"missing tip ROM: {tip}", file=sys.stderr)
        return 1

    rom = load_rom(tip)
    tbl = Tbl.load(tbl_path)
    lines = load_translation_lines(sheet)

    report = apply_translations_expanded(
        rom,
        tbl,
        lines,
        max_shared_phrases=1024,
        allow_bank_rebuild=True,
        allow_inplace=False,
        hangul_marker_code=0xE3DB,
        overflow_mode="exp_spill",
    )
    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    report["input_rom"] = str(tip)
    report["output_rom"] = str(tip)
    report["tbl"] = str(tbl_path)
    report["translations"] = str(sheet)

    tip.write_bytes(rom)
    slim = {
        k: report[k]
        for k in (
            "lines_patched",
            "lines_skipped_unencodable",
            "lines_skipped_no_capacity",
            "decode_failures",
            "slots_used",
            "mode_counts",
            "bank_rebuild",
            "checksum",
            "input_rom",
            "output_rom",
            "tbl",
            "translations",
        )
        if k in report
    }
    slim["results_sample"] = (report.get("results") or [])[:30]
    rep = ROOT / "out" / "patch" / "expspill_report.json"
    rep.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "out": str(tip),
        "lines_patched": slim.get("lines_patched"),
        "skipped_no_capacity": slim.get("lines_skipped_no_capacity"),
        "decode_failures": slim.get("decode_failures"),
        "mode_counts": slim.get("mode_counts"),
        "bank_rebuild": {
            k: (slim.get("bank_rebuild") or {}).get(k)
            for k in (
                "relocated_records",
                "pointer_fixes",
                "skipped_no_pointer_count",
                "skipped_no_seg_form_count",
                "expansion_bytes_used",
            )
        },
        "checksum": slim.get("checksum"),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
