#!/usr/bin/env python3
"""Analyze UI string blocks: pointers + bank75 battle/menu labels."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, read_encoded_z_safe  # noqa: E402

base = load_rom(find_rom(ROOT))
tbl = Tbl.load(ROOT / "data/monoeye.tbl")
d = Dictionary(base)

# Collect UI zstrings in 5F:2E00-3200 and 75:B600-B900
regions = [(0x5F2E00, 0x5F3200, "opt_gallery"), (0x75B600, 0x75B900, "battle_ui")]
blocks: dict[str, list[dict]] = {}
for start, end, name in regions:
    rows = []
    abs_off = start
    while abs_off < end:
        raw, nxt = read_encoded_z_safe(base, abs_off, max_len=96)
        if raw and len(raw) >= 2:
            plain = d.expand(raw, tbl)
            if plain and "<BAD" not in plain and len(plain) >= 2:
                # skip pure garbage-ish
                rows.append(
                    {
                        "abs": f"{abs_off:06X}",
                        "off16": f"{abs_off & 0xFFFF:04X}",
                        "jp": plain,
                        "nbytes": len(raw),
                    }
                )
                abs_off = nxt if nxt > abs_off else abs_off + len(raw) + 1
                continue
        abs_off += 1
    blocks[name] = rows

# Find LE16 pointers in bank 5F/7A/75/76 that point to these offs
targets = {}
for name, rows in blocks.items():
    for r in rows:
        targets[int(r["abs"], 16) & 0xFFFF] = r

ptr_hits = []
for bank in [0x5F, 0x75, 0x76, 0x7A, 0x7B, 0x7C, 0x7D, 0x7E, 0x7F, 0x50, 0x51]:
    bstart = bank << 16
    data = base[bstart : bstart + 0x10000]
    for i in range(0, len(data) - 1, 2):
        val = data[i] | (data[i + 1] << 8)
        if val in targets:
            ptr_hits.append(
                {
                    "ptr_abs": f"{bstart + i:06X}",
                    "points_to": targets[val]["abs"],
                    "jp": targets[val]["jp"],
                }
            )

out = {
    "blocks": {k: v for k, v in blocks.items()},
    "pointer_hits": ptr_hits[:200],
    "pointer_hit_count": len(ptr_hits),
}
path = ROOT / "out/script/ui_blocks_analysis.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# human readable
lines = []
for name, rows in blocks.items():
    lines.append(f"# {name} ({len(rows)})")
    for r in rows:
        lines.append(f"{r['abs']}\tlen={r['nbytes']}\t{r['jp']}")
    lines.append("")
lines.append(f"# pointers ({len(ptr_hits)})")
for h in ptr_hits[:80]:
    lines.append(f"{h['ptr_abs']} -> {h['points_to']}\t{h['jp']}")
(ROOT / "out/script/ui_blocks_analysis.md").write_text("\n".join(lines), encoding="utf-8")
print(f"opt={len(blocks['opt_gallery'])} battle={len(blocks['battle_ui'])} ptrs={len(ptr_hits)}")
print("sample battle:")
for r in blocks["battle_ui"][:40]:
    print(f"  {r['abs']} {r['jp']}")
print("sample ptrs:")
for h in ptr_hits[:20]:
    print(f"  {h['ptr_abs']}->{h['points_to']} {h['jp']}")
