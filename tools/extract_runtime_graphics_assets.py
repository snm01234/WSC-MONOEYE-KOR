#!/usr/bin/env python3
"""Read-only extraction of the runtime graphics identified from QuickSave4/5/6.

This tool does not patch either ROM.  It turns the measurements from the supplied
BizHawk 2.11.1 Cygne states into reusable raw 4bpp blobs, PNG contact sheets and
a machine-readable address manifest.

Sources
-------
* QuickSave4: stage-entry title (BG tilemap, no sprites)
* state5_plus24.State: the visible ``クリティカル!`` sprite popup
* state6_plus24.State: the visible ID-command ``↑攻撃`` plaque

Logical ROM offsets below refer to the original 8 MiB ROM body.  The current
16 MiB TIP keeps that body at +0x800000, so the manifest also records physical
TIP offsets where appropriate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from collections import defaultdict
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base  # noqa: E402
from render_bank_tiles import GREYS_16, tiles_4bpp  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    NEXT_SPRITE_COUNT_CORE_OFFSET,
    NEXT_SPRITE_TABLE_CORE_OFFSET,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    parse_sprites,
)

STOCK_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TIP_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
STATE_DIR = ROOT / "BizHawk-2.11.1-win-x64/WonderSwan/State/monoeye ko expanded.Cygne"
PROBE_DIR = ROOT / "out/patch/runtime_graphics_state_probe"
DEFAULT_OUT = ROOT / "out/patch/runtime_graphics_extract"
ZSTD_DLL = ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll"

# Measured in QuickSave4.  These 19 sequential ROM tiles are the exact live BG
# tiles 01E..030 used by the stage title capture.
STAGE_TITLE_LO = 0x4AC166
STAGE_TITLE_TILES = 19
# The surrounding long two-colour raw area.  It is extracted as a candidate
# stage-title/large-label atlas, without claiming per-title boundaries yet.
STAGE_ATLAS_A_LO = 0x4AC166
STAGE_ATLAS_A_TILES = 275
STAGE_ATLAS_B_LO = 0x4AE3E6
STAGE_ATLAS_B_TILES = 55

# Phase-0x12 source tile atlas containing all observed critical glyph pieces.
BATTLE_ATLAS_LO = 0x107412
BATTLE_ATLAS_HI = 0x108892

# 22 consecutive 0x140-byte, 5x2-tile plaque bodies around the observed attack
# body.  The observed body is block index 20 (0x4C5D54).
ID_BLOCK_LO = 0x4C4454
ID_BLOCK_BYTES = 0x140
ID_BLOCK_COUNT = 22
ID_ATTACK_BODY_LO = 0x4C5D54

CRITICAL_CHAR_BY_X = {
    92: "ク",
    100: "リ",
    108: "テ",
    116: "ィ",
    124: "カ",
    132: "ル",
    140: "!",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def all_hits(haystack: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        hit = haystack.find(needle, start)
        if hit < 0:
            return out
        out.append(hit)
        start = hit + 1


def decode_tile(raw: bytes) -> list[list[int]]:
    return tiles_4bpp(raw[:32])[0]


def draw_tile(dst: Image.Image, tile: list[list[int]], ox: int, oy: int, scale: int = 1) -> None:
    px = dst.load()
    for y in range(8):
        for x in range(8):
            c = GREYS_16[tile[y][x]]
            for sy in range(scale):
                for sx in range(scale):
                    px[ox + x * scale + sx, oy + y * scale + sy] = c


def render_tiles_contact(
    raw: bytes,
    base: int,
    path: Path,
    *,
    cols: int = 16,
    scale: int = 3,
    labels: bool = True,
) -> None:
    tiles = tiles_4bpp(raw)
    rows = (len(tiles) + cols - 1) // cols
    label_h = 11 if labels else 0
    cell_w = 8 * scale
    cell_h = 8 * scale + label_h
    img = Image.new("RGB", (cols * cell_w, rows * cell_h), (32, 32, 32))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    for i, tile in enumerate(tiles):
        c, r = i % cols, i // cols
        ox, oy = c * cell_w, r * cell_h
        draw_tile(img, tile, ox, oy, scale)
        if labels:
            draw.text((ox, oy + 8 * scale), f"{base + i * 0x20:06X}", fill=(255, 255, 255), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def render_block_5x2(raw: bytes, scale: int = 4) -> Image.Image:
    ts = tiles_4bpp(raw)
    if len(ts) != 10:
        raise ValueError("5x2 body must contain 10 tiles")
    img = Image.new("RGB", (40 * scale, 16 * scale), (0, 0, 0))
    for i, tile in enumerate(ts):
        draw_tile(img, tile, (i % 5) * 8 * scale, (i // 5) * 8 * scale, scale)
    return img


def render_id_block_sheet(rom: bytes, out: Path) -> list[dict]:
    font = ImageFont.load_default()
    scale = 3
    label_h = 14
    cell_w, cell_h = 40 * scale, 16 * scale + label_h
    cols = 4
    rows = (ID_BLOCK_COUNT + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    rows_json = []
    for i in range(ID_BLOCK_COUNT):
        off = ID_BLOCK_LO + i * ID_BLOCK_BYTES
        raw = rom[off : off + ID_BLOCK_BYTES]
        body = render_block_5x2(raw, scale)
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        sheet.paste(body, (x, y))
        draw.text((x, y + 16 * scale), f"#{i:02d} {off:06X}", fill=(255, 255, 255), font=font)
        rows_json.append(
            {
                "index": i,
                "logical_rom": f"{off:06X}-{off + ID_BLOCK_BYTES - 1:06X}",
                "bytes": ID_BLOCK_BYTES,
                "is_observed_attack_body": off == ID_ATTACK_BODY_LO,
                "nibble_values": sorted({n for b in raw for n in (b >> 4, b & 0x0F)}),
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return rows_json


def state_core_and_frame(path: Path, zstd: Zstd) -> tuple[bytes, Image.Image]:
    core, _ = read_state_core(path, zstd)
    with zipfile.ZipFile(path) as zf:
        frame = Image.open(BytesIO(zf.read("Framebuffer.bmp"))).convert("RGB")
    return core, frame


def current_sprites(core: bytes) -> list[dict]:
    cur = parse_sprites(core, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET)
    if cur:
        return cur
    return parse_sprites(core, NEXT_SPRITE_TABLE_CORE_OFFSET, NEXT_SPRITE_COUNT_CORE_OFFSET)


def render_sprite_selection(ram: bytes, sprites: list[dict], path: Path, scale: int = 5) -> list[int]:
    left = min(row["x"] for row in sprites)
    top = min(row["y"] for row in sprites)
    right = max(row["x"] + 8 for row in sprites)
    bottom = max(row["y"] + 8 for row in sprites)
    img = Image.new("RGB", ((right - left) * scale, (bottom - top) * scale), (0, 0, 0))
    for row in reversed(sprites):
        raw = ram[row["wsram_offset"] : row["wsram_offset"] + 0x20]
        tile = decode_tile(raw)
        if row.get("flip_h"):
            tile = [list(reversed(r)) for r in tile]
        if row.get("flip_v"):
            tile = list(reversed(tile))
        draw_tile(img, tile, (row["x"] - left) * scale, (row["y"] - top) * scale, scale)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return [left, top, right, bottom]


def extract_stage(stock: bytes, tip_base: int, zstd: Zstd, out: Path) -> dict:
    stage_dir = out / "stage_title"
    stage_dir.mkdir(parents=True, exist_ok=True)
    state = STATE_DIR / "Mednafen.QuickSave4.State"
    core, frame = state_core_and_frame(state, zstd)
    ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]

    raw = stock[STAGE_TITLE_LO : STAGE_TITLE_LO + STAGE_TITLE_TILES * 0x20]
    (stage_dir / "dakar_title_tiles.bin").write_bytes(raw)
    render_tiles_contact(raw, STAGE_TITLE_LO, stage_dir / "dakar_title_tiles_contact.png", cols=10, scale=4)

    # Exact native capture and a tight crop around the title itself.
    frame.save(stage_dir / "quicksave4_framebuffer.png")
    frame.crop((64, 48, 160, 88)).resize((384, 160), Image.Resampling.NEAREST).save(
        stage_dir / "dakar_title_screen_crop_x4.png"
    )

    live = []
    for row in range(18):
        for col in range(28):
            mo = 0x3800 + (row * 32 + col) * 2
            entry = int.from_bytes(ram[mo : mo + 2], "little")
            tid = entry & 0x1FF
            if 0x01E <= tid <= 0x030:
                live.append(
                    {
                        "col": col,
                        "row": row,
                        "tile": f"{tid:03X}",
                        "tilemap_entry": f"{entry:04X}",
                        "wsram_tile": f"{0x4000 + tid * 0x20:04X}",
                        "logical_rom": f"{STAGE_TITLE_LO + (tid - 0x01E) * 0x20:06X}",
                    }
                )

    atlas_rows = []
    for name, lo, count in (
        ("candidate_atlas_a", STAGE_ATLAS_A_LO, STAGE_ATLAS_A_TILES),
        ("candidate_atlas_b", STAGE_ATLAS_B_LO, STAGE_ATLAS_B_TILES),
    ):
        blob = stock[lo : lo + count * 0x20]
        (stage_dir / f"{name}.bin").write_bytes(blob)
        render_tiles_contact(blob, lo, stage_dir / f"{name}_contact.png", cols=16, scale=3)
        atlas_rows.append(
            {
                "name": name,
                "logical_rom": f"{lo:06X}-{lo + len(blob) - 1:06X}",
                "tiles": count,
                "bytes": len(blob),
                "nibble_values": sorted({n for b in blob for n in (b >> 4, b & 0x0F)}),
            }
        )

    return {
        "state": str(state),
        "renderer": "BG tilemap @ wsRAM 0x3800; no live sprites",
        "observed_title": "ダカールの灯火",
        "suggested_korean": "다카르의 등불",
        "source": {
            "logical_rom": f"{STAGE_TITLE_LO:06X}-{STAGE_TITLE_LO + len(raw) - 1:06X}",
            "tip_physical": f"{tip_base + STAGE_TITLE_LO:06X}-{tip_base + STAGE_TITLE_LO + len(raw) - 1:06X}",
            "tiles": STAGE_TITLE_TILES,
            "bytes": len(raw),
            "raw_file": str(stage_dir / "dakar_title_tiles.bin"),
        },
        "live_tilemap": live,
        "candidate_atlases": atlas_rows,
    }


def extract_critical(stock: bytes, tip_base: int, zstd: Zstd, out: Path) -> dict:
    d = out / "battle_popup"
    d.mkdir(parents=True, exist_ok=True)
    state = PROBE_DIR / "state5_plus24.State"
    core, frame = state_core_and_frame(state, zstd)
    ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    sprites = current_sprites(core)
    critical = [row for row in sprites if row["attr"] == 0x2E and 92 <= row["x"] <= 140 and 56 <= row["y"] <= 64]
    if len(critical) != 13:
        raise RuntimeError(f"expected 13 critical sprites, got {len(critical)}")

    bbox = render_sprite_selection(ram, critical, d / "critical_runtime_tiles.png", scale=6)
    frame.save(d / "state5_plus24_framebuffer.png")
    frame.crop((84, 48, 152, 80)).resize((408, 192), Image.Resampling.NEAREST).save(
        d / "critical_screen_crop_x6.png"
    )

    rows = []
    by_x: dict[int, list[dict]] = defaultdict(list)
    for row in sorted(critical, key=lambda q: (q["x"], q["y"])):
        raw = bytes(ram[row["wsram_offset"] : row["wsram_offset"] + 0x20])
        hits = all_hits(stock, raw)
        item = {
            "char": CRITICAL_CHAR_BY_X.get(row["x"], "?"),
            "x": row["x"],
            "y": row["y"],
            "vram_tile": f"{row['tile']:03X}",
            "wsram": f"{row['wsram_offset']:04X}",
            "logical_rom_hits": [f"{h:06X}" for h in hits],
            "unique_logical_rom": f"{hits[0]:06X}" if len(hits) == 1 else None,
            "unique_tip_physical": f"{tip_base + hits[0]:06X}" if len(hits) == 1 else None,
        }
        rows.append(item)
        by_x[row["x"]].append({**item, "raw": raw})

    glyph_rows = []
    for x in sorted(CRITICAL_CHAR_BY_X):
        char = CRITICAL_CHAR_BY_X[x]
        img = Image.new("RGB", (8 * 8, 16 * 8), (0, 0, 0))
        sources = []
        for item in by_x.get(x, []):
            tile = decode_tile(item["raw"])
            draw_tile(img, tile, 0, (item["y"] - 56) * 8, 8)
            sources.extend(item["logical_rom_hits"])
        safe = "bang" if char == "!" else f"u{ord(char):04X}"
        img.save(d / f"critical_glyph_{safe}.png")
        glyph_rows.append(
            {
                "char": char,
                "screen_x": x,
                "source_tiles": sources,
                "png": str(d / f"critical_glyph_{safe}.png"),
            }
        )

    atlas = stock[BATTLE_ATLAS_LO:BATTLE_ATLAS_HI]
    (d / "popup_source_atlas_phase12.bin").write_bytes(atlas)
    render_tiles_contact(atlas, BATTLE_ATLAS_LO, d / "popup_source_atlas_phase12_contact.png", cols=16, scale=3)

    return {
        "state": str(state),
        "renderer": "OBJ sprites; 8x16 character cells assembled from 8x8 raw 4bpp tiles",
        "observed_text": "クリティカル!",
        "suggested_korean": "크리티컬!",
        "screen_bbox": bbox,
        "sprite_count": len(critical),
        "sprite_tiles": rows,
        "glyphs": glyph_rows,
        "candidate_source_atlas": {
            "logical_rom": f"{BATTLE_ATLAS_LO:06X}-{BATTLE_ATLAS_HI - 1:06X}",
            "tiles": len(atlas) // 0x20,
            "bytes": len(atlas),
            "phase_mod_32": BATTLE_ATLAS_LO % 0x20,
        },
        "note": "Iフィールド is expected to reuse this popup family, but no I-field savestate was supplied, so its exact source tiles are intentionally not guessed.",
    }


def extract_id(stock: bytes, tip_base: int, zstd: Zstd, out: Path) -> dict:
    d = out / "id_command"
    d.mkdir(parents=True, exist_ok=True)
    state = PROBE_DIR / "state6_plus24.State"
    core, frame = state_core_and_frame(state, zstd)
    ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    sprites = current_sprites(core)
    plaque = [row for row in sprites if row["attr"] == 0x3A and 84 <= row["x"] <= 124 and 44 <= row["y"] <= 52]
    if len(plaque) != 12:
        raise RuntimeError(f"expected 12 ID plaque sprites, got {len(plaque)}")

    bbox = render_sprite_selection(ram, plaque, d / "id_attack_runtime_tiles.png", scale=6)
    frame.save(d / "state6_plus24_framebuffer.png")
    frame.crop((76, 36, 140, 68)).resize((384, 192), Image.Resampling.NEAREST).save(
        d / "id_attack_screen_crop_x6.png"
    )

    sprite_rows = []
    for row in sorted(plaque, key=lambda q: (q["y"], q["x"])):
        raw = bytes(ram[row["wsram_offset"] : row["wsram_offset"] + 0x20])
        hits = all_hits(stock, raw)
        sprite_rows.append(
            {
                "x": row["x"],
                "y": row["y"],
                "vram_tile": f"{row['tile']:03X}",
                "wsram": f"{row['wsram_offset']:04X}",
                "role": "shared_left_cap" if row["x"] == 84 else "40x16_body",
                "logical_rom_hits": [f"{h:06X}" for h in hits],
            }
        )

    body = stock[ID_ATTACK_BODY_LO : ID_ATTACK_BODY_LO + ID_BLOCK_BYTES]
    (d / "attack_body_40x16.bin").write_bytes(body)
    render_block_5x2(body, 8).save(d / "attack_body_40x16.png")
    blocks = render_id_block_sheet(stock, d / "id_plaque_candidate_blocks_contact.png")
    atlas = stock[ID_BLOCK_LO : ID_BLOCK_LO + ID_BLOCK_COUNT * ID_BLOCK_BYTES]
    (d / "id_plaque_candidate_blocks.bin").write_bytes(atlas)

    return {
        "state": str(state),
        "renderer": "OBJ sprite plaque, 6x2 tiles (48x16): one shared 8x16 cap + 5x2-tile body",
        "observed_text": "↑攻撃",
        "suggested_korean": "↑공격",
        "screen_bbox": bbox,
        "sprites": sprite_rows,
        "attack_body": {
            "logical_rom": f"{ID_ATTACK_BODY_LO:06X}-{ID_ATTACK_BODY_LO + ID_BLOCK_BYTES - 1:06X}",
            "tip_physical": f"{tip_base + ID_ATTACK_BODY_LO:06X}-{tip_base + ID_ATTACK_BODY_LO + ID_BLOCK_BYTES - 1:06X}",
            "bytes": ID_BLOCK_BYTES,
            "tiles": 10,
            "raw_file": str(d / "attack_body_40x16.bin"),
        },
        "candidate_blocks": blocks,
        "candidate_region": {
            "logical_rom": f"{ID_BLOCK_LO:06X}-{ID_BLOCK_LO + len(atlas) - 1:06X}",
            "blocks": ID_BLOCK_COUNT,
            "block_bytes": ID_BLOCK_BYTES,
            "raw_file": str(d / "id_plaque_candidate_blocks.bin"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock-rom", type=Path, default=STOCK_ROM)
    ap.add_argument("--tip-rom", type=Path, default=TIP_ROM)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    for p in (
        args.stock_rom,
        args.tip_rom,
        STATE_DIR / "Mednafen.QuickSave4.State",
        PROBE_DIR / "state5_plus24.State",
        PROBE_DIR / "state6_plus24.State",
        ZSTD_DLL,
    ):
        if not p.exists():
            raise SystemExit(f"missing required input: {p}")

    stock = args.stock_rom.read_bytes()
    tip = args.tip_rom.read_bytes()
    tip_base = stock_base(tip)
    if tip_base != 0x800000:
        raise RuntimeError(f"unexpected TIP stock base: {tip_base:06X}")
    before_tip_hash = sha256(tip)

    args.out.mkdir(parents=True, exist_ok=True)
    zstd = Zstd(ZSTD_DLL)
    manifest = {
        "schema_version": 1,
        "read_only_extraction": True,
        "stock_rom": {
            "path": str(args.stock_rom),
            "bytes": len(stock),
            "sha256": sha256(stock),
        },
        "tip_rom": {
            "path": str(args.tip_rom),
            "bytes": len(tip),
            "stock_base": f"{tip_base:06X}",
            "sha256_before": before_tip_hash,
        },
        "stage_title": extract_stage(stock, tip_base, zstd, args.out),
        "battle_popup": extract_critical(stock, tip_base, zstd, args.out),
        "id_command": extract_id(stock, tip_base, zstd, args.out),
    }

    after_tip_hash = sha256(args.tip_rom.read_bytes())
    manifest["tip_rom"]["sha256_after"] = after_tip_hash
    manifest["tip_rom"]["unchanged"] = before_tip_hash == after_tip_hash
    if not manifest["tip_rom"]["unchanged"]:
        raise RuntimeError("TIP ROM changed during a read-only extraction")

    manifest_path = args.out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readme = f"""# Runtime graphics extraction\n\nRead-only extraction generated by `tools/extract_runtime_graphics_assets.py`.\nNo ROM bytes are modified. Current TIP SHA-256 stayed `{before_tip_hash}`.\n\n## Stage title\n- observed `ダカールの灯火`: `stage_title/dakar_title_tiles.bin` and PNG proofs\n- exact logical source: `4AC166-4AC3C5` (19 raw 4bpp tiles / 608 B)\n- surrounding two-colour candidate atlases are exported separately; per-title boundaries are not yet asserted\n\n## Battle popup\n- observed `クリティカル!`: exact runtime sprite reconstruction and per-character 8x16 PNGs\n- every observed tile is mapped to its ROM hit(s) in `manifest.json`\n- a phase-correct source contact sheet covering `107412-108891` is included\n- `Iフィールド` is not assigned an address without a matching savestate\n\n## ID command\n- observed `↑攻撃`: runtime 48x16 sprite reconstruction\n- shared 8x16 left cap is kept separate from the 40x16 body\n- attack body exact logical source: `4C5D54-4C5E93` (10 tiles / 320 B)\n- 22 adjacent 40x16 candidate plaque bodies are exported as one contact sheet and raw blob\n\nSee `manifest.json` for addresses, sprite/tile mappings and current-TIP physical offsets.\n"""
    (args.out / "README.md").write_text(readme, encoding="utf-8")

    print(f"manifest: {manifest_path}")
    print(f"stage title: {STAGE_TITLE_TILES} tiles @ {STAGE_TITLE_LO:06X}")
    print(f"critical: {manifest['battle_popup']['sprite_count']} live sprites; {len(manifest['battle_popup']['glyphs'])} glyph cells")
    print(f"ID blocks: {ID_BLOCK_COUNT}; observed attack body @ {ID_ATTACK_BODY_LO:06X}")
    print(f"TIP unchanged: {manifest['tip_rom']['unchanged']} {after_tip_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
