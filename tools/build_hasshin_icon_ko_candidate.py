#!/usr/bin/env python3
"""Build a ROM-only candidate that localizes the 24x24 發進 icon to 발진."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_henkei_icon_ko_candidate as icon  # noqa: E402
from build_id_command_plaques_ko_candidate import make_masks  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/hasshin_icon_ko_candidate.wsc"
OUT_SAVE = ROOT / "sram/hasshin_icon_ko_candidate.sav"
REPORT = ROOT / "out/patch/hasshin_icon_ko_candidate_report.json"
PREVIEW = ROOT / "out/patch/hasshin_icon_ko_candidate_previews"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LOGICAL = 0x40919E
PHYSICAL = 0xC0919E
BLOB = 0x120
TEXT = "\ubc1c\uc9c4"
FONT = ROOT / "assets/fonts/galmuri_tmp/Galmuri7.ttf"
ZONE = (5, 5, 21, 21)
FACE = 0xC
INK = 0x6
SHADOW = 0x3
PRESERVE = frozenset({0x8, 0x9, 0xA, 0xB, 0xD, 0xE, 0xF})


class BuildError(RuntimeError):
    pass


def localize(source: list[list[int]]) -> tuple[list[list[int]], dict[str, object]]:
    """Erase the Japanese glyph and redraw Korean with a one-pixel down shadow."""
    pixels = [row[:] for row in source]
    x0, y0, x1, y1 = ZONE
    for y in range(y0, y1):
        for x in range(x0, x1):
            if pixels[y][x] not in PRESERVE and pixels[y][x] in (0x1, SHADOW, INK):
                pixels[y][x] = FACE

    font = ImageFont.truetype(str(FONT), 8)
    mask, _ = make_masks(TEXT, font, 0, 0)
    zone_w, zone_h = x1 - x0, y1 - y0
    if mask.width > zone_w or mask.height + 1 > zone_h:
        raise BuildError(f"glyph does not fit: {mask.size}")
    dx = x0 + (zone_w - mask.width) // 2
    dy = y0 + (zone_h - (mask.height + 1)) // 2
    mp = mask.load()

    for y in range(mask.height):
        for x in range(mask.width):
            if not mp[x, y]:
                continue
            gx, gy = dx + x, dy + y + 1
            if source[gy][gx] not in PRESERVE:
                pixels[gy][gx] = SHADOW
    for y in range(mask.height):
        for x in range(mask.width):
            if not mp[x, y]:
                continue
            gx, gy = dx + x, dy + y
            if source[gy][gx] not in PRESERVE:
                pixels[gy][gx] = INK

    changed = [(x, y) for y in range(24) for x in range(24) if pixels[y][x] != source[y][x]]
    if any(source[y][x] in PRESERVE for x, y in changed):
        raise BuildError("bezel or green pip changed")
    pip_before = [(x, y, source[y][x]) for y in range(24) for x in range(24) if source[y][x] in (8, 9)]
    pip_after = [(x, y, pixels[y][x]) for y in range(24) for x in range(24) if pixels[y][x] in (8, 9)]
    if pip_before != pip_after:
        raise BuildError("green pip changed")
    return pixels, {
        "text": TEXT,
        "font": {"path": icon.rel(FONT), "size": 8},
        "zone": list(ZONE),
        "glyph_mask": {"width": mask.width, "height": mask.height},
        "draw_origin": [dx, dy],
        "shadow": {"color_index": f"{SHADOW:X}", "offset": [0, 1]},
        "ink_index": f"{INK:X}",
        "face_index": f"{FACE:X}",
        "changed_pixel_count": len(changed),
    }


def main() -> int:
    for path in (MAIN, SAVE, STOCK):
        if not path.is_file():
            raise BuildError(f"missing input: {path}")
    parent = MAIN.read_bytes()
    stock = STOCK.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE or len(save) != SAVE_SIZE:
        raise BuildError("unexpected ROM or SaveRAM size")
    base = stock_base(parent)
    if base + LOGICAL != PHYSICAL:
        raise BuildError(f"unexpected stock base: {base:#x}")

    parent_icon_raw = bytes(parent[PHYSICAL : PHYSICAL + BLOB])
    stock_raw = bytes(stock[LOGICAL : LOGICAL + BLOB])
    if stock.count(stock_raw) != 1:
        raise BuildError(f"expected one stock blob, found {stock.count(stock_raw)}")

    source = icon.decode_grid(stock_raw, 3, 3)
    target, layout = localize(source)
    target_raw = icon.encode_grid(target, 3, 3)
    if len(target_raw) != BLOB or icon.decode_grid(target_raw, 3, 3) != target:
        raise BuildError("packed-4bpp encode/decode roundtrip failed")

    candidate = bytearray(parent)
    candidate[PHYSICAL : PHYSICAL + BLOB] = target_raw
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if MAIN.read_bytes() != parent or SAVE.read_bytes() != save:
        raise BuildError("main ROM or live SaveRAM changed during build")

    runs = icon.diff_runs(parent, result)
    allowed = []
    unexpected = []
    for start, end in runs:
        ok = PHYSICAL <= start < end <= PHYSICAL + BLOB
        ok = ok or ROM_SIZE - 2 <= start < end <= ROM_SIZE
        (allowed if ok else unexpected).append((start, end))
    if unexpected:
        raise BuildError(f"writes outside allowlist: {unexpected}")

    PREVIEW.mkdir(parents=True, exist_ok=True)
    before = icon.render_icon(source)
    after = icon.render_icon(target)
    before.save(PREVIEW / "before.png")
    after.save(PREVIEW / "after.png")
    sheet = Image.new("RGB", (before.width * 2 + 24, before.height + 28), (24, 24, 24))
    sheet.paste(before, (0, 22))
    sheet.paste(after, (before.width + 24, 22))
    draw = ImageDraw.Draw(sheet)
    draw.text((4, 4), "source 40:919E", fill=(220, 220, 220))
    draw.text((before.width + 28, 4), "KO Galmuri7 8px + down shadow", fill=(220, 220, 220))
    sheet.save(PREVIEW / "before_after.png")

    icon.atomic_bytes(OUT, result)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    tmp_save = OUT_SAVE.with_name(f".{OUT_SAVE.name}.{os.getpid()}.tmp")
    shutil.copyfile(SAVE, tmp_save)
    os.replace(tmp_save, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_hasshin_icon_ko_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "state_evidence": {
            "state": "C:/RetroArch-Win64/states/Beetle WonderSwan/monoeye_ko_expanded.state",
            "screenshot": "C:/RetroArch-Win64/states/Beetle WonderSwan/monoeye_ko_expanded.state.png",
            "tilemap": "FG 3800 rows 21-23 cols 21-23",
            "vram_tiles": [f"{value:03X}" for value in range(0x01D, 0x026)],
            "rom_exact_match": "40:919E",
        },
        "parent": icon.identity(MAIN, parent),
        "stock": icon.identity(STOCK, stock),
        "candidate": {**icon.identity(OUT, result), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": icon.identity(OUT_SAVE),
        "patch": {
            "logical": f"{LOGICAL:06X}",
            "physical": f"{PHYSICAL:08X}",
            "bytes": BLOB,
            "source_sha256": icon.sha256(parent_icon_raw),
            "stock_template_sha256": icon.sha256(stock_raw),
            "target_sha256": icon.sha256(target_raw),
            "layout": layout,
            "source_ascii": icon.ascii_grid(source),
            "target_ascii": icon.ascii_grid(target),
        },
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(end - start for start, end in runs),
            "ranges": [[f"{start:08X}", f"{end:08X}"] for start, end in runs],
            "allowlist_clean": True,
        },
        "guards": {
            "main_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": SAVE.read_bytes() == save,
            "stock_template_unique": True,
            "unique_stock_blob": True,
            "chrome_preserved": True,
            "green_pip_preserved": True,
            "encode_roundtrip": True,
        },
        "preview": icon.rel(PREVIEW / "before_after.png"),
        "runtime_note": "Old savestates restore old VRAM; re-enter the screen after loading the new ROM.",
    }
    icon.atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, icon.BuildError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
