#!/usr/bin/env python3
"""Statically trace the animated intermission focus layer through savestates.

BizHawk 2.11.1 serializes ``Core.bin`` as a four-byte native-core length,
followed by Cygne's GFX state and then the 64 KiB WonderSwan RAM image.  For this
build the GFX payload is 0x94E bytes, so wsRAM starts at Core.bin offset 0x952.

The supplied states show that the active focus plate is not one of the 158
background/label tiles patched by ``build_intermission_state_ab.py``.  It is a
sprite cluster (attribute 0x35) backed by tile numbers 0x110 and above.  This
tool extracts that cluster, maps each live wsRAM tile back to exact ROM bytes,
and renders a greyscale proof image without emulating menu input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402


STATE_DIR = (
    ROOT
    / "BizHawk-2.11.1-win-x64/WonderSwan/State"
    / "monoeye ko expanded.Cygne"
)
DEFAULT_OUT = ROOT / "out/patch/intermission_focus_trace/sprite_trace"

NATIVE_LENGTH_BYTES = 4
GFX_STATE_BYTES = 0x94E
WSRAM_CORE_OFFSET = NATIVE_LENGTH_BYTES + GFX_STATE_BYTES  # 0x952
WSRAM_BYTES = 0x10000

GFX_CORE_OFFSET = NATIVE_LENGTH_BYTES
SPRITE_TABLE_GFX_OFFSET = 0x525
SPRITE_COUNT_GFX_OFFSET = 0x725
NEXT_SPRITE_TABLE_GFX_OFFSET = 0x729
NEXT_SPRITE_COUNT_GFX_OFFSET = 0x929

SPRITE_TABLE_CORE_OFFSET = GFX_CORE_OFFSET + SPRITE_TABLE_GFX_OFFSET
SPRITE_COUNT_CORE_OFFSET = GFX_CORE_OFFSET + SPRITE_COUNT_GFX_OFFSET
NEXT_SPRITE_TABLE_CORE_OFFSET = GFX_CORE_OFFSET + NEXT_SPRITE_TABLE_GFX_OFFSET
NEXT_SPRITE_COUNT_CORE_OFFSET = GFX_CORE_OFFSET + NEXT_SPRITE_COUNT_GFX_OFFSET

FOCUS_ATTR = 0x35


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def all_hits(haystack: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return hits
        hits.append(found)
        start = found + 1


def parse_sprites(core: bytes, table_offset: int, count_offset: int) -> list[dict]:
    count = min(struct.unpack_from("<I", core, count_offset)[0], 0x80)
    out = []
    for index in range(count):
        tile_lo, attr, y, x_raw = core[
            table_offset + index * 4 : table_offset + index * 4 + 4
        ]
        x = x_raw - 0x100 if x_raw >= 249 else x_raw
        tile = tile_lo | ((attr & 1) << 8)
        out.append(
            {
                "index": index,
                "tile": tile,
                "attr": attr,
                "x": x,
                "y": y,
                # Sprite GetTile() always passes bank=0. In WSC packed-4bpp mode
                # Cygne resolves that to 0x4000 + tile_number*32.
                "wsram_offset": 0x4000 + tile * 0x20,
                "flip_h": bool(attr & 0x40),
                "flip_v": bool(attr & 0x80),
                "palette": (attr >> 1) & 7,
            }
        )
    return out


def render_cluster(ram: bytes, sprites: list[dict], scale: int) -> tuple[Image.Image, list[int]]:
    left = min(sprite["x"] for sprite in sprites)
    top = min(sprite["y"] for sprite in sprites)
    right = max(sprite["x"] + 8 for sprite in sprites)
    bottom = max(sprite["y"] + 8 for sprite in sprites)
    image = Image.new("RGB", (right - left, bottom - top), (255, 0, 255))
    pixels = image.load()
    # Cygne renders lower sprite-table indices last because it walks the table in
    # reverse. Reproduce that order for overlaps and deliberate duplicate tiles.
    for sprite in reversed(sprites):
        off = sprite["wsram_offset"]
        tile = tiles_4bpp(ram[off : off + 0x20])[0]
        for dy in range(8):
            sy = 7 - dy if sprite["flip_v"] else dy
            for dx in range(8):
                sx = 7 - dx if sprite["flip_h"] else dx
                value = tile[sy][sx]
                if value:
                    pixels[sprite["x"] - left + dx, sprite["y"] - top + dy] = (
                        GREYS_16[value]
                    )
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
    return image, [left, top, right, bottom]


def compact_runs(values: list[int], step: int = 0x20) -> list[dict]:
    if not values:
        return []
    values = sorted(set(values))
    runs: list[list[int]] = [[values[0], values[0]]]
    for value in values[1:]:
        if value == runs[-1][1] + step:
            runs[-1][1] = value
        else:
            runs.append([value, value])
    return [
        {
            "first_tile": f"{start:06X}",
            "last_tile": f"{end:06X}",
            "tile_count": (end - start) // step + 1,
            "byte_end_exclusive": f"{end + step:06X}",
        }
        for start, end in runs
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    )
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--scale", type=int, default=5)
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    args = ap.parse_args(argv)

    states = [
        ("state1_save", STATE_DIR / "Mednafen.QuickSave1.State"),
        ("state2_development_plan", STATE_DIR / "Mednafen.QuickSave2.State"),
        ("state3_supply", STATE_DIR / "Mednafen.QuickSave3.State"),
        (
            "ab_final_supply",
            ROOT / "out/patch/intermission_state_ab/B_focus_final.State",
        ),
    ]
    rom = args.rom.read_bytes()
    zstd = Zstd(args.zstd_dll)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "core_layout": {
            "native_length_prefix": "000000-000003",
            "gfx_state": "000004-000951",
            "wsram": "000952-010951",
            "wsram_bytes": WSRAM_BYTES,
            "basis": "BizHawk 2.11.1 wonderswan System::SyncState/GFX::SyncState/Memory::SyncState",
        },
        "sprite_rule": {
            "focus_attr": f"{FOCUS_ATTR:02X}",
            "tile_address": "wsRAM[0x4000 + tile_number*0x20] (WSC mode 7, sprite bank 0)",
        },
        "rom": str(args.rom),
        "rom_sha256": sha256(rom),
        "states": [],
    }

    for name, path in states:
        if not path.exists():
            continue
        core, _ = read_state_core(path, zstd)
        native_size = struct.unpack_from("<I", core, 0)[0]
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        current = parse_sprites(core, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET)
        focus = [sprite for sprite in current if sprite["attr"] == FOCUS_ATTR]
        if not focus:
            raise RuntimeError(f"{path} has no attr={FOCUS_ATTR:02X} focus sprites")

        preview, bounds = render_cluster(ram, focus, args.scale)
        preview_path = args.out_dir / f"{name}_focus_sprite.png"
        preview.save(preview_path)

        sprite_rows = []
        preferred_bank54: list[int] = []
        for sprite in focus:
            off = sprite["wsram_offset"]
            raw = bytes(ram[off : off + 0x20])
            hits = all_hits(rom, raw)
            bank54 = [hit for hit in hits if 0x540000 <= hit < 0x550000]
            if len(bank54) == 1:
                preferred_bank54.append(bank54[0])
            sprite_rows.append(
                {
                    **{
                        key: (f"{value:02X}" if key == "attr" else value)
                        for key, value in sprite.items()
                    },
                    "tile": f"{sprite['tile']:03X}",
                    "wsram_offset": f"{off:04X}",
                    "tile_sha256": sha256(raw),
                    "rom_exact_hits": [f"{hit:06X}" for hit in hits[:64]],
                    "bank54_unique_hit": f"{bank54[0]:06X}" if len(bank54) == 1 else None,
                }
            )

        with zipfile.ZipFile(path) as archive:
            framebuffer = Image.open(BytesIO(archive.read("Framebuffer.bmp"))).convert("RGB")
            framebuffer_path = args.out_dir / f"{name}_framebuffer.png"
            framebuffer.save(framebuffer_path)

        report["states"].append(
            {
                "name": name,
                "path": str(path),
                "core_sha256": sha256(core),
                "native_core_bytes": native_size,
                "current_sprite_count": len(current),
                "focus_sprite_count": len(focus),
                "focus_bounds_xyxy": bounds,
                "focus_tile_min": f"{min(s['tile'] for s in focus):03X}",
                "focus_tile_max": f"{max(s['tile'] for s in focus):03X}",
                "focus_wsram_min": f"{min(s['wsram_offset'] for s in focus):04X}",
                "focus_wsram_max": f"{max(s['wsram_offset'] for s in focus):04X}",
                "preview": str(preview_path),
                "framebuffer": str(framebuffer_path),
                "unique_bank54_source_runs": compact_runs(preferred_bank54),
                "sprites": sprite_rows,
            }
        )
        print(
            f"{name}: {len(focus)} focus sprites, bounds={bounds}, "
            f"VRAM {min(s['wsram_offset'] for s in focus):04X}-"
            f"{max(s['wsram_offset'] for s in focus) + 0x1F:04X}"
        )
        print(f"  source runs: {compact_runs(preferred_bank54)}")

    report_path = args.out_dir / "focus_sprite_trace.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
