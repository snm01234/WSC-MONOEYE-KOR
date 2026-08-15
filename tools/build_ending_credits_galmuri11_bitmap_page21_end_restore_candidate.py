#!/usr/bin/env python3
"""Restore Bitmap page-21 END placement on top of the proven 16-to-17 ROM.

The page16-exit candidate fixed Tom Create.  Later END-boundary/tile-release
hooks made ``제작 / 반다이`` glitch on the first END frame.  The original
Galmuri11 Bitmap ROM had no 21-to-END output problem: page 21 used tiles
``091-0AA`` and left stock ``7E:D652`` / ``7E:D67F`` alone.

This candidate keeps the 16-to-17 ownership clear and idle split, then moves
only page 21's ``first_tile`` back to the Bitmap value.  No END helper is added.
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


PARENT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_page16_exit_clear_candidate"
PARENT_ROM = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.wsc"
)
PARENT_SAVE = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.sav"
)
BITMAP_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate"
BITMAP_ROM = BITMAP_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_page21_end_restore_candidate"
OUT_ROM = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page21_end_restore_test.wsc"
)
OUT_SAVE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page21_end_restore_test.sav"
)
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_page21_end_restore_report.json"

EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
EXPECTED_PARENT_SHA256 = (
    "f3198ca1f29b3d4584c49186f1a02274046912feebed530c56d5fb4c852bcf77"
)
EXPECTED_BITMAP_SHA256 = (
    "5f92d13d7ec071f133971dfeab3135151d98975f875363fa9a91d32fe70f713e"
)
EXPECTED_SAVE_SHA256 = (
    "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"
)

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BASE = 0x500000
RECORD = struct.Struct("<BBBBHHHHHH")
FIRST_TILE_FIELD = 14
PAGE21_OFFSET = ATLAS_BASE + 21 * RECORD.size + FIRST_TILE_FIELD

BITMAP_PAGE21_FIRST = 0x091
PARENT_PAGE21_FIRST = 0x0BD
PAGE21_NTILES = 26

END_SITE = 0xFED652
EXPECTED_STOCK_END = bytes.fromhex("C706561B000F")


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
    return [index for index, (a, b) in enumerate(zip(before, after)) if a != b]


def page21_first(rom: bytes) -> int:
    return RECORD.unpack_from(rom, ATLAS_BASE + 21 * RECORD.size)[9]


def main() -> int:
    required = (PARENT_ROM, PARENT_SAVE, BITMAP_ROM, MAIN, LIVE_SAVE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError(f"missing inputs: {missing}")

    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    bitmap = BITMAP_ROM.read_bytes()
    main_before = MAIN.read_bytes()
    live_save_before = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"16-to-17 parent drifted: {sha256(parent)}")
    if len(bitmap) != ROM_SIZE or sha256(bitmap) != EXPECTED_BITMAP_SHA256:
        raise BuildError(f"Bitmap ROM drifted: {sha256(bitmap)}")
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP drifted")
    if len(parent_save) != SAVE_SIZE or sha256(parent_save) != EXPECTED_SAVE_SHA256:
        raise BuildError("paired SaveRAM identity drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("16-to-17 parent checksum invalid")

    parent_record = RECORD.unpack_from(parent, ATLAS_BASE + 21 * RECORD.size)
    bitmap_record = RECORD.unpack_from(bitmap, ATLAS_BASE + 21 * RECORD.size)
    if (
        parent_record[0],
        parent_record[1],
        parent_record[2],
        parent_record[3],
        parent_record[4],
        parent_record[8],
        parent_record[9],
    ) != (21, 1, 13, 5, PAGE21_NTILES, 6, PARENT_PAGE21_FIRST):
        raise BuildError(f"parent page21 drifted: {parent_record}")
    if bitmap_record[9] != BITMAP_PAGE21_FIRST or bitmap_record[4] != PAGE21_NTILES:
        raise BuildError(f"Bitmap page21 drifted: {bitmap_record}")
    if parent[END_SITE : END_SITE + 6] != EXPECTED_STOCK_END:
        raise BuildError("parent already patched D652; refuse to keep END hooks")
    if bitmap[END_SITE : END_SITE + 6] != EXPECTED_STOCK_END:
        raise BuildError("Bitmap D652 drifted")

    candidate = bytearray(parent)
    struct.pack_into("<H", candidate, PAGE21_OFFSET, BITMAP_PAGE21_FIRST)
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if not ws_checksum_valid(result):
        raise BuildError("candidate checksum update failed")
    if page21_first(result) != BITMAP_PAGE21_FIRST:
        raise BuildError("page21 first_tile was not restored")

    allowed = set(range(PAGE21_OFFSET, PAGE21_OFFSET + 2))
    allowed.update({ROM_SIZE - 2, ROM_SIZE - 1})
    diffs = changed_offsets(parent, result)
    outside = [offset for offset in diffs if offset not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    if result[0xFED1CA:0xFED1CF] != parent[0xFED1CA:0xFED1CF]:
        raise BuildError("page16-exit redirect changed")
    if result[0xFECA6E:0xFECA71] != parent[0xFECA6E:0xFECA71]:
        raise BuildError("idle branch changed")
    if result[END_SITE : END_SITE + 6] != EXPECTED_STOCK_END:
        raise BuildError("D652 changed")
    if result[0xFEFD83:0xFEFDCF] != parent[0xFEFD83:0xFEFDCF]:
        raise BuildError("END helper cave changed")
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared overlay changed")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)

    last = BITMAP_PAGE21_FIRST + PAGE21_NTILES - 1
    report = {
        "schema_version": 1,
        "generated_by": (
            "tools/build_ending_credits_galmuri11_bitmap_page21_end_restore_candidate.py"
        ),
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "purpose": (
            "keep the proven 16-to-17 clear and restore Bitmap page21 END tiles"
        ),
        "main_tip": {**identity(MAIN, main_before), "unchanged": True},
        "parent": identity(PARENT_ROM, parent),
        "bitmap_reference": identity(BITMAP_ROM, bitmap),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "fixes": {
            "page21_end_restore": {
                "old_first_tile": f"{PARENT_PAGE21_FIRST:03X}",
                "new_first_tile": f"{BITMAP_PAGE21_FIRST:03X}",
                "new_last_tile": f"{last:03X}",
                "matches_bitmap_page21_first_tile": True,
                "end_site_stock": True,
            }
        },
        "preserved": {
            "page16_exit_clear": True,
            "idle_overlay_suppressed": True,
            "pages_17_to_20_ranges": True,
            "galmuri11_bitmap_graphics": True,
            "stock_D652": True,
            "no_end_helper": True,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
            "paired_saveram_byte_exact": True,
        },
        "note": (
            "page20 091-0B7 and restored page21 091-0AA overlap the same way as "
            "the user-validated Bitmap ROM; 16-to-17 ranges stay disjoint."
        ),
        "diff": {
            "changed_bytes": len(diffs),
            "declared_ranges": [
                "50015E page21 first_tile",
                "FFFFFE-FFFFFF checksum",
            ],
            "outside_declared_ranges": 0,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "page16-to-17 Tom Create residue remains gone",
                "page21-to-END matches Bitmap: no 제작/반다이 glitch on first END frame",
            ],
            "savestate_note": (
                "Start from the paired SaveRAM and replay the ending."
            ),
        },
        "promotion": "blocked_pending_user_runtime_validation",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "candidate": report["candidate"],
                "fixes": report["fixes"],
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
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
