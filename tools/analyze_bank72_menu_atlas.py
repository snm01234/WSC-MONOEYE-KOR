#!/usr/bin/env python3
r"""
Decode the initial-menu graphics atlas in bank 72.

Prior state of knowledge (docs/UI_MENU_NEXT_STEPS.md): zeroing 2 KB slices of
``72:0000-17FF`` changed the menu screenshot, the encoded ``ニューゲーム`` byte
string is *not* in there, and the head of the bank "looks like an LE16 offset
table (``00 00 27 01 49 00 5A 00 ...``)".

The offset-table reading is wrong. Measured layout:

``72:0000-007F``
    Four 16-colour WonderSwan Color palettes. 32 B each, LE16 per entry, 12-bit
    ``0x0RGB``. The values form smooth ramps (``0000 0127 0049 005A 006A 006B
    007B 008C 019D 01AD 05CF ...``), which is what made them look like a
    monotonic-ish pointer table.

``72:0080 + n*0x280``
    Button plates. Each plate is **80x16 px, packed 4bpp, 8x8 tiles laid out 10
    tiles per row, 20 tiles = 640 B (0x280)**. Rendering at exactly 10 tiles per
    row makes every plate a clean rectangle with no drift across the whole
    region, which is the alignment proof.

The plates render as the three menu labels in several highlight states, i.e. the
menu buttons really are graphics, as the docs suspected -- but their pixels are
plainly readable in ROM, so no runtime decompression is involved.

Outputs
    ``out/title_menu_capture/bank72_atlas.json``   plate table + palettes
    ``out/title_menu_capture/tiles/plate_NN.png``  one PNG per plate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image  # noqa: E402

from monoeye_rom import find_rom, load_rom  # noqa: E402
from render_bank_tiles import render, tiles_4bpp  # noqa: E402

BANK72 = 0x720000
PALETTE_AREA = (0x0000, 0x0080)
PLATE_BASE = 0x0080
PLATE_SIZE = 0x0280          # 20 tiles * 32 B
PLATE_COLS = 10              # tiles per row -> 80 px wide
PLATE_ROWS = 2               # -> 16 px tall


def wsc_palette(block: bytes) -> list[tuple[int, int, int]]:
    """16 entries of 12-bit 0x0RGB -> 8-bit RGB."""
    out = []
    for i in range(16):
        v = block[i * 2] | (block[i * 2 + 1] << 8)
        r = (v >> 8) & 0x0F
        g = (v >> 4) & 0x0F
        b = v & 0x0F
        out.append((r * 17, g * 17, b * 17))
    return out


def looks_like_plate(block: bytes) -> bool:
    """A plate starts with the rounded top-left corner tile: 0x0F then a run of 0xFF."""
    if len(block) != PLATE_SIZE:
        return False
    return block[0] == 0x0F and block[1] == 0xFF and block[2] == 0xFF and block[3] == 0xFF


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--max-plates", type=int, default=64)
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out" / "title_menu_capture")
    args = ap.parse_args(argv)

    rom = bytes(load_rom(args.rom) if args.rom else load_rom(find_rom(ROOT)))
    tiles_dir = args.out_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    palettes = []
    for i in range(4):
        off = PALETTE_AREA[0] + i * 32
        blk = rom[BANK72 + off : BANK72 + off + 32]
        raw = [blk[j * 2] | (blk[j * 2 + 1] << 8) for j in range(16)]
        palettes.append(
            {
                "index": i,
                "abs": f"{BANK72 + off:06X}",
                "bank_off": f"72:{off:04X}",
                "entries_0rgb": [f"{v:04X}" for v in raw],
            }
        )

    plates = []
    for n in range(args.max_plates):
        off = PLATE_BASE + n * PLATE_SIZE
        abs_lo = BANK72 + off
        blk = rom[abs_lo : abs_lo + PLATE_SIZE]
        if not looks_like_plate(blk):
            break
        pal = wsc_palette(rom[BANK72 : BANK72 + 32])
        img = render(tiles_4bpp(blk), pal, PLATE_COLS, args.scale)
        png = tiles_dir / f"plate_{n:02d}.png"
        img.save(png)
        plates.append(
            {
                "index": n,
                "bank_off": f"72:{off:04X}-{off + PLATE_SIZE - 1:04X}",
                "abs": f"{abs_lo:06X}-{abs_lo + PLATE_SIZE - 1:06X}",
                "abs_lo": abs_lo,
                "tiles": PLATE_COLS * PLATE_ROWS,
                "png": str(png.relative_to(ROOT)),
            }
        )

    manifest = {
        "rom": str(args.rom or find_rom(ROOT)),
        "format": {
            "palettes": "4 x 16-colour WSC palettes, LE16 0x0RGB, 32 B each, 72:0000-007F",
            "plate": "80x16 px packed 4bpp, 8x8 tiles, 10 tiles/row, 20 tiles = 0x280 B",
            "plate_base": f"{BANK72 + PLATE_BASE:06X}",
            "plate_stride": PLATE_SIZE,
        },
        "palettes": palettes,
        "plate_count": len(plates),
        "plates": plates,
    }
    out = args.out_dir / "bank72_atlas.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"palettes: 4 at 72:0000-007F")
    for p in plates:
        print(f"plate {p['index']:2d}  {p['bank_off']}  abs {p['abs']}  -> {p['png']}")
    print(f"\n{len(plates)} plates -> {out}")

    # Contact sheet: every plate stacked, so the label/state sequence is one look.
    if plates:
        sheet_h = len(plates) * PLATE_ROWS * 8
        sheet = Image.new("RGB", (PLATE_COLS * 8, sheet_h))
        pal = wsc_palette(rom[BANK72 : BANK72 + 32])
        for p in plates:
            blk = rom[p["abs_lo"] : p["abs_lo"] + PLATE_SIZE]
            sheet.paste(render(tiles_4bpp(blk), pal, PLATE_COLS, 1), (0, p["index"] * 16))
        sheet = sheet.resize((sheet.width * args.scale, sheet.height * args.scale), Image.NEAREST)
        sheet_path = tiles_dir / "plate_contact_sheet.png"
        sheet.save(sheet_path)
        print(f"contact sheet -> {sheet_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
