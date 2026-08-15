#!/usr/bin/env python3
"""Inventory the raw 40x16 bodies used by the battle ID-command plaques.

The live ``attack`` measurement establishes the format: ten packed-4bpp tiles,
stored as five top-row tiles followed by five bottom-row tiles.  This tool keeps
the analysis read-only and renders the neighbouring blocks both with their raw
palette indices and as a binary transparency mask.  Palette index zero is
transparent in the live OBJ path; on the black battle background those holes
form the visible kanji strokes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402


STOCK_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TIP_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/id_command_plaque_analysis"

BLOCK_BYTES = 10 * 0x20
# Bank-4C scan hits whose zero/transparent mask exactly matches the observed
# attack body's rounded-right 40x16 plaque geometry.  These are starts, not an
# assumed fixed-stride partition of the surrounding graphics stream.
CANDIDATE_OFFSETS = [
    0x4C54F4,
    0x4C57B4,
    0x4C5914,
    0x4C5D54,
    0x4C5E94,
    0x4CB7AA,
    0x4CB8EA,
]
BODY_METADATA = {
    0x4C54F4: {"label_jp": "↑命中", "ko_candidate": "↑명중", "storage": "body_plus_shared_cap"},
    0x4C57B4: {"label_jp": "↑回避", "ko_candidate": "↑회피", "storage": "body_plus_shared_cap"},
    0x4C5914: {"label_jp": "↓回避", "ko_candidate": "↓회피", "storage": "body_plus_shared_cap"},
    0x4C5D54: {"label_jp": "↑攻撃", "ko_candidate": "↑공격", "storage": "body_plus_shared_cap"},
    0x4C5E94: {"label_jp": "↓攻撃", "ko_candidate": "↓공격", "storage": "body_plus_shared_cap"},
    0x4CB7AA: {"label_jp": "↑電撃", "ko_candidate": "↑전격", "storage": "body_plus_shared_cap"},
    0x4CB8EA: {"label_jp": "↓電撃", "ko_candidate": "↓전격", "storage": "body_plus_shared_cap"},
}
FULL_PLAQUE_BYTES = 12 * 0x20
# Exact rounded 48x16 silhouette matches across every byte phase in bank 4C.
# They include battle results, ID-command stat changes, and status effects.
FULL_PLAQUE_OFFSETS = [
    0x4C44D4,
    0x4C4654,
    0x4C48F4,
    0x4C5234,
    0x4C5634,
    0x4C5A54,
    0x4C5BD4,
    0x4CB38A,
    0x4CB62A,
    0x4CBA2A,
    0x4CBBAA,
    0x4CBD2A,
    0x4CC02A,
    0x4CC1AA,
    0x4CC52A,
    0x4CE56A,
    0x4CE6EA,
]
FULL_METADATA = {
    0x4C44D4: {"label_jp": "↑LEVEL", "ko_candidate": "↑레벨", "storage": "full_48x16"},
    0x4C4654: {"label_jp": "成功!", "ko_candidate": "성공!", "storage": "full_48x16"},
    0x4C48F4: {"label_jp": "撃破!", "ko_candidate": "격파!", "storage": "full_48x16"},
    0x4C5234: {"label_jp": "捨て身!", "ko_candidate": "육탄!", "storage": "full_48x16"},
    0x4C5634: {"label_jp": "↓命中", "ko_candidate": "↓명중", "storage": "full_48x16"},
    0x4C5A54: {"label_jp": "↑機動", "ko_candidate": "↑기동", "storage": "full_48x16"},
    0x4C5BD4: {"label_jp": "↓機動", "ko_candidate": "↓기동", "storage": "full_48x16"},
    0x4CB38A: {"label_jp": "↑防御", "ko_candidate": "↑방어", "storage": "full_48x16"},
    0x4CB62A: {"label_jp": "↓防御", "ko_candidate": "↓방어", "storage": "full_48x16"},
    0x4CBA2A: {"label_jp": "↑反応", "ko_candidate": "↑반응", "storage": "full_48x16"},
    0x4CBBAA: {"label_jp": "↓反応", "ko_candidate": "↓반응", "storage": "full_48x16"},
    0x4CBD2A: {"label_jp": "↑移動", "ko_candidate": "↑이동", "storage": "full_48x16"},
    0x4CC02A: {"label_jp": "手加減", "ko_candidate": "봐주기", "storage": "full_48x16"},
    0x4CC1AA: {"label_jp": "足止め", "ko_candidate": "발묶기", "storage": "full_48x16"},
    0x4CC52A: {"label_jp": "HP回復", "ko_candidate": "HP회복", "storage": "full_48x16"},
    0x4CE56A: {"label_jp": "ID消去", "ko_candidate": "ID소거", "storage": "full_48x16"},
    0x4CE6EA: {"label_jp": "散開!", "ko_candidate": "산개!", "storage": "full_48x16"},
}
OBSERVED_ATTACK_LO = 0x4C5D54
OBSERVED_FULL_GEOMETRY_LO = 0x4C5BD4
SHARED_CAP_TOP_LO = 0x4C44D4
SHARED_CAP_BOTTOM_LO = 0x4C4594


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def all_hits(haystack: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    start = 0
    while True:
        hit = haystack.find(needle, start)
        if hit < 0:
            return hits
        hits.append(hit)
        start = hit + 1


def decode_body(raw: bytes) -> list[list[int]]:
    decoded = tiles_4bpp(raw)
    if len(decoded) != 10:
        raise ValueError("a plaque body must contain exactly ten tiles")
    pixels = [[0] * 40 for _ in range(16)]
    for index, tile in enumerate(decoded):
        ox = (index % 5) * 8
        oy = (index // 5) * 8
        for y in range(8):
            pixels[oy + y][ox : ox + 8] = tile[y]
    return pixels


def decode_full_plaque(raw: bytes) -> list[list[int]]:
    decoded = tiles_4bpp(raw)
    if len(decoded) != 12:
        raise ValueError("a full plaque must contain exactly twelve tiles")
    pixels = [[0] * 48 for _ in range(16)]
    for index, tile in enumerate(decoded):
        ox = (index % 6) * 8
        oy = (index // 6) * 8
        for y in range(8):
            pixels[oy + y][ox : ox + 8] = tile[y]
    return pixels


def compose_body_with_shared_cap(rom: bytes, body_pixels: list[list[int]]) -> list[list[int]]:
    top = tiles_4bpp(rom[SHARED_CAP_TOP_LO : SHARED_CAP_TOP_LO + 0x20])[0]
    bottom = tiles_4bpp(rom[SHARED_CAP_BOTTOM_LO : SHARED_CAP_BOTTOM_LO + 0x20])[0]
    return [
        (top[y] if y < 8 else bottom[y - 8]) + body_pixels[y]
        for y in range(16)
    ]


LIVE_OBJ_PALETTE_5 = {
    0x0: (0, 0, 0),
    0xB: (170, 255, 187),
    0xC: (68, 255, 68),
    0xD: (0, 204, 0),
    0xE: (0, 68, 0),
    0xF: (255, 255, 255),
}


def render_body(pixels: list[list[int]], *, mode: str, scale: int) -> Image.Image:
    pixel_width = len(pixels[0])
    image = Image.new("RGB", (pixel_width, 16))
    out = image.load()
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            if mode == "zero_mask":
                # Zero is transparent in the OBJ path and reveals the black
                # battle backdrop.  This mask mainly exposes outer cut-outs.
                out[x, y] = (0, 0, 0) if value == 0 else (255, 255, 255)
            elif mode == "ink_mask":
                # The observed attack glyph's darkest drawn ink is index 0xE.
                # Include transparent zero so edge cut-outs remain legible.
                out[x, y] = (0, 0, 0) if value in {0, 0xE} else (255, 255, 255)
            elif mode == "live_palette":
                out[x, y] = LIVE_OBJ_PALETTE_5.get(value, GREYS_16[value])
            else:
                out[x, y] = GREYS_16[value]
    if scale > 1:
        image = image.resize((pixel_width * scale, 16 * scale), Image.Resampling.NEAREST)
    return image


def render_sheet(rows: list[dict], path: Path, *, mode: str, scale: int) -> None:
    font = ImageFont.load_default()
    label_h = 15
    width = len(rows[0]["pixels"][0]) * scale
    height = 16 * scale + label_h
    sheet = Image.new("RGB", (width, height * len(rows)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    for index, row in enumerate(rows):
        body = render_body(row["pixels"], mode=mode, scale=scale)
        y = index * height
        sheet.paste(body, (0, y))
        suffix = " observed attack" if row["logical_start"] == OBSERVED_ATTACK_LO else ""
        draw.text(
            (2, y + 16 * scale + 1),
            f"#{row['index']:02d} {row['logical_start']:06X}{suffix}",
            fill=(255, 255, 255),
            font=font,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def encoded_pointer_hits(rom: bytes, logical: int) -> dict[str, list[str]]:
    bank = (logical >> 16) & 0xFF
    offset = logical & 0xFFFF
    patterns = {
        "offset_le16": offset.to_bytes(2, "little"),
        "offset_le16_bank": offset.to_bytes(2, "little") + bytes([bank]),
        "bank_offset_le16": bytes([bank]) + offset.to_bytes(2, "little"),
    }


def zero_mask(pixels: list[list[int]]) -> bytes:
    return bytes(value == 0 for row in pixels for value in row)


def scan_matching_geometry(rom: bytes, observed_pixels: list[list[int]]) -> list[int]:
    """Scan every byte phase in bank 4C for the attack-body silhouette."""
    wanted = zero_mask(observed_pixels)
    matches: list[int] = []
    for logical in range(0x4C0000, 0x4D0000 - BLOCK_BYTES + 1):
        if zero_mask(decode_body(rom[logical : logical + BLOCK_BYTES])) == wanted:
            matches.append(logical)
    return matches


def scan_matching_full_geometry(rom: bytes, observed_pixels: list[list[int]]) -> list[int]:
    wanted = zero_mask(observed_pixels)
    matches: list[int] = []
    for logical in range(0x4C0000, 0x4D0000 - FULL_PLAQUE_BYTES + 1):
        if zero_mask(decode_full_plaque(rom[logical : logical + FULL_PLAQUE_BYTES])) == wanted:
            matches.append(logical)
    return matches
    return {
        name: [f"{hit:06X}" for hit in all_hits(rom, pattern)]
        for name, pattern in patterns.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-rom", type=Path, default=STOCK_ROM)
    parser.add_argument("--tip-rom", type=Path, default=TIP_ROM)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scale", type=int, default=7)
    args = parser.parse_args(argv)

    stock = args.stock_rom.read_bytes()
    tip = args.tip_rom.read_bytes()
    tip_stock_base = stock_base(tip)
    before_tip = sha256(tip)

    observed_pixels = decode_body(stock[OBSERVED_ATTACK_LO : OBSERVED_ATTACK_LO + BLOCK_BYTES])
    geometry_hits = scan_matching_geometry(stock, observed_pixels)
    if geometry_hits != CANDIDATE_OFFSETS:
        raise RuntimeError(
            "bank-4C plaque geometry hit set changed: "
            f"expected {[f'{x:06X}' for x in CANDIDATE_OFFSETS]}, "
            f"got {[f'{x:06X}' for x in geometry_hits]}"
        )

    observed_full_pixels = decode_full_plaque(
        stock[
            OBSERVED_FULL_GEOMETRY_LO : OBSERVED_FULL_GEOMETRY_LO + FULL_PLAQUE_BYTES
        ]
    )
    full_geometry_hits = scan_matching_full_geometry(stock, observed_full_pixels)
    if full_geometry_hits != FULL_PLAQUE_OFFSETS:
        raise RuntimeError(
            "bank-4C full-plaque geometry hit set changed: "
            f"expected {[f'{x:06X}' for x in FULL_PLAQUE_OFFSETS]}, "
            f"got {[f'{x:06X}' for x in full_geometry_hits]}"
        )

    rows: list[dict] = []
    for index, logical in enumerate(CANDIDATE_OFFSETS):
        raw = stock[logical : logical + BLOCK_BYTES]
        pixels = decode_body(raw)
        rows.append(
            {
                "index": index,
                "logical_start": logical,
                "pixels": pixels,
                "manifest": {
                    "index": index,
                    **BODY_METADATA[logical],
                    "logical_rom": f"{logical:06X}-{logical + BLOCK_BYTES - 1:06X}",
                    "tip_physical": (
                        f"{tip_stock_base + logical:06X}-"
                        f"{tip_stock_base + logical + BLOCK_BYTES - 1:06X}"
                    ),
                    "bytes": BLOCK_BYTES,
                    "tiles": 10,
                    "is_observed_attack": logical == OBSERVED_ATTACK_LO,
                    "runtime_evidence": (
                        "QuickSave6 exact live OBJ/VRAM/ROM match"
                        if logical == OBSERVED_ATTACK_LO
                        else "static geometry and glyph reading; no matching live savestate"
                    ),
                    "sha256": sha256(raw),
                    "tip_bytes_match_stock": (
                        tip[tip_stock_base + logical : tip_stock_base + logical + BLOCK_BYTES]
                        == raw
                    ),
                    "exact_full_block_hits_in_stock": [
                        f"{hit:06X}" for hit in all_hits(stock, raw)
                    ],
                    "palette_indices": sorted({value for row in pixels for value in row}),
                    "encoded_pointer_hits": encoded_pointer_hits(stock, logical),
                },
            }
        )

    composed_rows = [
        {
            "index": row["index"],
            "logical_start": row["logical_start"],
            "pixels": compose_body_with_shared_cap(stock, row["pixels"]),
        }
        for row in rows
    ]


    full_rows: list[dict] = []
    for index, logical in enumerate(FULL_PLAQUE_OFFSETS):
        raw = stock[logical : logical + FULL_PLAQUE_BYTES]
        pixels = decode_full_plaque(raw)
        full_rows.append(
            {
                "index": index,
                "logical_start": logical,
                "pixels": pixels,
                "manifest": {
                    "index": index,
                    **FULL_METADATA[logical],
                    "logical_rom": f"{logical:06X}-{logical + FULL_PLAQUE_BYTES - 1:06X}",
                    "tip_physical": (
                        f"{tip_stock_base + logical:06X}-"
                        f"{tip_stock_base + logical + FULL_PLAQUE_BYTES - 1:06X}"
                    ),
                    "bytes": FULL_PLAQUE_BYTES,
                    "tiles": 12,
                    "runtime_evidence": "static geometry and glyph reading; no matching live savestate",
                    "sha256": sha256(raw),
                    "tip_bytes_match_stock": (
                        tip[
                            tip_stock_base + logical :
                            tip_stock_base + logical + FULL_PLAQUE_BYTES
                        ]
                        == raw
                    ),
                    "exact_full_block_hits_in_stock": [
                        f"{hit:06X}" for hit in all_hits(stock, raw)
                    ],
                    "palette_indices": sorted({value for row in pixels for value in row}),
                    "encoded_pointer_hits": encoded_pointer_hits(stock, logical),
                },
            }
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    render_sheet(rows, args.out_dir / "candidate_bodies_greyscale.png", mode="greyscale", scale=args.scale)
    render_sheet(rows, args.out_dir / "candidate_bodies_live_palette.png", mode="live_palette", scale=args.scale)
    render_sheet(rows, args.out_dir / "candidate_bodies_zero_mask.png", mode="zero_mask", scale=args.scale)
    render_sheet(rows, args.out_dir / "candidate_bodies_ink_mask.png", mode="ink_mask", scale=args.scale)
    render_sheet(
        composed_rows,
        args.out_dir / "candidate_bodies_with_shared_cap_live_palette.png",
        mode="live_palette",
        scale=args.scale,
    )
    render_sheet(
        composed_rows,
        args.out_dir / "candidate_bodies_with_shared_cap_ink_mask.png",
        mode="ink_mask",
        scale=args.scale,
    )
    render_sheet(full_rows, args.out_dir / "full_plaques_greyscale.png", mode="greyscale", scale=args.scale)
    render_sheet(full_rows, args.out_dir / "full_plaques_live_palette.png", mode="live_palette", scale=args.scale)
    render_sheet(full_rows, args.out_dir / "full_plaques_ink_mask.png", mode="ink_mask", scale=args.scale)

    report = {
        "schema_version": 1,
        "read_only": True,
        "basis": {
            "observed_attack_logical": f"{OBSERVED_ATTACK_LO:06X}",
            "body_geometry": "40x16, ten row-major packed-4bpp tiles",
            "live_obj_geometry": "48x16 = shared 8x16 left cap + 40x16 body",
            "live_obj_palette_5": {f"{key:X}": list(value) for key, value in LIVE_OBJ_PALETTE_5.items()},
            "palette_basis": (
                "pixel-exact correlation of the observed attack body with "
                "state6_plus24 framebuffer at x=92..131, y=44..59"
            ),
            "zero_mask_meaning": "OBJ palette index 0 is transparent and reveals the black backdrop",
            "ink_mask_meaning": "indices 0 and E, the transparent cut-outs plus darkest drawn glyph ink",
            "shared_cap": {
                "top_tile_logical": f"{SHARED_CAP_TOP_LO:06X}",
                "bottom_tile_logical": f"{SHARED_CAP_BOTTOM_LO:06X}",
                "top_exact_tile_hits": [
                    f"{hit:06X}"
                    for hit in all_hits(stock, stock[SHARED_CAP_TOP_LO : SHARED_CAP_TOP_LO + 0x20])
                ],
                "bottom_exact_tile_hits": [
                    f"{hit:06X}"
                    for hit in all_hits(stock, stock[SHARED_CAP_BOTTOM_LO : SHARED_CAP_BOTTOM_LO + 0x20])
                ],
                "composition": "one top and one bottom 8x8 tile prepended to a 5x2 body",
            },
            "geometry_scan": {
                "bank": "4C",
                "scan_scope": "every byte phase in bank 4C",
                "hit_phases_mod_32": sorted({f"{logical & 0x1F:02X}" for logical in geometry_hits}),
                "predicate": "zero/transparency mask exactly equals observed attack 40x16 body",
                "hit_count": len(geometry_hits),
                "hits": [f"{logical:06X}" for logical in geometry_hits],
            },
            "full_geometry_scan": {
                "bank": "4C",
                "scan_scope": "every byte phase in bank 4C",
                "hit_phases_mod_32": sorted({f"{logical & 0x1F:02X}" for logical in full_geometry_hits}),
                "predicate": "zero/transparency mask exactly equals observed rounded 48x16 block",
                "hit_count": len(full_geometry_hits),
                "hits": [f"{logical:06X}" for logical in full_geometry_hits],
            },
        },
        "stock_rom": {
            "path": str(args.stock_rom),
            "sha256": sha256(stock),
        },
        "tip_rom": {
            "path": str(args.tip_rom),
            "stock_base": f"{tip_stock_base:06X}",
            "sha256_before": before_tip,
            "sha256_after": sha256(args.tip_rom.read_bytes()),
            "unchanged": before_tip == sha256(args.tip_rom.read_bytes()),
        },
        "body_only_candidates": [row["manifest"] for row in rows],
        "full_plaque_candidates": [row["manifest"] for row in full_rows],
        "all_tip_asset_bytes_match_stock": all(
            row["manifest"]["tip_bytes_match_stock"] for row in rows + full_rows
        ),
    }
    (args.out_dir / "plaque_inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out_dir / 'plaque_inventory.json'}")
    print(f"candidate bodies: {len(rows)}; full plaques: {len(full_rows)}")
    print(f"TIP unchanged: {report['tip_rom']['unchanged']} {report['tip_rom']['sha256_after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
