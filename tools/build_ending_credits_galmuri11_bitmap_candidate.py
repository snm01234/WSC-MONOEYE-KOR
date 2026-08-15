#!/usr/bin/env python3
"""Build a Galmuri11 Bitmap Regular ending-credit test ROM.

The TTF is a build-time input only.  The ROM receives pre-rendered packed-4bpp
tiles, not a font file or a new runtime font loader.  To keep the complete
21-record atlas in expansion bank 50, standard pages store the contiguous union
of rows used by the stock Japanese page and the new Korean page.  Those rows
still contain a black background, so all old Japanese pixels are overwritten.

The parent is the user-validated cinematic transition-guard test candidate.
Its code, upper cinematic art path, and paired SaveRAM are preserved.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import update_ws_checksum  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildError(RuntimeError):
    pass


RENDERER = load_module(
    "ending_credits_bitmap_renderer", ROOT / "tools/build_ending_credits_ko_previews.py"
)
ATLAS = load_module(
    "ending_credits_bitmap_atlas", ROOT / "tools/build_ending_credits_ko_page_atlas.py"
)

SPEC = ROOT / "data/ending_credits_ko.json"
NATIVE = ROOT / "out/patch/ending_credits"
FONT = (
    ROOT
    / "assets/fonts/galmuri_tmp/Galmuri11Bitmap-Regular-2.40.3.ttf"
)
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT_DIR = ROOT / "out/patch/ending_credits_cinematic_transition_guard_candidate"
PARENT_ROM = (
    PARENT_DIR / "monoeye_ko_expanded_ending_credits_cinematic_transition_guard_test.wsc"
)
PARENT_SAVE = (
    PARENT_DIR / "monoeye_ko_expanded_ending_credits_cinematic_transition_guard_test.sav"
)
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate"
OUT_ROM = OUT_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.wsc"
OUT_SAVE = OUT_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.sav"
PREVIEWS = OUT_DIR / "previews"
ATLAS_DUMP = OUT_DIR / "ending_credits_galmuri11_bitmap_bank50.bin"
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_report.json"

EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
EXPECTED_PARENT_SHA256 = (
    "a8be5f53b4d3c45365ff7ec267f7c9c2590229e0b1229efd1a967bc1a62085fa"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BANK = 0x50
ATLAS_BASE = ATLAS_BANK * 0x10000
ATLAS_SIZE = 0x10000
HEADER = 16
RECORD = struct.Struct("<BBBBHHHHHH")
SCREEN_W, SCREEN_H = 224, 144

# Captured-state-safe ranges.  Pages 17-19 share a range below the preserved
# separator tile 090 and below the later B5-BC dynamic FG.  Pages 20-21 retain
# the already validated post-separator base.
CINEMATIC_FIRST_TILE = {
    17: 0x051,
    18: 0x051,
    19: 0x051,
    20: 0x091,
    21: 0x091,
}


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


def render_page(page: dict, font16, font12) -> Image.Image:
    kind = page["kind"]
    if kind == "center_stack":
        return RENDERER.render_center_stack(page, font16)
    if kind == "two_col":
        return RENDERER.render_two_col(page, font12)
    if kind in {"header_two_col", "header_two_col_footer"}:
        return RENDERER.render_header_two_col(page, font16, font12)
    if kind in {"bar_lr", "bar_left_right_stack"}:
        font = RENDERER.pick_bar_font(page, font16, font12)
        return RENDERER.render_bar(page, font)
    raise BuildError(f"unknown page kind: {kind}")


def nonblack_tile_rows(img: Image.Image) -> list[int]:
    rgb = img.convert("RGB")
    pixels = rgb.load()
    rows = []
    for row in range(18):
        if any(
            pixels[x, y] != (0, 0, 0)
            for y in range(row * 8, row * 8 + 8)
            for x in range(SCREEN_W)
        ):
            rows.append(row)
    return rows


def standard_row_span(slot: int, rendered: Image.Image) -> tuple[int, int, list[int], list[int]]:
    native_path = NATIVE / f"slot{slot:02d}_native.png"
    if not native_path.is_file():
        raise BuildError(f"missing native reference: {native_path}")
    native = Image.open(native_path).convert("RGB")
    if native.size != (SCREEN_W, SCREEN_H):
        raise BuildError(f"native reference size drifted: {native_path} {native.size}")
    stock_rows = nonblack_tile_rows(native)
    korean_rows = nonblack_tile_rows(rendered)
    used = sorted(set(stock_rows) | set(korean_rows))
    if not used:
        raise BuildError(f"slot {slot} has no visible rows")
    row0 = used[0]
    nrows = used[-1] - used[0] + 1
    if any(row < row0 or row >= row0 + nrows for row in stock_rows + korean_rows):
        raise BuildError(f"slot {slot} row-span containment failed")
    return row0, nrows, stock_rows, korean_rows


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def main() -> int:
    required = (SPEC, FONT, MAIN, LIVE_SAVE, PARENT_ROM, PARENT_SAVE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError(f"missing inputs: {missing}")

    main_before = MAIN.read_bytes()
    live_save_before = LIVE_SAVE.read_bytes()
    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drifted: {len(main_before)} {sha256(main_before)}")
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"transition-guard parent drifted: {len(parent)} {sha256(parent)}")
    if len(live_save_before) != SAVE_SIZE or len(parent_save) != SAVE_SIZE:
        raise BuildError("SaveRAM size drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("transition-guard parent checksum is invalid")
    old_header = struct.unpack_from("<4sHHHHHH", parent, ATLAS_BASE)
    if old_header[:3] != (b"ECKO", 2, 21):
        raise BuildError(f"bank 50 header drifted: {old_header}")

    pages = json.loads(SPEC.read_text(encoding="utf-8"))["pages"]
    if len(pages) != 21:
        raise BuildError(f"expected 21 prepared pages, got {len(pages)}")
    font16 = RENDERER.CellFont(FONT, 16, 16)
    font12 = RENDERER.CellFont(FONT, 16, 12)
    PREVIEWS.mkdir(parents=True, exist_ok=True)

    rendered_pages: list[tuple[int, int, dict, Image.Image, int, int, bytes, bytes, int]] = []
    width_issues: list[str] = []
    summaries: list[dict] = []
    for index, page in enumerate(pages):
        rom_page = index + 1
        slot = int(page["slot"])
        old_record = RECORD.unpack_from(parent, ATLAS_BASE + rom_page * RECORD.size)
        if old_record[0] != slot:
            raise BuildError(
                f"record {rom_page} slot drifted: {old_record[0]} != {slot}"
            )
        width_issues.extend(RENDERER.width_report(page, font16, font12))
        image = render_page(page, font16, font12)
        if image.size != (SCREEN_W, SCREEN_H):
            raise BuildError(f"slot {slot} render size drifted: {image.size}")
        image.save(PREVIEWS / f"slot{slot:02d}_ko.png")
        image.resize((SCREEN_W * 3, SCREEN_H * 3), Image.Resampling.NEAREST).save(
            PREVIEWS / f"slot{slot:02d}_ko_x3.png"
        )

        cinematic = bool(page.get("art"))
        if cinematic:
            row0, nrows = 13, 5
            stock_rows = []
            korean_rows = nonblack_tile_rows(image)
        else:
            row0, nrows, stock_rows, korean_rows = standard_row_span(slot, image)
        tilemap, gfx, ntiles = ATLAS.page_atlas(image, row0, nrows)
        palette = old_record[8]
        first_tile = CINEMATIC_FIRST_TILE.get(rom_page, old_record[9])
        last_tile = first_tile + ntiles - 1
        if not 0 <= first_tile <= last_tile <= 0x1FF:
            raise BuildError(
                f"page {rom_page} tile range invalid: {first_tile:03X}-{last_tile:03X}"
            )
        if cinematic and first_tile <= 0x090 <= last_tile:
            raise BuildError(f"page {rom_page} overwrites separator tile 090")
        rendered_pages.append(
            (
                rom_page,
                slot,
                page,
                image,
                row0,
                nrows,
                tilemap,
                gfx,
                ntiles,
            )
        )
        summaries.append(
            {
                "rom_page": rom_page,
                "capture_slot": slot,
                "cinematic": cinematic,
                "row0": row0,
                "nrows": nrows,
                "stock_nonblack_rows": stock_rows,
                "korean_nonblack_rows": korean_rows,
                "ntiles": ntiles,
                "palette": palette,
                "first_tile": f"{first_tile:03X}",
                "last_tile": f"{last_tile:03X}",
            }
        )
    if width_issues:
        raise BuildError(f"font layout width failures: {width_issues}")

    table_size = len(pages) * RECORD.size
    cursor = HEADER + table_size
    records: list[bytes] = []
    blobs: list[bytes] = []
    for rendered, summary in zip(rendered_pages, summaries):
        rom_page, slot, page, image, row0, nrows, tilemap, gfx, ntiles = rendered
        old_record = RECORD.unpack_from(parent, ATLAS_BASE + rom_page * RECORD.size)
        map_off = cursor
        gfx_off = map_off + len(tilemap)
        cursor = gfx_off + len(gfx)
        if cursor > ATLAS_SIZE:
            raise BuildError(
                f"bank 50 overflow at page {rom_page}: {cursor} > {ATLAS_SIZE}"
            )
        first_tile = CINEMATIC_FIRST_TILE.get(rom_page, old_record[9])
        records.append(
            RECORD.pack(
                slot,
                1 if page.get("art") else 0,
                row0,
                nrows,
                ntiles,
                map_off,
                gfx_off,
                28,
                old_record[8],
                first_tile,
            )
        )
        blobs.append(tilemap + gfx)
        summary.update(
            {
                "map_off": f"{map_off:04X}",
                "gfx_off": f"{gfx_off:04X}",
                "bytes": len(tilemap) + len(gfx),
            }
        )

    header = struct.pack(
        "<4sHHHHHH",
        b"ECKO",
        2,
        len(pages),
        HEADER,
        cursor,
        ATLAS_BANK,
        0,
    )
    payload = header + b"".join(records) + b"".join(blobs)
    if len(payload) != cursor:
        raise BuildError(f"atlas cursor mismatch: {len(payload)} != {cursor}")

    # Re-read every packed record/blob contract before touching the parent copy.
    for rendered in rendered_pages:
        rom_page, slot, page, image, row0, nrows, tilemap, gfx, ntiles = rendered
        record = RECORD.unpack_from(payload, rom_page * RECORD.size)
        if record[:5] != (
            slot,
            1 if page.get("art") else 0,
            row0,
            nrows,
            ntiles,
        ):
            raise BuildError(f"page {rom_page} packed record mismatch: {record}")
        if payload[record[5] : record[5] + len(tilemap)] != tilemap:
            raise BuildError(f"page {rom_page} tilemap mismatch")
        if payload[record[6] : record[6] + len(gfx)] != gfx:
            raise BuildError(f"page {rom_page} graphics mismatch")

    candidate = bytearray(parent)
    candidate[ATLAS_BASE : ATLAS_BASE + ATLAS_SIZE] = b"\xFF" * ATLAS_SIZE
    candidate[ATLAS_BASE : ATLAS_BASE + len(payload)] = payload
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if not ws_checksum_valid(result):
        raise BuildError("candidate WonderSwan checksum update failed")

    diffs = changed_offsets(parent, result)
    outside = [
        off
        for off in diffs
        if not (ATLAS_BASE <= off < ATLAS_BASE + ATLAS_SIZE)
        and off not in (ROM_SIZE - 2, ROM_SIZE - 1)
    ]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")
    if result[0xFE0000:0xFF0000] != parent[0xFE0000:0xFF0000]:
        raise BuildError("transition-guard bank 7E code changed unexpectedly")
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared ending-credit overlay changed unexpectedly")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        raise BuildError("transition-guard parent inputs changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)
    atomic_bytes(ATLAS_DUMP, payload)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_galmuri11_bitmap_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "font": {
            **identity(FONT),
            "face": "Galmuri11Bitmap Regular 2.40.3",
            "pillow_size": 16,
            "embedded_in_rom": False,
            "rom_representation": "pre-rendered packed-4bpp tiles only",
        },
        "main_tip": {**identity(MAIN, main_before), "unchanged": True},
        "parent": identity(PARENT_ROM, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "atlas": {
            "bank": "50",
            "bytes": len(payload),
            "free_bytes": ATLAS_SIZE - len(payload),
            "records": len(pages),
            "row_storage": (
                "standard pages store the contiguous union of stock and Korean "
                "non-black tile rows; cinematic pages keep rows 13-17"
            ),
            "dump": rel(ATLAS_DUMP),
            "pages": summaries,
        },
        "preserved": {
            "strings_and_layout": True,
            "runtime_overlay_byte_exact": True,
            "transition_guard_code_byte_exact": True,
            "cinematic_upper_art_not_packed": True,
            "main_tip_unchanged": MAIN.read_bytes() == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save_before,
            "paired_saveram_byte_exact": OUT_SAVE.read_bytes() == parent_save,
        },
        "diff": {
            "parent_changed_bytes": len(diffs),
            "declared_ranges": ["500000-50FFFF", "FFFFFE-FFFFFF checksum"],
            "outside_declared_ranges": 0,
        },
        "previews": {
            "directory": rel(PREVIEWS),
            "width_issues": 0,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "all standard pages have no residual Japanese glyph rows",
                "뱅가드 and other Hangul vowels are distinct at native scale",
                "pages 16-17 transition remains clean",
                "pages 17-21 upper animations and separators remain intact",
            ],
            "savestate_note": (
                "Use the test ROM and paired SaveRAM and replay the ending. "
                "Do not validate entry-time hooks from an old credits savestate."
            ),
        },
        "promotion": "blocked_pending_user_runtime_validation",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["status"],
                "candidate": report["candidate"],
                "paired_saveram": report["paired_saveram"],
                "atlas": {
                    "bytes": report["atlas"]["bytes"],
                    "free_bytes": report["atlas"]["free_bytes"],
                },
                "promotion": report["promotion"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
