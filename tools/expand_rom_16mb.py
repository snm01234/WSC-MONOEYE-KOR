#!/usr/bin/env python3
"""
Expand an 8 MiB Mono-Eye WSC to 16 MiB by prepending 8 MiB of 0xFF.

Why prepend (not append): stock code uses `mov al, bank|0x80` before OUT C3h.
On 8 MiB that mirrors (C0→bank40). Appending would make C0 a new empty bank
and break the game. Prepending moves stock data to +0x800000 so C0 still hits
the original bank40. See docs/ROM_16MB_EXPANSION.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    ROM_SIZE,
    ROM_SIZE_16MB,
    ROM_SIZE_CODE_16MB,
    expand_rom_to_16mb,
    find_rom,
    load_rom,
    logical_bank_offset,
    stock_base,
    ws_header,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "-i",
        "--input",
        type=Path,
        default=None,
        help="Source 8MB (or already-16MB) .wsc",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_16mb_base.wsc",
        help="Output 16MB .wsc",
    )
    ap.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional JSON summary path",
    )
    args = ap.parse_args()

    src = args.input or find_rom()
    rom8 = load_rom(src)
    if len(rom8) not in (ROM_SIZE, ROM_SIZE_16MB):
        raise SystemExit(f"bad size {len(rom8):#x}")

    hdr_before = ws_header(rom8)
    out = expand_rom_to_16mb(rom8)
    hdr_after = ws_header(out)

    # Sanity: stock bank40 table bytes unchanged under new base.
    old_font = rom8[logical_bank_offset(0x40, 0x440) : logical_bank_offset(0x40, 0x450)]
    new_font = out[
        stock_base(out) + logical_bank_offset(0x40, 0x440) : stock_base(out)
        + logical_bank_offset(0x40, 0x450)
    ]
    if old_font != new_font:
        raise SystemExit("stock font probe mismatch after expand")

    # Expansion region must be blank.
    if any(b != 0xFF for b in out[:ROM_SIZE]):
        raise SystemExit("expansion region is not blank FF")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out)

    summary = {
        "input": str(src),
        "output": str(args.output),
        "size_before": len(rom8),
        "size_after": len(out),
        "rom_size_code_before": hdr_before["rom_size_code"],
        "rom_size_code_after": hdr_after["rom_size_code"],
        "mapper": hdr_after["mapper"],
        "checksum": hdr_after["checksum"],
        "stock_base": hdr_after["stock_base"],
        "expansion_banks": "0x00-0x7F",
        "stock_banks_file": "0x80-0xFF",
        "expected_rom_size_code": ROM_SIZE_CODE_16MB,
        "ok": hdr_after["rom_size_code"] == ROM_SIZE_CODE_16MB
        and len(out) == ROM_SIZE_16MB,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.json:
        args.json.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
