#!/usr/bin/env python3
"""Package the earliest retained 2026-08-13 Main-TIP backup as an ending probe.

No ROM bytes are modified.  The historical ROM is copied byte-exact and paired
with the current live SaveRAM under a distinct filename so it can be runtime
checked without touching Main TIP or live SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/patch/backup/20260813_115727_pre_bank59_enc5c_name75/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT = ROOT / "out/patch/ending_scene_20260813_baseline_probe.wsc"
OUT_SAVE = ROOT / "sram/ending_scene_20260813_baseline_probe.sav"
REPORT = ROOT / "out/patch/ending_scene_20260813_baseline_probe_report.json"
EXPECTED_SHA = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    src = SOURCE.read_bytes()
    live = LIVE_SAVE.read_bytes()
    if sha(src) != EXPECTED_SHA:
        raise SystemExit(f"historical source drifted: {sha(src)}")
    if len(src) != 0x1000000 or int.from_bytes(src[-2:], "little") != sum(src[:-2]) & 0xFFFF:
        raise SystemExit("historical ROM size/checksum invalid")
    OUT.write_bytes(src)
    shutil.copyfile(LIVE_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_scene_20260813_baseline_probe.py",
        "ok": True,
        "historical_source": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "probe_rom": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "rom_sha256": sha(src),
        "checksum": f"{int.from_bytes(src[-2:], 'little'):04X}",
        "paired_saveram": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
        "paired_saveram_sha256": sha(OUT_SAVE.read_bytes()),
        "rom_byte_exact_historical": OUT.read_bytes() == src,
        "paired_saveram_byte_exact_live": OUT_SAVE.read_bytes() == live,
        "main_tip_untouched": True,
        "purpose": "Chronology probe: determine whether the ending seam already existed by 2026-08-13 11:57, before the ending-credit work.",
        "promotion": "not_a_candidate",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
