#!/usr/bin/env python3
"""Static verification for pad3 hooks on the tip 16MB ROM (no emulator)."""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, ws_header  # noqa: E402
from patch_pad3_expansion import (  # noqa: E402
    FONT_PADDING_RANGES,
    PAD12_SLOTS,
    PAD2_HELPER,
    PAD3_BANK_AL,
    PAD2_BANK_AL,
    sab,
    pad3_file_offset,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--map",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_char_map_pad3.json",
    )
    ap.add_argument(
        "--font-source",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded_8mb.wsc",
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    path = args.rom
    rom = load_rom(path)
    hdr = ws_header(rom)
    assert hdr["rom_size_code"] == 0x09
    assert hdr["size"] == 0x1000000
    assert stock_base(rom) == 0x800000

    # Caves
    assert rom[sab(rom, 0x7A0521)] == 0xE9
    ext_dict = bytes(rom[sab(rom, 0x7FFC8C) : sab(rom, PAD2_HELPER)])
    assert ext_dict.startswith(bytes.fromhex("81feee1d"))
    pad_hi = bytes(rom[sab(rom, PAD2_HELPER) : sab(rom, PAD2_HELPER) + 70])
    assert bytes([0xB0, PAD2_BANK_AL]) in pad_hi
    assert bytes([0xB0, PAD3_BANK_AL]) in pad_hi
    assert struct.pack("<H", PAD12_SLOTS) in pad_hi

    # Stock bank40 font and the explicit padding ranges must match the
    # font-only source, while all unrelated source bytes are ignored.
    source = load_rom(args.font_source)
    source_base = stock_base(source)
    rom = load_rom(path)
    assert rom[0xC00440:0xC00450] == source[
        source_base + 0x400440 : source_base + 0x400450
    ]
    font_ranges = []
    for name, logical_start, length in FONT_PADDING_RANGES:
        target = bytes(rom[sab(rom, logical_start) : sab(rom, logical_start) + length])
        expected = bytes(
            source[
                source_base + logical_start : source_base + logical_start + length
            ]
        )
        same = target == expected
        assert same, f"font range differs: {name}"
        font_ranges.append(
            {
                "name": name,
                "stock_offset": f"{logical_start:06X}",
                "length": length,
                "sha256": __import__("hashlib").sha256(target).hexdigest(),
            }
        )

    # Every migrated legacy slot and every baked overflow slot must contain a
    # real compact record, not an all-FF/all-zero placeholder.
    migrated_empty = []
    for slot in range(PAD12_SLOTS, 1027):
        off = pad3_file_offset(rom, slot)
        rec = bytes(rom[off : off + 16])
        if all(b == 0xFF for b in rec) or all(b == 0 for b in rec):
            migrated_empty.append(slot)
    assert not migrated_empty, f"empty pad3 migrated slots: {migrated_empty[:8]}"

    for slot in (528, 1026, 1185):
        off = pad3_file_offset(rom, slot)
        rec = bytes(rom[off : off + 16])
        assert not all(b == 0xFF for b in rec), f"slot {slot} empty"

    # Expansion bank0 only used in low region; high stock still mirrors layout.
    assert any(b != 0xFF for b in rom[0:0x3000])
    assert all(b == 0xFF for b in rom[0x30000:0x30100])

    m = json.loads(args.map.read_text(encoding="utf-8"))
    sticky_count = int(m["padding_store"]["count"])
    assert sticky_count == 1186
    pad3_n = sum(
        1
        for _, info in m["mapping"].items()
        if info.get("pool") == "padding_store_pad3"
    )
    assert pad3_n == 159

    report = {
        "rom": str(path),
        "font_source": str(args.font_source),
        "header": hdr,
        "ext_dict_ok": True,
        "pad_hi_al": {"pad2": f"{PAD2_BANK_AL:02X}", "pad3": f"{PAD3_BANK_AL:02X}"},
        "sticky_count": sticky_count,
        "pad3_migrated_slots": 499,
        "pad3_migrated_empty": migrated_empty,
        "pad3_overflow_chars": pad3_n,
        "font_ranges": font_ranges,
        "ok": True,
    }
    out = args.out or path.with_suffix(".pad3_verify.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())