#!/usr/bin/env python3
r"""
Model of a bank-72 menu plate: pixel grid, state groups, background, label mask.

Replacing the Japanese label with Korean needs the *background* -- the plate with
its glyphs removed -- and no such plate exists in ROM. Two things make it
recoverable.

1. **State groups.** 29 plates carry 9 labels in 4 highlight states. Plates in one
   state share a single background, found from a signature over the two leftmost
   tiles (frame + gradient, never touched by a label) rather than assumed from
   plate order, because the per-label variant counts are uneven (3,4,4,3,3,3,3,3,3).

2. **The label uses two reserved palette indices.** The gradient body is drawn
   with mid-tones; the glyphs use the brightest index (``E``) for the stroke and
   one dark index for the drop shadow, and those two never appear in the
   background. So a pixel can be classified as label/background *by value*, per
   plate, with no reference image.

A naive per-pixel mode over a group does **not** work: many labels share glyphs
(``モード`` appears in four of them), so the mode keeps ghost text. Restricting the
mode to samples that are not label pixels removes it.
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "out" / "title_menu_capture" / "bank72_atlas.json"

PLATE_W, PLATE_H = 80, 16
PLATE_COLS = 10
PLATE_SIZE = 0x280
TILE_BYTES = 32

#: Frame colours. Present in every plate, never part of the gradient body.
FRAME_INDICES = frozenset({0x0, 0xF})

Grid = list[list[int]]


# --------------------------------------------------------------------------
# tile <-> pixel grid
# --------------------------------------------------------------------------
def to_grid(block: bytes) -> Grid:
    """640 packed-4bpp bytes (20 tiles, 10 per row) -> 80x16 index grid."""
    g = [[0] * PLATE_W for _ in range(PLATE_H)]
    for t in range(len(block) // TILE_BYTES):
        ox, oy = (t % PLATE_COLS) * 8, (t // PLATE_COLS) * 8
        blk = block[t * TILE_BYTES : (t + 1) * TILE_BYTES]
        for y in range(8):
            for x in range(4):
                b = blk[y * 4 + x]
                g[oy + y][ox + x * 2] = b >> 4
                g[oy + y][ox + x * 2 + 1] = b & 0x0F
    return g


def to_block(g: Grid) -> bytes:
    """80x16 index grid -> 640 packed-4bpp bytes."""
    out = bytearray(PLATE_SIZE)
    for t in range(PLATE_SIZE // TILE_BYTES):
        ox, oy = (t % PLATE_COLS) * 8, (t // PLATE_COLS) * 8
        for y in range(8):
            for x in range(4):
                hi = g[oy + y][ox + x * 2] & 0x0F
                lo = g[oy + y][ox + x * 2 + 1] & 0x0F
                out[t * TILE_BYTES + y * 4 + x] = (hi << 4) | lo
    return bytes(out)


# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------
def wsc_palette(block: bytes) -> list[tuple[int, int, int]]:
    out = []
    for i in range(16):
        v = block[i * 2] | (block[i * 2 + 1] << 8)
        out.append((((v >> 8) & 0xF) * 17, ((v >> 4) & 0xF) * 17, (v & 0xF) * 17))
    return out


def luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def luma_rank(pal) -> dict[int, int]:
    """index -> rank, dark to bright. The gradient is not index-ordered."""
    return {v: i for i, v in enumerate(sorted(range(16), key=lambda i: luma(pal[i])))}


# --------------------------------------------------------------------------
@dataclass
class Group:
    name: str
    members: list[int]
    stroke: int
    shadow: int
    background: Grid
    #: pixels no member could sample because every member has a glyph there
    holes: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Plate:
    index: int
    abs_lo: int
    grid: Grid
    group: str = ""


@dataclass
class TextModel:
    plate: int
    group: str
    mask: list[list[bool]]
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 inclusive
    stroke: int
    shadow: int
    shadow_delta: tuple[int, int]


class Atlas:
    def __init__(self, rom: bytes, manifest: dict | None = None):
        man = manifest or json.loads(ATLAS.read_text(encoding="utf-8"))
        self.rom = rom
        self.manifest = man
        self.palette = wsc_palette(rom[0x720000:0x720020])
        self.rank = luma_rank(self.palette)
        self.plates: dict[int, Plate] = {}
        for p in man["plates"]:
            lo = p["abs_lo"]
            self.plates[p["index"]] = Plate(p["index"], lo, to_grid(rom[lo : lo + PLATE_SIZE]))
        self.groups: dict[str, Group] = {}
        self._build_groups()

    # ----------------------------------------------------------------------
    def _signature(self, p: Plate) -> bytes:
        lo = p.abs_lo
        return self.rom[lo : lo + TILE_BYTES] + self.rom[lo + 10 * TILE_BYTES : lo + 11 * TILE_BYTES]

    def _text_indices(self, members: list[int]) -> tuple[int, int]:
        """Stroke = brightest non-frame index in use; shadow = the index most often
        directly under a stroke pixel."""
        used = collections.Counter()
        for m in members:
            for row in self.plates[m].grid:
                used.update(row)
        cands = [v for v in used if v not in FRAME_INDICES]
        stroke = max(cands, key=lambda v: self.rank[v])

        under = collections.Counter()
        for m in members:
            g = self.plates[m].grid
            for y in range(PLATE_H - 1):
                for x in range(PLATE_W):
                    if g[y][x] == stroke and g[y + 1][x] not in FRAME_INDICES | {stroke}:
                        under[g[y + 1][x]] += 1
        shadow = under.most_common(1)[0][0] if under else stroke
        return stroke, shadow

    def _build_groups(self) -> None:
        buckets: dict[bytes, list[int]] = collections.defaultdict(list)
        for i, p in self.plates.items():
            buckets[self._signature(p)].append(i)
        for n, (_, members) in enumerate(
            sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[1][0]))
        ):
            name = chr(ord("A") + n)
            stroke, shadow = self._text_indices(members)
            bg = [[0] * PLATE_W for _ in range(PLATE_H)]
            holes = []
            for y in range(PLATE_H):
                for x in range(PLATE_W):
                    samples = [
                        self.plates[m].grid[y][x]
                        for m in members
                        if self.plates[m].grid[y][x] not in (stroke, shadow)
                    ]
                    if samples:
                        bg[y][x] = collections.Counter(samples).most_common(1)[0][0]
                    else:
                        holes.append((x, y))
            self.groups[name] = Group(name, members, stroke, shadow, bg, holes)
            for m in members:
                self.plates[m].group = name
        self._unify_backgrounds()

    # ----------------------------------------------------------------------
    def _learn_remap(self, src: Group, dst: Group) -> dict[int, int]:
        """Palette-index map taking src's background to dst's.

        The four signature groups are not four different artworks. They are one
        gradient drawn with two index sets (normal / highlighted); the extra two
        groups exist only because ``コンティニュー`` is long enough to reach into the
        signature tiles. Measured on the stock ROM this map is exact:
        A->C is the identity, and A->B equals A->D.
        """
        skip = set(src.holes) | set(dst.holes)
        votes: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
        for y in range(PLATE_H):
            for x in range(PLATE_W):
                if (x, y) in skip:
                    continue
                votes[src.background[y][x]][dst.background[y][x]] += 1
        return {k: c.most_common(1)[0][0] for k, c in votes.items()}

    def _unify_backgrounds(self) -> None:
        """One canonical background, every group derived from it by remap.

        Recovering each group independently leaves the label area unresolved for
        small groups -- all three ``コンティニュー`` plates carry the same glyphs, so no
        member can sample the pixels underneath (226 of 1280). Deriving them from
        the 17-member group removes the guesswork.
        """
        canonical = min(self.groups.values(), key=lambda g: len(g.holes))
        self.canonical = canonical.name

        # Patch the canonical's own holes from any group that could sample them.
        remaining = []
        for x, y in canonical.holes:
            filled = False
            for g in self.groups.values():
                if g is canonical or (x, y) in set(g.holes):
                    continue
                inv = {v: k for k, v in self._learn_remap(canonical, g).items()}
                val = inv.get(g.background[y][x])
                if val is not None:
                    canonical.background[y][x] = val
                    filled = True
                    break
            if not filled:
                remaining.append((x, y))
        self._fill_holes(canonical.background, remaining)
        canonical.holes = remaining
        self.canonical_holes = list(remaining)

        self.remaps: dict[str, dict[int, int]] = {}
        for name, g in self.groups.items():
            if g is canonical:
                self.remaps[name] = {i: i for i in range(16)}
                continue
            remap = self._learn_remap(canonical, g)
            missing = {
                canonical.background[y][x]
                for y in range(PLATE_H)
                for x in range(PLATE_W)
            } - set(remap)
            if missing:
                # Cannot rebuild this group faithfully; keep its own recovery.
                self._fill_holes(g.background, g.holes)
                self.remaps[name] = {}
                continue
            g.background = [
                [remap[canonical.background[y][x]] for x in range(PLATE_W)]
                for y in range(PLATE_H)
            ]
            g.holes = []
            self.remaps[name] = remap

    def _fill_holes(self, bg: Grid, holes: list[tuple[int, int]]) -> None:
        """Horizontal nearest-neighbour by luma rank. The gradient runs in long
        horizontal runs, so left/right neighbours are the right source."""
        hole_set = set(holes)
        inv = {r: v for v, r in self.rank.items()}
        for x, y in holes:
            left = right = None
            for i in range(x - 1, -1, -1):
                if (i, y) not in hole_set:
                    left = bg[y][i]
                    break
            for i in range(x + 1, PLATE_W):
                if (i, y) not in hole_set:
                    right = bg[y][i]
                    break
            if left is None and right is None:
                continue
            if left is None:
                bg[y][x] = right
            elif right is None:
                bg[y][x] = left
            else:
                bg[y][x] = inv[(self.rank[left] + self.rank[right]) // 2]

    # ----------------------------------------------------------------------
    def text_model(self, index: int) -> TextModel:
        p = self.plates[index]
        g = self.groups[p.group]
        mask = [
            [p.grid[y][x] in (g.stroke, g.shadow) for x in range(PLATE_W)]
            for y in range(PLATE_H)
        ]
        pts = [(x, y) for y in range(PLATE_H) for x in range(PLATE_W) if mask[y][x]]
        if not pts:
            raise ValueError(f"plate {index}: no label pixels found")
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]

        stroke_px = {(x, y) for x, y in pts if p.grid[y][x] == g.stroke}
        shadow_px = {(x, y) for x, y in pts if p.grid[y][x] == g.shadow}
        best, best_hit = (1, 1), -1
        for dy in (0, 1):
            for dx in (0, 1):
                if dx == dy == 0:
                    continue
                hit = len({(x + dx, y + dy) for x, y in stroke_px} & shadow_px)
                if hit > best_hit:
                    best, best_hit = (dx, dy), hit
        return TextModel(
            index,
            p.group,
            mask,
            (min(xs), min(ys), max(xs), max(ys)),
            g.stroke,
            g.shadow,
            best,
        )


def render_grid(g: Grid, pal, scale: int = 4):
    from PIL import Image

    img = Image.new("RGB", (PLATE_W, PLATE_H))
    px = img.load()
    for y in range(PLATE_H):
        for x in range(PLATE_W):
            px[x, y] = pal[g[y][x]]
    return img.resize((PLATE_W * scale, PLATE_H * scale), Image.NEAREST)
