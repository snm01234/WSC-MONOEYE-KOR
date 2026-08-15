#!/usr/bin/env python3
"""
Freeze tip script banks 60–69 to JP and clear expansion spill 30–4F.

Produces:
  out/patch/monoeye_free_space_base.wsc  — clean base for free-space KO
  optionally promotes tip (with .pre_free_space_base backup)

Does not touch 5E/5F dict, fonts, hooks, or unit banks.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    load_rom,
    patch_bank,
    patch_expansion_bank,
    slice_bank,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from build_script_ko import JAGD_GUARD_ABS, JAGD_GUARD_GOOD  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BASE_OUT = ROOT / "out/patch/monoeye_free_space_base.wsc"
BACKUP = ROOT / "out/patch/monoeye_ko_expanded.pre_free_space_base.wsc"

DIALOGUE = list(range(0x60, 0x6A))
EXP = list(range(0x30, 0x50))


def bank_diff(a: bytes, b: bytes, seg: int) -> int:
    sa = stock_base(a) + (seg << 16)
    sb = stock_base(b) + (seg << 16)
    return sum(1 for x, y in zip(a[sa : sa + 0x10000], b[sb : sb + 0x10000]) if x != y)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=TIP,
        help="Source 16MiB ROM to reset (default: tip)",
    )
    ap.add_argument(
        "--promote-tip",
        action="store_true",
        help="Also replace monoeye_ko_expanded.wsc (backup first)",
    )
    ap.add_argument("--out", type=Path, default=BASE_OUT)
    args = ap.parse_args()

    if not args.rom.exists() or not JP.exists():
        print("missing source rom or JP", file=sys.stderr)
        return 1

    src = args.rom.resolve()
    dst = args.out.resolve()
    if src != dst:
        shutil.copy2(src, dst)
    rom = bytearray(load_rom(dst))
    jp = load_rom(JP)

    restored = []
    for seg in DIALOGUE:
        before = bank_diff(rom, jp, seg)
        patch_bank(rom, seg, slice_bank(jp, seg))
        restored.append({"bank": f"{seg:02X}", "tip_vs_jp_before": before})

    blank = bytes([0xFF] * 0x10000)
    exp_cleared = 0
    for seg in EXP:
        cur = bytes(slice_expansion_bank(rom, seg))
        if any(b != 0xFF for b in cur):
            exp_cleared += 1
        patch_expansion_bank(rom, seg, blank)

    cs = update_ws_checksum(rom)
    args.out.write_bytes(rom)

    out = load_rom(args.out)
    fo = stock_base(out) + JAGD_GUARD_ABS
    jagd_ok = bytes(out[fo : fo + 3]) == JAGD_GUARD_GOOD
    dialogue_vs_jp = {f"{seg:02X}": bank_diff(out, jp, seg) for seg in DIALOGUE}

    if args.promote_tip:
        if TIP.exists():
            shutil.copy2(TIP, BACKUP)
        shutil.copy2(args.out, TIP)

    report = {
        "out": str(args.out),
        "promoted_tip": bool(args.promote_tip),
        "backup": str(BACKUP) if args.promote_tip else None,
        "checksum": f"{cs:04X}",
        "jagd_guard_ok": jagd_ok,
        "restored_60_69": restored,
        "exp_banks_cleared_nonempty": exp_cleared,
        "dialogue_vs_jp": dialogue_vs_jp,
        "note": "Clean free-space base: JP dialogue 60-69, empty expansion 30-4F.",
    }
    rep_path = ROOT / "out/patch/monoeye_free_space_base_report.json"
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("->", rep_path)
    except UnicodeEncodeError:
        print(json.dumps(report, ensure_ascii=True, indent=2))
        print("->", rep_path)
    if not jagd_ok or any(dialogue_vs_jp.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
