#!/usr/bin/env python3
"""Build a static A/B pair for the Korean ``補給`` focus sprite.

The active focus is an attribute-0x35 sprite plate, independent from the 158
background label tiles.  QuickSave3 proves that its 18 live tiles at wsRAM
0x6200-0x643F are copied verbatim from ROM bank 54:2E26-3065.

A keeps the already-Korean static menu but leaves the active Japanese sprite.
B changes only the ``補給`` glyph pixels in that ROM sprite source and mirrors
the same bytes into the serialized live wsRAM so the result is visible as soon
as the state is loaded.  Plate pixels, border pixels, palette indices, sprite
attributes, and animation state are preserved.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import (  # noqa: E402
    Zstd,
    read_state_core,
    sha256_file,
    write_state_with_core,
)
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402


TILE_BYTES = 0x20
WSRAM_CORE_OFFSET = 0x952
FOCUS_WSRAM = 0x6200
FOCUS_ROM = 0x542E26
FOCUS_COLS = 6
FOCUS_ROWS = 3
FOCUS_BYTES = FOCUS_COLS * FOCUS_ROWS * TILE_BYTES

# The first eight entries in the static-label patch report, alternating top and
# bottom rows, form the four-column ``補給`` / ``보급`` overlay strip.
STATIC_SUPPLY_TILES = [
    0x5453A0,
    0x545640,
    0x5453C0,
    0x545660,
    0x5453E0,
    0x545680,
    0x545400,
    0x5456A0,
]
STATIC_COLS = 4
STATIC_ROWS = 2
GLYPH_ROWS = 14


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_run(data: bytes, cols: int, rows: int) -> list[list[int]]:
    if len(data) != cols * rows * TILE_BYTES:
        raise ValueError("tile run has the wrong byte length")
    grid = [[0] * (cols * 8) for _ in range(rows * 8)]
    for index in range(cols * rows):
        tile = tiles_4bpp(data[index * TILE_BYTES : (index + 1) * TILE_BYTES])[0]
        ox, oy = (index % cols) * 8, (index // cols) * 8
        for y in range(8):
            for x in range(8):
                grid[oy + y][ox + x] = tile[y][x]
    return grid


def decode_static_strip(rom: bytes, base: int) -> list[list[int]]:
    grid = [[0] * (STATIC_COLS * 8) for _ in range(STATIC_ROWS * 8)]
    for col in range(STATIC_COLS):
        for row in range(STATIC_ROWS):
            off = base + STATIC_SUPPLY_TILES[col * 2 + row]
            tile = tiles_4bpp(rom[off : off + TILE_BYTES])[0]
            for y in range(8):
                for x in range(8):
                    grid[row * 8 + y][col * 8 + x] = tile[y][x]
    return grid


def encode_run(grid: list[list[int]], cols: int, rows: int) -> bytes:
    out = bytearray()
    for tile_y in range(rows):
        for tile_x in range(cols):
            for y in range(8):
                for pair in range(4):
                    x = tile_x * 8 + pair * 2
                    hi = grid[tile_y * 8 + y][x] & 0x0F
                    lo = grid[tile_y * 8 + y][x + 1] & 0x0F
                    out.append((hi << 4) | lo)
    return bytes(out)


def find_alignment(
    focus: list[list[int]], japanese: list[list[int]]
) -> tuple[int, int, int, int]:
    points = [
        (x, y)
        for y in range(GLYPH_ROWS)
        for x in range(len(japanese[0]))
        if japanese[y][x] != 0
    ]
    scores: list[tuple[int, int, int]] = []
    for oy in range(len(focus) - len(japanese) + 1):
        for ox in range(len(focus[0]) - len(japanese[0]) + 1):
            exact = sum(focus[oy + y][ox + x] == japanese[y][x] for x, y in points)
            scores.append((exact, ox, oy))
    scores.sort(reverse=True)
    exact, ox, oy = scores[0]
    if exact / len(points) < 0.98:
        raise RuntimeError(
            f"static/focus glyph alignment is weak: {exact}/{len(points)} at {ox},{oy}"
        )
    if len(scores) > 1 and scores[1][0] == exact:
        raise RuntimeError("static/focus glyph alignment is not unique")
    return ox, oy, exact, len(points)


def row_background(row: list[int]) -> int:
    # The plate interior uses bevel levels 6/7/8.  Excluding transparent, border,
    # and white glyph values recovers the unoccluded level for each scanline.
    candidates = [value for value in row if 2 <= value <= 0x0E]
    if not candidates:
        # Fully transparent rows below the 24 px plate are never touched by the
        # aligned 16 px label.  Keep a neutral sentinel in the evidence array.
        return 0
    return collections.Counter(candidates).most_common(1)[0][0]


def localize_focus(
    focus: list[list[int]], japanese: list[list[int]], korean: list[list[int]]
) -> tuple[list[list[int]], dict]:
    ox, oy, exact, total = find_alignment(focus, japanese)
    out = [row[:] for row in focus]
    backgrounds = [row_background(row) for row in focus]

    removed = 0
    for y in range(GLYPH_ROWS):
        for x in range(len(japanese[0])):
            if japanese[y][x] != 0:
                out[oy + y][ox + x] = backgrounds[oy + y]
                removed += 1

    drawn = 0
    for y in range(len(korean)):
        for x in range(len(korean[0])):
            if korean[y][x] != 0:
                out[oy + y][ox + x] = korean[y][x]
                drawn += 1

    outside = 0
    for y, (before_row, after_row) in enumerate(zip(focus, out)):
        for x, (before, after) in enumerate(zip(before_row, after_row)):
            in_strip = ox <= x < ox + len(japanese[0]) and oy <= y < oy + len(japanese)
            if before != after and not in_strip:
                outside += 1
    if outside:
        raise RuntimeError(f"localized sprite changed {outside} pixels outside label strip")

    return out, {
        "alignment_xy": [ox, oy],
        "japanese_mask_match": f"{exact}/{total}",
        "japanese_mask_match_ratio": exact / total,
        "removed_mask_pixels": removed,
        "drawn_korean_pixels": drawn,
        "row_background_indices": backgrounds,
    }


def render_grid(grid: list[list[int]], path: Path, scale: int) -> None:
    image = Image.new("RGB", (len(grid[0]), len(grid)), (255, 0, 255))
    pixels = image.load()
    for y, row in enumerate(grid):
        for x, value in enumerate(row):
            if value:
                pixels[x, y] = GREYS_16[value]
    if scale != 1:
        image = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
    image.save(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--stock-rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "out/patch/intermission_state_ab/A_intermission_ko_stock_vram.wsc",
    )
    ap.add_argument(
        "--source-state",
        type=Path,
        default=ROOT / "out/patch/intermission_focus_trace/state3_ab/B_patched_vram.State",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_supply_focus_ab",
    )
    ap.add_argument("--scale", type=int, default=5)
    args = ap.parse_args(argv)

    for path in (args.stock_rom, args.base_rom, args.source_state, args.zstd_dll):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    stock = args.stock_rom.read_bytes()
    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    if base_rom[base + FOCUS_ROM : base + FOCUS_ROM + FOCUS_BYTES] != stock[
        FOCUS_ROM : FOCUS_ROM + FOCUS_BYTES
    ]:
        raise SystemExit("base ROM focus source no longer matches the stock 54:2E26 run")

    old_source = stock[FOCUS_ROM : FOCUS_ROM + FOCUS_BYTES]
    focus = decode_run(old_source, FOCUS_COLS, FOCUS_ROWS)
    japanese = decode_static_strip(stock, 0)
    korean = decode_static_strip(base_rom, base)
    localized, evidence = localize_focus(focus, japanese, korean)
    new_source = encode_run(localized, FOCUS_COLS, FOCUS_ROWS)
    if len(new_source) != FOCUS_BYTES or new_source == old_source:
        raise RuntimeError("focus localization produced no usable source change")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_a = args.out_dir / "A_static_ko_focus_jp.wsc"
    rom_b = args.out_dir / "B_static_ko_focus_ko.wsc"
    state_a = args.out_dir / "A_static_ko_focus_jp.State"
    state_b = args.out_dir / "B_static_ko_focus_ko.State"
    shutil.copy2(args.base_rom, rom_a)
    shutil.copy2(args.source_state, state_a)

    b_rom = bytearray(base_rom)
    source_file_offset = base + FOCUS_ROM
    b_rom[source_file_offset : source_file_offset + FOCUS_BYTES] = new_source
    checksum = update_ws_checksum(b_rom)
    rom_b.write_bytes(b_rom)

    zstd = Zstd(args.zstd_dll)
    core_a, core_name = read_state_core(args.source_state, zstd)
    live_core_offset = WSRAM_CORE_OFFSET + FOCUS_WSRAM
    if core_a[live_core_offset : live_core_offset + FOCUS_BYTES] != old_source:
        raise RuntimeError("QuickSave3 live focus tiles do not match ROM 54:2E26")
    core_b = bytearray(core_a)
    core_b[live_core_offset : live_core_offset + FOCUS_BYTES] = new_source
    write_state_with_core(args.source_state, state_b, core_name, bytes(core_b), zstd, 3)
    check_core, check_name = read_state_core(state_b, zstd)
    if check_name != core_name or check_core != bytes(core_b):
        raise RuntimeError("B state failed its ZIP/Zstandard round trip")

    before_preview = args.out_dir / "A_supply_focus_jp.png"
    after_preview = args.out_dir / "B_supply_focus_ko.png"
    render_grid(focus, before_preview, args.scale)
    render_grid(localized, after_preview, args.scale)

    rom_changed = [i for i, (a, b) in enumerate(zip(base_rom, b_rom)) if a != b]
    core_changed = [i for i, (a, b) in enumerate(zip(core_a, core_b)) if a != b]
    report = {
        "purpose": "A/B for the separately serialized active supply focus sprite",
        "static_label": "보급",
        "focus_source": {
            "rom_logical_start": f"{FOCUS_ROM:06X}",
            "rom_logical_end_exclusive": f"{FOCUS_ROM + FOCUS_BYTES:06X}",
            "rom_file_start": f"{source_file_offset:07X}",
            "wsram_start": f"{FOCUS_WSRAM:04X}",
            "wsram_end_exclusive": f"{FOCUS_WSRAM + FOCUS_BYTES:04X}",
            "core_start": f"{live_core_offset:06X}",
            "core_end_exclusive": f"{live_core_offset + FOCUS_BYTES:06X}",
            "tiles": FOCUS_COLS * FOCUS_ROWS,
        },
        "composition": evidence,
        "preserved": [
            "sprite geometry and attr=0x35",
            "focus plate border and bevel pixels",
            "glyph palette indices F/1",
            "serialized palette/animation state",
        ],
        "a": {
            "rom": str(rom_a),
            "rom_sha256": sha256_file(rom_a),
            "state": str(state_a),
            "state_sha256": sha256_file(state_a),
            "core_sha256": sha256_bytes(core_a),
            "preview": str(before_preview),
            "meaning": "Korean static label, Japanese active focus sprite",
        },
        "b": {
            "rom": str(rom_b),
            "rom_sha256": sha256_file(rom_b),
            "state": str(state_b),
            "state_sha256": sha256_file(state_b),
            "core_sha256": sha256_bytes(bytes(core_b)),
            "preview": str(after_preview),
            "meaning": "same screen/state with the active focus sprite localized",
        },
        "diff": {
            "rom_changed_bytes_including_checksum": len(rom_changed),
            "rom_first_changed": f"{min(rom_changed):07X}",
            "rom_last_changed": f"{max(rom_changed):07X}",
            "core_changed_bytes": len(core_changed),
            "core_first_changed": f"{min(core_changed):06X}",
            "core_last_changed": f"{max(core_changed):06X}",
            "old_source_sha256": sha256_bytes(old_source),
            "new_source_sha256": sha256_bytes(new_source),
        },
        "checksum": f"{checksum:04X}",
    }
    report_path = args.out_dir / "supply_focus_ab_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"alignment          : {evidence['alignment_xy']}")
    print(f"Japanese mask match: {evidence['japanese_mask_match']}")
    print(f"ROM bytes changed  : {len(rom_changed)} (includes checksum)")
    print(f"Core bytes changed : {len(core_changed)}")
    print(f"A                  : {rom_a.name} + {state_a.name}")
    print(f"B                  : {rom_b.name} + {state_b.name}")
    print(f"report             : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
