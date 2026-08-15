#!/usr/bin/env python3
"""Match title/menu screen tiles against raw WonderSwan ROM graphics."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import decode_ws_4bpp_tile, find_rom, load_rom  # noqa: E402


def decode_ws_2bpp(tile16: bytes) -> list[list[int]]:
    pixels = [[0] * 8 for _ in range(8)]
    for row in range(8):
        p0 = tile16[row * 2]
        p1 = tile16[row * 2 + 1]
        for col in range(8):
            bit = 7 - col
            pixels[row][col] = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
    return pixels


def canonical(values: Iterable[object]) -> bytes:
    """Encode a pixel pattern independent of palette/color numbering."""
    labels: dict[object, int] = {}
    out = bytearray()
    for value in values:
        if value not in labels:
            labels[value] = len(labels)
        out.append(labels[value])
    return bytes(out)


def matrix_key(matrix: Sequence[Sequence[int]]) -> bytes:
    return canonical(value for row in matrix for value in row)


def image_key(image: Image.Image, x: int, y: int) -> tuple[bytes, int]:
    pixels = list(image.crop((x, y, x + 8, y + 8)).getdata())
    return canonical(pixels), len(set(pixels))


def build_index(rom: bytes, stride: int, bpp: int) -> dict[bytes, list[int]]:
    index: dict[bytes, list[int]] = defaultdict(list)
    decoder = decode_ws_4bpp_tile if bpp == 4 else decode_ws_2bpp
    for offset in range(0, len(rom) - stride + 1, stride):
        key = matrix_key(decoder(bytes(rom[offset : offset + stride])))
        if len(index[key]) < 32:
            index[key].append(offset)
    return index


def scan_image(
    image_path: Path,
    indices: dict[int, dict[bytes, list[int]]],
    *,
    region: tuple[int, int, int, int],
) -> list[dict]:
    image = Image.open(image_path).convert("RGB")
    x0, y0, x1, y1 = region
    matches: list[dict] = []
    for y in range(y0, y1 - 7):
        for x in range(x0, x1 - 7):
            key, colors = image_key(image, x, y)
            for bpp, index in indices.items():
                if colors > (16 if bpp == 4 else 4):
                    continue
                offsets = index.get(key)
                if offsets:
                    matches.append(
                        {
                            "image": image_path.name,
                            "x": x,
                            "y": y,
                            "bpp": bpp,
                            "colors": colors,
                            "rom_offsets": [f"{off:06X}" for off in offsets],
                        }
                    )
    return matches


def make_overlay(image_path: Path, matches: list[dict], out_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for match in matches:
        x, y = match["x"], match["y"]
        draw.rectangle((x, y, x + 7, y + 7), outline=(255, 0, 255))
    image.resize((image.width * 3, image.height * 3), Image.Resampling.NEAREST).save(
        out_path
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument(
        "--title",
        type=Path,
        default=ROOT / "out" / "title_trace" / "title.png",
    )
    ap.add_argument(
        "--menu",
        type=Path,
        default=ROOT / "out" / "title_trace" / "menu.png",
    )
    ap.add_argument(
        "--out", type=Path, default=ROOT / "out" / "title_graphics_analysis"
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rom = bytes(load_rom(args.rom or find_rom(ROOT)))
    indices = {
        4: build_index(rom, 32, 4),
        2: build_index(rom, 16, 2),
    }
    all_matches: list[dict] = []
    for path, region in (
        (args.title, (40, 38, 190, 90)),
        (args.menu, (55, 35, 160, 105)),
    ):
        matches = scan_image(path, indices, region=region)
        all_matches.extend(matches)
        make_overlay(path, matches, args.out / f"{path.stem}_matches.png")

    # Prefer matches that form runs of neighboring source tiles. Those are much
    # more likely to be the menu asset than incidental one-tile collisions.
    for match in all_matches:
        offsets = [int(value, 16) for value in match["rom_offsets"]]
        match["neighbor_score"] = sum(
            1
            for other in all_matches
            if other["image"] == match["image"]
            and other["bpp"] == match["bpp"]
            and abs(other["x"] - match["x"]) in (0, 8)
            and abs(other["y"] - match["y"]) in (0, 8)
            and any(
                abs(int(candidate, 16) - offset)
                in (16, 32, 16 * 16, 32 * 16)
                for candidate in other["rom_offsets"]
                for offset in offsets
            )
        )
    all_matches.sort(key=lambda item: (-item["neighbor_score"], item["image"], item["y"], item["x"]))
    report = {
        "rom": str(args.rom or find_rom(ROOT)),
        "method": "palette-independent raw 2bpp/4bpp tile pattern matching",
        "match_count": len(all_matches),
        "matches": all_matches,
    }
    (args.out / "matches.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Found {len(all_matches)} raw tile matches")
    for match in all_matches[:30]:
        print(
            f"{match['image']} ({match['x']},{match['y']}) "
            f"{match['bpp']}bpp score={match['neighbor_score']} "
            f"@{','.join(match['rom_offsets'][:4])}"
        )
    print(f"Wrote {args.out / 'matches.json'}")


if __name__ == "__main__":
    main()
