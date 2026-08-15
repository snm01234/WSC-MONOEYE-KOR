#!/usr/bin/env python3
"""Build the Galmuri11 Bitmap full-BG-clear cinematic test ROM.

Two independent residues are addressed together:

* the transition prelude clears the complete 28x18 BG map to stable blank
  bank-1 tile 1F6 while BG display is disabled;
* page 17 stores a complete 28x18 map whose upper thirteen rows are explicitly
  black and whose lower five rows contain the Korean bar.

Page 17 graphics move to captured-state-safe bank-0 tiles 06C-087.  All other
Galmuri11 Bitmap pixels, strings, and cinematic transition code remain intact.
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

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import update_ws_checksum  # noqa: E402


class BuildError(RuntimeError):
    pass


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATLAS_MOD = load_module(
    "ending_credits_full_bg_clear_atlas",
    ROOT / "tools/build_ending_credits_ko_page_atlas.py",
)

PARENT_DIR = (
    ROOT / "out/patch/ending_credits_galmuri11_bitmap_transition_ranges_candidate"
)
PARENT_ROM = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_transition_ranges_test.wsc"
)
PARENT_SAVE = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_transition_ranges_test.sav"
)
SOURCE_PREVIEWS = (
    ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate/previews"
)
SPEC = ROOT / "data/ending_credits_ko.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_full_bg_clear_candidate"
OUT_ROM = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_full_bg_clear_test.wsc"
)
OUT_SAVE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_full_bg_clear_test.sav"
)
PREVIEWS = OUT_DIR / "previews"
ATLAS_DUMP = OUT_DIR / "ending_credits_galmuri11_bitmap_full_bg_clear_bank50.bin"
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_full_bg_clear_report.json"

EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
EXPECTED_PARENT_SHA256 = (
    "cf08fd546c15d5b549b0bca42656904c4bddb05d48e243a74c1a2881b48af9b5"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BASE = 0x500000
ATLAS_SIZE = 0x10000
HEADER = 16
RECORD = struct.Struct("<BBBBHHHHHH")
PRELOAD_HELPER = 0xFEFD50
PRELOAD_HELPER_IP = 0xFD50
PRELOAD_HELPER_END = 0xFEFD6F
EXPECTED_OLD_HELPER = bytes.fromhex("C7040000E40024FEE600E918CD")

EXPECTED_PARENT_RANGES = {
    17: (28, 8, 0x0DF),
    18: (40, 8, 0x091),
    19: (43, 8, 0x051),
    20: (39, 10, 0x091),
    21: (26, 6, 0x0BD),
}
NEW_FIRST_TILE = {
    17: 0x06C,
    18: 0x091,
    19: 0x051,
    20: 0x091,
    21: 0x0BD,
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


def near_jmp(src_ip: int, dst_ip: int) -> bytes:
    displacement = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE9" + struct.pack("<H", displacement)


def build_preload_clear_helper() -> bytes:
    """Disable BG, clear the 28x18 map to 21F6, then resume at CA75."""
    body = bytearray()
    body += bytes.fromhex("C7040000")  # mov word [si],0000
    body += bytes.fromhex("E40024FEE600")  # in 00; and FE; out 00 (BG off)
    body += bytes.fromhex("57")  # push di (page pointer)
    body += bytes.fromhex("33C08EC0")  # xor ax,ax; mov es,ax
    body += bytes.fromhex("BF0030")  # mov di,3000 (BG map)
    body += bytes.fromhex("B8F621")  # mov ax,21F6 (blank bank-1 tile)
    body += bytes.fromhex("B9F801")  # mov cx,01F8 (28*18 words)
    body += bytes.fromhex("FCF3AB")  # cld; rep stosw
    body += bytes.fromhex("5F")  # pop di
    body += near_jmp(PRELOAD_HELPER_IP + len(body), 0xCA75)
    if len(body) != 31:
        raise BuildError(f"unexpected preload-clear helper size: {len(body)}")
    return bytes(body)


def ranges_overlap(first_a: int, count_a: int, first_b: int, count_b: int) -> bool:
    return max(first_a, first_b) <= min(
        first_a + count_a - 1, first_b + count_b - 1
    )


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [index for index, (a, b) in enumerate(zip(before, after)) if a != b]


def main() -> int:
    required = (PARENT_ROM, PARENT_SAVE, SPEC, MAIN, LIVE_SAVE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError(f"missing inputs: {missing}")
    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    main_before = MAIN.read_bytes()
    live_save_before = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"range parent drifted: {len(parent)} {sha256(parent)}")
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drifted: {len(main_before)} {sha256(main_before)}")
    if len(parent_save) != SAVE_SIZE or len(live_save_before) != SAVE_SIZE:
        raise BuildError("SaveRAM size drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("range parent checksum invalid")
    if parent[PRELOAD_HELPER : PRELOAD_HELPER + len(EXPECTED_OLD_HELPER)] != EXPECTED_OLD_HELPER:
        raise BuildError("old transition preload helper drifted")
    if any(
        byte != 0xFF
        for byte in parent[
            PRELOAD_HELPER + len(EXPECTED_OLD_HELPER) : PRELOAD_HELPER_END
        ]
    ):
        raise BuildError("preload-helper extension cave is no longer FF")

    pages = json.loads(SPEC.read_text(encoding="utf-8"))["pages"]
    if len(pages) != 21:
        raise BuildError(f"prepared page count drifted: {len(pages)}")
    old_header = struct.unpack_from("<4sHHHHHH", parent, ATLAS_BASE)
    if old_header[:3] != (b"ECKO", 2, 21):
        raise BuildError(f"parent bank 50 header drifted: {old_header}")
    for page, (ntiles, palette, first) in EXPECTED_PARENT_RANGES.items():
        record = RECORD.unpack_from(parent, ATLAS_BASE + page * RECORD.size)
        if (
            record[0],
            record[1],
            record[2],
            record[3],
            record[4],
            record[8],
            record[9],
        ) != (page, 1, 13, 5, ntiles, palette, first):
            raise BuildError(f"parent page {page} record drifted: {record}")

    PREVIEWS.mkdir(parents=True, exist_ok=True)
    packed_pages = []
    summaries = []
    for index, page_spec in enumerate(pages):
        rom_page = index + 1
        slot = int(page_spec["slot"])
        source_path = SOURCE_PREVIEWS / f"slot{slot:02d}_ko.png"
        if not source_path.is_file():
            raise BuildError(f"missing Bitmap preview: {source_path}")
        image = Image.open(source_path).convert("RGB")
        if image.size != (224, 144):
            raise BuildError(f"preview size drifted: {source_path} {image.size}")
        old_record = RECORD.unpack_from(parent, ATLAS_BASE + rom_page * RECORD.size)
        if old_record[0] != slot:
            raise BuildError(f"page {rom_page} slot drifted: {old_record}")

        row0, nrows = old_record[2], old_record[3]
        output_image = image.copy()
        if rom_page == 17:
            # Upper cinematic art is supplied by the live FG layer.  The Korean
            # BG record explicitly clears its old standard-page map instead.
            ImageDraw.Draw(output_image).rectangle((0, 0, 223, 103), fill=(0, 0, 0))
            row0, nrows = 0, 18
            output_image.save(PREVIEWS / "slot17_full_bg_clear_ko.png")
            output_image.resize((672, 432), Image.Resampling.NEAREST).save(
                PREVIEWS / "slot17_full_bg_clear_ko_x3.png"
            )
        tilemap, gfx, ntiles = ATLAS_MOD.page_atlas(output_image, row0, nrows)
        first_tile = NEW_FIRST_TILE.get(rom_page, old_record[9])
        last_tile = first_tile + ntiles - 1
        if last_tile > 0x1FF:
            raise BuildError(f"page {rom_page} target exceeds 1FF")
        if rom_page >= 17 and first_tile <= 0x090 <= last_tile:
            raise BuildError(f"page {rom_page} reaches separator tile 090")
        packed_pages.append(
            {
                "rom_page": rom_page,
                "slot": slot,
                "cinematic": bool(page_spec.get("art")),
                "row0": row0,
                "nrows": nrows,
                "ntiles": ntiles,
                "tilemap": tilemap,
                "gfx": gfx,
                "palette": old_record[8],
                "first_tile": first_tile,
            }
        )
        summaries.append(
            {
                "rom_page": rom_page,
                "capture_slot": slot,
                "cinematic": bool(page_spec.get("art")),
                "row0": row0,
                "nrows": nrows,
                "ntiles": ntiles,
                "palette": old_record[8],
                "first_tile": f"{first_tile:03X}",
                "last_tile": f"{last_tile:03X}",
                "upper_bg_action": (
                    "explicit black rows 0-12" if rom_page == 17 else "unchanged"
                ),
            }
        )

    # The page 17 blank upper map reuses the bar's existing blank tile, so the
    # graphics count stays 28 while its tilemap grows by thirteen rows.
    page17 = packed_pages[16]
    if (
        page17["rom_page"],
        page17["row0"],
        page17["nrows"],
        page17["ntiles"],
        page17["first_tile"],
    ) != (17, 0, 18, 28, 0x06C):
        raise BuildError(f"page17 full-clear contract failed: {page17}")

    table_size = len(pages) * RECORD.size
    cursor = HEADER + table_size
    records = []
    blobs = []
    for packed, summary in zip(packed_pages, summaries):
        map_off = cursor
        gfx_off = map_off + len(packed["tilemap"])
        cursor = gfx_off + len(packed["gfx"])
        if cursor > ATLAS_SIZE:
            raise BuildError(
                f"bank 50 overflow at page {packed['rom_page']}: {cursor}"
            )
        records.append(
            RECORD.pack(
                packed["slot"],
                1 if packed["cinematic"] else 0,
                packed["row0"],
                packed["nrows"],
                packed["ntiles"],
                map_off,
                gfx_off,
                28,
                packed["palette"],
                packed["first_tile"],
            )
        )
        blobs.append(packed["tilemap"] + packed["gfx"])
        summary.update(
            {
                "map_off": f"{map_off:04X}",
                "gfx_off": f"{gfx_off:04X}",
                "bytes": len(packed["tilemap"]) + len(packed["gfx"]),
            }
        )

    header = struct.pack(
        "<4sHHHHHH", b"ECKO", 2, len(pages), HEADER, cursor, 0x50, 0
    )
    payload = header + b"".join(records) + b"".join(blobs)
    if len(payload) != cursor:
        raise BuildError(f"atlas cursor mismatch: {len(payload)} != {cursor}")
    if len(payload) != 63_152:
        raise BuildError(f"unexpected full-clear atlas size: {len(payload)}")

    # Reconstruct every record directly from its preview-derived bytes.
    for packed in packed_pages:
        page = packed["rom_page"]
        record = RECORD.unpack_from(payload, page * RECORD.size)
        if record[:5] != (
            packed["slot"],
            1 if packed["cinematic"] else 0,
            packed["row0"],
            packed["nrows"],
            packed["ntiles"],
        ):
            raise BuildError(f"page {page} packed record mismatch: {record}")
        if payload[record[5] : record[5] + len(packed["tilemap"])] != packed["tilemap"]:
            raise BuildError(f"page {page} packed tilemap mismatch")
        if payload[record[6] : record[6] + len(packed["gfx"])] != packed["gfx"]:
            raise BuildError(f"page {page} packed graphics mismatch")

    sequence = {
        16: (
            RECORD.unpack_from(payload, 16 * RECORD.size)[9],
            RECORD.unpack_from(payload, 16 * RECORD.size)[4],
        ),
        **{
            page: (
                RECORD.unpack_from(payload, page * RECORD.size)[9],
                RECORD.unpack_from(payload, page * RECORD.size)[4],
            )
            for page in range(17, 22)
        },
    }
    pair_rows = []
    for previous, current in zip(range(16, 21), range(17, 22)):
        previous_first, previous_count = sequence[previous]
        current_first, current_count = sequence[current]
        overlap = ranges_overlap(
            previous_first, previous_count, current_first, current_count
        )
        if overlap:
            raise BuildError(f"pages {previous}->{current} ranges overlap")
        pair_rows.append(
            {
                "previous": previous,
                "current": current,
                "previous_range": (
                    f"{previous_first:03X}-{previous_first + previous_count - 1:03X}"
                ),
                "current_range": (
                    f"{current_first:03X}-{current_first + current_count - 1:03X}"
                ),
                "overlap": False,
            }
        )

    candidate = bytearray(parent)
    candidate[ATLAS_BASE : ATLAS_BASE + ATLAS_SIZE] = b"\xFF" * ATLAS_SIZE
    candidate[ATLAS_BASE : ATLAS_BASE + len(payload)] = payload
    helper = build_preload_clear_helper()
    candidate[PRELOAD_HELPER:PRELOAD_HELPER_END] = helper
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if not ws_checksum_valid(result):
        raise BuildError("candidate checksum update failed")

    diffs = changed_offsets(parent, result)
    outside = [
        offset
        for offset in diffs
        if not (ATLAS_BASE <= offset < ATLAS_BASE + ATLAS_SIZE)
        and not (PRELOAD_HELPER <= offset < PRELOAD_HELPER_END)
        and offset not in {ROM_SIZE - 2, ROM_SIZE - 1}
    ]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared ending-credit overlay changed")
    if result[0xFEFD1E:0xFEFD50] != parent[0xFEFD1E:0xFEFD50]:
        raise BuildError("cinematic page stubs changed")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        raise BuildError("range parent inputs changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)
    atomic_bytes(ATLAS_DUMP, payload)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_galmuri11_bitmap_full_bg_clear_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "main_tip": {**identity(MAIN, main_before), "unchanged": True},
        "parent": identity(PARENT_ROM, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "fixes": {
            "early_full_bg_clear": {
                "site": "7E:FD50",
                "display": "BG disabled at port 00 before clear",
                "destination": "IRAM 3000, 28x18 words",
                "fill_entry": "21F6 (bank-1 tile 1F6, palette 0)",
                "return": "7E:CA75",
                "helper_bytes": len(helper),
            },
            "page17_full_map": {
                "row0": 0,
                "nrows": 18,
                "upper_rows": "0-12 explicit black",
                "bar_rows": "13-17 Galmuri11 Bitmap Korean",
                "ntiles": page17["ntiles"],
                "first_tile": "06C",
                "last_tile": "087",
            },
        },
        "atlas": {
            "bank": "50",
            "bytes": len(payload),
            "free_bytes": ATLAS_SIZE - len(payload),
            "records": len(pages),
            "dump": rel(ATLAS_DUMP),
            "pages": summaries,
        },
        "consecutive_pairs": pair_rows,
        "diff": {
            "changed_bytes": len(diffs),
            "declared_ranges": [
                "500000-50FFFF bank 50 atlas",
                "FEFD50-FEFD6E preload clear helper",
                "FFFFFE-FFFFFF checksum",
            ],
            "outside_declared_ranges": 0,
        },
        "preserved": {
            "strings_and_galmuri11_bitmap_glyphs": True,
            "shared_overlay_byte_exact": True,
            "cinematic_page_stubs_byte_exact": True,
            "main_tip_unchanged": MAIN.read_bytes() == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save_before,
            "paired_saveram_byte_exact": OUT_SAVE.read_bytes() == parent_save,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "Tom Create disappears before page 17 art becomes visible",
                "page16 text tiles leave no upper BG fragments",
                "page17 art has no Korean-tile FG corruption",
                "pages17-21 bars, art, and separators remain intact",
            ],
            "savestate_note": (
                "Replay the ending with the paired SaveRAM.  Do not validate the "
                "entry-time clear by loading an old credits savestate."
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
                "fixes": report["fixes"],
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
