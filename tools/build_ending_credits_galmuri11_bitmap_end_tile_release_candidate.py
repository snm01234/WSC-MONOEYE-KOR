#!/usr/bin/env python3
"""Build the Galmuri11 Bitmap END tile-release test ROM.

Map-only clear at ``7E:D652`` makes ``제작 / 반다이`` disappear, but END's first
frame still glitches in that region.  Stock END (``73:B6ED``) writes tile ``0``
into empty rows 0-6 and 11-17, and tiles ``001-04B`` into the logo rows 7-10.
Those bank-0 slots still hold page-16 / page-21 Korean pixels until END DMA
finishes, so the first visible END map reinterprets them.

This candidate keeps the proven D652 near-call and 18x32 ``21F6`` map fill,
then zeroes bank-0 tiles ``000-04B`` and ``0BD-0D6`` before END is registered.
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


PARENT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_end_boundary_clear_candidate"
PARENT_ROM = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_boundary_clear_test.wsc"
)
PARENT_SAVE = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_boundary_clear_test.sav"
)
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_end_tile_release_candidate"
OUT_ROM = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_tile_release_test.wsc"
)
OUT_SAVE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_tile_release_test.sav"
)
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_end_tile_release_report.json"

EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
EXPECTED_PARENT_SHA256 = (
    "ad8ea25e1c36e34bb79feef8e5b15e0c4d65479f5b6762733944c8949bfa06bf"
)
EXPECTED_SAVE_SHA256 = (
    "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"
)

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

END_SITE = 0xFED652
HELPER_PHYS = 0xFEFD83
HELPER_IP = 0xFD83
OLD_HELPER_END = 0xFEFDBA
HELPER_END = 0xFEFDCF

EXPECTED_END_CALL = bytes.fromhex("E82E27909090")
EXPECTED_OLD_HELPER = bytes.fromhex(
    "9C5053515256571E06"
    "B8F62150B8180050B8200050B80030BB000033C933D29AC77C008083C406"
    "C706561B000F"
    "071F5F5E5A595B589DC3"
)
EXPECTED_STOCK_BG_CLEAR = bytes.fromhex(
    "B8F62150B8180050B8200050B80030BB000033C933D29AC77C008083C406"
)
EXPECTED_END_WRITE = bytes.fromhex("C706561B000F")
TILE_ZERO = bytes.fromhex(
    "33C08EC0"  # xor ax,ax; mov es,ax
    "BF0040"    # mov di,4000  tiles 000-04B
    "B9C004"    # mov cx,04C0  (0x4C tiles * 16 words)
    "FCF3AB"    # cld; rep stosw
    "BFA057"    # mov di,57A0  tile 0BD
    "B9A001"    # mov cx,01A0  (26 tiles * 16 words)
    "F3AB"      # rep stosw
)


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


def build_end_tile_release_helper() -> bytes:
    body = bytearray()
    body += bytes.fromhex("9C5053515256571E06")
    body += EXPECTED_STOCK_BG_CLEAR
    body += TILE_ZERO
    body += EXPECTED_END_WRITE
    body += bytes.fromhex("071F5F5E5A595B589DC3")
    if len(body) != HELPER_END - HELPER_PHYS:
        raise BuildError(f"unexpected END tile-release helper size: {len(body)}")
    return bytes(body)


def main() -> int:
    required = (PARENT_ROM, PARENT_SAVE, MAIN, LIVE_SAVE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError(f"missing inputs: {missing}")

    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    main_before = MAIN.read_bytes()
    live_save_before = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"END-boundary parent drifted: {len(parent)} {sha256(parent)}")
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drifted: {len(main_before)} {sha256(main_before)}")
    if len(parent_save) != SAVE_SIZE or sha256(parent_save) != EXPECTED_SAVE_SHA256:
        raise BuildError("paired SaveRAM identity drifted")
    if len(live_save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("END-boundary parent checksum invalid")
    if parent[END_SITE : END_SITE + 6] != EXPECTED_END_CALL:
        raise BuildError("END call site drifted")
    if parent[HELPER_PHYS:OLD_HELPER_END] != EXPECTED_OLD_HELPER:
        raise BuildError("parent END helper drifted")
    if any(byte != 0xFF for byte in parent[OLD_HELPER_END:HELPER_END]):
        raise BuildError("tile-release helper extension cave is no longer free FF")

    helper = build_end_tile_release_helper()
    candidate = bytearray(parent)
    candidate[HELPER_PHYS:HELPER_END] = helper

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if not ws_checksum_valid(result):
        raise BuildError("candidate checksum update failed")

    allowed = set(range(HELPER_PHYS, HELPER_END))
    allowed.update({ROM_SIZE - 2, ROM_SIZE - 1})
    diffs = changed_offsets(parent, result)
    outside = [offset for offset in diffs if offset not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    if result[END_SITE : END_SITE + 6] != EXPECTED_END_CALL:
        raise BuildError("END call site changed")
    if result[0xFEFD5D:0xFEFD83] != parent[0xFEFD5D:0xFEFD83]:
        raise BuildError("page16-exit helper changed")
    if result[0x500000:0x510000] != parent[0x500000:0x510000]:
        raise BuildError("atlas changed")
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared overlay changed")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        raise BuildError("parent inputs changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": (
            "tools/build_ending_credits_galmuri11_bitmap_end_tile_release_candidate.py"
        ),
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "purpose": (
            "zero END-owned and page21 Korean bank-0 tiles at D652 so the first "
            "END frame cannot display leftover 제작/반다이 pixels"
        ),
        "main_tip": {**identity(MAIN, main_before), "unchanged": True},
        "parent": identity(PARENT_ROM, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "fixes": {
            "end_tile_release": {
                "site": "7E:FD83 helper, still called from 7E:D652",
                "map_clear": "unchanged 18x32 21F6 via 8000:7CC7",
                "zero_tiles": [
                    {"range": "000-04B", "reason": "END empty cells use tile 0; logo uses 001-04B"},
                    {"range": "0BD-0D6", "reason": "page21 Korean 제작/반다이 bar"},
                ],
                "destination": "IRAM 4000 bank-0 packed-4bpp",
                "preserves_replaced_instruction": "mov word [1B56],0F00",
                "helper_bytes": len(helper),
            }
        },
        "preserved": {
            "page16_exit_clear": True,
            "end_boundary_call_site": True,
            "cinematic_first_tile_ranges": True,
            "galmuri11_bitmap_graphics": True,
            "shared_overlay_byte_exact": True,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
            "paired_saveram_byte_exact": True,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "declared_ranges": [
                "FEFD83-FEFDCE END helper including tile zero",
                "FFFFFE-FFFFFF checksum",
            ],
            "outside_declared_ranges": 0,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "page16-to-17 Tom Create residue remains gone",
                "제작/반다이 still disappears before END",
                "first END frame has no 제작/반다이 fragments in the old bar region",
            ],
            "savestate_note": (
                "Start from the paired SaveRAM and replay the ending. Old savestates "
                "restore stale VRAM."
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
