#!/usr/bin/env python3
"""Build a title-menu Galmuri11 Bitmap + Korean copyright test ROM.

Parent is the current main TIP.  Menu plates 0-28 are re-rasterised with the
ending-credit Galmuri11Bitmap Regular 16 px face so ㅖ in 계속 keeps a 1 px
gap.  The unique 224x16 footer strip at 55:19DC keeps the original © and
©BANDAI 2002 columns and replaces only 創通エージェンシー・サンライ즈.

ROM-only.  Paired SaveRAM is a byte-exact copy of live SaveRAM.  Old
savestates restore previous VRAM, so the title must be re-entered from a
cold boot or reset — do not load state27 onto this ROM to judge the footer.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_id_command_plaques_ko_candidate import decode_grid, encode_grid  # noqa: E402
from menu_plate_model import PLATE_SIZE, Atlas, render_grid, to_block  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_menu_plates_ko import Rasteriser, draw_label  # noqa: E402


MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
LABELS = ROOT / "data/menu_plate_labels_ko.json"
COPYRIGHT = ROOT / "data/title_copyright_ko.json"
FONT = ROOT / "assets/fonts/galmuri_tmp/Galmuri11Bitmap-Regular-2.40.3.ttf"
COPYRIGHT_FONT = ROOT / "assets/fonts/galmuri_tmp/Galmuri9Bitmap-Regular-2.40.3.ttf"
OUT_DIR = ROOT / "out/patch/title_menu_bitmap_copyright_candidate"
OUT_ROM = OUT_DIR / "monoeye_ko_expanded_title_menu_bitmap_copyright_test.wsc"
OUT_SAVE = OUT_DIR / "monoeye_ko_expanded_title_menu_bitmap_copyright_test.sav"
REPORT = OUT_DIR / "title_menu_bitmap_copyright_report.json"
PREVIEWS = OUT_DIR / "previews"

EXPECTED_MAIN_SHA256 = (
    "c0a2b429e9162c9648c21fbbab0dcd28b70c0cdcc0966b11407cef2db54b2631"
)
EXPECTED_SAVE_SHA256 = (
    "7edaa450d28eaeeebea61bd59b710480e333a805c4872ec8d8adeb5efd780d99"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MENU_LO = 0x720080
MENU_HI = 0x7248FF
COPYRIGHT_LO = 0x5519DC
COPYRIGHT_BYTES = 1792
PLATE_FONT_SIZE = 16
PLATE_TOP = 1
PLATE_SPACING = 1
PLATE_SPACE_WIDTH = 4
PLATE_SHADOW = (0, 1)

PAL6 = {
    0x0: (0, 0, 0),
    0x1: (119, 85, 68),
    0x5: (153, 153, 136),
    0xF: (255, 255, 255),
}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def ws_checksum_valid(rom: bytes) -> bool:
    stored = struct.unpack_from("<H", rom, len(rom) - 2)[0]
    return stored == (sum(rom[:-2]) & 0xFFFF)


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def render_indices(pixels: list[list[int]], scale: int) -> Image.Image:
    img = Image.new("RGB", (len(pixels[0]), len(pixels)))
    px = img.load()
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            px[x, y] = PAL6.get(value, (value * 17,) * 3)
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def layout_copyright(text: str, ras: Rasteriser, spacing: int, space_width: int) -> list[tuple[str, int]]:
    x = 0
    placed: list[tuple[str, int]] = []
    for index, ch in enumerate(text):
        adv = space_width if ch == " " else ras.advance(ch) + spacing
        if ch != " ":
            placed.append((ch, x))
        if index == len(text) - 1 and ch != " ":
            x += ras.advance(ch)
        else:
            x += adv
    return placed


def patch_copyright(
    stock: bytes,
    ras: Rasteriser,
    spec: dict,
) -> tuple[bytes, dict]:
    blob = stock[COPYRIGHT_LO : COPYRIGHT_LO + COPYRIGHT_BYTES]
    if encode_grid(decode_grid(blob, spec["cols"], spec["rows"]), spec["cols"], spec["rows"]) != blob:
        raise BuildError("copyright strip encode/decode roundtrip failed")
    source = decode_grid(blob, spec["cols"], spec["rows"])
    pixels = [row[:] for row in source]
    x0 = int(spec["keep_first_copyright_x1"])
    x1 = int(spec["keep_english_x0"])
    for y in range(16):
        for x in range(x0, x1):
            pixels[y][x] = 0

    placed = layout_copyright(
        spec["ko"], ras, int(spec["spacing"]), int(spec["space_width"])
    )
    if not placed:
        raise BuildError("empty copyright label")
    dx, dy = spec["shadow_delta"]
    top = int(spec["top"])
    origin = x0
    stroke_px: set[tuple[int, int]] = set()
    for ch, gx in placed:
        bits, gw, gh = ras.glyph(ch)
        for yy in range(gh):
            for xx in range(gw):
                if bits[yy][xx]:
                    stroke_px.add((origin + gx + xx, top + yy))
    align_top = spec.get("align_stroke_top")
    if align_top is not None:
        min_y = min(y for _, y in stroke_px)
        shift = int(align_top) - min_y
        if shift:
            stroke_px = {(x, y + shift) for x, y in stroke_px}
    shadow_px = {(x + dx, y + dy) for x, y in stroke_px} - stroke_px

    def in_draw_zone(x: int, y: int) -> bool:
        return x0 <= x < x1 and 0 <= y < 16

    leaked = [p for p in sorted(stroke_px | shadow_px) if not in_draw_zone(*p)]
    if leaked:
        raise BuildError(f"copyright ink leaked outside JP zone: {leaked[:8]}")

    for x, y in sorted(shadow_px):
        pixels[y][x] = int(spec["shadow_index"])
    for x, y in sorted(stroke_px):
        pixels[y][x] = int(spec["stroke_index"])

    for y in range(16):
        for x in list(range(0, x0)) + list(range(x1, 224)):
            if pixels[y][x] != source[y][x]:
                raise BuildError(f"reserved copyright column mutated at {x},{y}")
            pixels[y][x] = source[y][x]

    out = encode_grid(pixels, spec["cols"], spec["rows"])
    if len(out) != COPYRIGHT_BYTES:
        raise BuildError("copyright blob length drifted")
    last_x = max(x for x, _ in stroke_px)
    return out, {
        "jp": spec["jp"],
        "ko": spec["ko"],
        "english": spec["english"],
        "draw_zone": [x0, x1],
        "glyph_x": [origin + gx for _, gx in placed],
        "stroke_px": len(stroke_px),
        "shadow_px": len(shadow_px),
        "last_stroke_x": last_x,
        "stroke_y": [min(y for _, y in stroke_px), max(y for _, y in stroke_px) + 1],
        "stroke_height": max(y for _, y in stroke_px) - min(y for _, y in stroke_px) + 1,
        "font": ras.path.name,
        "size": ras.size,
        "bytes_changed": sum(1 for a, b in zip(blob, out) if a != b),
        "keep_first_copyright_exact": all(
            pixels[y][x] == source[y][x] for y in range(16) for x in range(x0)
        ),
        "keep_english_exact": all(
            pixels[y][x] == source[y][x] for y in range(16) for x in range(x1, 224)
        ),
        "before_png": rel(PREVIEWS / "copyright_before_x4.png"),
        "after_png": rel(PREVIEWS / "copyright_after_x4.png"),
        "pixels": pixels,
        "source": source,
    }


def patch_plates(rom: bytearray, ras: Rasteriser) -> list[dict]:
    base = stock_base(rom)
    atlas = Atlas(bytes(rom[base:]))
    spec = json.loads(LABELS.read_text(encoding="utf-8"))
    report = []
    for entry in spec["labels"]:
        for pi in entry["plates"]:
            grid, info = draw_label(
                atlas,
                pi,
                entry["ko"],
                ras,
                PLATE_TOP,
                PLATE_SPACING,
                PLATE_SPACE_WIDTH,
                True,
                PLATE_SHADOW,
            )
            if info["clipped_px"]:
                raise BuildError(
                    f"plate {pi} clipped {info['clipped_px']} px for {entry['ko']!r}"
                )
            lo = base + atlas.plates[pi].abs_lo
            block = to_block(grid)
            before = bytes(rom[lo : lo + PLATE_SIZE])
            info["jp"] = entry["jp"]
            info["abs"] = f"{lo - base:06X}-{lo - base + PLATE_SIZE - 1:06X}"
            info["bytes_changed"] = sum(1 for a, b in zip(before, block) if a != b)
            rom[lo : lo + PLATE_SIZE] = block
            report.append(info)
            render_grid(grid, atlas.palette, 5).save(
                PREVIEWS / f"plate_{pi:02d}_ko.png"
            )
    return report


def main() -> int:
    required = (MAIN, LIVE_SAVE, LABELS, COPYRIGHT, FONT, COPYRIGHT_FONT)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError(f"missing inputs: {missing}")

    parent = MAIN.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drifted: {len(parent)} {sha256(parent)}")
    if len(live_save) != SAVE_SIZE or sha256(live_save) != EXPECTED_SAVE_SHA256:
        raise BuildError(f"live SaveRAM drifted: {len(live_save)} {sha256(live_save)}")
    if not ws_checksum_valid(parent):
        raise BuildError("main TIP checksum is invalid")

    spec = json.loads(COPYRIGHT.read_text(encoding="utf-8"))
    if int(spec["logical"], 16) != COPYRIGHT_LO or int(spec["bytes"]) != COPYRIGHT_BYTES:
        raise BuildError("copyright spec address/size drifted")

    PREVIEWS.mkdir(parents=True, exist_ok=True)
    plate_ras = Rasteriser(FONT, PLATE_FONT_SIZE)
    copy_font = ROOT / spec["font"] if spec.get("font") else COPYRIGHT_FONT
    if not copy_font.is_file():
        raise BuildError(f"missing copyright font: {copy_font}")
    copy_ras = Rasteriser(copy_font, int(spec.get("size", 12)))
    rom = bytearray(parent)
    base = stock_base(rom)
    stock = bytes(rom[base:])

    copyright_blob, copyright_info = patch_copyright(stock, copy_ras, spec)
    render_indices(copyright_info.pop("source"), 4).save(
        PREVIEWS / "copyright_before_x4.png"
    )
    render_indices(copyright_info["pixels"], 4).save(
        PREVIEWS / "copyright_after_x4.png"
    )
    render_indices(copyright_info.pop("pixels"), 1).save(
        PREVIEWS / "copyright_after_native.png"
    )
    rom[base + COPYRIGHT_LO : base + COPYRIGHT_LO + COPYRIGHT_BYTES] = copyright_blob

    plate_report = patch_plates(rom, plate_ras)
    if any(item["clipped_px"] for item in plate_report):
        raise BuildError("a menu plate clipped Hangul ink")

    checksum = update_ws_checksum(rom)
    diffs = changed_offsets(parent, bytes(rom))
    allowed = set()
    for off in range(base + MENU_LO, base + MENU_HI + 1):
        allowed.add(off)
    for off in range(base + COPYRIGHT_LO, base + COPYRIGHT_LO + COPYRIGHT_BYTES):
        allowed.add(off)
    allowed.update((len(rom) - 2, len(rom) - 1))
    outside = [off for off in diffs if off not in allowed]
    if outside:
        raise BuildError(
            f"unexpected diffs outside plates/copyright/checksum: "
            f"{[f'{off:06X}' for off in outside[:8]]}"
        )

    atomic_bytes(OUT_ROM, bytes(rom))
    atomic_copy(LIVE_SAVE, OUT_SAVE)
    report = {
        "generated_by": "tools/build_title_menu_bitmap_copyright_candidate.py",
        "ok": True,
        "font": rel(FONT),
        "font_size": PLATE_FONT_SIZE,
        "copyright_font": rel(copy_font),
        "copyright_size": copy_ras.size,
        "plate_top": PLATE_TOP,
        "plate_spacing": PLATE_SPACING,
        "plate_shadow_delta": list(PLATE_SHADOW),
        "parent": identity(MAIN, parent),
        "live_save": identity(LIVE_SAVE, live_save),
        "candidate": identity(OUT_ROM, bytes(rom)),
        "candidate_save": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "diff_bytes": len(diffs),
        "menu_plate_bytes_changed": sum(item["bytes_changed"] for item in plate_report),
        "plates": plate_report,
        "copyright": copyright_info,
        "note": (
            "ROM-only. Load the test ROM and reset to the title; do not reuse "
            "state27, which restores the previous footer VRAM."
        ),
    }
    atomic_json(REPORT, report)
    print(f"rom -> {rel(OUT_ROM)}  checksum {checksum:04X}  sha {report['candidate']['sha256']}")
    print(f"save -> {rel(OUT_SAVE)}")
    print(f"report -> {rel(REPORT)}")
    print(
        f"plates {len(plate_report)}  copyright {copyright_info['bytes_changed']} B  "
        f"total diff {len(diffs)} B"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
