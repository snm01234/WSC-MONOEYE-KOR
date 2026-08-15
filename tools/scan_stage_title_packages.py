from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(r"D:\monoeye")
DEFAULT_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_OUT = Path(__file__).resolve().parent / "stage_title_packages"


def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def decode_tile(data: bytes, off: int) -> list[list[int]]:
    pixels: list[list[int]] = []
    for y in range(8):
        row: list[int] = []
        for x in range(4):
            value = data[off + y * 4 + x]
            row.extend((value >> 4, value & 0x0F))
        pixels.append(row)
    return pixels


@dataclass
class Title:
    index: int
    descriptor_start: int
    descriptor_end: int
    source_commands: list[dict]
    global_tiles: list[int]
    cells: list[int]


@dataclass
class Package:
    header: int
    count: int
    descriptor_table: int
    graphics_base: int
    graphics_end: int
    titles: list[Title]


def parse_package(rom: bytes, header: int) -> Package | None:
    bank = header & ~0xFFFF
    bank_end = bank + 0x10000
    if header + 8 > len(rom) or header + 8 > bank_end:
        return None
    count = u16(rom, header)
    if not 1 <= count <= 64:
        return None
    table = bank + u16(rom, header + 2)
    graphics_base = bank + u16(rom, header + 4)
    graphics_end = bank + u16(rom, header + 6)
    if not (header + 8 < table < graphics_base < graphics_end <= min(bank_end, len(rom))):
        return None
    if graphics_base != table + (count + 1) * 2:
        return None
    if (graphics_end - graphics_base) % 32:
        return None
    graphics_tiles = (graphics_end - graphics_base) // 32
    if not 1 <= graphics_tiles <= 2048:
        return None

    pointers = [bank + u16(rom, table + i * 2) for i in range(count + 1)]
    if pointers[0] != header + 8 or pointers[-1] != table:
        return None
    if any(a >= b for a, b in zip(pointers, pointers[1:])):
        return None

    titles: list[Title] = []
    try:
        for index, (start, stop) in enumerate(zip(pointers, pointers[1:])):
            if not (header + 8 <= start < stop <= table) or stop - start < 16:
                return None
            source_command_count = u16(rom, start)
            if not (source_command_count & 0x8000):
                return None
            source_command_count &= 0x7FFF
            if not 1 <= source_command_count <= 128:
                return None
            tail = bank + u16(rom, start + 2)
            layout_header = bank + u16(rom, start + 4)
            if not (start + 6 <= layout_header < tail < stop):
                return None

            cursor = start + 6
            global_tiles: list[int] = []
            source_commands: list[dict] = []
            for _ in range(source_command_count):
                if cursor + 2 > layout_header:
                    return None
                word = u16(rom, cursor)
                cursor += 2
                if word & 0x4000:
                    if cursor + 2 > layout_header:
                        return None
                    source = word & 0x3FFF
                    run_count = u16(rom, cursor)
                    cursor += 2
                    if not 1 <= run_count <= 512 or source + run_count > graphics_tiles:
                        return None
                    global_tiles.extend(range(source, source + run_count))
                    source_commands.append({"kind": "run", "start": source, "count": run_count})
                else:
                    if word >= graphics_tiles:
                        return None
                    global_tiles.append(word)
                    source_commands.append({"kind": "single", "tile": word})
            if cursor != layout_header or not global_tiles:
                return None
            if u16(rom, layout_header) != 1 or u16(rom, layout_header + 2) != 0x023B:
                return None

            cells: list[int] = []
            cursor = layout_header + 4
            while cursor < tail:
                word = u16(rom, cursor)
                cursor += 2
                if word & 0x8000:
                    if cursor + 2 > tail:
                        return None
                    cells.extend([0] * u16(rom, cursor))
                    cursor += 2
                elif word & 0x4000:
                    if cursor + 2 > tail:
                        return None
                    local_start = word & 0x1FFF
                    run_count = u16(rom, cursor)
                    cursor += 2
                    cells.extend(range(local_start, local_start + run_count))
                else:
                    cells.append(word)
                if len(cells) > 28 * 18:
                    return None
            if cursor != tail or len(cells) != 28 * 18 or max(cells) >= len(global_tiles):
                return None
            expected_tail = [1, 1, 0, 0, (layout_header + 2) & 0xFFFF]
            if stop - tail != 10 or [u16(rom, tail + i * 2) for i in range(5)] != expected_tail:
                return None
            titles.append(
                Title(
                    index=index,
                    descriptor_start=start,
                    descriptor_end=stop,
                    source_commands=source_commands,
                    global_tiles=global_tiles,
                    cells=cells,
                )
            )
    except (IndexError, struct.error):
        return None
    return Package(header, count, table, graphics_base, graphics_end, titles)


