#!/usr/bin/env python3
"""Bake Korean 변형 into the unit-status 24x24 transform button.

The Beetle WonderSwan status-screen state proved this control is a precomposed
packed-4bpp 3x3 tile graphic at stock ``40:F638`` (288 bytes), not compact-font
E073/E196.  This candidate overwrites only that blob.  Gold chrome, the green
pip, tilemaps, dictionary text, and every other 変/形 consumer stay byte-exact.

Savestate VRAM must be allowed to re-upload: load the candidate ROM, then leave
and re-enter the unit status screen.  Loading the old RetroArch state on top of
the new ROM will still show 變形.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_id_command_plaques_ko_candidate import (  # noqa: E402
    decode_grid,
    encode_grid,
    make_masks,
)
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/henkei_icon_ko_candidate.wsc"
OUT_SAVE = ROOT / "sram/henkei_icon_ko_candidate.sav"
REPORT = ROOT / "out/patch/henkei_icon_ko_candidate_report.json"
PREVIEW = ROOT / "out/patch/henkei_icon_ko_candidate_previews"
FONT = ROOT / "assets/fonts/galmuri_tmp/Galmuri11-Condensed.ttf"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LOGICAL = 0x40F638
BLOB = 0x120
COLS, ROWS = 3, 3
TEXT = "변형"
FONT_SIZE = 11
STROKE = 1
MIN_INK = 40
# Inner orange face, exclusive end. Covers the original 變形 ink bbox
# (5,5)-(20,20). Condensed 11px + stroke is 16x13 and is centred in 16x16.
ZONE = (5, 5, 21, 21)
FACE = 0xC
INK = 0x6
OUTLINE = 0x1
CHROME = frozenset({0x3, 0x8, 0x9, 0xA, 0xB, 0xD, 0xE, 0xF})
PALETTE = {
    0x0: (0, 0, 0),
    0x1: (0, 0, 0),
    0x3: (0, 0, 0),
    0x6: (0, 255, 255),
    0x8: (0, 255, 0),
    0x9: (17, 153, 0),
    0xA: (85, 51, 0),
    0xB: (170, 85, 17),
    0xC: (255, 170, 51),
    0xD: (255, 221, 51),
    0xE: (255, 255, 102),
    0xF: (255, 255, 255),
}


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


def ascii_grid(pixels: list[list[int]]) -> list[str]:
    chars = "0123456789ABCDEF"
    return ["".join(chars[v] for v in row) for row in pixels]


def render_icon(pixels: list[list[int]], scale: int = 8) -> Image.Image:
    image = Image.new("RGB", (24, 24))
    dst = image.load()
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            dst[x, y] = PALETTE.get(value, (value * 17,) * 3)
    return image.resize((24 * scale, 24 * scale), Image.NEAREST)


def localize(source: list[list[int]]) -> tuple[list[list[int]], dict[str, Any]]:
    x0, y0, x1, y1 = ZONE
    pixels = [row[:] for row in source]
    if not FONT.is_file():
        raise BuildError(f"missing font: {FONT}")
    font = ImageFont.truetype(str(FONT), FONT_SIZE)
    outer, inner = make_masks(TEXT, font, STROKE, 0)
    zone_w, zone_h = x1 - x0, y1 - y0
    if outer.width > zone_w or outer.height > zone_h:
        raise BuildError(
            f"{TEXT!r} does not fit zone {zone_w}x{zone_h}: mask={outer.width}x{outer.height}"
        )
    dx = x0 + (zone_w - outer.width) // 2
    dy = y0 + (zone_h - outer.height) // 2

    cleared = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if pixels[y][x] in CHROME:
                continue
            if pixels[y][x] in (OUTLINE, INK):
                pixels[y][x] = FACE
                cleared += 1

    skipped_chrome = 0
    op, ip = outer.load(), inner.load()
    for y in range(outer.height):
        for x in range(outer.width):
            gx, gy = dx + x, dy + y
            if not (0 <= gx < 24 and 0 <= gy < 24):
                raise BuildError("glyph drew outside 24x24")
            if source[gy][gx] in CHROME or pixels[gy][gx] in CHROME:
                if op[x, y] or ip[x, y]:
                    skipped_chrome += 1
                continue
            if op[x, y]:
                pixels[gy][gx] = OUTLINE
            if ip[x, y]:
                pixels[gy][gx] = INK

    changed = [
        (x, y) for y in range(24) for x in range(24) if pixels[y][x] != source[y][x]
    ]
    if not changed:
        raise BuildError("icon did not change")
    chrome_hits = [(x, y) for x, y in changed if source[y][x] in CHROME]
    if chrome_hits:
        raise BuildError(f"chrome pixels changed: {chrome_hits[:8]}")
    pip_before = [(x, y) for y in range(24) for x in range(24) if source[y][x] in (0x8, 0x9)]
    pip_after = [(x, y) for y in range(24) for x in range(24) if pixels[y][x] in (0x8, 0x9)]
    if pip_before != pip_after:
        raise BuildError("green pip moved or vanished")
    ink_n = sum(1 for row in pixels for v in row if v == INK)
    if ink_n < MIN_INK:
        raise BuildError(f"too little cyan ink: {ink_n}")
    return pixels, {
        "text": TEXT,
        "font": {
            "path": rel(FONT),
            "size": FONT_SIZE,
            "stroke_width": STROKE,
        },
        "zone": [x0, y0, x1, y1],
        "glyph_mask": {"width": outer.width, "height": outer.height},
        "draw_origin": [dx, dy],
        "cleared_glyph_pixels": cleared,
        "skipped_chrome_pixels": skipped_chrome,
        "changed_pixel_count": len(changed),
        "changed_pixel_bbox": [
            min(x for x, _ in changed),
            min(y for _, y in changed),
            max(x for x, _ in changed) + 1,
            max(y for _, y in changed) + 1,
        ],
        "ink_pixels": ink_n,
        "outline_index": f"{OUTLINE:X}",
        "ink_index": f"{INK:X}",
        "face_index": f"{FACE:X}",
    }


def main() -> int:
    if not MAIN.is_file() or not STOCK.is_file() or not SAVE.is_file():
        raise BuildError("missing parent ROM, stock ROM, or SaveRAM")
    parent = MAIN.read_bytes()
    stock = STOCK.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE:
        raise BuildError(f"parent is not 16 MiB: {len(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"SaveRAM size {len(save)}")
    base = stock_base(parent)
    if base != 0x800000:
        raise BuildError(f"unexpected stock base {base:#x}")
    physical = base + LOGICAL
    source_raw = bytes(parent[physical : physical + BLOB])
    stock_raw = bytes(stock[LOGICAL : LOGICAL + BLOB])
    if source_raw != stock_raw:
        raise BuildError(f"40:F638 is no longer stock-exact in the parent")
    if stock.count(stock_raw) != 1:
        raise BuildError(f"expected a unique 288-byte blob, found {stock.count(stock_raw)}")

    source = decode_grid(source_raw, COLS, ROWS)
    target, layout = localize(source)
    target_raw = encode_grid(target, COLS, ROWS)
    if len(target_raw) != BLOB:
        raise BuildError("encoded size drift")
    if decode_grid(target_raw, COLS, ROWS) != target:
        raise BuildError("encode/decode roundtrip failed")
    if target_raw == source_raw:
        raise BuildError("encoded bytes identical to source")

    candidate = bytearray(parent)
    candidate[physical : physical + BLOB] = target_raw
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if MAIN.read_bytes() != parent:
        raise BuildError("parent ROM mutated during build")
    if SAVE.read_bytes() != save:
        raise BuildError("live SaveRAM mutated during build")

    runs = diff_runs(parent, result)
    blob_lo, blob_hi = physical, physical + BLOB
    checksum_run = (ROM_SIZE - 2, ROM_SIZE)

    def allowed(run: tuple[int, int]) -> bool:
        start, end = run
        inside_blob = blob_lo <= start < end <= blob_hi
        return inside_blob or run == checksum_run

    unexpected = [run for run in runs if not allowed(run)]
    if unexpected:
        raise BuildError(f"writes outside allowlist: {unexpected}")

    PREVIEW.mkdir(parents=True, exist_ok=True)
    before = render_icon(source)
    after = render_icon(target)
    before.save(PREVIEW / "before.png")
    after.save(PREVIEW / "after.png")
    sheet = Image.new("RGB", (before.width * 2 + 24, before.height + 28), (24, 24, 24))
    sheet.paste(before, (0, 22))
    sheet.paste(after, (before.width + 24, 22))
    draw = ImageDraw.Draw(sheet)
    draw.text((4, 4), "變形 stock 40:F638", fill=(220, 220, 220))
    draw.text((before.width + 28, 4), "변형 Galmuri11-Condensed", fill=(220, 220, 220))
    sheet.save(PREVIEW / "before_after.png")

    atomic_bytes(OUT, result)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    tmp_save = OUT_SAVE.with_name(f".{OUT_SAVE.name}.{os.getpid()}.tmp")
    shutil.copyfile(SAVE, tmp_save)
    os.replace(tmp_save, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_henkei_icon_ko_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_user_runtime_test",
        "hypothesis": (
            "unit-status 變形 is a unique packed-4bpp 24x24 graphic at 40:F638; "
            "replace only the interior cyan/black glyph with Galmuri11-Condensed 변형"
        ),
        "side_effect": (
            "none expected beyond this one graphic. compact-font 変/形 and all "
            "other 24x24 header icons are untouched."
        ),
        "parent": identity(MAIN, parent),
        "stock": identity(STOCK, stock),
        "candidate": {**identity(OUT, result), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(OUT_SAVE),
        "patch": {
            "logical": f"{LOGICAL:06X}",
            "physical": f"{physical:08X}",
            "bytes": BLOB,
            "source_sha256": sha256(source_raw),
            "target_sha256": sha256(target_raw),
            "layout": layout,
            "source_ascii": ascii_grid(source),
            "target_ascii": ascii_grid(target),
        },
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(b - a for a, b in runs),
            "ranges": [[f"{a:08X}", f"{b:08X}"] for a, b in runs],
            "allowlist_clean": True,
        },
        "guards": {
            "main_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": SAVE.read_bytes() == save,
            "source_stock_exact": True,
            "unique_stock_blob": True,
            "chrome_preserved": True,
            "green_pip_preserved": True,
            "encode_roundtrip": True,
        },
        "preview": rel(PREVIEW / "before_after.png"),
        "how_to_run": (
            "Open out/patch/henkei_icon_ko_candidate.wsc in RetroArch Beetle "
            "WonderSwan (or BizHawk). Paired SaveRAM is "
            "sram/henkei_icon_ko_candidate.sav. Do not judge from the old "
            "monoeye_ko_expanded.state: that savestate restores VRAM. Enter the "
            "unit status screen from gameplay so the 24x24 graphic re-uploads."
        ),
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({k: report[k] for k in ("ok", "status", "candidate", "patch", "diff", "how_to_run", "promotion")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
