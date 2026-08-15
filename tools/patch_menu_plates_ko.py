#!/usr/bin/env python3
r"""
Draw Korean labels into the bank-72 initial-menu plates.

This is the first patch built on the title-menu source that was proven by
measurement rather than assumed. The gate set in
docs/TITLE_MENU_FAILED_EXPERIMENT.md ("a minimal ROM where mutating one graphics
candidate changes one specific on-screen tile") passed for
``721020`` -> screen block (col 15, row 8), so the plates are the real source.

Method
------
1. :mod:`menu_plate_model` recovers each state's **background** -- the plate with
   its glyphs removed. It exists nowhere in ROM, but the label is drawn with two
   reserved palette indices, so label pixels can be excluded per plate and a mode
   over the 17-plate group reconstructs the gradient underneath.
2. The plate's label rectangle is cleared to that background. Clearing the whole
   rectangle (not just the two label indices) matters: the Japanese glyphs also
   put a handful of edge pixels in other tones, 0-11 per plate.
3. Korean syllables are rasterised with Galmuri11Bitmap Regular at native 16 px
   (the ending-credit face). Vector Galmuri11 11 px collapsed ㅖ in 계속; the
   bitmap grid keeps a 1 px vowel gap. Stroke uses the plate's own index, with
   a straight-down drop shadow so Hangul stems stay open.

Geometry, measured from the stock plates: glyph advance **10 px**, stroke cell
8x8 at **y = 4..11**, shadow at **(+1, +1)**. The Japanese labels sit on exactly
that grid (``ニューゲーム`` at x = 11, 21, 31, 41, 51, 61).

Nothing outside ``720080-72147F`` is touched and the WonderSwan checksum is
refreshed. Verify with::

    python tools/run_menu_candidates.py out/patch/menu_bisect/MENU_KO.wsc --overlays
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from menu_plate_model import (  # noqa: E402
    PLATE_H,
    PLATE_SIZE,
    PLATE_W,
    Atlas,
    render_grid,
    to_block,
)
from monoeye_rom import find_rom, load_rom, stock_base, update_ws_checksum  # noqa: E402

LABELS_JSON = ROOT / "data" / "menu_plate_labels_ko.json"
DEFAULT_OUT = ROOT / "out" / "patch" / "menu_bisect" / "MENU_KO.wsc"

FONT_DIR = ROOT / "assets" / "fonts"
#: Ending-credit face: Galmuri11Bitmap Regular at its native 16 px cell. A plate
#: is a free 80x16 bitmap, so unlike dialogue glyphs (fixed 8x8 at 40:0440) there
#: is no 8 px ceiling. The vector Galmuri11 11 px face collapsed ㅖ in 계속; the
#: bitmap grid keeps a 1 px vowel gap. Glyph ink is 11 px tall (bbox y=1..12).
DEFAULT_FONT = FONT_DIR / "galmuri_tmp" / "Galmuri11Bitmap-Regular-2.40.3.ttf"
DEFAULT_SIZE = 16
DEFAULT_TOP = 1          # ink y=2..13, shadow y=3..14; avoids the y=0/15 frame
DEFAULT_SPACING = 1
DEFAULT_SPACE_WIDTH = 4
#: The stock plates cast the drop shadow at (+1, +1). Measured on screen, that
#: offset fills the 1 px vertical gaps between Hangul stems, so an unselected
#: button reads as a dark blob with light lines through it. Straight down keeps
#: the stock contrast (light stroke over dark shadow) and stays legible.
DEFAULT_SHADOW_DELTA = (0, 1)

#: Reference syllable used to fix one shared vertical origin. Normalising each
#: glyph to its own ink box would make syllables with and without batchim sit at
#: different heights.
VREF = "갱"


class Rasteriser:
    def __init__(self, font_path: Path, size: int):
        from PIL import ImageFont

        if not font_path.exists():
            raise SystemExit(f"missing font: {font_path}")
        self.font = ImageFont.truetype(str(font_path), size=size)
        self.size = size
        self.path = font_path
        self._top = self._ink_top(VREF)

    def _ink_top(self, ch: str) -> int:
        from PIL import Image, ImageDraw

        img = Image.new("L", (self.size * 3, self.size * 3), 0)
        d = ImageDraw.Draw(img)
        return d.textbbox((0, 0), ch, font=self.font)[1]

    def advance(self, ch: str) -> int:
        return int(round(self.font.getlength(ch)))

    def glyph(self, ch: str) -> tuple[list[list[int]], int, int]:
        """Binary bitmap plus its (width, height)."""
        from PIL import Image, ImageDraw

        w = max(1, self.advance(ch))
        h = self.size + 3
        img = Image.new("L", (w + 2, h), 0)
        d = ImageDraw.Draw(img)
        d.text((0, -self._top), ch, fill=255, font=self.font)
        px = img.load()
        bits = [[1 if px[x, y] >= 128 else 0 for x in range(img.width)] for y in range(h)]
        return bits, img.width, h


def layout(text: str, ras: Rasteriser, spacing: int, space_width: int) -> list[tuple[str, int]]:
    """Place syllables centred in the plate; returns [(char, x)]."""
    items = []
    for ch in text:
        items.append((ch, space_width if ch == " " else ras.advance(ch) + spacing))
    if not items:
        return []
    # Ink width: drop the trailing letter spacing, add one column for the shadow.
    ink = sum(a for _, a in items) - (0 if text[-1] == " " else spacing) + 1
    x = (PLATE_W - ink) // 2
    placed = []
    for ch, adv in items:
        if ch != " ":
            placed.append((ch, x))
        x += adv
    return placed


def draw_label(
    atlas: Atlas,
    plate_index: int,
    text: str,
    ras: Rasteriser,
    top: int,
    spacing: int,
    space_width: int,
    shadow: bool,
    shadow_delta: tuple[int, int] | None = None,
) -> tuple[list[list[int]], dict]:
    plate = atlas.plates[plate_index]
    group = atlas.groups[plate.group]
    tm = atlas.text_model(plate_index)
    # Start from the recovered background: that clears the Japanese label,
    # including the handful of edge pixels it draws in other tones.
    grid = [row[:] for row in group.background]

    placed = layout(text, ras, spacing, space_width)
    if not placed:
        raise SystemExit(f"plate {plate_index}: empty label")
    if placed[0][1] < 0:
        raise SystemExit(
            f"plate {plate_index}: {text!r} needs more than {PLATE_W} px; shorten it"
        )

    dx, dy = shadow_delta or tm.shadow_delta
    stroke_px: set[tuple[int, int]] = set()
    for ch, gx in placed:
        bits, gw, gh = ras.glyph(ch)
        for yy in range(gh):
            for xx in range(gw):
                if bits[yy][xx]:
                    stroke_px.add((gx + xx, top + yy))
    shadow_px = ({(x + dx, y + dy) for x, y in stroke_px} - stroke_px) if shadow else set()

    def paintable(x: int, y: int) -> bool:
        # Never touch the frame: index 0 is outside the rounded corner, F is the
        # border. Painting either would deform the button silhouette.
        return (
            0 <= x < PLATE_W
            and 0 <= y < PLATE_H
            and grid[y][x] not in (0x0, 0xF)
        )

    clipped = 0
    for x, y in sorted(shadow_px):
        if paintable(x, y):
            grid[y][x] = group.shadow
    for x, y in sorted(stroke_px):
        if paintable(x, y):
            grid[y][x] = group.stroke
        else:
            clipped += 1

    info = {
        "plate": plate_index,
        "group": plate.group,
        "text": text,
        "font": ras.path.name,
        "size": ras.size,
        "top": top,
        "shadow_delta": [dx, dy] if shadow else None,
        "glyph_x": [gx for _, gx in placed],
        "stroke_px": len(stroke_px),
        "shadow_px": len(shadow_px),
        "clipped_px": clipped,
        "stroke_index": f"{group.stroke:X}",
        "shadow_index": f"{group.shadow:X}",
    }
    return grid, info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=None, help="base ROM (default: stock)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--labels", type=Path, default=LABELS_JSON)
    ap.add_argument(
        "--only-boot-plates",
        action="store_true",
        help="patch only the plate each label actually draws at boot",
    )
    ap.add_argument("--preview-dir", type=Path, default=ROOT / "out" / "title_menu_capture" / "ko_preview")
    ap.add_argument("--dry-run", action="store_true", help="previews only, no ROM written")
    ap.add_argument("--font", type=Path, default=DEFAULT_FONT)
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--spacing", type=int, default=DEFAULT_SPACING)
    ap.add_argument("--space-width", type=int, default=DEFAULT_SPACE_WIDTH)
    ap.add_argument("--no-shadow", action="store_true")
    ap.add_argument(
        "--shadow-delta",
        default=",".join(str(v) for v in DEFAULT_SHADOW_DELTA),
        metavar="DX,DY",
        help="drop-shadow offset; 'stock' uses the (1,1) learned from the plate, "
        f"default {DEFAULT_SHADOW_DELTA} avoids filling the 1 px gaps in dense Hangul",
    )
    args = ap.parse_args(argv)
    delta = None
    if args.shadow_delta and args.shadow_delta != "stock":
        dxs, dys = args.shadow_delta.split(",")
        delta = (int(dxs), int(dys))

    src = args.rom or find_rom(ROOT)
    rom = bytearray(load_rom(src))
    # Atlas addresses are stock-relative. The 16 MiB tip keeps the stock body at
    # 0x800000, so index through the stock slice and write back with the offset.
    base = stock_base(rom)
    atlas = Atlas(bytes(rom[base:]))
    if base:
        print(f"stock base: {base:06X} (expanded ROM)")
    spec = json.loads(args.labels.read_text(encoding="utf-8"))
    args.preview_dir.mkdir(parents=True, exist_ok=True)
    ras = Rasteriser(args.font, args.size)
    print(f"font: {args.font.name} @ {args.size}px  top=y{args.top}  shadow={not args.no_shadow}")

    report = {
        "base": str(src),
        "out": str(args.out),
        "font": str(args.font),
        "size": args.size,
        "plates": [],
    }
    for entry in spec["labels"]:
        if args.only_boot_plates:
            # Labels that never appear on the initial menu have no boot plate.
            if "drawn_at_boot" not in entry:
                continue
            targets = [entry["drawn_at_boot"]]
        else:
            targets = list(entry["plates"])
        for pi in targets:
            grid, info = draw_label(
                atlas,
                pi,
                entry["ko"],
                ras,
                args.top,
                args.spacing,
                args.space_width,
                not args.no_shadow,
                delta,
            )
            info["jp"] = entry["jp"]
            lo = base + atlas.plates[pi].abs_lo
            block = to_block(grid)
            before = bytes(rom[lo : lo + PLATE_SIZE])
            info["abs"] = f"{lo:06X}-{lo + PLATE_SIZE - 1:06X}"
            info["bytes_changed"] = sum(1 for a, b in zip(before, block) if a != b)
            rom[lo : lo + PLATE_SIZE] = block
            report["plates"].append(info)
            render_grid(grid, atlas.palette, 5).save(args.preview_dir / f"plate_{pi:02d}_ko.png")
            print(
                f"plate {pi:2d} ({entry['jp']} -> {entry['ko']}) {info['abs']} "
                f"group {info['group']} stroke {info['stroke_index']} "
                f"shadow {info['shadow_index']} x={info['glyph_x']} "
                f"ink={info['stroke_px']}px clipped={info['clipped_px']} "
                f"{info['bytes_changed']} B changed"
            )

    touched = [p["abs"] for p in report["plates"]]
    print(f"\nplates patched: {len(touched)}")
    print(f"previews -> {args.preview_dir}")

    if args.dry_run:
        print("dry run: no ROM written")
        return 0

    checksum = update_ws_checksum(rom)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(bytes(rom))
    report["checksum"] = f"{checksum:04X}" if isinstance(checksum, int) else None
    rep = args.out.with_suffix(".json")
    rep.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rom -> {args.out}  (checksum {report['checksum']})")
    print(f"report -> {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
