#!/usr/bin/env python3
"""Build the Galmuri11 Bitmap page-16-exit BG-clear test ROM.

The reported 16-to-17 residue is the still-live Korean BG map from
``(주) 톰 크리에이트`` showing through the cinematic FG for about 0.5s.
The existing loader guard at ``7E:CA71`` runs after that prologue.

This candidate keeps the Galmuri11 Bitmap atlas and loader guard, then:

* clears BG ``3000`` with stock ``8000:7CC7`` / ``21F6`` immediately after the
  page 13-16 loop, before the cinematic resource load;
* stops the stage-0 idle path from re-applying the Korean atlas;
* moves cinematic first-tile ranges so consecutive pages do not overlap.
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
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_page16_exit_clear_candidate"
OUT_ROM = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.wsc"
)
OUT_SAVE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.sav"
)
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_page16_exit_clear_report.json"

EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
EXPECTED_PARENT_SHA256 = (
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

IDLE_SITE = 0xFECA6E
PRELOAD_SITE = 0xFECA71
PAGE17_REDIRECT_SITE = 0xFECB0E
PAGE16_EXIT_SITE = 0xFED1CA
HELPER_PHYS = 0xFEFD5D
HELPER_IP = 0xFD5D
HELPER_END = 0xFEFD83

EXPECTED_IDLE = bytes.fromhex("E99D00")       # jmp CB0E
NEW_IDLE = bytes.fromhex("E96001")            # jmp CBD1
EXPECTED_PRELOAD = bytes.fromhex("E9DC32")    # jmp FD50
EXPECTED_PAGE17 = bytes.fromhex("E90D32")     # jmp FD1E
EXPECTED_PAGE16_EXIT = bytes.fromhex("833E061B03")  # cmp word [1B06],3
EXPECTED_STOCK_BG_CLEAR = bytes.fromhex(
    "B8F62150B8180050B8200050B80030BB000033C933D29AC77C008083C406"
)
EXPECTED_PRELOAD_HELPER = bytes.fromhex("C7040000E40024FEE600E918CD")

EXPECTED_OLD = {
    17: {"ntiles": 28, "palette": 8, "first": 0x051},
    18: {"ntiles": 40, "palette": 8, "first": 0x051},
    19: {"ntiles": 43, "palette": 8, "first": 0x051},
    20: {"ntiles": 39, "palette": 10, "first": 0x091},
    21: {"ntiles": 26, "palette": 6, "first": 0x091},
}
NEW_FIRST_TILE = {
    17: 0x06C,
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


def near_jmp(src_ip: int, dst_ip: int) -> bytes:
    displacement = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE9" + struct.pack("<H", displacement)


def ranges_overlap(first_a: int, count_a: int, first_b: int, count_b: int) -> bool:
    return max(first_a, first_b) <= min(
        first_a + count_a - 1, first_b + count_b - 1
    )


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [index for index, (a, b) in enumerate(zip(before, after)) if a != b]


def build_exit_clear_helper() -> bytes:
    """Clear BG 3000, restore the overwritten cmp, and continue at D1CF."""
    body = bytearray(EXPECTED_STOCK_BG_CLEAR)
    body += bytes.fromhex("833E061B03")  # cmp word [1B06],3
    body += near_jmp(HELPER_IP + len(body), 0xD1CF)
    if len(body) != HELPER_END - HELPER_PHYS:
        raise BuildError(f"unexpected page16-exit helper size: {len(body)}")
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
        raise BuildError(f"Bitmap parent drifted: {len(parent)} {sha256(parent)}")
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drifted: {len(main_before)} {sha256(main_before)}")
    if len(parent_save) != SAVE_SIZE or sha256(parent_save) != EXPECTED_SAVE_SHA256:
        raise BuildError("paired SaveRAM identity drifted")
    if len(live_save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("Bitmap parent checksum invalid")

    if parent[IDLE_SITE : IDLE_SITE + 3] != EXPECTED_IDLE:
        raise BuildError("idle branch drifted")
    if parent[PRELOAD_SITE : PRELOAD_SITE + 3] != EXPECTED_PRELOAD:
        raise BuildError("preload redirect drifted")
    if parent[PAGE17_REDIRECT_SITE : PAGE17_REDIRECT_SITE + 3] != EXPECTED_PAGE17:
        raise BuildError("page17 overlay redirect drifted")
    if parent[PAGE16_EXIT_SITE : PAGE16_EXIT_SITE + 5] != EXPECTED_PAGE16_EXIT:
        raise BuildError("page16-exit cmp site drifted")
    if parent[0xFED16B : 0xFED189] != EXPECTED_STOCK_BG_CLEAR:
        raise BuildError("stock pre-loop BG clear drifted")
    if parent[0xFEFD50 : 0xFEFD50 + len(EXPECTED_PRELOAD_HELPER)] != EXPECTED_PRELOAD_HELPER:
        raise BuildError("preload BG-off helper drifted")
    if any(byte != 0xFF for byte in parent[HELPER_PHYS:HELPER_END]):
        raise BuildError("page16-exit helper cave is no longer free FF")

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

    page16 = RECORD.unpack_from(parent, ATLAS_BASE + 16 * RECORD.size)
    if (page16[2], page16[3], page16[9], page16[4]) != (3, 12, 0x001, 100):
        raise BuildError(f"page16 parent record drifted: {page16}")
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

    helper = build_exit_clear_helper()
    site_redirect = near_jmp(0xD1CA, HELPER_IP) + b"\x90\x90"
    if len(site_redirect) != 5:
        raise BuildError("page16-exit site redirect size drifted")

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

    candidate[IDLE_SITE : IDLE_SITE + 3] = NEW_IDLE
    candidate[PAGE16_EXIT_SITE : PAGE16_EXIT_SITE + 5] = site_redirect
    candidate[HELPER_PHYS : HELPER_END] = helper
    allowed.update(range(IDLE_SITE, IDLE_SITE + 3))
    allowed.update(range(PAGE16_EXIT_SITE, PAGE16_EXIT_SITE + 5))
    allowed.update(range(HELPER_PHYS, HELPER_END))

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    allowed.update({ROM_SIZE - 2, ROM_SIZE - 1})
    if not ws_checksum_valid(result):
        raise BuildError("candidate checksum update failed")
    diffs = changed_offsets(parent, result)
    outside = [offset for offset in diffs if offset not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    if result[PRELOAD_SITE : PRELOAD_SITE + 3] != EXPECTED_PRELOAD:
        raise BuildError("preload redirect was not preserved")
    if result[PAGE17_REDIRECT_SITE : PAGE17_REDIRECT_SITE + 3] != EXPECTED_PAGE17:
        raise BuildError("page17 overlay redirect was not preserved")
    if result[0xFEFD1E:0xFEFD2E] != parent[0xFEFD1E:0xFEFD2E]:
        raise BuildError("one-shot page17 overlay stub changed")
    if result[0xFEFD50 : 0xFEFD50 + len(EXPECTED_PRELOAD_HELPER)] != EXPECTED_PRELOAD_HELPER:
        raise BuildError("preload BG-off helper changed")
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared ending-credit overlay changed")
    if result[0xFED16B:0xFED1A7] != parent[0xFED16B:0xFED1A7]:
        raise BuildError("stock cinematic-entry map clears changed")
    if result[HELPER_PHYS:HELPER_END] != helper:
        raise BuildError("page16-exit helper was not planted")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        raise BuildError("Bitmap parent inputs changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)

    consecutive = []
    for previous, current in zip(range(16, 21), range(17, 22)):
        prev_first, prev_count = sequence[previous]
        cur_first, cur_count = sequence[current]
        consecutive.append(
            {
                "previous": previous,
                "current": current,
                "previous_range": (
                    f"{prev_first:03X}-{prev_first + prev_count - 1:03X}"
                ),
                "current_range": f"{cur_first:03X}-{cur_first + cur_count - 1:03X}",
                "overlap": False,
            }
        )

    report = {
        "schema_version": 1,
        "generated_by": (
            "tools/build_ending_credits_galmuri11_bitmap_page16_exit_clear_candidate.py"
        ),
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "purpose": (
            "clear the page16 Korean BG map at the cinematic-entry boundary so "
            "Tom Create cannot show through the page17 FG prologue"
        ),
        "main_tip": {**identity(MAIN, main_before), "unchanged": True},
        "parent": identity(PARENT_ROM, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "fixes": {
            "page16_exit_clear": {
                "site": "7E:D1CA -> 7E:FD5D",
                "timing": "after pages 13-16 loop, before cinematic resource load",
                "routine": "stock 8000:7CC7",
                "destination": "BG map 3000",
                "shape": "18 rows x 32 cells",
                "fill_entry": "21F6 stable bank-1 blank",
                "resume": "cmp word [1B06],3 then 7E:D1CF",
                "helper_bytes": len(helper),
            },
            "overlay_lifecycle": {
                "idle_site": "7E:CA6E",
                "old_target": "7E:CB0E recurring atlas upload",
                "new_target": "7E:CBD1 stock idle epilogue",
                "reload_path": "CB0B fall-through -> CB0E -> FD1E remains one-shot",
            },
            "cinematic_ranges": record_changes,
        },
        "consecutive_page_pairs": consecutive,
        "preserved": {
            "galmuri11_bitmap_graphics": True,
            "page17_bar_only_rows_13_17": True,
            "preload_bg_off_guard": True,
            "shared_overlay_byte_exact": True,
            "stock_pre_loop_bg_fg_clears": True,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
            "paired_saveram_byte_exact": True,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "declared_ranges": [
                "50011E/50012E/50015E cinematic first_tile",
                "FECA6E-FECA70 stage-0 idle branch",
                "FED1CA-FED1CE page16-exit redirect",
                "FEFD5D-FEFD82 page16-exit 7CC7 helper",
                "FFFFFE-FFFFFF checksum",
            ],
            "outside_declared_ranges": 0,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "Tom Create is gone on the first visible page16-to-17 transition frame",
                "page17 flower/stars and Galmuri11 director bar remain intact",
                "pages18-21 bars, art, and separators remain intact",
            ],
            "savestate_note": (
                "Start from the paired SaveRAM and replay the ending. Old savestates "
                "restore stale VRAM and cannot validate the page16-exit clear."
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
                "consecutive_page_pairs": report["consecutive_page_pairs"],
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
