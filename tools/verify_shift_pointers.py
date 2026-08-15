#!/usr/bin/env python3
"""Verify shift-rebuild overflow updates pointer forms and keeps KO decodable."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_translations_expanded import apply_translations_expanded
from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, update_ws_checksum
from rebuild_script_banks import discover_pointer_hits, record_offset_set


def main() -> None:
    rom_path = ROOT / "out" / "patch" / "rom_font_only.wsc"
    if not rom_path.exists():
        rom_path = find_rom(ROOT)
    tbl_path = ROOT / "out" / "patch" / "hangul_patch.tbl"
    if not tbl_path.exists():
        raise SystemExit("hangul_patch.tbl missing; build font first")

    seed = json.loads(
        (ROOT / "data" / "translations_seed.json").read_text(encoding="utf-8")
    )
    # Force overflow: keep seed KO but disable dict slots by asking for many
    # unique long lines beyond reclaimable count via batch placeholders.
    batch_path = ROOT / "data" / "translations_batch.json"
    if batch_path.exists():
        lines = json.loads(batch_path.read_text(encoding="utf-8"))["lines"][:80]
    else:
        lines = seed["lines"]

    rom = load_rom(rom_path)
    tbl = Tbl.load(tbl_path)

    before_hits = {}
    for seg in {int(line["abs"], 16) // 0x10000 for line in lines}:
        offs = record_offset_set(rom, seg)
        hits = discover_pointer_hits(rom, seg, offs)
        before_hits[f"{seg:02X}"] = len(hits)

    report = apply_translations_expanded(
        rom,
        tbl,
        lines,
        max_shared_phrases=0,
        allow_bank_rebuild=True,
    )
    checksum = update_ws_checksum(rom)
    out_rom = ROOT / "out" / "patch" / "monoeye_ko_shift_test.wsc"
    out_rom.write_bytes(rom)

    bank = report.get("bank_rebuild") or {}
    summary = {
        "lines": report["lines_patched"],
        "modes": report["mode_counts"],
        "decode_failures": report["decode_failures"],
        "checksum": f"{checksum:04X}",
        "pointer_hits_before_sample": before_hits,
        "bank_rebuild": {
            "mode": bank.get("mode"),
            "pointer_fixes": bank.get("pointer_fixes"),
            "pointer_fix_counts": bank.get("pointer_fix_counts"),
            "relocated_records": bank.get("relocated_records"),
            "banks": bank.get("banks"),
        },
        "sample_results": report["results"][:12],
        "out_rom": str(out_rom),
    }
    out_json = ROOT / "out" / "patch" / "shift_pointer_verify.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["decode_failures"]:
        sys.exit(1)
    if bank and bank.get("pointer_fixes", 0) <= 0 and any(
        mode.startswith("shift_rebuild") or "+shifted" in mode
        for mode in report["mode_counts"]
    ):
        print("WARNING: shift occurred but pointer_fixes=0")
        sys.exit(2)


if __name__ == "__main__":
    main()
