#!/usr/bin/env python3
"""Pack approved Korean credit previews as packed-4bpp page atlases in expansion bank 0x50.

No code hook yet. This only plants the tilemaps + unique tiles so a later blit
can DMA them through stock 7A:0000 (AX=off BX=seg CX=tilecount DX=first_tile).

Text pages are full 28x18. Cinematic pages store only the bottom bar (rows 13-17)
so the 3F/68 art is not posterized. Ink index 1, background 0xE.
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

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/ending_credits_ko.json"
PREVIEWS = ROOT / "out/patch/ending_credits_ko_previews"
OUT = ROOT / "out/patch/ending_credits_ko_page_atlas_candidate.wsc"
OUT_SAVE = ROOT / "sram/ending_credits_ko_page_atlas_candidate.sav"
REPORT = ROOT / "out/patch/ending_credits_ko_page_atlas_report.json"
ATLAS_DUMP = ROOT / "out/patch/ending_credits_ko_page_atlas.bin"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXP_BANK = 0x50  # reserved expansion; hangul pool is 00-0F, dict 10-2F
SCREEN_W, SCREEN_H = 224, 144
COLS, ROWS = 28, 18
BAR_ROW0 = 13  # y=104
BAR_ROWS = 5
INK, BG = 1, 0xE
HEADER = 16
RECORD = 16


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict:
    raw = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def pack_tile(grid: list[list[int]]) -> bytes:
    out = bytearray(32)
    for y in range(8):
        for x in range(4):
            hi = grid[y][x * 2] & 0xF
            lo = grid[y][x * 2 + 1] & 0xF
            out[y * 4 + x] = (hi << 4) | lo
    return bytes(out)


def quantize(img: Image.Image) -> list[list[int]]:
    px = img.convert("RGB").load()
    grid = []
    for y in range(SCREEN_H):
        row = []
        for x in range(SCREEN_W):
            r, g, b = px[x, y]
            lum = r + g + b
            row.append(INK if lum >= 300 else BG)
        grid.append(row)
    return grid


def page_atlas(
    img: Image.Image, row0: int, nrows: int
) -> tuple[bytes, bytes, int]:
    pix = quantize(img)
    tiles: list[bytes] = []
    index: dict[bytes, int] = {}
    cells = []
    for row in range(row0, row0 + nrows):
        for col in range(COLS):
            block = [
                pix[row * 8 + y][col * 8 : col * 8 + 8]
                for y in range(8)
            ]
            raw = pack_tile(block)
            tid = index.get(raw)
            if tid is None:
                tid = len(tiles)
                if tid >= 0x1FF:
                    raise BuildError("too many unique tiles")
                index[raw] = tid
                tiles.append(raw)
            # pal 2, bank 0x4000
            cells.append(tid | (2 << 9))
    gfx = b"".join(tiles)
    tilemap = b"".join(struct.pack("<H", w) for w in cells)
    return tilemap, gfx, len(tiles)


def main() -> int:
    if not MAIN.is_file() or not SAVE.is_file():
        raise BuildError("missing parent ROM or SaveRAM")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    parent = MAIN.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE:
        raise BuildError(f"parent is not 16 MiB: {len(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"SaveRAM size {len(save)}")
    base = stock_base(parent)
    if base != 0x800000:
        raise BuildError(f"unexpected stock base {base:#x}")

    pages = spec["pages"]
    table_off = HEADER
    table_size = len(pages) * RECORD
    cursor = table_off + table_size
    records = []
    blobs = []
    summaries = []
    for page in pages:
        slot = page["slot"]
        png = PREVIEWS / f"slot{slot:02d}_ko.png"
        if not png.is_file():
            raise BuildError(f"missing preview {png}")
        img = Image.open(png)
        if img.size != (SCREEN_W, SCREEN_H):
            raise BuildError(f"{png.name} size {img.size}")
        cinematic = bool(page.get("art"))
        row0 = BAR_ROW0 if cinematic else 0
        nrows = BAR_ROWS if cinematic else ROWS
        tilemap, gfx, ntiles = page_atlas(img, row0, nrows)
        map_off = cursor
        gfx_off = cursor + len(tilemap)
        cursor = gfx_off + len(gfx)
        if cursor > 0x10000:
            raise BuildError(f"expansion bank 0x{EXP_BANK:02X} overflow at slot {slot}")
        rec = struct.pack(
            "<BBBBHHHHHH",
            slot,
            1 if cinematic else 0,
            row0,
            nrows,
            ntiles,
            map_off,
            gfx_off,
            COLS,
            2,
            0,
        )
        records.append(rec)
        blobs.append(tilemap + gfx)
        summaries.append(
            {
                "slot": slot,
                "cinematic": cinematic,
                "row0": row0,
                "nrows": nrows,
                "ntiles": ntiles,
                "map_off": f"{map_off:04X}",
                "gfx_off": f"{gfx_off:04X}",
                "bytes": len(tilemap) + len(gfx),
            }
        )

    header = struct.pack(
        "<4sHHHHHH",
        b"ECKO",
        1,
        len(pages),
        table_off,
        cursor,
        EXP_BANK,
        0,
    )
    payload = header + b"".join(records) + b"".join(blobs)
    if len(payload) != cursor:
        raise BuildError(f"payload size {len(payload)} != cursor {cursor}")
    if len(payload) > 0x10000:
        raise BuildError("atlas exceeds 64 KiB")

    exp_off = EXP_BANK * 0x10000
    existing = parent[exp_off : exp_off + 0x10000]
    if any(b != 0xFF for b in existing):
        raise BuildError(f"expansion bank {EXP_BANK:02X} is not empty FF")

    candidate = bytearray(parent)
    candidate[exp_off : exp_off + len(payload)] = payload
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if MAIN.read_bytes() != parent:
        raise BuildError("parent ROM mutated")
    if SAVE.read_bytes() != save:
        raise BuildError("live SaveRAM mutated")

    atomic_bytes(OUT, result)
    atomic_bytes(ATLAS_DUMP, payload)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    tmp_save = OUT_SAVE.with_name(f".{OUT_SAVE.name}.{os.getpid()}.tmp")
    shutil.copyfile(SAVE, tmp_save)
    os.replace(tmp_save, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_ko_page_atlas.py",
        "ok": True,
        "status": "data_planted_hook_pending",
        "note": (
            "Korean packed-4bpp pages live in expansion bank 50 (text=full 28x18, "
            "cinematic=bottom bar only). Visible credits still come from stock 6B "
            "until a blit hook is installed. Pair with the 6B XOR probe."
        ),
        "parent": identity(MAIN, parent),
        "candidate": {**identity(OUT, result), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(OUT_SAVE),
        "atlas": {
            "bank": f"{EXP_BANK:02X}",
            "bytes": len(payload),
            "pages": summaries,
            "dump": rel(ATLAS_DUMP),
        },
        "guards": {
            "main_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": SAVE.read_bytes() == save,
            "expansion_was_ff": True,
        },
        "promotion": "blocked_pending_blit_hook",
    }
    atomic_json(REPORT, report)
    print(json.dumps(
        {k: report[k] for k in ("ok", "status", "atlas", "promotion")},
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
