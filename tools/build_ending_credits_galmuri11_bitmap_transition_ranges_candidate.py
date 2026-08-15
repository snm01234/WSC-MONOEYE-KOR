#!/usr/bin/env python3
"""Build the Galmuri11 Bitmap cinematic transition-range test ROM.

The first Galmuri11 Bitmap candidate used consecutive-page VRAM ranges that
overlapped.  While the previous page map was still live, the next page graphics
could therefore turn its final company line into fragments.  This candidate
changes only three cinematic ``first_tile`` fields so every consecutive pair
uses disjoint, captured-state-safe ranges.
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


PARENT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate"
PARENT_ROM = PARENT_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.wsc"
PARENT_SAVE = PARENT_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.sav"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_transition_ranges_candidate"
OUT_ROM = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_transition_ranges_test.wsc"
)
OUT_SAVE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_transition_ranges_test.sav"
)
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_transition_ranges_report.json"

EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
EXPECTED_PARENT_SHA256 = (
    "5f92d13d7ec071f133971dfeab3135151d98975f875363fa9a91d32fe70f713e"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BASE = 0x500000
RECORD = struct.Struct("<BBBBHHHHHH")
FIRST_TILE_FIELD = 14

EXPECTED_OLD = {
    17: {"ntiles": 28, "palette": 8, "first": 0x051},
    18: {"ntiles": 40, "palette": 8, "first": 0x051},
    19: {"ntiles": 43, "palette": 8, "first": 0x051},
    20: {"ntiles": 39, "palette": 10, "first": 0x091},
    21: {"ntiles": 26, "palette": 6, "first": 0x091},
}
NEW_FIRST_TILE = {
    17: 0x0DF,
    18: 0x091,
    19: 0x051,
    20: 0x091,
    21: 0x0BD,
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


def ranges_overlap(first_a: int, count_a: int, first_b: int, count_b: int) -> bool:
    return max(first_a, first_b) <= min(
        first_a + count_a - 1, first_b + count_b - 1
    )


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [index for index, (a, b) in enumerate(zip(before, after)) if a != b]


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
        raise BuildError(f"Bitmap parent drifted: {len(parent)} {sha256(parent)}")
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drifted: {len(main_before)} {sha256(main_before)}")
    if len(parent_save) != SAVE_SIZE or len(live_save_before) != SAVE_SIZE:
        raise BuildError("SaveRAM size drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("Bitmap parent checksum invalid")

    records_before = {}
    for page, expected in EXPECTED_OLD.items():
        record = RECORD.unpack_from(parent, ATLAS_BASE + page * RECORD.size)
        if (
            record[0],
            record[1],
            record[2],
            record[3],
            record[4],
            record[8],
            record[9],
        ) != (
            page,
            1,
            13,
            5,
            expected["ntiles"],
            expected["palette"],
            expected["first"],
        ):
            raise BuildError(f"page {page} parent record drifted: {record}")
        records_before[page] = record

    # Page 16 is the immediately preceding full-page record.
    page16 = RECORD.unpack_from(parent, ATLAS_BASE + 16 * RECORD.size)
    sequence = {
        16: (page16[9], page16[4]),
        **{
            page: (NEW_FIRST_TILE[page], EXPECTED_OLD[page]["ntiles"])
            for page in range(17, 22)
        },
    }
    overlaps = []
    for previous, current in zip(range(16, 21), range(17, 22)):
        previous_first, previous_count = sequence[previous]
        current_first, current_count = sequence[current]
        if ranges_overlap(
            previous_first, previous_count, current_first, current_count
        ):
            overlaps.append((previous, current))
    if overlaps:
        raise BuildError(f"new consecutive ranges overlap: {overlaps}")

    candidate = bytearray(parent)
    record_changes = []
    allowed: set[int] = set()
    for page in range(17, 22):
        old = records_before[page]
        new_first = NEW_FIRST_TILE[page]
        last = new_first + old[4] - 1
        if new_first <= 0x090 <= last:
            raise BuildError(f"page {page} reaches separator tile 090")
        if last > 0x1FF:
            raise BuildError(f"page {page} exceeds tile index 1FF")
        offset = ATLAS_BASE + page * RECORD.size + FIRST_TILE_FIELD
        if new_first != old[9]:
            struct.pack_into("<H", candidate, offset, new_first)
            allowed.update(range(offset, offset + 2))
        record_changes.append(
            {
                "rom_page": page,
                "ntiles": old[4],
                "palette": old[8],
                "old_first_tile": f"{old[9]:03X}",
                "new_first_tile": f"{new_first:03X}",
                "new_last_tile": f"{last:03X}",
                "changed": new_first != old[9],
            }
        )

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    allowed.update({ROM_SIZE - 2, ROM_SIZE - 1})
    if not ws_checksum_valid(result):
        raise BuildError("candidate checksum update failed")
    diffs = changed_offsets(parent, result)
    outside = [offset for offset in diffs if offset not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    # Atlas payload, code, and all non-record data remain byte-exact.
    masked_parent = bytearray(parent)
    masked_result = bytearray(result)
    for page in (17, 18, 21):
        offset = ATLAS_BASE + page * RECORD.size + FIRST_TILE_FIELD
        masked_parent[offset : offset + 2] = b"\x00\x00"
        masked_result[offset : offset + 2] = b"\x00\x00"
    masked_parent[-2:] = b"\x00\x00"
    masked_result[-2:] = b"\x00\x00"
    if masked_parent != masked_result:
        raise BuildError("bytes outside the three first_tile fields changed")
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared ending-credit overlay changed")
    if result[0xFE0000:0xFF0000] != parent[0xFE0000:0xFF0000]:
        raise BuildError("transition-guard code changed")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        raise BuildError("Bitmap parent inputs changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": (
            "tools/build_ending_credits_galmuri11_bitmap_transition_ranges_candidate.py"
        ),
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "purpose": (
            "prevent the next cinematic graphics upload from reinterpreting the "
            "still-live previous-page Korean tilemap"
        ),
        "main_tip": {**identity(MAIN, main_before), "unchanged": True},
        "parent": identity(PARENT_ROM, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "ranges": record_changes,
        "consecutive_page_pairs": [
            {
                "previous": previous,
                "current": current,
                "previous_range": (
                    f"{sequence[previous][0]:03X}-"
                    f"{sequence[previous][0] + sequence[previous][1] - 1:03X}"
                ),
                "current_range": (
                    f"{sequence[current][0]:03X}-"
                    f"{sequence[current][0] + sequence[current][1] - 1:03X}"
                ),
                "overlap": False,
            }
            for previous, current in zip(range(16, 21), range(17, 22))
        ],
        "diff": {
            "changed_bytes": len(diffs),
            "changed_offsets": [f"{offset:08X}" for offset in diffs],
            "outside_declared_fields": 0,
        },
        "preserved": {
            "galmuri11_bitmap_tiles_byte_exact": True,
            "atlas_tilemaps_byte_exact": True,
            "atlas_graphics_byte_exact": True,
            "shared_overlay_byte_exact": True,
            "transition_guard_code_byte_exact": True,
            "main_tip_unchanged": MAIN.read_bytes() == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save_before,
            "paired_saveram_byte_exact": OUT_SAVE.read_bytes() == parent_save,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "page 16 Tom Create line leaves no fragments during page 17 entry",
                "pages 17-21 have no previous-page text fragments",
                "upper cinematic art and row-12 separators remain intact",
                "Galmuri11 Bitmap text remains readable",
            ],
            "savestate_note": (
                "Replay the ending with the paired SaveRAM.  Old credits states "
                "restore stale VRAM and cannot validate entry-time uploads."
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
                "ranges": report["ranges"],
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
