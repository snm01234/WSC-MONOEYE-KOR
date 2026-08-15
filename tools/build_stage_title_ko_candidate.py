#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from scan_stage_title_packages import Package, Title, decode_tile, parse_package  # noqa: E402


DEFAULT_PROJECT = Path(r"D:\monoeye")
DEFAULT_PARENT = DEFAULT_PROJECT / "out" / "patch" / "monoeye_ko_expanded.wsc"
DEFAULT_SPEC = DEFAULT_PROJECT / "data" / "stage_title_translations_ko.json"
DEFAULT_OUT = DEFAULT_PROJECT / "out" / "patch" / "stage_title_ko_candidate.wsc"
DEFAULT_REPORT = DEFAULT_PROJECT / "out" / "patch" / "stage_title_ko_candidate_report.json"
DEFAULT_PREVIEWS = DEFAULT_PROJECT / "out" / "patch" / "stage_title_ko_candidate_previews"
EXPECTED_PARENT_SHA256 = "9402f7efc1c557746015eb6352799a79f7f66febf1eb0ad4039734028a16a9f2"
PACKAGE_HEADERS = (0x4ABD0C, 0x4DB4B8, 0x53B9F4)
SCREEN_COLS = 28
SCREEN_ROWS = 18
SCREEN_W = SCREEN_COLS * 8
SCREEN_H = SCREEN_ROWS * 8
VREF = "갱"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise ValueError("diff requires equal lengths")
    out: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            out.append((start, index))
            start = None
    if start is not None:
        out.append((start, len(before)))
    return out


class Rasteriser:
    def __init__(
        self,
        font_path: Path,
        size: int,
        spacing: int,
        line_gap: int,
        vertical_offset: int = 0,
    ):
        if not font_path.exists():
            raise FileNotFoundError(font_path)
        self.font = ImageFont.truetype(str(font_path), size=size)
        self.font_path = font_path
        self.size = size
        self.spacing = spacing
        self.line_gap = line_gap
        self.vertical_offset = vertical_offset
        probe = Image.new("L", (size * 3, size * 3), 0)
        draw = ImageDraw.Draw(probe)
        self.top = draw.textbbox((0, 0), VREF, font=self.font)[1]
        self.line_height = size + 3

    def advance(self, ch: str) -> int:
        return max(1, int(round(self.font.getlength(ch))))

    def glyph(self, ch: str) -> list[list[int]]:
        width = self.advance(ch)
        image = Image.new("L", (width + 1, self.line_height), 0)
        draw = ImageDraw.Draw(image)
        draw.text((0, -self.top), ch, fill=255, font=self.font)
        px = image.load()
        return [
            [1 if px[x, y] >= 128 else 0 for x in range(image.width)]
            for y in range(image.height)
        ]

    def line_width(self, text: str) -> int:
        if not text:
            return 0
        return sum(self.advance(ch) for ch in text) + self.spacing * (len(text) - 1)

    def render(self, lines: list[str]) -> tuple[list[list[int]], dict]:
        if not 1 <= len(lines) <= 2 or any(not line for line in lines):
            raise ValueError(f"one or two non-empty lines required: {lines!r}")
        widths = [self.line_width(line) for line in lines]
        if max(widths) > SCREEN_W:
            raise ValueError(f"line too wide: {lines!r} widths={widths}")
        total_height = len(lines) * self.line_height + (len(lines) - 1) * self.line_gap
        top = (SCREEN_H - total_height) // 2 + self.vertical_offset
        mask = [[0] * SCREEN_W for _ in range(SCREEN_H)]
        line_rows = []
        for line_index, (text, width) in enumerate(zip(lines, widths)):
            x = (SCREEN_W - width) // 2
            y = top + line_index * (self.line_height + self.line_gap)
            start_x = x
            for ch in text:
                bits = self.glyph(ch)
                for yy, row in enumerate(bits):
                    for xx, value in enumerate(row):
                        if value and 0 <= x + xx < SCREEN_W and 0 <= y + yy < SCREEN_H:
                            mask[y + yy][x + xx] = 1
                x += self.advance(ch) + self.spacing
            line_rows.append({"text": text, "x": start_x, "y": y, "width": width})

        coords = [(x, y) for y, row in enumerate(mask) for x, value in enumerate(row) if value]
        if not coords:
            raise ValueError(f"rendered no pixels: {lines!r}")
        bbox = [
            min(x for x, _ in coords),
            min(y for _, y in coords),
            max(x for x, _ in coords) + 1,
            max(y for _, y in coords) + 1,
        ]
        return mask, {"lines": line_rows, "bbox": bbox, "ink_pixels": len(coords)}


