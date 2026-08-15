#!/usr/bin/env python3
"""Search a Korean 追撃! layout compatible with its shared 8x16 runtime column."""
from __future__ import annotations

import sys
from pathlib import Path
from PIL import ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_id_command_plaques_ko_candidate as base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
FONT = ROOT / "assets/fonts/galmuri_tmp/Galmuri11-Bold.ttf"
PURSUIT = 0x4CC32A
SHARED_TOP = 0x4CB80A
SHARED_BOTTOM = 0x4CB8AA


def decode_tile(raw: bytes) -> list[list[int]]:
    return base.decode_grid(raw, 1, 1)


def current_display(main: bytes, stock_base: int) -> list[list[int]]:
    body = base.decode_grid(main[stock_base + PURSUIT : stock_base + PURSUIT + 0x140], 5, 2)
    st = decode_tile(main[stock_base + SHARED_TOP : stock_base + SHARED_TOP + 0x20])
    sb = decode_tile(main[stock_base + SHARED_BOTTOM : stock_base + SHARED_BOTTOM + 0x20])
    out = [[0] * 48 for _ in range(16)]
    for y in range(16):
        shared = st[y] if y < 8 else sb[y - 8]
        private_cols = [body[y][i * 8 : (i + 1) * 8] for i in range(5)]
        cols = private_cols[:3] + [shared] + private_cols[3:]
        out[y] = [v for col in cols for v in col]
    return out


def mismatch(a: list[list[int]], b: list[list[int]], x0: int, x1: int) -> int:
    return sum(a[y][x] != b[y][x] for y in range(16) for x in range(x0, x1))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main_rom = MAIN.read_bytes()
    stock = STOCK.read_bytes()
    sb = len(main_rom) - len(stock)
    source = current_display(main_rom, sb)
    fixed = [row[24:32] for row in source]
    candidates = []
    for font_size in range(8, 13):
        font = ImageFont.truetype(str(FONT), font_size)
        outer, inner = base.make_masks("추격!", font, 1)
        if outer.height > 14:
            continue
        for zone0, zone1 in ((5, 43), (8, 40), (6, 42), (4, 44)):
            if outer.width > zone1 - zone0:
                continue
            centered = zone0 + ((zone1 - zone0) - outer.width) // 2
            for dx in range(centered - 5, centered + 6):
                if dx < zone0 or dx + outer.width > zone1:
                    continue
                target = [row[:] for row in source]
                for y in range(1, 15):
                    for x in range(zone0, zone1):
                        target[y][x] = 0xC
                for y in (0, 15):
                    for x in range(max(zone0, 6), min(zone1, 42)):
                        target[y][x] = 0xF
                dy = 1 + (14 - outer.height) // 2
                op, ip = outer.load(), inner.load()
                for y in range(outer.height):
                    for x in range(outer.width):
                        if op[x, y]:
                            target[dy + y][dx + x] = 0xF
                        if ip[x, y]:
                            target[dy + y][dx + x] = 0xE
                mm = sum(target[y][24 + x] != fixed[y][x] for y in range(16) for x in range(8))
                candidates.append((mm, font_size, zone0, zone1, dx, dy, outer.width, outer.height))
    candidates.sort()
    for row in candidates[:30]:
        print(row)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
