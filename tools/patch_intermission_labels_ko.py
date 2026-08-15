#!/usr/bin/env python3
r"""
Draw Korean into the intermission menu labels (bank 54 overlay tile atlas).

How the target was established
------------------------------
1. A hand-made savestate puts the intermission on screen; ``run_state_capture.py``
   grabs it at native 224x144, deterministic across runs.
2. ``find_screen_tile_in_rom.py --match overlay`` maps screen blocks to ROM tiles.
   Overlay matching is required because the labels are drawn on a layer where
   palette index 0 is transparent, so only a tile's non-zero pixels are constrained.
3. ``resolve_tilemap.py`` removes the residual ambiguity: a screen row's tiles are
   uploaded as a contiguous run, so within a row the true offsets satisfy
   ``base + col*0x20``.
4. ``data/intermission_labels_ko.json`` says which 2x2 cells hold which label; the
   coordinates were read off the capture with an 8 px grid.

Only the four tile addresses per character are needed -- the tilemap keeps pointing
at the same tiles, so a rewritten character follows to every screen that uses it.

Style: the original glyphs are a fill colour with a darker outline over transparent.
This measures both indices from the tile being replaced rather than assuming them,
so a patched character keeps the label's own colours.

**Not verified in game.** A savestate restores VRAM, so tiles already uploaded do
not change until the game re-uploads them; confirming this patch on screen needs a
fresh entry into the intermission, not a state load.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from monoeye_rom import find_rom, load_rom, stock_base, update_ws_checksum  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402

LABELS = ROOT / "data" / "intermission_labels_ko.json"
RESOLVED = ROOT / "out" / "title_menu_capture" / "intermission_overlay_resolved.json"
DEFAULT_OUT = ROOT / "out" / "patch" / "menu_bisect" / "INTER_KO.wsc"
FONT = ROOT / "assets" / "fonts" / "galmuri_tmp" / "Galmuri11.ttf"

CELL = 16
TILE_BYTES = 32
#: Reference syllable giving one shared vertical origin, as in patch_menu_plates_ko.
VREF = "갱"


STOCK_SIZE = 0x800000


def load_resolved(path: Path) -> dict[tuple[int, int], int]:
    """Offsets normalised to stock-relative addresses.

    The map is usually resolved against the 16 MiB tip, whose stock body sits at
    0x800000, so the recorded offsets look like ``D4xxxx``. Normalising here lets
    the same map drive either the stock 8 MiB ROM or the tip.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for r, info in data["rows"].items():
        for c, off in info["resolved"].items():
            v = int(off, 16)
            if v >= STOCK_SIZE:
                v -= STOCK_SIZE
            out[(int(c), int(r))] = v
    return out


def cell_tiles(resolved, col: int, row: int) -> list[int] | None:
    quad = [(col, row), (col + 1, row), (col, row + 1), (col + 1, row + 1)]
    if all(q in resolved for q in quad):
        return [resolved[q] for q in quad]
    return None


