#!/usr/bin/env python3
"""Test ROM: replace compact-font 変/形 with Galmuri7 변/형.

Hypothesis: the unit-status 變形 button is runtime composition. The blue chrome
is a separate tile; the yellow hanja are the compact-font glyphs for E073/E196,
drawn with the screen palette. Overwriting only those two 16-byte records should
change the button without touching chrome, tilemaps, or dictionary text.

Side effect (intentional for the probe): every other compact-font consumer of
変/形 will also show 변/형.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_hangul_font import render_compact_glyph, render_preview  # noqa: E402
from monoeye_rom import (  # noqa: E402
    COMPACT_FONT_RECORD_SIZE,
    compact_font_file_offset,
    decode_compact_font_record,
    encode_compact_font_record,
    load_rom,
    stock_base,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/henkei_compact_font_ko_candidate.wsc"
OUT_SAVE = ROOT / "sram/henkei_compact_font_ko_candidate.sav"
REPORT = ROOT / "out/patch/henkei_compact_font_ko_candidate_report.json"
PREVIEW = ROOT / "out/patch/henkei_compact_font_ko_candidate_previews"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
GLYPHS = (
    {"jp": "変", "ko": "변", "code": 0xE073},
    {"jp": "形", "ko": "형", "code": 0xE196},
)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    raw = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(a)))
    return out


def ascii_glyph(grid: list[list[int]]) -> list[str]:
    chars = " .:#"
    mx = max((max(row) for row in grid), default=1) or 1
    lines = []
    for row in grid:
        lines.append("".join(chars[min(3, v * 3 // mx)] for v in row))
    return lines


def double_16(grid: list[list[int]]) -> list[list[int]]:
    out = [[0] * 16 for _ in range(16)]
    for y, row in enumerate(grid):
        for x, v in enumerate(row):
            out[y * 2][x * 2] = v
            out[y * 2][x * 2 + 1] = v
            out[y * 2 + 1][x * 2] = v
            out[y * 2 + 1][x * 2 + 1] = v
    return out


def render_pair(left: list[list[int]], right: list[list[int]], *, yellow: bool, scale: int = 8) -> Image.Image:
    pair = [lrow + rrow for lrow, rrow in zip(double_16(left), double_16(right))]
    w, h = 32, 16
    img = Image.new("RGB", (w, h), (48, 168, 232) if yellow else (16, 16, 24))
    px = img.load()
    for y, row in enumerate(pair):
        for x, v in enumerate(row):
            if yellow:
                if v:
                    px[x, y] = (240, 220, 32)
            else:
                g = v * 85
                px[x, y] = (g, g, g)
    return img.resize((w * scale, h * scale), Image.Resampling.NEAREST)


def count_code(rom: bytes, code: int) -> int:
    needle = bytes([(code >> 8) & 0xFF, code & 0xFF])
    n = 0
    start = 0
    while True:
        i = rom.find(needle, start)
        if i < 0:
            break
        n += 1
        start = i + 1
    return n


def main() -> int:
    parent = load_rom(MAIN)
    save = SAVE.read_bytes()
    stock = STOCK.read_bytes()
    parent_snapshot = bytes(parent)
    save_snapshot = save
    if len(parent) != ROM_SIZE:
        raise BuildError(f"TIP must be 16 MiB, got {len(parent):#x}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM must be 32 KiB, got {len(save)}")
    if len(stock) != 0x800000:
        raise BuildError("stock ROM missing or wrong size")
    base = stock_base(parent)
    if base != 0x800000:
        raise BuildError(f"unexpected stock base {base:#x}")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    manifest: list[dict[str, Any]] = []
    grids: list[tuple[dict[str, Any], list[list[int]], list[list[int]]]] = []

    for spec in GLYPHS:
        code = spec["code"]
        logical = compact_font_file_offset(code) - base
        physical = base + logical
        before = bytes(parent[physical : physical + COMPACT_FONT_RECORD_SIZE])
        stock_rec = stock[logical : logical + COMPACT_FONT_RECORD_SIZE]
        if before != stock_rec:
            raise BuildError(f"{spec['jp']} glyph at {logical:06X} is no longer stock-exact")
        if len(before) != COMPACT_FONT_RECORD_SIZE:
            raise BuildError(f"short glyph record at {logical:06X}")
        src_grid = decode_compact_font_record(before)
        dst_grid = render_compact_glyph(spec["ko"], str(ROOT / "assets/fonts/Galmuri7.ttf"))
        encoded = encode_compact_font_record(dst_grid)
        if encoded == before:
            raise BuildError(f"no-op encode for {spec['ko']}")
        if decode_compact_font_record(encoded) != dst_grid:
            raise BuildError(f"round-trip failed for {spec['ko']}")
        candidate[physical : physical + COMPACT_FONT_RECORD_SIZE] = encoded
        allowed.append((physical, physical + COMPACT_FONT_RECORD_SIZE))
        manifest.append(
            {
                "jp": spec["jp"],
                "ko": spec["ko"],
                "code": f"{code:04X}",
                "logical": f"{logical:06X}",
                "physical": f"{physical:08X}",
                "bytes": COMPACT_FONT_RECORD_SIZE,
                "source_sha256": sha256(before),
                "target_sha256": sha256(encoded),
                "source_ascii": ascii_glyph(src_grid),
                "target_ascii": ascii_glyph(dst_grid),
                "stock_be_pair_hits": count_code(stock, code),
            }
        )
        grids.append((spec, src_grid, dst_grid))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent_snapshot, result)
    unexpected = [(a, b) for a, b in runs if not any(lo <= a and b <= hi for lo, hi in allowed)]
    if unexpected:
        raise BuildError(f"diff outside allowlist {unexpected}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("checksum invalid")
    if parent[0x7A0000:0x7B0000] != result[0x7A0000:0x7B0000]:
        raise BuildError("runtime bank 7A changed")
    if parent[0x7F0000:0x7FFFFE] != result[0x7F0000:0x7FFFFE]:
        raise BuildError("runtime bank 7F changed")

    PREVIEW.mkdir(parents=True, exist_ok=True)
    for spec, before, after in grids:
        render_preview(before, 12).save(PREVIEW / f"{spec['code']:04X}_{spec['jp']}_before.png")
        render_preview(after, 12).save(PREVIEW / f"{spec['code']:04X}_{spec['ko']}_after.png")
    before_pair = render_pair(grids[0][1], grids[1][1], yellow=True)
    after_pair = render_pair(grids[0][2], grids[1][2], yellow=True)
    sheet = Image.new("RGB", (before_pair.width * 2, before_pair.height + 24), (20, 20, 28))
    sheet.paste(before_pair, (0, 24))
    sheet.paste(after_pair, (before_pair.width, 24))
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 6), "変形 (stock compact)", fill=(220, 220, 240))
    draw.text((before_pair.width + 8, 6), "변형 (Galmuri7 probe)", fill=(220, 220, 240))
    sheet.save(PREVIEW / "henkei_yellow_on_blue_before_after.png")

    atomic_bytes(OUT, result)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SAVE, OUT_SAVE)
    if MAIN.read_bytes() != parent_snapshot:
        raise BuildError("live TIP changed during build")
    if SAVE.read_bytes() != save_snapshot:
        raise BuildError("live SaveRAM changed during build")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_henkei_compact_font_ko_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_user_runtime_test",
        "hypothesis": (
            "unit-status 變形 is compact-font E073/E196 drawn in yellow onto "
            "separate blue chrome; replace those two 8x8 records with 변/형"
        ),
        "side_effect": (
            "every compact-font consumer of 変/形, not only the transform button, "
            "will render 변/형. That is the probe: if the button changes and other "
            "変/形 text also changes, the shared-font hypothesis holds."
        ),
        "parent": identity(MAIN, parent_snapshot),
        "stock": identity(STOCK, stock),
        "candidate": {**identity(OUT, result), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(OUT_SAVE),
        "glyphs": manifest,
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(b - a for a, b in runs),
            "ranges": [[f"{a:08X}", f"{b:08X}"] for a, b in runs],
            "allowlist_clean": not unexpected,
        },
        "guards": {
            "main_unchanged": MAIN.read_bytes() == parent_snapshot,
            "live_saveram_unchanged": SAVE.read_bytes() == save_snapshot,
            "glyph_sources_stock_exact": True,
            "runtime_bank_7a_exact": True,
            "runtime_bank_7f_exact_except_checksum": True,
        },
        "preview": rel(PREVIEW / "henkei_yellow_on_blue_before_after.png"),
        "how_to_run": (
            "Open out/patch/henkei_compact_font_ko_candidate.wsc in BizHawk. "
            "SaveRAM pair is sram/henkei_compact_font_ko_candidate.sav "
            "(raw 32 KiB). For bundled Cygne, pad +1024 zero EEPROM and name "
            "WonderSwan/SaveRAM/henkei compact font ko candidate.SaveRAM"
        ),
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
