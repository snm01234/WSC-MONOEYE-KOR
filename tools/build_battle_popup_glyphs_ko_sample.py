#!/usr/bin/env python3
"""Build a Galmuri7 Korean sample for all bank-10 battle popup glyph records.

The renderer keeps every record's animation/layout program byte-exact.  Only the
compressed source-tile lists are remapped to a pooled set of new 8x8 patterns,
and the documented 0x4F..0x94 popup glyph pool is replaced.  This avoids making
assumptions about animation timing or OBJ placement while still giving every
local runtime tile the Korean pixels intended for that position.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402


DEFAULT_PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
DEFAULT_SPEC = ROOT / "data/battle_popup_glyph_translations_ko.json"
DEFAULT_OUT = ROOT / "out/patch/battle_popup_glyphs_ko_galmuri7_sample.wsc"
DEFAULT_OUT_SAVE = ROOT / "sram/battle_popup_glyphs_ko_galmuri7_sample.sav"
DEFAULT_REPORT = ROOT / "out/patch/battle_popup_glyphs_ko_galmuri7_sample_report.json"
DEFAULT_PREVIEWS = ROOT / "out/patch/battle_popup_glyphs_ko_galmuri7_sample_previews"

EXPECTED_PARENT_SHA256 = "42051b189eff4d23d509b83da7aad81384ee932adbc06964990dc1a8578608ad"
EXPECTED_STOCK_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16 * 1024 * 1024
STOCK_SIZE = 8 * 1024 * 1024
SAVE_SIZE = 32 * 1024
TILE_BYTES = 0x20
POOL_TILE_COUNT = 0x94 - 0x4F + 1
EXPECTED_RECORD_STARTS = {
    0x106458, 0x106612, 0x106884, 0x1069F0, 0x106C6C, 0x106E52,
    0x107094, 0x107122, 0x1072FA, 0x107390, 0x107470,
}

# The source-list byte budgets are fixed by the following mapping pointer.
# Repartitioning the same local tile sequence within those exact budgets lets
# the 11 Korean records share a 61-tile supersequence instead of requiring 74
# tiles.  Runtime tile order and every mapping/animation byte remain unchanged.
REPARTITION_LENGTHS = {
    "i_field": (10,),
    "if_canceller": (4, 9, 1),
    "f_barrier": (2, 6),
    "p_defender": (4, 2, 1, 2, 1, 1, 2),
    "beam_coat": (1, 1, 2, 4, 1),
    "bio_field": (7, 1, 1, 5),
    "afterimage": (4,),
    "critical": (1,) * 13,
    "miss": (1, 4, 1),
    "moonlight_butterfly": (6,),
    "light_activation": (1, 1, 4),
}

PREVIEW_PALETTE = {
    0: (0, 0, 0),
    1: (42, 57, 82),
    2: (144, 164, 190),
    3: (244, 249, 255),
}


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceGroup:
    entry_offset: int
    is_range: bool
    ids: tuple[int, ...]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def h(value: str | int) -> int:
    return value if isinstance(value, int) else int(value, 16)


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    raw = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(temporary, target)


def decode_tile(raw: bytes) -> list[list[int]]:
    if len(raw) != TILE_BYTES:
        raise BuildError(f"tile needs {TILE_BYTES} bytes, got {len(raw)}")
    rows: list[list[int]] = []
    for y in range(8):
        row: list[int] = []
        for value in raw[y * 4 : y * 4 + 4]:
            row.extend((value >> 4, value & 0x0F))
        rows.append(row)
    return rows


def encode_tile(rows: list[list[int]]) -> bytes:
    if len(rows) != 8 or any(len(row) != 8 for row in rows):
        raise BuildError("8x8 tile required")
    out = bytearray()
    for row in rows:
        for x in range(0, 8, 2):
            left, right = row[x], row[x + 1]
            if not (0 <= left <= 15 and 0 <= right <= 15):
                raise BuildError("4bpp palette index out of range")
            out.append((left << 4) | right)
    return bytes(out)


def encode_canvas(pixels: list[list[int]], cells: int) -> bytes:
    out = bytearray()
    for half in range(2):
        for cell in range(cells):
            out.extend(encode_tile([
                row[cell * 8 : cell * 8 + 8]
                for row in pixels[half * 8 : half * 8 + 8]
            ]))
    return bytes(out)


def parse_source_groups(rom: bytes, record_start: int) -> tuple[list[SourceGroup], int]:
    group_count_word = int.from_bytes(rom[record_start : record_start + 2], "little")
    if not group_count_word & 0x8000:
        raise BuildError(f"record {record_start:06X}: missing source-list flag")
    group_count = group_count_word & 0x7FFF
    mapping_pointer = int.from_bytes(rom[record_start + 4 : record_start + 6], "little")
    expected_end = (record_start & 0xFF0000) | mapping_pointer
    cursor = record_start + 6
    groups: list[SourceGroup] = []
    for _ in range(group_count):
        entry = cursor
        word = int.from_bytes(rom[cursor : cursor + 2], "little")
        cursor += 2
        if word & 0x4000:
            count = int.from_bytes(rom[cursor : cursor + 2], "little")
            cursor += 2
            start_id = word & 0x3FFF
            ids = tuple(range(start_id, start_id + count))
            groups.append(SourceGroup(entry, True, ids))
        else:
            groups.append(SourceGroup(entry, False, (word,)))
    if cursor != expected_end:
        raise BuildError(
            f"record {record_start:06X}: source list ended {cursor:06X}, "
            f"pointer says {expected_end:06X}"
        )
    return groups, expected_end


def slot_table(row: dict[str, Any]) -> dict[int, list[tuple[int, int]]]:
    slots: dict[int, list[tuple[int, int]]] = {}
    for cell, pair in enumerate(row["cell_tiles"]):
        for half, value in enumerate(pair):
            if value is None:
                continue
            slots.setdefault(h(value), []).append((cell, half))
    return slots


def source_canvas(stock: bytes, atlas_base: int, row: dict[str, Any]) -> list[list[int]]:
    cells = int(row["cells"])
    pixels = [[0] * (cells * 8) for _ in range(16)]
    for tile_id, occurrences in slot_table(row).items():
        tile = decode_tile(stock[atlas_base + tile_id * TILE_BYTES : atlas_base + (tile_id + 1) * TILE_BYTES])
        for cell, half in occurrences:
            for y in range(8):
                pixels[half * 8 + y][cell * 8 : cell * 8 + 8] = tile[y]
    return pixels


def glyph_mask(font: ImageFont.FreeTypeFont, text: str) -> tuple[Image.Image, tuple[int, int, int, int]]:
    bbox = font.getbbox(text)
    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), text, font=font, fill=255)
    return mask.point(lambda value: 255 if value >= 128 else 0), bbox


def render_target(row: dict[str, Any], font: ImageFont.FreeTypeFont, font_spec: dict[str, Any]) -> list[list[int]]:
    cells = int(row["cells"])
    width = cells * 8
    mask = Image.new("L", (width, 16), 0)
    draw = ImageDraw.Draw(mask)
    layout = row["layout"]
    if layout["mode"] != "cells":
        raise BuildError(f"{row['id']}: unsupported layout {layout['mode']!r}")
    glyph_top = int(font_spec["glyph_top"])
    occupied: set[int] = set()
    for placement in layout["placements"]:
        if len(placement) not in (2, 3):
            raise BuildError(f"{row['id']}: placement must be [cell, text] or [cell, text, y]")
        cell_value, text = placement[0], placement[1]
        cell = int(cell_value)
        if cell in occupied or not (0 <= cell < cells):
            raise BuildError(f"{row['id']}: invalid/reused target cell {cell}")
        occupied.add(cell)
        glyph, bbox = glyph_mask(font, text)
        if glyph.width > 8:
            raise BuildError(f"{row['id']}: {text!r} is {glyph.width}px wide, exceeds one cell")
        x = cell * 8 + (8 - glyph.width) // 2
        y = int(placement[2]) if len(placement) == 3 else glyph_top
        if y + glyph.height > 16:
            raise BuildError(f"{row['id']}: {text!r} exceeds popup height")
        draw.bitmap((x, y), glyph, fill=255)

    foreground = int(font_spec["foreground_index"])
    shadow = int(font_spec["shadow_index"])
    dx, dy = (int(value) for value in font_spec["shadow_offset"])
    mp = mask.load()
    pixels = [[0] * width for _ in range(16)]
    coords = [(x, y) for y in range(16) for x in range(width) if mp[x, y]]
    for x, y in coords:
        sx, sy = x + dx, y + dy
        if 0 <= sx < width and 0 <= sy < 16:
            pixels[sy][sx] = shadow
    for x, y in coords:
        pixels[y][x] = foreground

    for cell, pair in enumerate(row["cell_tiles"]):
        for half, source_id in enumerate(pair):
            if source_id is not None:
                continue
            raw = encode_tile([
                line[cell * 8 : cell * 8 + 8]
                for line in pixels[half * 8 : half * 8 + 8]
            ])
            if raw != bytes(TILE_BYTES):
                raise BuildError(
                    f"{row['id']}: target ink enters unavailable cell={cell} half={half}"
                )
    return pixels


def desired_patterns(row: dict[str, Any], pixels: list[list[int]]) -> dict[int, bytes]:
    desired: dict[int, bytes] = {}
    for tile_id, occurrences in slot_table(row).items():
        for cell, half in occurrences:
            raw = encode_tile([
                line[cell * 8 : cell * 8 + 8]
                for line in pixels[half * 8 : half * 8 + 8]
            ])
            if tile_id in desired and desired[tile_id] != raw:
                raise BuildError(
                    f"{row['id']}: shared source tile {tile_id:02X} needs conflicting Korean patterns"
                )
            desired[tile_id] = raw
    return desired


def subsequence_index(haystack: tuple[bytes, ...], needle: tuple[bytes, ...]) -> int:
    for index in range(len(haystack) - len(needle) + 1):
        if haystack[index : index + len(needle)] == needle:
            return index
    return -1


def overlap(left: tuple[bytes, ...], right: tuple[bytes, ...]) -> int:
    for size in range(min(len(left), len(right)), 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def pooled_supersequence(sequences: Iterable[tuple[bytes, ...]]) -> tuple[bytes, ...]:
    unique = sorted(set(sequences), key=lambda seq: (-len(seq), sha256(b"".join(seq))))
    kept: list[tuple[bytes, ...]] = []
    for sequence in unique:
        if any(subsequence_index(other, sequence) >= 0 for other in kept):
            continue
        kept.append(sequence)
    work = kept[:]
    while len(work) > 1:
        choices: list[tuple[int, int, str, int, int, tuple[bytes, ...]]] = []
        for i, left in enumerate(work):
            for j, right in enumerate(work):
                if i == j:
                    continue
                shared = overlap(left, right)
                merged = left + right[shared:]
                choices.append((-shared, len(merged), sha256(b"".join(merged)), i, j, merged))
        _, _, _, i, j, merged = min(choices)
        work = [sequence for index, sequence in enumerate(work) if index not in (i, j)] + [merged]
    return work[0] if work else tuple()


def partition_sequence(sequence: tuple[bytes, ...], lengths: tuple[int, ...]) -> list[tuple[bytes, ...]]:
    if sum(lengths) != len(sequence) or any(length <= 0 for length in lengths):
        raise BuildError(f"invalid repartition {lengths} for {len(sequence)} local tiles")
    chunks: list[tuple[bytes, ...]] = []
    cursor = 0
    for length in lengths:
        chunks.append(sequence[cursor : cursor + length])
        cursor += length
    return chunks


def encoded_group_bytes(tile_start: int, length: int) -> bytes:
    if length == 1:
        return tile_start.to_bytes(2, "little")
    return (0x4000 | tile_start).to_bytes(2, "little") + length.to_bytes(2, "little")


def render_popup(pixels: list[list[int]], cells: int, scale: int = 6) -> Image.Image:
    image = Image.new("RGB", (cells * 8, 16), PREVIEW_PALETTE[0])
    px = image.load()
    for y, line in enumerate(pixels):
        for x, value in enumerate(line):
            px[x, y] = PREVIEW_PALETTE.get(value, (value * 17,) * 3)
    return image.resize((cells * 8 * scale, 16 * scale), Image.Resampling.NEAREST)


def render_previews(rows: list[dict[str, Any]], out_dir: Path, font_path: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = 6
    panel_w = 64 * scale
    art_h = 16 * scale
    label_h = 25
    row_h = art_h + label_h
    sheet = Image.new("RGB", (panel_w * 2, row_h * len(rows)), (18, 22, 31))
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(str(font_path), 14)
    individual: list[str] = []
    for index, row in enumerate(rows):
        cells = int(row["cells"])
        before = render_popup(row["before_pixels"], cells, scale)
        after = render_popup(row["after_pixels"], cells, scale)
        y = index * row_h
        bx = (panel_w - before.width) // 2
        ax = panel_w + (panel_w - after.width) // 2
        sheet.paste(before, (bx, y))
        sheet.paste(after, (ax, y))
        draw.text((5, y + art_h + 2), f"{row['jp']}  {h(row['record_start']):06X}", font=label_font, fill=(238, 242, 250))
        draw.text((panel_w + 5, y + art_h + 2), row["ko"], font=label_font, fill=(238, 242, 250))

        pair = Image.new("RGB", (before.width * 2, art_h), (18, 22, 31))
        pair.paste(before, (0, 0))
        pair.paste(after, (before.width, 0))
        path = out_dir / f"{index + 1:02d}_{row['id']}.png"
        pair.save(path)
        individual.append(rel(path))

    comparison = out_dir / "all_11_before_after.png"
    sheet.save(comparison)

    after_sheet = Image.new("RGB", (panel_w, row_h * len(rows)), (18, 22, 31))
    after_draw = ImageDraw.Draw(after_sheet)
    for index, row in enumerate(rows):
        cells = int(row["cells"])
        after = render_popup(row["after_pixels"], cells, scale)
        y = index * row_h
        x = (panel_w - after.width) // 2
        after_sheet.paste(after, (x, y))
        after_draw.text((5, y + art_h + 2), row["ko"], font=label_font, fill=(238, 242, 250))
    korean = out_dir / "all_11_korean_sample.png"
    after_sheet.save(korean)
    return {
        "comparison_sheet": rel(comparison),
        "korean_sample_sheet": rel(korean),
        "individual_pairs": individual,
    }


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise BuildError("diff requires equal-size ROMs")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def is_allowed(start: int, end: int, allowed: list[tuple[int, int]]) -> bool:
    return any(lo <= start and end <= hi for lo, hi in allowed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--stock", type=Path, default=DEFAULT_STOCK)
    parser.add_argument("--save", type=Path, default=DEFAULT_SAVE)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--out-save", type=Path, default=DEFAULT_OUT_SAVE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--preview-dir", type=Path, default=DEFAULT_PREVIEWS)
    parser.add_argument("--allow-parent-drift", action="store_true")
    args = parser.parse_args(argv)

    if args.out.stem != args.out_save.stem:
        raise BuildError("test ROM and SaveRAM stems must match")
    parent = args.parent.read_bytes()
    stock = args.stock.read_bytes()
    save = args.save.read_bytes()
    if len(parent) != ROM_SIZE or len(stock) != STOCK_SIZE or len(save) != SAVE_SIZE:
        raise BuildError("unexpected ROM or SaveRAM size")
    parent_sha = sha256(parent)
    if not args.allow_parent_drift and parent_sha != EXPECTED_PARENT_SHA256:
        raise BuildError(f"parent SHA-256 drift: {parent_sha}")
    if sha256(stock) != EXPECTED_STOCK_SHA256:
        raise BuildError("stock ROM SHA-256 drift")
    base = stock_base(parent)
    if base != 0x800000:
        raise BuildError(f"unexpected stock body base {base:#x}")

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    rows = spec["records"]
    starts = {h(row["record_start"]) for row in rows}
    if len(rows) != 11 or starts != EXPECTED_RECORD_STARTS:
        raise BuildError("spec must contain the exact 11 documented popup records")
    atlas_base = h(spec["atlas"]["logical_base"])
    pool_first = h(spec["atlas"]["pool_first_tile_id"])
    pool_last = h(spec["atlas"]["pool_last_tile_id"])
    if pool_first != 0x4F or pool_last != 0x94 or pool_last - pool_first + 1 != POOL_TILE_COUNT:
        raise BuildError("unexpected popup pool bounds")
    pool_lo = atlas_base + pool_first * TILE_BYTES
    pool_hi = atlas_base + (pool_last + 1) * TILE_BYTES
    if pool_lo != 0x107F52 or pool_hi != 0x108812:
        raise BuildError("BATTLE_GLYPH pool formula did not resolve to 107F52-108811")

    font_path = ROOT / spec["font"]["path"]
    if not font_path.is_file():
        raise BuildError(f"missing font: {font_path}")
    font = ImageFont.truetype(str(font_path), size=int(spec["font"]["size"]))

    candidate = bytearray(parent)
    group_sequences: list[tuple[bytes, ...]] = []
    record_work: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for row in rows:
        start = h(row["record_start"])
        end = h(row["record_end_exclusive"])
        if not (start < end <= 0x107554):
            raise BuildError(f"{row['id']}: invalid record extent")
        if parent[base + start : base + end] != stock[start:end]:
            raise BuildError(f"{row['id']}: parent descriptor is not stock-exact")
        groups, source_list_end = parse_source_groups(stock, start)
        before_pixels = source_canvas(stock, atlas_base, row)
        after_pixels = render_target(row, font, spec["font"])
        desired = desired_patterns(row, after_pixels)
        expanded = tuple(tile_id for group in groups for tile_id in group.ids)
        if len(expanded) != len(set(expanded)) or set(expanded) != set(desired):
            raise BuildError(
                f"{row['id']}: descriptor source IDs do not match documented cell map: "
                f"descriptor={sorted(set(expanded))}, cells={sorted(desired)}"
            )
        full_sequence = tuple(desired[tile_id] for tile_id in expanded)
        custom_repartitions = spec.get("repartition_lengths", {})
        repartition_source = custom_repartitions.get(row["id"], REPARTITION_LENGTHS.get(row["id"]))
        if repartition_source is None:
            raise BuildError(f"{row['id']}: missing source-list repartition")
        repartition = tuple(int(length) for length in repartition_source)
        sequences = partition_sequence(full_sequence, repartition)
        source_list_budget = source_list_end - (start + 6)
        encoded_budget = sum(2 if length == 1 else 4 for length in repartition)
        if encoded_budget != source_list_budget:
            raise BuildError(
                f"{row['id']}: repartition uses {encoded_budget} source-list bytes, "
                f"record has {source_list_budget}"
            )
        group_sequences.extend(sequences)
        record_work.append({
            "row": row,
            "original_groups": groups,
            "source_list_end": source_list_end,
            "sequences": sequences,
            "repartition": repartition,
            "before_pixels": before_pixels,
            "after_pixels": after_pixels,
            "desired": desired,
        })
        allowed.append((base + start, base + source_list_end))

    if parent[base + pool_lo : base + pool_hi] != stock[pool_lo:pool_hi]:
        raise BuildError("parent popup glyph pool is not stock-exact")

    supersequence = pooled_supersequence(group_sequences)
    if len(supersequence) > POOL_TILE_COUNT:
        raise BuildError(
            f"pooled Korean patterns need {len(supersequence)} tiles, pool has {POOL_TILE_COUNT}"
        )
    blank = bytes(TILE_BYTES)
    pool_patterns = list(supersequence) + [blank] * (POOL_TILE_COUNT - len(supersequence))
    candidate[base + pool_lo : base + pool_hi] = b"".join(pool_patterns)
    allowed.append((base + pool_lo, base + pool_hi))

    manifest: list[dict[str, Any]] = []
    preview_rows: list[dict[str, Any]] = []
    for work in record_work:
        row = work["row"]
        start = h(row["record_start"])
        patched_groups = []
        encoded_list = bytearray()
        for sequence in work["sequences"]:
            pool_index = subsequence_index(supersequence, sequence)
            if pool_index < 0:
                raise BuildError(f"{row['id']}: pooled sequence lookup failed")
            tile_start = pool_first + pool_index
            encoded_list.extend(encoded_group_bytes(tile_start, len(sequence)))
            patched_ids = list(range(tile_start, tile_start + len(sequence)))
            patched_groups.append({
                "encoding": "single" if len(sequence) == 1 else "range",
                "patched_ids": [f"{tile_id:02X}" for tile_id in patched_ids],
                "pattern_sha256": [sha256(pattern) for pattern in sequence],
            })
        source_list_bytes = work["source_list_end"] - (start + 6)
        if len(encoded_list) != source_list_bytes:
            raise BuildError(f"{row['id']}: encoded source list changed byte budget")
        candidate[base + start : base + start + 2] = (0x8000 | len(work["sequences"])).to_bytes(2, "little")
        candidate[base + start + 6 : base + work["source_list_end"]] = encoded_list

        actual_groups, actual_end = parse_source_groups(bytes(candidate[base:base + STOCK_SIZE]), h(row["record_start"]))
        actual_patterns: list[bytes] = []
        for actual_group in actual_groups:
            actual_patterns.extend(
                bytes(candidate[base + atlas_base + tile_id * TILE_BYTES : base + atlas_base + (tile_id + 1) * TILE_BYTES])
                for tile_id in actual_group.ids
            )
        expected_patterns = [pattern for sequence in work["sequences"] for pattern in sequence]
        if actual_end != work["source_list_end"] or actual_patterns != expected_patterns:
            raise BuildError(f"{row['id']}: remapped runtime source sequence verification failed")

        target_canvas = encode_canvas(work["after_pixels"], int(row["cells"]))
        manifest.append({
            "id": row["id"],
            "record_logical": f"{h(row['record_start']):06X}-{h(row['record_end_exclusive']) - 1:06X}",
            "record_physical": f"{base + h(row['record_start']):06X}-{base + h(row['record_end_exclusive']) - 1:06X}",
            "source_list_logical": f"{h(row['record_start']) + 6:06X}-{work['source_list_end'] - 1:06X}",
            "jp": row["jp"],
            "ko": row["ko"],
            "cells": int(row["cells"]),
            "original_group_lengths": [len(group.ids) for group in work["original_groups"]],
            "patched_group_lengths": list(work["repartition"]),
            "groups": patched_groups,
            "target_canvas_sha256": sha256(target_canvas),
            "target_nonzero_pixels": sum(value != 0 for line in work["after_pixels"] for value in line),
            "descriptor_tail_preserved": candidate[
                base + work["source_list_end"] : base + h(row["record_end_exclusive"])
            ] == parent[base + work["source_list_end"] : base + h(row["record_end_exclusive"])],
        })
        preview_rows.append({
            **row,
            "before_pixels": work["before_pixels"],
            "after_pixels": work["after_pixels"],
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    runs = diff_runs(parent, candidate)
    unexpected = [(start, end) for start, end in runs if not is_allowed(start, end, allowed)]
    if unexpected:
        raise BuildError(f"diff allowlist failure: {unexpected[:8]}")
    if (sum(candidate[:-2]) & 0xFFFF) != int.from_bytes(candidate[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")

    previews = render_previews(preview_rows, args.preview_dir, font_path)
    atomic_bytes(args.out, bytes(candidate))
    atomic_copy(args.save, args.out_save)
    candidate_check = args.out.read_bytes()
    paired_save = args.out_save.read_bytes()
    checks = {
        "parent_sha256_bound": parent_sha == EXPECTED_PARENT_SHA256 or args.allow_parent_drift,
        "stock_sha256_bound": sha256(stock) == EXPECTED_STOCK_SHA256,
        "parent_unchanged_on_disk": sha256(args.parent.read_bytes()) == parent_sha,
        "live_saveram_unchanged_on_disk": sha256(args.save.read_bytes()) == sha256(save),
        "paired_saveram_exact_copy": paired_save == save,
        "rom_and_saveram_stems_match": args.out.stem == args.out_save.stem,
        "all_11_records_present": len(manifest) == 11,
        "all_record_tails_preserved": all(row["descriptor_tail_preserved"] for row in manifest),
        "pool_within_documented_70_tiles": len(supersequence) <= POOL_TILE_COUNT,
        "diff_allowlist_clean": not unexpected,
        "candidate_reread_exact": candidate_check == bytes(candidate),
        "candidate_checksum_valid": (sum(candidate_check[:-2]) & 0xFFFF) == int.from_bytes(candidate_check[-2:], "little"),
    }
    if not all(checks.values()):
        raise BuildError(f"post-build checks failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_popup_glyphs_ko_sample.py",
        "ok": True,
        "scope": (
            "all 11 BATTLE_GLYPH bank-10 popup records, "
            f"{font_path.name} {int(spec['font']['size'])}px Korean sample"
        ),
        "promotion_status": "test ROM only; main TIP unchanged; user runtime validation required",
        "parent": identity(args.parent, parent),
        "stock": identity(args.stock, stock),
        "live_saveram": identity(args.save, save),
        "candidate": {**identity(args.out, candidate_check), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(args.out_save, paired_save),
        "spec": identity(args.spec),
        "font": identity(font_path),
        "atlas": {
            "formula": "107572 + tile_id * 0x20",
            "logical_pool": f"{pool_lo:06X}-{pool_hi - 1:06X}",
            "physical_pool": f"{base + pool_lo:06X}-{base + pool_hi - 1:06X}",
            "capacity_tiles": POOL_TILE_COUNT,
            "packed_supersequence_tiles": len(supersequence),
            "padding_blank_tiles": POOL_TILE_COUNT - len(supersequence),
            "unique_patterns": len(set(supersequence)),
        },
        "translations": manifest,
        "diff": {
            "runs_including_checksum": len(runs),
            "changed_bytes_including_checksum": sum(end - start for start, end in runs),
            "allowed_ranges": [
                {"start": f"{start:06X}", "end_exclusive": f"{end:06X}"}
                for start, end in allowed
            ],
            "unexpected_runs": [],
        },
        "previews": previews,
        "checks": checks,
    }
    atomic_bytes(
        args.report,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "ok": True,
        "candidate_sha256": report["candidate"]["sha256"],
        "ws_checksum": report["candidate"]["ws_checksum"],
        "paired_save_sha256": report["paired_saveram"]["sha256"],
        "packed_tiles": len(supersequence),
        "changed_bytes": report["diff"]["changed_bytes_including_checksum"],
        "comparison_sheet": previews["comparison_sheet"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