def read_cell(rom: bytes, offs: list[int]) -> list[list[int]]:
    """4 tiles -> 16x16 index grid."""
    g = [[0] * CELL for _ in range(CELL)]
    for k, (ox, oy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
        t = tiles_4bpp(rom[offs[k] : offs[k] + TILE_BYTES])[0]
        for y in range(8):
            for x in range(8):
                g[oy + y][ox + x] = t[y][x]
    return g


def write_cell(rom: bytearray, offs: list[int], grid: list[list[int]]) -> int:
    changed = 0
    for k, (ox, oy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
        for y in range(8):
            for x in range(4):
                hi = grid[oy + y][ox + x * 2] & 0x0F
                lo = grid[oy + y][ox + x * 2 + 1] & 0x0F
                b = (hi << 4) | lo
                p = offs[k] + y * 4 + x
                if rom[p] != b:
                    changed += 1
                rom[p] = b
    return changed


def measure_style(grid: list[list[int]], scr: list[tuple[int, int, int]] | None) -> tuple[int, int]:
    """(fill, outline) palette indices of an original glyph.

    Geometry does not decide this reliably. "Most common index is the fill" is wrong
    because a 1 px ring around thin kanji strokes often has more pixels than the
    strokes. "The index touching transparency is the outline" flips on several of the
    real cells too. What does decide it is the **screen colour**: every intermission
    label is a light fill with a dark outline, and the capture gives the index ->
    colour mapping directly, so the brighter of the two ink indices is the fill.

    Without a capture, fall back to the geometric guess, but the caller should
    prefer the measured answer.
    """
    counts = collections.Counter(v for row in grid for v in row if v != 0)
    if not counts:
        raise ValueError("cell has no ink")
    if len(counts) == 1:
        only = next(iter(counts))
        return only, only
    solid = [v for v, n in counts.items() if n >= 3] or list(counts)

    if scr is not None:
        flat = [v for row in grid for v in row]
        colour: dict[int, tuple[int, int, int]] = {}
        for rv, sv in zip(flat, scr):
            if rv != 0:
                colour.setdefault(rv, sv)
        known = [v for v in solid if v in colour]
        if len(known) >= 2:
            def luma(v: int) -> float:
                r, g, b = colour[v]
                return 0.299 * r + 0.587 * g + 0.114 * b

            fill = max(known, key=luma)
            outline = min(known, key=luma)
            return fill, outline

    touching: collections.Counter = collections.Counter()
    for y in range(CELL):
        for x in range(CELL):
            v = grid[y][x]
            if v == 0:
                continue
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if not (0 <= ny < CELL and 0 <= nx < CELL) or grid[ny][nx] == 0:
                    touching[v] += 1
                    break
    frac = {v: touching[v] / counts[v] for v in solid}
    outline = max(solid, key=lambda v: (frac[v], counts[v]))
    fill = min(solid, key=lambda v: (frac[v], -counts[v]))
    if fill == outline:
        fill = min(solid, key=lambda v: counts[v])
    return fill, outline


def write_tile(rom: bytearray, off: int, cell: list[list[int]]) -> int:
    changed = 0
    for y in range(8):
        for x in range(4):
            b = ((cell[y][x * 2] & 0x0F) << 4) | (cell[y][x * 2 + 1] & 0x0F)
            p = off + y * 4 + x
            if rom[p] != b:
                changed += 1
            rom[p] = b
    return changed


def overlay_strip_ok(strip: list[list[int]], scr: list[tuple[int, int, int]], width: int) -> bool:
    """Overlay match over a whole label strip (see :func:`overlay_ok`)."""
    flat = [v for row in strip for v in row]
    fwd: dict = {}
    used: set = set()
    for rv, sv in zip(flat, scr):
        if rv == 0:
            continue
        prev = fwd.get(rv)
        if prev is None:
            if sv in used:
                return False
            fwd[rv] = sv
            used.add(sv)
        elif prev != sv:
            return False
    return len(fwd) >= 2


def measure_style_strip(
    strip: list[list[int]], scr: list[tuple[int, int, int]] | None, width: int
) -> tuple[int, int]:
    """(fill, outline) for a whole strip, decided by on-screen brightness."""
    counts = collections.Counter(v for row in strip for v in row if v != 0)
    if not counts:
        raise ValueError("strip has no ink")
    if len(counts) == 1:
        only = next(iter(counts))
        return only, only
    solid = [v for v, n in counts.items() if n >= 3] or list(counts)
    if scr is not None:
        flat = [v for row in strip for v in row]
        colour: dict[int, tuple[int, int, int]] = {}
        for rv, sv in zip(flat, scr):
            if rv != 0:
                colour.setdefault(rv, sv)
        known = [v for v in solid if v in colour]
        if len(known) >= 2:
            def luma(v: int) -> float:
                r, g, b = colour[v]
                return 0.299 * r + 0.587 * g + 0.114 * b

            return max(known, key=luma), min(known, key=luma)
    ranked = sorted(solid, key=lambda v: counts[v], reverse=True)
    return ranked[0], ranked[1]


def render_strip_pair(before, after, width: int, scale: int) -> Image.Image:
    img = Image.new("RGB", (width, CELL * 2 + 2), (255, 0, 255))
    px = img.load()
    for i, g in enumerate((before, after)):
        for y in range(CELL):
            for x in range(width):
                px[x, i * (CELL + 2) + y] = GREYS_16[g[y][x]]
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def overlay_ok(cell: list[list[int]], scr: list[tuple[int, int, int]]) -> bool:
    """Does this 16x16 ROM cell explain the 16x16 screen crop?

    Same rule as the overlay matcher, applied to all four tiles at once: index 0 is
    transparent and unconstrained, every other index must map to exactly one screen
    colour and two indices may not share one. Checking the whole character is far
    stricter than checking single tiles, which is what stops a tile that merely has
    the same ink structure from being written over.
    """
    flat = [v for row in cell for v in row]
    fwd: dict[int, tuple[int, int, int]] = {}
    used: set = set()
    for rv, sv in zip(flat, scr):
        if rv == 0:
            continue
        prev = fwd.get(rv)
        if prev is None:
            if sv in used:
                return False
            fwd[rv] = sv
            used.add(sv)
        elif prev != sv:
            return False
    return len(fwd) >= 2


class Rasteriser:
    def __init__(self, path: Path, size: int):
        if not path.exists():
            raise SystemExit(f"missing font: {path}")
        self.font = ImageFont.truetype(str(path), size=size)
        self.size = size
        self.path = path
        img = Image.new("L", (size * 3, size * 3), 0)
        d = ImageDraw.Draw(img)
        box = d.textbbox((0, 0), VREF, font=self.font)
        self._top = box[1]

    def bits(self, ch: str) -> list[list[int]]:
        w = max(1, int(round(self.font.getlength(ch))))
        img = Image.new("L", (w + 2, self.size + 3), 0)
        d = ImageDraw.Draw(img)
        d.text((0, -self._top), ch, fill=255, font=self.font)
        px = img.load()
        return [[1 if px[x, y] >= 128 else 0 for x in range(img.width)] for y in range(img.height)]


def draw_strip(
    text: str, width: int, ras: Rasteriser, fill: int, outline: int, spacing: int
) -> list[list[int]]:
    """``width`` x 16 index grid: text centred, glyph in ``fill``, 1 px ``outline``.

    Rendering the whole label at once is what makes this work. The Japanese
    characters are not aligned to the 8 px tile grid, so there is no per-character
    tile window to replace; the strip has no alignment to get wrong.
    """
    glyphs = [(ch, ras.bits(ch)) for ch in text if ch != " "]
    if not glyphs:
        return [[0] * width for _ in range(CELL)]
    widths = [len(b[0]) for _, b in glyphs]
    total = sum(widths) + spacing * (len(glyphs) - 1)
    x0 = (width - total) // 2
    gh = len(glyphs[0][1])
    y0 = (CELL - gh) // 2 + 1

    ink = set()
    x = x0
    for (_, bits), w in zip(glyphs, widths):
        for y in range(gh):
            for xx in range(w):
                if bits[y][xx]:
                    ink.add((x + xx, y0 + y))
        x += w + spacing

    ring = set()
    for (px, py) in ink:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                p = (px + dx, py + dy)
                if p not in ink:
                    ring.add(p)
    grid = [[0] * width for _ in range(CELL)]
    for (px, py) in ring:
        if 0 <= px < width and 0 <= py < CELL:
            grid[py][px] = outline
    for (px, py) in ink:
        if 0 <= px < width and 0 <= py < CELL:
            grid[py][px] = fill
    return grid


def render_pair(before: list[list[int]], after: list[list[int]], scale: int) -> Image.Image:
    img = Image.new("RGB", (CELL * 2 + 2, CELL), (255, 0, 255))
    px = img.load()
    for i, g in enumerate((before, after)):
        for y in range(CELL):
            for x in range(CELL):
                px[i * (CELL + 2) + x, y] = GREYS_16[g[y][x]]
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--resolved", type=Path, default=RESOLVED)
    ap.add_argument("--font", type=Path, default=FONT)
    ap.add_argument("--size", type=int, default=13)
    ap.add_argument("--letter-spacing", type=int, default=1)
    ap.add_argument(
        "--max-wipe-ink",
        type=int,
        default=64,
        help="a tile in the surrounding ring is only cleared when it holds at most "
        "this many ink pixels, i.e. a sliver of this label rather than a neighbouring "
        "label that happens to share the same two colours",
    )
    ap.add_argument("--scale", type=int, default=5)
    ap.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "out" / "title_menu_capture" / "state" / "intermission_r1_s00.png",
        help="native 224x144 intermission capture used to validate every cell",
    )
    ap.add_argument("--no-validate", action="store_true", help="skip the capture check (not advised)")
    ap.add_argument(
        "--min-transparent",
        type=int,
        default=48,
        help="of the 256 px in a cell, how many must be index 0 for it to count as "
        "a pure overlay glyph cell. Dense kanji legitimately fill most of a cell "
        "(補 leaves only 79 px transparent), so this is a loose sanity bound; the "
        "real discriminator is --max-foreign",
    )
    ap.add_argument(
        "--max-foreign",
        type=int,
        default=8,
        help="how many px may use an index other than 0 / fill / outline",
    )
    ap.add_argument(
        "--scan-rows",
        default=None,
        metavar="R1,R2,...",
        help="instead of patching, list which columns of these screen rows hold a "
        "valid 16x16 glyph cell. Use this to fix the column numbers in the label "
        "spec rather than reading them off a screenshot by hand",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--tile-list",
        type=Path,
        default=ROOT / "data" / "intermission_glyph_tiles.json",
        help="where to publish the written tile addresses for the stock-invasion gate",
    )
    ap.add_argument("--preview-dir", type=Path, default=ROOT / "out" / "title_menu_capture" / "inter_preview")
    args = ap.parse_args(argv)

    rom = bytearray(load_rom(args.rom))
    base = stock_base(rom)
    resolved = load_resolved(args.resolved)
    spec = json.loads(args.labels.read_text(encoding="utf-8"))
    ras = Rasteriser(args.font, args.size)
    args.preview_dir.mkdir(parents=True, exist_ok=True)

    cap = None
    if not args.no_validate:
        if not args.capture.exists():
            raise SystemExit(f"missing capture: {args.capture} (use --no-validate to skip)")
        cap = Image.open(args.capture).convert("RGB")
        if (cap.width, cap.height) != (224, 144):
            raise SystemExit(
                f"{args.capture} is {cap.width}x{cap.height}; a native 224x144 capture is required"
            )

    print(f"rom  : {args.rom.name}  stock base {base:06X}")
    print(f"font : {args.font.name} @ {args.size}px")

    def cell_ok(col: int, row: int, offs: list[int]) -> tuple[bool, str]:
        abs_offs = [base + o for o in offs]
        grid = read_cell(bytes(rom), abs_offs)
        if cap is not None:
            px = cap.load()
            scr = [px[col * 8 + x, row * 8 + y] for y in range(CELL) for x in range(CELL)]
            if not overlay_ok(grid, scr):
                return False, "capture mismatch"
        flat = [v for r_ in grid for v in r_]
        transparent = sum(1 for v in flat if v == 0)
        if transparent < args.min_transparent:
            return False, f"transparent {transparent}/256"
        try:
            f, o = measure_style(grid, scr if cap is not None else None)
        except ValueError:
            return False, "no ink"
        foreign = sum(1 for v in flat if v not in (0, f, o))
        if foreign > args.max_foreign:
            return False, f"foreign {foreign}"
        return True, f"{f:X}/{o:X}"

    if args.scan_rows:
        for row in (int(v) for v in args.scan_rows.split(",")):
            good = []
            for col in range(27):
                offs = cell_tiles(resolved, col, row)
                if offs is None:
                    continue
                ok, why = cell_ok(col, row, offs)
                if ok:
                    good.append(f"c{col}({why})")
            print(f"row {row:2d} valid cells: " + (" ".join(good) if good else "(none)"))

            # Ink profile over the band. A validated 2x2 window only proves the
            # tile mapping, not that the window is one character -- a window
            # straddling two characters validates just as well. Character starts
            # have to come from where the ink actually is: each glyph is 16 px, so
            # ink runs in the profile below mark them.
            prof = []
            for col in range(28):
                marks = 0
                unresolved = False
                for r2 in (row, row + 1):
                    off = resolved.get((col, r2))
                    if off is None:
                        unresolved = True
                        break
                    t = tiles_4bpp(rom[base + off : base + off + TILE_BYTES])[0]
                    flat = [v for r_ in t for v in r_]
                    ink = [v for v in flat if v != 0]
                    # A glyph half: mostly transparent, at most two ink colours.
                    if ink and len(flat) - len(ink) >= 16 and len(set(ink)) <= 2:
                        marks += 1
                prof.append("?" if unresolved else "G" if marks == 2 else "-" if marks else ".")
            print("        cols  : " + "".join(f"{c % 10}" for c in range(28)))
            print("        glyph : " + "".join(prof))
        return 0
    report = {"rom": str(args.rom), "font": str(args.font), "size": args.size, "labels": []}
    total_changed = 0
    skipped = []

    for entry in spec["labels"]:
        ko = entry.get("ko")
        row, c_from, c_to = entry["row"], entry["from"], entry["to"]
        if not ko:
            skipped.append((entry["jp"], "no ko text"))
            continue

        cols = list(range(c_from, c_to + 1))
        width = len(cols) * 8
        info = {
            "jp": entry["jp"],
            "ko": ko,
            "row": row,
            "cols": [c_from, c_to],
            "tiles": [],
        }

        # Pass 1: gather and validate every tile of the strip.
        tiles: list[tuple[int, int, int, list[list[int]]]] = []  # col, row, abs, 8x8
        styles: list[tuple[int, int]] = []
        reject = None
        for col in cols:
            for r2 in (row, row + 1):
                off = resolved.get((col, r2))
                if off is None:
                    reject = f"tile col {col} row {r2} not resolved"
                    break
                abs_off = base + off
                t = tiles_4bpp(bytes(rom[abs_off : abs_off + TILE_BYTES]))[0]
                tiles.append((col, r2, abs_off, t))
            if reject:
                break
        if reject:
            skipped.append((entry["jp"], reject))
            continue

        # The strip as one 16-row grid, so validation and styling see the label whole.
        before = [[0] * width for _ in range(CELL)]
        for col, r2, _abs, t in tiles:
            ox, oy = (col - c_from) * 8, (r2 - row) * 8
            for y in range(8):
                for x in range(8):
                    before[oy + y][ox + x] = t[y][x]

        scr = None
        if cap is not None:
            px = cap.load()
            scr = [
                px[c_from * 8 + x, row * 8 + y] for y in range(CELL) for x in range(width)
            ]
            if not overlay_strip_ok(before, scr, width):
                skipped.append((entry["jp"], "strip does not match the capture"))
                continue

        try:
            fill, outline = measure_style_strip(before, scr, width)
        except ValueError:
            skipped.append((entry["jp"], "strip has no ink"))
            continue

        flat = [v for r_ in before for v in r_]
        foreign = sum(1 for v in flat if v not in (0, fill, outline))
        transparent = sum(1 for v in flat if v == 0)
        if transparent < args.min_transparent:
            skipped.append(
                (entry["jp"], f"strip is too solid to be an overlay (transparent {transparent})")
            )
            continue

        drawn = draw_strip(ko, width, ras, fill, outline, args.letter_spacing)
        # Some strips also carry plate artwork -- ``システム`` has 26 such pixels and
        # ``図鑑`` 32. Those pixels are kept exactly as they are, so the button frame
        # survives and only the label's own ink is replaced. That is why a strip is
        # not rejected for containing them.
        after = [
            [
                before[y][x] if before[y][x] not in (0, fill, outline) else drawn[y][x]
                for x in range(width)
            ]
            for y in range(CELL)
        ]
        changed = 0
        if not args.dry_run:
            for col, r2, abs_off, _t in tiles:
                ox, oy = (col - c_from) * 8, (r2 - row) * 8
                cell = [[after[oy + y][ox + x] for x in range(8)] for y in range(8)]
                changed += write_tile(rom, abs_off, cell)

        # The Japanese glyph bleeds past the strip in both axes -- the characters are
        # not aligned to the 8 px grid horizontally *or* vertically, so 作戦 and 編成
        # leave a sliver to the right and a mark one row above. Widening the strip is
        # not an option because the extra tiles frequently fail to resolve, so instead
        # the label's own ink is erased in the surrounding one-tile ring. Only tiles
        # whose ink is entirely the label's two colours are touched, and only those
        # pixels are cleared, so plate artwork is never disturbed.
        wiped: list[str] = []
        wiped_tiles: list[int] = []
        ring = (
            [
                (col, r2)
                for col in range(c_from - 1, c_to + 2)
                for r2 in range(row - 1, row + 3)
                if not (c_from <= col <= c_to and row <= r2 <= row + 1)
            ]
            if entry.get("wipe", True)
            else []
        )
        for col, r2 in ring:
            if 0 <= col <= 27 and 0 <= r2 <= 17:
                off = resolved.get((col, r2))
                if off is None:
                    continue
                abs_off = base + off
                t = tiles_4bpp(bytes(rom[abs_off : abs_off + TILE_BYTES]))[0]
                # Only tiles that are *nothing but* label ink may be wiped. Relaxing
                # this to "clear the label colours wherever they appear" was tried and
                # is wrong: the plate frame uses the same two indices, so the ring wipe
                # ate the light-blue border around 編成 and the bar above the minimap.
                # The cost of the strict rule is a couple of 1-2 px marks (next to 図鑑).
                ink = {v for r_ in t for v in r_ if v != 0}
                if not ink or not ink <= {fill, outline}:
                    continue
                # A tile full of label ink is a neighbouring label sharing the same two
                # colours, not a sliver of this one -- wiping it ate the "MS" above 開発.
                if len(ink) and sum(1 for r_ in t for v in r_ if v != 0) > args.max_wipe_ink:
                    continue
                cell = [[0 if v in (fill, outline) else v for v in r_] for r_ in t]
                n = 0 if args.dry_run else write_tile(rom, abs_off, cell)
                changed += n
                wiped.append(f"{col}/{r2}")
                wiped_tiles.append(abs_off)
        if wiped:
            info["ink_wiped_outside_strip"] = wiped
        total_changed += changed
        info["fill"] = f"{fill:X}"
        info["outline"] = f"{outline:X}"
        info["bytes_changed"] = changed
        info["tiles"] = [f"{abs_off:06X}" for _c, _r, abs_off, _t in tiles] + [
            f"{o:06X}" for o in wiped_tiles
        ]
        report["labels"].append(info)

        name = "".join(c if c.isalnum() else "_" for c in entry["jp"])[:20]
        render_strip_pair(before, after, width, args.scale).save(
            args.preview_dir / f"{row:02d}_{name}.png"
        )
        print(
            f"  row {row:2d} cols {c_from:2d}-{c_to:2d} {entry['jp']} -> {ko}  "
            f"{fill:X}/{outline:X}  {len(tiles)} tiles  {changed} B"
        )

    if skipped:
        print("\nskipped:")
        for jp, why in skipped:
            print(f"  {jp}: {why}")

    print(f"\nbytes changed: {total_changed}")
    print(f"previews -> {args.preview_dir}")
    if args.dry_run:
        print("dry run: no ROM written")
        return 0

    checksum = update_ws_checksum(rom)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(bytes(rom))
    report["checksum"] = f"{checksum:04X}" if isinstance(checksum, int) else None
    report["bytes_changed"] = total_changed
    report["skipped"] = [{"jp": jp, "why": why} for jp, why in skipped]

    # Publish the exact tiles written, stock-relative, so the stock-invasion gate
    # can classify them as intended instead of flagging bank 54 wholesale. Bank 54
    # is shared UI data, so an address list is the right granularity here -- a band
    # would fence off far more than this patch touches.
    approved = sorted(
        {int(t, 16) - base for lab in report["labels"] for t in lab["tiles"]}
    )
    args.tile_list.parent.mkdir(parents=True, exist_ok=True)
    args.tile_list.write_text(
        json.dumps(
            {
                "_note": "Stock-relative 32-byte tile addresses written by "
                "tools/patch_intermission_labels_ko.py. Read by "
                "tools/diff_stock_3way.py to classify them as intended.",
                "tile_bytes": TILE_BYTES,
                "tiles": [f"{t:06X}" for t in approved],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"approved tiles ({len(approved)}) -> {args.tile_list}")
    args.out.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"rom -> {args.out} (checksum {report['checksum']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