def encode_tile(values: tuple[int, ...]) -> bytes:
    if len(values) != 64:
        raise ValueError("tile needs 64 pixels")
    out = bytearray()
    for y in range(8):
        for x in range(0, 8, 2):
            out.append((values[y * 8 + x] << 4) | values[y * 8 + x + 1])
    return bytes(out)


def title_palette(stock: bytes, package: Package, title: Title) -> tuple[int, int]:
    blank = decode_tile(stock, package.graphics_base + title.global_tiles[0] * 32)
    background_values = {value for row in blank for value in row}
    if len(background_values) != 1:
        raise ValueError(f"package {package.header:06X} title {title.index}: non-uniform background")
    background = next(iter(background_values))
    values: set[int] = set()
    for global_tile in set(title.global_tiles[1:]):
        values.update(value for row in decode_tile(stock, package.graphics_base + global_tile * 32) for value in row)
    inks = values - {background}
    if len(inks) != 1:
        raise ValueError(
            f"package {package.header:06X} title {title.index}: palette bg={background} values={sorted(values)}"
        )
    return background, next(iter(inks))


def target_cells(mask: list[list[int]], background: int, ink: int) -> tuple[list[bytes | None], dict]:
    cells: list[bytes | None] = []
    nonblank = 0
    patterns: set[bytes] = set()
    blank_raw = encode_tile((background,) * 64)
    for row in range(SCREEN_ROWS):
        for col in range(SCREEN_COLS):
            binary = tuple(mask[row * 8 + y][col * 8 + x] for y in range(8) for x in range(8))
            if not any(binary):
                cells.append(None)
                continue
            raw = encode_tile(tuple(ink if value else background for value in binary))
            cells.append(raw)
            nonblank += 1
            patterns.add(raw)
    bridge_cells = 0
    for row in range(SCREEN_ROWS):
        start = row * SCREEN_COLS
        occupied = [col for col in range(SCREEN_COLS) if cells[start + col] is not None]
        if not occupied:
            continue
        for col in range(min(occupied), max(occupied) + 1):
            if cells[start + col] is None:
                cells[start + col] = blank_raw
                bridge_cells += 1
    return cells, {
        "visible_nonblank_cells": nonblank,
        "layout_bridge_cells": bridge_cells,
        "layout_tile_occurrences": nonblank + bridge_cells,
        "unique_visible_patterns": len(patterns),
    }


def allocate_contiguous_blocks(
    package: Package,
    targets: list[list[bytes | None]],
    backgrounds: list[int],
) -> tuple[dict[int, bytes], list[list[int]], list[list[int]], list[dict]]:
    capacity = (package.graphics_end - package.graphics_base) // 32
    assignments: dict[int, bytes] = {}
    local_maps: list[list[int]] = []
    source_lists: list[list[int]] = []
    rows: list[dict] = []
    cursor = 0
    for title_index, (cells, background) in enumerate(zip(targets, backgrounds)):
        nonblank = [pattern for pattern in cells if pattern is not None]
        block_count = 1 + len(nonblank)
        if cursor + block_count > capacity:
            raise RuntimeError(
                f"package {package.header:06X}: contiguous allocation needs "
                f"{cursor + block_count}/{capacity} tiles at title {title_index}"
            )
        block_start = cursor
        globals_for_title = list(range(block_start, block_start + block_count))
        assignments[block_start] = encode_tile((background,) * 64)
        local_cells: list[int] = []
        next_local = 1
        for pattern in cells:
            if pattern is None:
                local_cells.append(0)
            else:
                assignments[block_start + next_local] = pattern
                local_cells.append(next_local)
                next_local += 1
        local_maps.append(local_cells)
        source_lists.append(globals_for_title)
        rows.append(
            {
                "index": title_index,
                "allocation_mode": "independent_contiguous_block",
                "global_tile_start": block_start,
                "global_tile_end": block_start + block_count - 1,
                "background_global_tile": block_start,
                "nonblank_tile_occurrences": len(nonblank),
                "physical_tiles": block_count,
            }
        )
        cursor += block_count
    return assignments, local_maps, source_lists, rows


