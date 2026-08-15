#!/usr/bin/env python3
"""Build the second cinematic ending-credit runtime test candidate.

This candidate addresses three frame-timeline failures observed in the first
``first_tile=0x098`` test ROM:

* pages 17-19 return to tile 0x080, outside the later FG use of 0x0B5-0x0BC;
* pages 20-21 alone move to 0x091, avoiding both separator tile 0x090 and the
  later FG range;
* the cinematic loader temporarily disables BG while it replaces graphics
  under the still-live page-16 Korean tilemap, then re-enables BG after the
  stock screen writer;
* pages 17-19 use palette 8, which is already white/black at the overlay hook,
  instead of palette 10, which is still in its transition gradient.

The current main TIP and live SaveRAM are read-only inputs.  This script only
writes a separate test ROM, paired SaveRAM, and report.
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
OUT_DIR = ROOT / "out/patch/ending_credits_cinematic_transition_guard_candidate"
OUT_ROM = OUT_DIR / "monoeye_ko_expanded_ending_credits_cinematic_transition_guard_test.wsc"
OUT_SAVE = OUT_DIR / "monoeye_ko_expanded_ending_credits_cinematic_transition_guard_test.sav"
REPORT = OUT_DIR / "ending_credits_cinematic_transition_guard_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_PARENT_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)

ATLAS_BASE = 0x500000
ATLAS_RECORD = struct.Struct("<BBBBHHHHHH")
PALETTE_FIELD = 12
FIRST_TILE_FIELD = 14

# Physical offsets in the expanded 16 MiB ROM.
PRELOAD_SITE = 0xFECA71       # stock: mov word [si],0 before resource setup/load
PAGE17_RETURN_SITE = 0xFECB0E  # already jumps to 7E:FD1E
PAGE21_SITE = 0xFED5C0         # existing redirect to old 7E:FD28 stub
STUB_REGION = 0xFEFD1E
STUB_REGION_END = 0xFEFD5D

# Logical 16-bit addresses inside bank 7E.
PAGE17_STUB_IP = 0xFD1E
PAGE21_STUB_IP = 0xFD40
PRELOAD_STUB_IP = 0xFD50

EXPECTED_PRELOAD = bytes.fromhex("C7040000")
EXPECTED_PAGE17_REDIRECT = bytes.fromhex("E90D32")
EXPECTED_PAGE21_REDIRECT = bytes.fromhex("E9652790")
EXPECTED_OLD_STUBS = bytes.fromhex(
    "8A059A30FF00F0E9A9CE"
    "B0159A30FF00F032D2E9ABD8"
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


def near_jmp(src_ip: int, dst_ip: int) -> bytes:
    displacement = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE9" + struct.pack("<H", displacement)


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def record_offset(page: int) -> int:
    return ATLAS_BASE + page * ATLAS_RECORD.size


def build_page17_stub() -> bytes:
    # Re-enable BG after the stock 7E:CB06 screen writer, then run the existing
    # generic page-selected overlay and return to the stock epilogue.
    return (
        bytes.fromhex("E400")       # in al,00h (display control)
        + bytes.fromhex("0C01")     # or al,01h (BG enable)
        + bytes.fromhex("E600")     # out 00h,al
        + bytes.fromhex("8A05")     # mov al,[di] (ROM page 17-19)
        + bytes.fromhex("9A30FF00F0")  # lcall F000:FF30
        + near_jmp(PAGE17_STUB_IP + 13, 0xCBD1)
    )


def build_page21_stub() -> bytes:
    return (
        bytes.fromhex("B015")       # mov al,21
        + bytes.fromhex("9A30FF00F0")  # lcall F000:FF30
        + bytes.fromhex("32D2")     # xor dl,dl (overwritten stock instruction)
        + near_jmp(PAGE21_STUB_IP + 9, 0xD5DF)
    )


def build_preload_stub() -> bytes:
    # Preserve the overwritten stock reset, hide BG while CAD1/CB06 replace the
    # page-16 graphics/map, then return to the untouched setup at CA75.
    return (
        bytes.fromhex("C7040000")   # mov word [si],0000
        + bytes.fromhex("E400")     # in al,00h
        + bytes.fromhex("24FE")     # and al,FEh (BG disable; keep FG/OBJ)
        + bytes.fromhex("E600")     # out 00h,al
        + near_jmp(PRELOAD_STUB_IP + 10, 0xCA75)
    )


def main() -> int:
    if not MAIN.is_file() or not LIVE_SAVE.is_file():
        raise BuildError("current main TIP or live SaveRAM is missing")
    parent = MAIN.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"main TIP identity drifted: {len(parent)} {sha256(parent)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(live_save)}")
    if not ws_checksum_valid(parent):
        raise BuildError("parent WonderSwan checksum is invalid")

    if parent[PRELOAD_SITE : PRELOAD_SITE + 4] != EXPECTED_PRELOAD:
        raise BuildError("cinematic preload site drifted")
    if parent[PAGE17_RETURN_SITE : PAGE17_RETURN_SITE + 3] != EXPECTED_PAGE17_REDIRECT:
        raise BuildError("page 17-19 redirect site drifted")
    if parent[PAGE21_SITE : PAGE21_SITE + 4] != EXPECTED_PAGE21_REDIRECT:
        raise BuildError("page 21 redirect site drifted")
    if parent[STUB_REGION : STUB_REGION + len(EXPECTED_OLD_STUBS)] != EXPECTED_OLD_STUBS:
        raise BuildError("existing ending-credit redirect stubs drifted")
    if any(
        byte != 0xFF
        for byte in parent[
            STUB_REGION + len(EXPECTED_OLD_STUBS) : STUB_REGION_END
        ]
    ):
        raise BuildError("7E:FD34-FD5C is no longer a free code cave")

    candidate = bytearray(parent)
    record_changes = []
    allowed: set[int] = set()

    # Pages 17-19 keep the proven 0x080 tile range, but use palette 8 because it
    # is already white/black when the overlay hook runs.
    for page in (17, 18, 19):
        off = record_offset(page)
        before = ATLAS_RECORD.unpack_from(parent, off)
        if (before[0], before[1], before[2], before[3], before[8], before[9]) != (
            page,
            1,
            13,
            5,
            10,
            0x080,
        ):
            raise BuildError(f"page {page} atlas record drifted: {before}")
        struct.pack_into("<H", candidate, off + PALETTE_FIELD, 8)
        allowed.update(range(off + PALETTE_FIELD, off + PALETTE_FIELD + 2))
        record_changes.append(
            {
                "rom_page": page,
                "ntiles": before[4],
                "first_tile": "080",
                "last_tile": f"{0x080 + before[4] - 1:03X}",
                "old_palette": 10,
                "new_palette": 8,
            }
        )

    # Pages 20-21 move just above the preserved separator tile 0x090 while
    # ending below the observed later FG use beginning at 0x0B5.
    for page in (20, 21):
        off = record_offset(page)
        before = ATLAS_RECORD.unpack_from(parent, off)
        if (before[0], before[1], before[2], before[3], before[9]) != (
            page,
            1,
            13,
            5,
            0x080,
        ):
            raise BuildError(f"page {page} atlas record drifted: {before}")
        first_tile = 0x091
        if first_tile + before[4] - 1 >= 0x0B5:
            raise BuildError(f"page {page} target reaches dynamic FG range")
        struct.pack_into("<H", candidate, off + FIRST_TILE_FIELD, first_tile)
        allowed.update(range(off + FIRST_TILE_FIELD, off + FIRST_TILE_FIELD + 2))
        record_changes.append(
            {
                "rom_page": page,
                "ntiles": before[4],
                "old_first_tile": "080",
                "new_first_tile": "091",
                "new_last_tile": f"{first_tile + before[4] - 1:03X}",
                "palette": before[8],
            }
        )

    # Redirect the resource-load prelude through a BG-disable helper.
    candidate[PRELOAD_SITE : PRELOAD_SITE + 3] = near_jmp(0xCA71, PRELOAD_STUB_IP)
    allowed.update(range(PRELOAD_SITE, PRELOAD_SITE + 3))

    # Relocate the page-21 stub so the page17-19 stub can grow by six bytes.
    candidate[PAGE21_SITE : PAGE21_SITE + 3] = near_jmp(0xD5C0, PAGE21_STUB_IP)
    allowed.update(range(PAGE21_SITE, PAGE21_SITE + 3))

    candidate[STUB_REGION:STUB_REGION_END] = b"\xFF" * (STUB_REGION_END - STUB_REGION)
    page17_stub = build_page17_stub()
    page21_stub = build_page21_stub()
    preload_stub = build_preload_stub()
    if len(page17_stub) != 16 or len(page21_stub) != 12 or len(preload_stub) != 13:
        raise BuildError(
            f"unexpected helper sizes: {len(page17_stub)}, {len(page21_stub)}, "
            f"{len(preload_stub)}"
        )
    candidate[STUB_REGION : STUB_REGION + len(page17_stub)] = page17_stub
    candidate[
        0xFE0000 + PAGE21_STUB_IP : 0xFE0000 + PAGE21_STUB_IP + len(page21_stub)
    ] = page21_stub
    candidate[
        0xFE0000 + PRELOAD_STUB_IP : 0xFE0000 + PRELOAD_STUB_IP + len(preload_stub)
    ] = preload_stub
    allowed.update(range(STUB_REGION, STUB_REGION_END))

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    allowed.update({ROM_SIZE - 2, ROM_SIZE - 1})
    if not ws_checksum_valid(result):
        raise BuildError("candidate WonderSwan checksum update failed")

    diffs = changed_offsets(parent, result)
    outside = [off for off in diffs if off not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    # The shared 7F:FF18 overlay implementation must stay byte-exact.
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared ending-credit overlay changed unexpectedly")
    if result[PAGE17_RETURN_SITE : PAGE17_RETURN_SITE + 3] != EXPECTED_PAGE17_REDIRECT:
        raise BuildError("page17 redirect was not preserved")
    if MAIN.read_bytes() != parent or LIVE_SAVE.read_bytes() != live_save:
        raise BuildError("live main TIP or SaveRAM changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(LIVE_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_cinematic_transition_guard_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "parent": identity(MAIN, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "fixes": {
            "atlas_records": record_changes,
            "page16_to_17_guard": {
                "site": "7E:CA71",
                "helper": "7E:FD50",
                "action": "disable BG at display-control port 00 before CAD1 resource load",
            },
            "page17_to_19_return": {
                "site": "7E:CB0E -> 7E:FD1E",
                "action": "re-enable BG after CB06, then apply Korean bar",
            },
            "page21_stub": {
                "site": "7E:D5C0 -> 7E:FD40",
                "action": "relocated without semantic change",
            },
        },
        "diff": {
            "changed_bytes": len(diffs),
            "changed_offsets": [f"{off:08X}" for off in diffs],
            "outside_declared_ranges": 0,
        },
        "guards": {
            "parent_identity_exact": True,
            "parent_checksum_valid": True,
            "candidate_checksum_valid": True,
            "record_contracts_exact": True,
            "code_sites_exact": True,
            "new_cave_was_ff": True,
            "shared_overlay_byte_exact": True,
            "main_tip_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save,
            "paired_saveram_byte_exact": OUT_SAVE.read_bytes() == live_save,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "page 16 -> 17 transition contains no mismatched BG graphics",
                "page 17 bottom bar is white on black immediately when shown",
                "page 19 transition contains no upper-left FG glyph fragments",
                "pages 20-21 retain the clean row-12 separator",
                "pages 17-21 upper animations and Korean text remain intact",
            ],
            "savestate_note": (
                "Use the candidate ROM and paired SaveRAM and replay the ending. "
                "Old states restore stale VRAM and cannot validate entry-time hooks."
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
