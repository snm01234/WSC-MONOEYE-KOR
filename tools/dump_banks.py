#!/usr/bin/env python3
"""Slice key ROM banks and expand the segment-5F compression dictionary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    DICT_PTR_END,
    DICT_PTR_START,
    SEG_DICT,
    SEG_FONT,
    SEG_PROG,
    SEG_TEXT,
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
    slice_bank,
    ws_header,
)


def save_banks(rom: bytearray, out_dir: Path) -> None:
    banks = {
        "40_font": SEG_FONT,
        "5F_dict": SEG_DICT,
        "60_text": SEG_TEXT,
        "7A_program": SEG_PROG,
    }
    for name, seg in banks.items():
        path = out_dir / f"bank_{name}.bin"
        path.write_bytes(slice_bank(rom, seg))
        print(f"Wrote {path} ({path.stat().st_size} bytes)")


def dump_dictionary(rom: bytearray, tbl: Tbl | None, out_dir: Path, limit: int | None) -> None:
    d = Dictionary(rom)
    count = d.count if limit is None else min(d.count, limit)
    lines = []
    meta = {
        "count": d.count,
        "ptr_start": f"5F:{DICT_PTR_START:04X}",
        "ptr_end": f"5F:{DICT_PTR_END:04X}",
        "scheme": "Fx yy -> dict[((x-F0)<<8)|yy]",
    }
    (out_dir / "dictionary_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    raw_path = out_dir / "dictionary_raw.txt"
    dec_path = out_dir / "dictionary_decoded.txt"
    with raw_path.open("w", encoding="utf-8") as fr, dec_path.open("w", encoding="utf-8") as fd:
        for i in range(count):
            raw = d.raw_entry(i)
            hex_raw = " ".join(f"{b:02X}" for b in raw)
            decoded = d.expand_index(i, tbl) if tbl else d.expand_index(i)
            fr.write(f"{i:04X}\t{d.entry_offset(i):04X}\t{hex_raw}\n")
            fd.write(f"{i:04X}\t{decoded}\n")
            if i < 40:
                lines.append(f"[{i:04X}] {decoded}")
    print(f"Wrote {raw_path}")
    print(f"Wrote {dec_path}")
    print("Sample (first 20):")
    for line in lines[:20]:
        try:
            print(" ", line)
        except UnicodeEncodeError:
            print(" ", line.encode("unicode_escape").decode("ascii"))


def dump_text_preview(rom: bytearray, tbl: Tbl, out_dir: Path, nbytes: int = 0x800) -> None:
    d = Dictionary(rom)
    base = SEG_TEXT * 0x10000
    chunk = bytes(rom[base : base + nbytes])
    # Split on 00 into records
    records = []
    start = 0
    for i, b in enumerate(chunk):
        if b == 0:
            if i > start:
                records.append((base + start, chunk[start:i]))
            start = i + 1
    out = out_dir / "text60_preview.txt"
    with out.open("w", encoding="utf-8") as f:
        for abs_off, payload in records[:200]:
            hex_raw = " ".join(f"{b:02X}" for b in payload[:48])
            decoded = d.expand(payload, tbl)
            f.write(f"@{abs_off:06X}\t{hex_raw}\n\t{decoded}\n")
    print(f"Wrote {out} ({min(200, len(records))} records)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data" / "monoeye.tbl")
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--dict-limit", type=int, default=None)
    args = ap.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    rom_path = args.rom or find_rom(ROOT)
    rom = load_rom(rom_path)
    print(f"ROM: {rom_path}  header={ws_header(rom)}")

    save_banks(rom, out_dir)

    tbl = Tbl.load(args.tbl) if args.tbl.exists() else None
    if tbl is None:
        print(f"WARNING: TBL not found at {args.tbl}; dumping codes only")
    else:
        print(f"TBL entries: {len(tbl.code_to_char)}")

    dump_dictionary(rom, tbl, out_dir, args.dict_limit)
    if tbl:
        dump_text_preview(rom, tbl, out_dir)
    print("Done.")


if __name__ == "__main__":
    main()
