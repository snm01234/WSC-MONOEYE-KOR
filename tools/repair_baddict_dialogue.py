#!/usr/bin/env python3
"""Restore dialogue zstrings that expand to <BADDICT:...> back to JP bodies."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z,
    stock_base,
    update_ws_checksum,
)

JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--out-rom", type=Path, default=None)
    ap.add_argument("--lo", type=lambda s: int(s, 16), default=0x600000)
    ap.add_argument("--hi", type=lambda s: int(s, 16), default=0x69FFFF)
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_apply_all.json",
        help="Abs list to scan (default: apply sheet)",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/repair_baddict_report.json",
    )
    args = ap.parse_args()
    out = args.out_rom or args.rom

    tip = bytearray(load_rom(args.rom))
    jp = load_rom(JP)
    tbl = Tbl.load(TBL)
    d = Dictionary(tip)
    st_t = stock_base(tip)
    st_j = stock_base(jp)

    lines = json.loads(args.sheet.read_text(encoding="utf-8"))["lines"]
    restored = []
    for row in lines:
        a = int(row["abs"], 16)
        if not (args.lo <= a <= args.hi):
            continue
        fo = st_t + a
        try:
            original, _ = read_encoded_z(tip, fo, 256)
        except Exception:
            continue
        body = split_prefix_body(original)[1]
        try:
            text = d.expand(body, tbl) or ""
        except Exception:
            text = ""
        if "BADDICT" not in text:
            continue
        try:
            jp_rec, _ = read_encoded_z(jp, st_j + a, 256)
        except Exception:
            continue
        # Write JP payload + mandatory NUL (sequential scan terminator).
        tip[fo : fo + len(jp_rec)] = jp_rec
        tip[fo + len(jp_rec)] = 0x00
        restored.append({"abs": f"{a:06X}", "was": text[:60]})

    cs = update_ws_checksum(tip)
    out.write_bytes(tip)
    report = {
        "rom": str(args.rom),
        "out": str(out),
        "restored": len(restored),
        "checksum": f"{cs:04X}",
        "sample": restored[:40],
    }
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