def encode_source_exact(global_tiles: list[int], target_words: int) -> tuple[int, list[int]]:
    total = len(global_tiles)
    parents: dict[tuple[int, int], tuple[tuple[int, int], tuple[str, int]]] = {}
    states: list[set[int]] = [set() for _ in range(total + 1)]
    states[0].add(0)
    for pos in range(total):
        for used in sorted(states[pos]):
            if used + 1 <= target_words:
                key = (pos + 1, used + 1)
                if used + 1 not in states[pos + 1]:
                    states[pos + 1].add(used + 1)
                    parents[key] = ((pos, used), ("single", 1))
            if used + 2 <= target_words:
                for length in range(total - pos, 0, -1):
                    key = (pos + length, used + 2)
                    if used + 2 not in states[pos + length]:
                        states[pos + length].add(used + 2)
                        parents[key] = ((pos, used), ("run", length))
    key = (total, target_words)
    if target_words not in states[total]:
        raise RuntimeError(
            f"source list of {total} tiles cannot use exactly {target_words} words; "
            f"reachable={sorted(states[total])}"
        )
    tokens: list[tuple[int, str, int]] = []
    while key != (0, 0):
        previous, (kind, length) = parents[key]
        tokens.append((previous[0], kind, length))
        key = previous
    tokens.reverse()
    words: list[int] = []
    for pos, kind, length in tokens:
        start = global_tiles[pos]
        if kind == "single":
            words.append(start)
        elif kind == "run":
            words.extend((0x4000 | start, length))
        else:
            raise AssertionError(kind)
    if len(words) != target_words:
        raise AssertionError((len(words), target_words))
    return len(tokens), words


def encode_layout_exact(cells: list[int], target_words: int) -> list[int]:
    total = len(cells)
    parents: dict[tuple[int, int], tuple[tuple[int, int], tuple[str, int]]] = {}
    states: list[set[int]] = [set() for _ in range(total + 1)]
    states[0].add(0)
    for pos in range(total):
        if not states[pos]:
            continue
        zero_run = 0
        while pos + zero_run < total and cells[pos + zero_run] == 0:
            zero_run += 1
        seq_run = 1
        if cells[pos] != 0:
            while (
                pos + seq_run < total
                and cells[pos + seq_run] == cells[pos] + seq_run
                and cells[pos + seq_run] != 0
            ):
                seq_run += 1
        for used in sorted(states[pos]):
            if used + 1 <= target_words:
                key = (pos + 1, used + 1)
                if used + 1 not in states[pos + 1]:
                    states[pos + 1].add(used + 1)
                    parents[key] = ((pos, used), ("single", 1))
            if used + 2 <= target_words:
                if zero_run:
                    for length in range(zero_run, 0, -1):
                        key = (pos + length, used + 2)
                        if used + 2 not in states[pos + length]:
                            states[pos + length].add(used + 2)
                            parents[key] = ((pos, used), ("skip", length))
                if seq_run >= 2:
                    for length in range(seq_run, 1, -1):
                        key = (pos + length, used + 2)
                        if used + 2 not in states[pos + length]:
                            states[pos + length].add(used + 2)
                            parents[key] = ((pos, used), ("run", length))
    key = (total, target_words)
    if target_words not in states[total]:
        reachable = sorted(states[total])
        minimum = [10**9] * (total + 1)
        minimum[0] = 0
        for pos in range(total):
            minimum[pos + 1] = min(minimum[pos + 1], minimum[pos] + 1)
            zero_run = 0
            while pos + zero_run < total and cells[pos + zero_run] == 0:
                zero_run += 1
            for length in range(1, zero_run + 1):
                minimum[pos + length] = min(minimum[pos + length], minimum[pos] + 2)
            if cells[pos] != 0:
                seq_run = 1
                while (
                    pos + seq_run < total
                    and cells[pos + seq_run] == cells[pos] + seq_run
                    and cells[pos + seq_run] != 0
                ):
                    seq_run += 1
                for length in range(2, seq_run + 1):
                    minimum[pos + length] = min(minimum[pos + length], minimum[pos] + 2)
        raise RuntimeError(
            f"layout cannot use exactly {target_words} words; "
            f"minimum={minimum[total]} reachable={reachable}"
        )

    tokens: list[tuple[int, str, int]] = []
    while key != (0, 0):
        previous, (kind, length) = parents[key]
        tokens.append((previous[0], kind, length))
        key = previous
    tokens.reverse()
    words: list[int] = []
    for pos, kind, length in tokens:
        if kind == "single":
            words.append(cells[pos])
        elif kind == "skip":
            words.extend((0x8000, length))
        elif kind == "run":
            words.extend((0x4000 | cells[pos], length))
        else:
            raise AssertionError(kind)
    if len(words) != target_words:
        raise AssertionError((len(words), target_words))
    return words


