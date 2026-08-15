#!/usr/bin/env python3
"""Build a test ROM that relocates cinematic credit overlay tiles.

The promoted ending-credit hook currently uploads every cinematic record at
bank-0 tile 0x080.  Pages 20 and 21 preserve BG row 12, whose separator uses
tile 0x090, so the Korean upload overwrites the separator graphics.  This
candidate changes only the five cinematic atlas records (ROM pages 17-21) to
start at tile 0x098.  The shared hook already reads ``first_tile`` from each
record for both the VRAM upload destination and tilemap remap.

The current main TIP and live SaveRAM are read-only inputs.  The candidate,
paired SaveRAM, and JSON report are written under ``out/patch``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import update_ws_checksum  # noqa: E402


MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_DIR = ROOT / "out/patch/ending_credits_cinematic_vram_reloc_candidate"
OUT_ROM = OUT_DIR / "monoeye_ko_expanded_ending_credits_cinematic_vram_reloc_test.wsc"
OUT_SAVE = OUT_DIR / "monoeye_ko_expanded_ending_credits_cinematic_vram_reloc_test.sav"
REPORT = OUT_DIR / "ending_credits_cinematic_vram_reloc_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_PARENT_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
ATLAS_BANK = 0x50
ATLAS_BASE = ATLAS_BANK * 0x10000
ATLAS_MAGIC = b"ECKO"
ATLAS_VERSION = 2
ATLAS_RECORD_SIZE = 16
ATLAS_RECORD = struct.Struct("<BBBBHHHHHH")
FIRST_TILE_FIELD = 14

CINEMATIC_PAGES = tuple(range(17, 22))
OLD_FIRST_TILE = 0x080
NEW_FIRST_TILE = 0x098
EXPECTED_CAPTURE_SLOTS = {page: page for page in CINEMATIC_PAGES}


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


def record_offset(page: int) -> int:
    # The atlas header occupies logical record 0, so ROM page N is at N * 0x10.
    return ATLAS_BASE + page * ATLAS_RECORD_SIZE


def main() -> int:
    if not MAIN.is_file() or not LIVE_SAVE.is_file():
        raise BuildError("current main TIP or live SaveRAM is missing")

    parent = MAIN.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE:
        raise BuildError(f"main TIP size is {len(parent)}, expected {ROM_SIZE}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size is {len(live_save)}, expected {SAVE_SIZE}")
    if sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"main TIP identity drifted: {sha256(parent)}")
    if not ws_checksum_valid(parent):
        raise BuildError("parent WonderSwan checksum is invalid")

    magic, version, count, table_off, atlas_size, bank, reserved = struct.unpack_from(
        "<4sHHHHHH", parent, ATLAS_BASE
    )
    if (magic, version, count, table_off, bank, reserved) != (
        ATLAS_MAGIC,
        ATLAS_VERSION,
        21,
        16,
        ATLAS_BANK,
        0,
    ):
        raise BuildError(
            "unexpected ending-credit atlas header: "
            f"{(magic, version, count, table_off, atlas_size, bank, reserved)!r}"
        )
    if atlas_size != 58_264:
        raise BuildError(f"ending-credit atlas size drifted: {atlas_size}")

    candidate = bytearray(parent)
    records = []
    declared_record_bytes: set[int] = set()
    for page in CINEMATIC_PAGES:
        off = record_offset(page)
        values = list(ATLAS_RECORD.unpack_from(parent, off))
        (
            capture_slot,
            cinematic,
            row0,
            nrows,
            ntiles,
            map_off,
            gfx_off,
            cols,
            palette,
            first_tile,
        ) = values
        expected = EXPECTED_CAPTURE_SLOTS[page]
        if capture_slot != expected:
            raise BuildError(
                f"page {page} capture slot is {capture_slot}, expected {expected}"
            )
        if (cinematic, row0, nrows, cols, first_tile) != (1, 13, 5, 28, OLD_FIRST_TILE):
            raise BuildError(
                f"page {page} record contract drifted: "
                f"cinematic={cinematic} row0={row0} nrows={nrows} "
                f"cols={cols} first={first_tile:#x}"
            )
        if not 1 <= ntiles <= 37:
            raise BuildError(f"page {page} tile count is unsafe: {ntiles}")
        if NEW_FIRST_TILE + ntiles - 1 >= 0x1FF:
            raise BuildError(f"page {page} relocated tile range exceeds bank-0 tiles")

        struct.pack_into("<H", candidate, off + FIRST_TILE_FIELD, NEW_FIRST_TILE)
        declared_record_bytes.update(range(off + FIRST_TILE_FIELD, off + FIRST_TILE_FIELD + 2))
        records.append(
            {
                "rom_page": page,
                "capture_slot": capture_slot,
                "row0": row0,
                "nrows": nrows,
                "ntiles": ntiles,
                "palette": palette,
                "map_off": f"{map_off:04X}",
                "gfx_off": f"{gfx_off:04X}",
                "old_first_tile": f"{OLD_FIRST_TILE:03X}",
                "new_first_tile": f"{NEW_FIRST_TILE:03X}",
                "new_last_tile": f"{NEW_FIRST_TILE + ntiles - 1:03X}",
                "physical_first_tile_field": f"{off + FIRST_TILE_FIELD:08X}",
            }
        )

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if not ws_checksum_valid(result):
        raise BuildError("candidate WonderSwan checksum update failed")

    diffs = changed_offsets(parent, result)
    allowed = declared_record_bytes | {ROM_SIZE - 2, ROM_SIZE - 1}
    outside = [off for off in diffs if off not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    for page in CINEMATIC_PAGES:
        off = record_offset(page)
        before = ATLAS_RECORD.unpack_from(parent, off)
        after = ATLAS_RECORD.unpack_from(result, off)
        if before[:-1] != after[:-1] or after[-1] != NEW_FIRST_TILE:
            raise BuildError(f"page {page} changed outside first_tile")

    atlas_end = ATLAS_BASE + atlas_size
    atlas_diff = [
        off
        for off in diffs
        if ATLAS_BASE <= off < atlas_end
    ]
    expected_atlas_diff = [record_offset(page) + FIRST_TILE_FIELD for page in CINEMATIC_PAGES]
    if atlas_diff != expected_atlas_diff:
        raise BuildError(
            f"unexpected atlas byte diff: {[f'{off:08X}' for off in atlas_diff]}"
        )
    if MAIN.read_bytes() != parent:
        raise BuildError("main TIP changed during build")
    if LIVE_SAVE.read_bytes() != live_save:
        raise BuildError("live SaveRAM changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(LIVE_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_cinematic_vram_reloc_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "purpose": (
            "Relocate cinematic ending-credit overlay tiles from 0x080 to 0x098 "
            "so pages 20-21 no longer overwrite preserved separator tile 0x090."
        ),
        "parent": identity(MAIN, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "atlas": {
            "bank": f"{ATLAS_BANK:02X}",
            "bytes": atlas_size,
            "records": records,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "changed_offsets": [f"{off:08X}" for off in diffs],
            "atlas_changed_bytes": len(atlas_diff),
            "atlas_changed_offsets": [f"{off:08X}" for off in atlas_diff],
            "outside_declared_ranges": 0,
            "note": (
                "Only the low byte of first_tile changes in each record; the final "
                "two checksum bytes may also differ."
            ),
        },
        "guards": {
            "parent_identity_exact": True,
            "parent_checksum_valid": True,
            "candidate_checksum_valid": True,
            "atlas_header_exact": True,
            "cinematic_record_contracts_exact": True,
            "only_first_tile_fields_and_checksum_changed": True,
            "main_tip_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save,
            "paired_saveram_byte_exact": OUT_SAVE.read_bytes() == live_save,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "page 16 -> 17 transition has no transient color/graphics corruption",
                "pages 17-19 preserve upper animation and Korean bottom bars",
                "pages 20-21 preserve the clean row-12 separator",
                "pages 20-21 Korean text remains correctly positioned and readable",
            ],
            "savestate_note": (
                "Old savestates restore serialized VRAM. Load the candidate ROM and "
                "paired SaveRAM, then enter/replay the ending so the relocated tiles "
                "are uploaded by the candidate hook."
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
                "diff": report["diff"],
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
