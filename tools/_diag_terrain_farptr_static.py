#!/usr/bin/env python3
"""Find unaligned 16:16 far pointers that resolve to candidate terrain strings."""
from __future__ import annotations

from pathlib import Path

import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
STOCK_BASE = 0x800000


def main() -> None:
    whole = ROM.read_bytes()
    rom = whole[STOCK_BASE : STOCK_BASE + 0x800000]
    targets = (
        (0x74398E, 0x40000 + 0x398E),
        (0x75EE4C, 0x50000 + 0xEE4C),
        (0x75B3CE, 0x50000 + 0xB3CE),
        (0x75E58C, 0x50000 + 0xE58C),
        (0x75E59A, 0x50000 + 0xE59A),
        (0x75BD77, 0x50000 + 0xBD77),
        (0x75B457, 0x50000 + 0xB457),
    )
    wanted = {physical: logical for logical, physical in targets}
    found = {logical: [] for logical, _physical in targets}
    for pos in range(len(rom) - 3):
        offset = rom[pos] | (rom[pos + 1] << 8)
        segment = rom[pos + 2] | (rom[pos + 3] << 8)
        physical = ((segment << 4) + offset) & 0xFFFFF
        logical = wanted.get(physical)
        if logical is not None:
            found[logical].append(pos)
    for logical, physical in targets:
        hits = found[logical]
        print(
            f"{logical:06X} phys={physical:05X} count={len(hits)} "
            + " ".join(f"{x >> 16:02X}:{x & 0xFFFF:04X}" for x in hits[:100])
        )

    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
    dictionary = make_dictionary_ext3(
        whole,
        load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )
    base = stock_base(whole)
    rows = []
    for index in range(64):
        entry = base + 0x74398E + index * 4
        offset = int.from_bytes(whole[entry : entry + 2], "little")
        segment = int.from_bytes(whole[entry + 2 : entry + 4], "little")
        physical = ((segment << 4) + offset) & 0xFFFFF
        logical = 0x750000 | (physical & 0xFFFF)
        got = read_encoded_z_safe(whole, base + logical, max_len=64)
        rows.append(
            {
                "index": index,
                "entry": f"{0x74398E + index * 4:06X}",
                "far": f"{segment:04X}:{offset:04X}",
                "logical": f"{logical:06X}",
                "raw": bytes(got[0]).hex().upper() if got else "",
                "text": dictionary.expand(got[0], tbl) if got else "",
            }
        )
    print(json.dumps(rows, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