def render_title(rom: bytes, package: Package, title: Title) -> Image.Image:
    blank_global = title.global_tiles[0]
    blank = decode_tile(rom, package.graphics_base + blank_global * 32)
    blank_values = {value for row in blank for value in row}
    if len(blank_values) != 1:
        background = max((value for row in blank for value in row), key=lambda v: sum(r.count(v) for r in blank))
    else:
        background = next(iter(blank_values))

    image = Image.new("RGB", (224, 144), "black")
    pixels = image.load()
    for pos, local_tile in enumerate(title.cells):
        global_tile = title.global_tiles[local_tile]
        tile = decode_tile(rom, package.graphics_base + global_tile * 32)
        ox, oy = (pos % 28) * 8, (pos // 28) * 8
        for y in range(8):
            for x in range(8):
                pixels[ox + x, oy + y] = (255, 255, 255) if tile[y][x] != background else (0, 0, 0)
    return image


def package_to_json(package: Package) -> dict:
    def ranges(values: set[int]) -> list[dict]:
        if not values:
            return []
        ordered = sorted(values)
        groups: list[tuple[int, int]] = []
        start = previous = ordered[0]
        for value in ordered[1:]:
            if value != previous + 1:
                groups.append((start, previous))
                start = value
            previous = value
        groups.append((start, previous))
        return [
            {
                "global_tiles": f"{start:03X}-{end:03X}" if start != end else f"{start:03X}",
                "logical_rom": (
                    f"{package.graphics_base + start * 32:06X}-"
                    f"{package.graphics_base + (end + 1) * 32 - 1:06X}"
                ),
                "tiles": end - start + 1,
            }
            for start, end in groups
        ]

    seen: set[int] = set()
    titles_json = []
    for title in package.titles:
        used = {title.global_tiles[cell] for cell in title.cells if cell != 0}
        new = used - seen
        reused = used & seen
        seen.update(used)
        titles_json.append(
            {
                "index": title.index,
                "descriptor": f"{title.descriptor_start:06X}-{title.descriptor_end - 1:06X}",
                "source_commands": title.source_commands,
                "local_tiles": len(title.global_tiles),
                "nonblank_tiles_used": len(used),
                "new_nonblank_tiles": len(new),
                "reused_nonblank_tiles": len(reused),
                "new_tile_ranges": ranges(new),
                "reused_tile_ranges": ranges(reused),
                "background_global_tile": title.global_tiles[0],
            }
        )
    return {
        "header": f"{package.header:06X}",
        "title_count": package.count,
        "descriptor_table": f"{package.descriptor_table:06X}",
        "graphics_range": f"{package.graphics_base:06X}-{package.graphics_end - 1:06X}",
        "graphics_tiles": (package.graphics_end - package.graphics_base) // 32,
        "referenced_nonblank_tiles": len(seen),
        "titles": titles_json,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    rom = args.rom.read_bytes()
    packages: list[Package] = []
    for header in range(0, len(rom) - 8, 2):
        package = parse_package(rom, header)
        if package is not None:
            packages.append(package)

    args.out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    report = []
    for package_index, package in enumerate(packages):
        package_dir = args.out / f"package_{package.header:06X}"
        package_dir.mkdir(parents=True, exist_ok=True)
        images = [render_title(rom, package, title) for title in package.titles]
        cols = min(3, len(images))
        rows = (len(images) + cols - 1) // cols
        sheet = Image.new("RGB", (224 * cols, (144 + 18) * rows), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for title, image in zip(package.titles, images):
            x = (title.index % cols) * 224
            y = (title.index // cols) * (144 + 18)
            sheet.paste(image, (x, y))
            draw.text((x + 4, y + 145), f"#{title.index:02d}", fill="white", font=font)
            image.resize((448, 288), Image.Resampling.NEAREST).save(package_dir / f"title_{title.index:02d}.png")
        sheet.save(package_dir / "contact.png")
        report.append(package_to_json(package))
        print(
            f"#{package_index:02d} header={package.header:06X} titles={package.count} "
            f"table={package.descriptor_table:06X} graphics={package.graphics_base:06X}-{package.graphics_end - 1:06X} "
            f"tiles={(package.graphics_end - package.graphics_base) // 32}"
        )
    (args.out / "packages.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"packages={len(packages)} report={args.out / 'packages.json'}")


if __name__ == "__main__":
    main()