def write_words(buffer: bytearray, base: int, words: list[int]) -> None:
    for index, word in enumerate(words):
        buffer[base + index * 2] = word & 0xFF
        buffer[base + index * 2 + 1] = (word >> 8) & 0xFF


def render_candidate_mask(stock: bytes, package: Package, title: Title) -> list[list[int]]:
    blank = decode_tile(stock, package.graphics_base + title.global_tiles[0] * 32)
    background = blank[0][0]
    mask = [[0] * SCREEN_W for _ in range(SCREEN_H)]
    for pos, local_tile in enumerate(title.cells):
        global_tile = title.global_tiles[local_tile]
        tile = decode_tile(stock, package.graphics_base + global_tile * 32)
        ox, oy = (pos % SCREEN_COLS) * 8, (pos // SCREEN_COLS) * 8
        for y in range(8):
            for x in range(8):
                mask[oy + y][ox + x] = 1 if tile[y][x] != background else 0
    return mask


def save_mask(mask: list[list[int]], path: Path, scale: int = 3) -> None:
    image = Image.new("RGB", (SCREEN_W, SCREEN_H), "black")
    pixels = image.load()
    for y, row in enumerate(mask):
        for x, value in enumerate(row):
            pixels[x, y] = (255, 255, 255) if value else (0, 0, 0)
    if scale > 1:
        image = image.resize((SCREEN_W * scale, SCREEN_H * scale), Image.Resampling.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def make_contact(images: list[tuple[str, Path]], path: Path) -> None:
    loaded = [(label, Image.open(image_path).convert("RGB")) for label, image_path in images]
    cols = 3
    cell_w = SCREEN_W * 3
    cell_h = SCREEN_H * 3 + 18
    rows = (len(loaded) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (label, image) in enumerate(loaded):
        x, y = (index % cols) * cell_w, (index // cols) * cell_h
        sheet.paste(image, (x, y))
        draw.text((x + 4, y + SCREEN_H * 3 + 3), label, fill="white", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--preview-dir", type=Path, default=DEFAULT_PREVIEWS)
    parser.add_argument("--allow-parent-sha", default=EXPECTED_PARENT_SHA256)
    args = parser.parse_args()

    sys.path.insert(0, str(args.project_root / "tools"))
    from monoeye_rom import stock_base, update_ws_checksum  # type: ignore

    parent = args.parent.read_bytes()
    parent_hash = sha256(parent)
    if parent_hash.lower() != args.allow_parent_sha.lower():
        raise SystemExit(f"unexpected parent SHA-256: {parent_hash}")
    base = stock_base(parent)
    if base != 0x800000:
        raise SystemExit(f"expected expanded stock base 0x800000, got {base:#x}")
    stock = parent[base:]
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    spec_by_header = {int(row["header"], 16): row for row in spec["packages"]}
    font_spec = spec["font"]
    font_path = args.project_root / font_spec["path"]
    raster = Rasteriser(
        font_path,
        int(font_spec["size"]),
        int(font_spec["letter_spacing"]),
        int(font_spec["line_gap"]),
        int(font_spec.get("vertical_offset", 0)),
    )

    packages: list[Package] = []
    for header in PACKAGE_HEADERS:
        package = parse_package(stock, header)
        if package is None:
            raise SystemExit(f"failed to parse package {header:06X}")
        packages.append(package)

    candidate = bytearray(parent)
    package_reports = []
    preview_paths: list[tuple[str, Path]] = []
    target_masks: dict[tuple[int, int], list[list[int]]] = {}
    for package in packages:
        package_spec = spec_by_header.get(package.header)
        if package_spec is None or len(package_spec["titles"]) != package.count:
            raise SystemExit(f"spec mismatch for package {package.header:06X}")
        title_specs = sorted(package_spec["titles"], key=lambda row: row["index"])
        targets: list[list[bytes | None]] = []
        backgrounds: list[int] = []
        title_reports = []
        for title, title_spec in zip(package.titles, title_specs):
            if title.index != title_spec["index"]:
                raise SystemExit(f"title index mismatch in package {package.header:06X}")
            mask, render_info = raster.render(title_spec["ko_lines"])
            background, ink = title_palette(stock, package, title)
            cells, budget = target_cells(mask, background, ink)
            targets.append(cells)
            backgrounds.append(background)
            target_masks[(package.header, title.index)] = mask
            title_reports.append(
                {
                    "index": title.index,
                    "stage_id": title_spec["stage_id"],
                    "jp": title_spec["jp"],
                    "ko_lines": title_spec["ko_lines"],
                    "descriptor": f"{title.descriptor_start:06X}-{title.descriptor_end - 1:06X}",
                    "palette": {"background": background, "ink": ink},
                    "render": render_info,
                    "budget": budget,
                }
            )

        print(
            f"package {package.header:06X}: "
            f"visible cells={sum(row['budget']['visible_nonblank_cells'] for row in title_reports)}, "
            f"layout cells={sum(row['budget']['layout_tile_occurrences'] for row in title_reports)}, "
            f"loaded nonblank slots={sum(len(title.global_tiles) - 1 for title in package.titles)}, "
            f"physical tiles={(package.graphics_end - package.graphics_base) // 32}"
        )
        for row, title in zip(title_reports, package.titles):
            print(
                f"  title {row['index']:02d}: visible={row['budget']['visible_nonblank_cells']} "
                f"bridge={row['budget']['layout_bridge_cells']} "
                f"layout={row['budget']['layout_tile_occurrences']} "
                f"slots={len(title.global_tiles) - 1}"
            )

        assignments, local_maps, source_lists, allocation_rows = allocate_contiguous_blocks(
            package, targets, backgrounds
        )
        for global_tile, raw in assignments.items():
            logical = package.graphics_base + global_tile * 32
            candidate[base + logical : base + logical + 32] = raw

        for title, local_cells, source_tiles, title_report, allocation in zip(
            package.titles, local_maps, source_lists, title_reports, allocation_rows
        ):
            layout_header = int.from_bytes(
                stock[title.descriptor_start + 4 : title.descriptor_start + 6], "little"
            ) | (package.header & ~0xFFFF)
            source_start = title.descriptor_start + 6
            source_target_words = (layout_header - source_start) // 2
            source_command_count, source_words = encode_source_exact(source_tiles, source_target_words)
            candidate[base + title.descriptor_start : base + title.descriptor_start + 2] = (
                0x8000 | source_command_count
            ).to_bytes(2, "little")
            write_words(candidate, base + source_start, source_words)

            layout_start = layout_header + 4
            tail = int.from_bytes(stock[title.descriptor_start + 2 : title.descriptor_start + 4], "little") | (
                package.header & ~0xFFFF
            )
            target_words = (tail - layout_start) // 2
            try:
                words = encode_layout_exact(local_cells, target_words)
            except RuntimeError as error:
                raise RuntimeError(
                    f"package {package.header:06X} title {title.index} "
                    f"({title_report['stage_id']}): {error}"
                ) from error
            write_words(candidate, base + layout_start, words)
            title_report["allocation"] = allocation
            title_report["source"] = {
                "logical_range": f"{source_start:06X}-{layout_header - 1:06X}",
                "words": source_target_words,
                "commands": source_command_count,
                "loaded_tiles": len(source_tiles),
            }
            title_report["layout"] = {
                "logical_range": f"{layout_start:06X}-{tail - 1:06X}",
                "words": target_words,
                "expanded_cells": len(local_cells),
            }

        package_reports.append(
            {
                "header": f"{package.header:06X}",
                "descriptor_table": f"{package.descriptor_table:06X}",
                "graphics_range": f"{package.graphics_base:06X}-{package.graphics_end - 1:06X}",
                "assigned_graphics_tiles": len(assignments),
                "titles": title_reports,
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_stock = candidate_bytes[base:]

    pixel_failures = []
    for package, package_report in zip(packages, package_reports):
        parsed = parse_package(candidate_stock, package.header)
        if parsed is None:
            raise RuntimeError(f"candidate package failed parse: {package.header:06X}")
        for title, title_report in zip(parsed.titles, package_report["titles"]):
            actual = render_candidate_mask(candidate_stock, parsed, title)
            expected = target_masks[(package.header, title.index)]
            diff = sum(
                actual[y][x] != expected[y][x]
                for y in range(SCREEN_H)
                for x in range(SCREEN_W)
            )
            title_report["static_render_pixel_diff"] = diff
            if diff:
                pixel_failures.append(
                    {"package": f"{package.header:06X}", "index": title.index, "pixels": diff}
                )
            preview = args.preview_dir / f"{package.header:06X}_{title.index:02d}_{title_report['stage_id'].replace(' ', '_')}.png"
            save_mask(actual, preview, scale=3)
            preview_paths.append((f"{package.header:06X} #{title.index:02d} {title_report['stage_id']}", preview))

    contact = args.preview_dir / "stage_title_ko_contact.png"
    make_contact(preview_paths, contact)

    runs = diff_runs(parent, candidate_bytes)
    allowed_logical = [
        (package.header + 8, package.descriptor_table)
        for package in packages
    ] + [
        (package.graphics_base, package.graphics_end)
        for package in packages
    ]
    allowed_physical = [(base + left, base + right) for left, right in allowed_logical]
    allowed_physical.append((len(candidate_bytes) - 2, len(candidate_bytes)))
    out_of_scope = [
        (left, right)
        for left, right in runs
        if not any(a <= left and right <= b for a, b in allowed_physical)
    ]
    checksum_valid = int.from_bytes(candidate_bytes[-2:], "little") == (sum(candidate_bytes[:-2]) & 0xFFFF)
    protected_exact = []
    for package in packages:
        for title in package.titles:
            layout_header = (package.header & ~0xFFFF) | int.from_bytes(
                stock[title.descriptor_start + 4 : title.descriptor_start + 6], "little"
            )
            tail = (package.header & ~0xFFFF) | int.from_bytes(
                stock[title.descriptor_start + 2 : title.descriptor_start + 4], "little"
            )
            protected_exact.append(
                parent[base + title.descriptor_start + 2 : base + title.descriptor_start + 6]
                == candidate_bytes[base + title.descriptor_start + 2 : base + title.descriptor_start + 6]
            )
            protected_exact.append(
                parent[base + layout_header : base + layout_header + 4]
                == candidate_bytes[base + layout_header : base + layout_header + 4]
            )
            protected_exact.append(
                parent[base + tail : base + title.descriptor_end]
                == candidate_bytes[base + tail : base + title.descriptor_end]
            )
    checks = {
        "parent_sha_expected": parent_hash.lower() == args.allow_parent_sha.lower(),
        "package_count": len(packages) == 3,
        "title_count": sum(package.count for package in packages) == 43,
        "candidate_packages_parse": all(parse_package(candidate_stock, h) is not None for h in PACKAGE_HEADERS),
        "all_static_render_pixel_diff_zero": not pixel_failures,
        "descriptor_pointers_layout_markers_and_tails_unchanged": all(protected_exact),
        "diff_outside_descriptor_graphics_checksum": not out_of_scope,
        "checksum_valid": checksum_valid,
        "main_tip_unchanged": sha256(args.parent.read_bytes()) == parent_hash,
    }
    if not all(checks.values()):
        raise RuntimeError(f"candidate verification failed: {checks}; pixel={pixel_failures}; scope={out_of_scope}")

    report = {
        "schema_version": 1,
        "status": "candidate_static_verified_runtime_test_required",
        "generated_by": "tools/build_stage_title_ko_candidate.py",
        "translation_spec": str(args.spec),
        "font": {
            "path": str(font_path),
            "size": raster.size,
            "letter_spacing": raster.spacing,
            "line_gap": raster.line_gap,
            "vertical_offset": raster.vertical_offset,
        },
        "parent": {"path": str(args.parent), "bytes": len(parent), "sha256": parent_hash},
        "candidate": {
            "path": str(args.out),
            "bytes": len(candidate_bytes),
            "sha256": sha256(candidate_bytes),
            "checksum": f"{checksum:04X}",
        },
        "preview_contact": str(contact),
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(right - left for left, right in runs),
            "out_of_scope_runs": [f"{left:06X}-{right - 1:06X}" for left, right in out_of_scope],
        },
        "checks": checks,
        "packages": package_reports,
        "remaining_gate": "user emulator validation; do not promote candidate to main TIP",
    }
    atomic_bytes(args.out, candidate_bytes)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"candidate={args.out}")
    print(f"sha256={report['candidate']['sha256']} checksum={report['candidate']['checksum']}")
    print(f"titles=43 pixel_diff=0 changed_bytes={report['diff']['changed_bytes']} runs={report['diff']['runs']}")
    print(f"contact={contact}")
    print(f"report={args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
