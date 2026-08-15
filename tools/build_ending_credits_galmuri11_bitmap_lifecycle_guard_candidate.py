#!/usr/bin/env python3
"""Build the Galmuri11 Bitmap ending-credit lifecycle-guard test ROM.

This candidate replaces the late, incorrectly shaped contiguous BG clear with
two ownership-boundary fixes:

* the common page handler no longer re-applies the Korean atlas while its
  stage-0 timer is merely waiting; the atlas is applied once after the stock
  screen writer completes;
* page 21's BG band is cleared, with the stock 32-cell row stride, after its
  hold finishes and before the asynchronous END transition is started.

The earlier BG-off loader guard and all proven cinematic tile ranges remain.
Page 17 returns to a bar-only five-row map so the upper cinematic region keeps
the stock stable bank-1 blank map instead of referencing a bank-0 atlas tile.
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
    "ending_credits_lifecycle_atlas",
    ROOT / "tools/build_ending_credits_ko_page_atlas.py",
)

PARENT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_full_bg_clear_candidate"
PARENT_ROM = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_full_bg_clear_test.wsc"
)
PARENT_SAVE = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_full_bg_clear_test.sav"
)
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SOURCE_PREVIEWS = ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate/previews"

OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_lifecycle_guard_candidate"
OUT_ROM = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_lifecycle_guard_test.wsc"
)
OUT_SAVE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_lifecycle_guard_test.sav"
)
ATLAS_DUMP = OUT_DIR / "ending_credits_galmuri11_bitmap_lifecycle_guard_bank50.bin"
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_lifecycle_guard_report.json"

EXPECTED_PARENT_SHA256 = (
    "c59b749249b62562d227436a654c23ff9b5c223f7486e8a95301f8692b4dea1d"
)
EXPECTED_SAVE_SHA256 = (
    "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"
)
EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BASE = 0x500000
ATLAS_SIZE = 0x10000
HEADER = struct.Struct("<4sHHHHHH")
RECORD = struct.Struct("<BBBBHHHHHH")
PAGE_COUNT = 21

# Physical offsets in the expanded 16 MiB ROM.
IDLE_BRANCH_SITE = 0xFECA6E
PRELOAD_REDIRECT_SITE = 0xFECA71
PAGE17_REDIRECT_SITE = 0xFECB0E
END_BOUNDARY_SITE = 0xFED652
STUB_REGION = 0xFEFD50
STUB_REGION_END = 0xFEFD98

# Logical IPs inside runtime bank 7E.
PRELOAD_HELPER_IP = 0xFD50
END_HELPER_IP = 0xFD60

EXPECTED_IDLE_BRANCH = bytes.fromhex("E99D00")  # jmp CB0E (aliased idle/reload)
NEW_IDLE_BRANCH = bytes.fromhex("E96001")       # jmp CBD1 (stock idle semantics)
EXPECTED_PRELOAD_REDIRECT = bytes.fromhex("E9DC32")
EXPECTED_PAGE17_REDIRECT = bytes.fromhex("E90D32")
EXPECTED_END_BOUNDARY = bytes.fromhex("C706561B000F")
EXPECTED_OLD_PRELOAD_HELPER = bytes.fromhex(
    "C7040000E40024FEE6005733C08EC0BF0030B8F621B9F801FCF3AB5FE906CD"
)

EXPECTED_CINEMATIC_RANGES = {
    17: (28, 8, 0x06C, 0x087),
    18: (40, 8, 0x091, 0x0B8),
    19: (43, 8, 0x051, 0x07B),
    20: (39, 10, 0x091, 0x0B7),
    21: (26, 6, 0x0BD, 0x0D6),
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


def near_call(src_ip: int, dst_ip: int) -> bytes:
    displacement = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE8" + struct.pack("<H", displacement)


def near_jmp(src_ip: int, dst_ip: int) -> bytes:
    displacement = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE9" + struct.pack("<H", displacement)


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [index for index, (a, b) in enumerate(zip(before, after)) if a != b]


def build_preload_guard() -> bytes:
    """Reset the stage timer, hide BG for the stock writer, and resume CA75."""
    body = bytearray()
    body += bytes.fromhex("C7040000")       # mov word [si],0000
    body += bytes.fromhex("E40024FEE600")   # BG off, preserve FG/OBJ bits
    body += near_jmp(PRELOAD_HELPER_IP + len(body), 0xCA75)
    if len(body) != 13:
        raise BuildError(f"unexpected preload guard size: {len(body)}")
    return bytes(body)


def build_end_boundary_helper() -> bytes:
    """Clear BG rows 12-17 with stock stride, perform D652, and return."""
    body = bytearray()
    # The replaced D652 instruction did not alter registers or flags.  Preserve
    # that contract around the stock 8000:7CC7 rectangular fill routine.
    body += bytes.fromhex("9C5053515256571E06")  # pushf, AX/BX/CX/DX/SI/DI/DS/ES
    body += bytes.fromhex("B8F62150")            # fill entry 21F6
    body += bytes.fromhex("B8060050")            # height 6
    body += bytes.fromhex("B8200050")            # width 32 (hardware stride)
    body += bytes.fromhex("B80030")              # BG map base 3000
    body += bytes.fromhex("BB0000")              # x = 0
    body += bytes.fromhex("33C9")                # y high/aux = 0
    body += bytes.fromhex("BA0C00")              # y = 12
    body += bytes.fromhex("9AC77C0080")          # lcall 8000:7CC7
    body += bytes.fromhex("83C406")              # discard three arguments
    body += EXPECTED_END_BOUNDARY                  # mov word [1B56],0F00
    body += bytes.fromhex("071F5F5E5A595B589DC3")  # restore, near ret
    if len(body) != 56:
        raise BuildError(f"unexpected END helper size: {len(body)}")
    return bytes(body)


def rebuild_atlas(parent: bytes) -> tuple[bytes, list[dict]]:
    old = parent[ATLAS_BASE : ATLAS_BASE + ATLAS_SIZE]
    header = HEADER.unpack_from(old, 0)
    if header[:4] != (b"ECKO", 2, PAGE_COUNT, HEADER.size):
        raise BuildError(f"parent atlas header drifted: {header}")

    pages = []
    for page in range(1, PAGE_COUNT + 1):
        record = RECORD.unpack_from(old, page * RECORD.size)
        slot, cinematic, row0, nrows, ntiles, map_off, gfx_off, cols, pal, first = record
        if cols != 28:
            raise BuildError(f"page {page} column count drifted: {record}")
        map_size = nrows * cols * 2
        gfx_size = ntiles * 32
        tilemap = old[map_off : map_off + map_size]
        gfx = old[gfx_off : gfx_off + gfx_size]
        if len(tilemap) != map_size or len(gfx) != gfx_size:
            raise BuildError(f"page {page} atlas blob truncated")
        pages.append(
            {
                "page": page,
                "slot": slot,
                "cinematic": cinematic,
                "row0": row0,
                "nrows": nrows,
                "ntiles": ntiles,
                "cols": cols,
                "palette": pal,
                "first_tile": first,
                "tilemap": tilemap,
                "gfx": gfx,
            }
        )

    for page, (ntiles, palette, first, last) in EXPECTED_CINEMATIC_RANGES.items():
        entry = pages[page - 1]
        if (
            entry["ntiles"],
            entry["palette"],
            entry["first_tile"],
            entry["first_tile"] + entry["ntiles"] - 1,
        ) != (ntiles, palette, first, last):
            raise BuildError(f"page {page} cinematic range drifted: {entry}")

    page17 = pages[16]
    if (page17["row0"], page17["nrows"]) != (0, 18):
        raise BuildError(f"parent page17 is not the full-map workaround: {page17}")
    source = SOURCE_PREVIEWS / f"slot{page17['slot']:02d}_ko.png"
    if not source.is_file():
        raise BuildError(f"missing page17 source preview: {source}")
    image = Image.open(source).convert("RGB")
    if image.size != (224, 144):
        raise BuildError(f"page17 preview size drifted: {image.size}")
    tilemap, gfx, ntiles = ATLAS_MOD.page_atlas(image, 13, 5)
    if ntiles != page17["ntiles"] or gfx != page17["gfx"]:
        raise BuildError("page17 bar-only rebuild changed Galmuri11 graphics")
    page17.update({"row0": 13, "nrows": 5, "tilemap": tilemap, "gfx": gfx})

    cursor = HEADER.size + PAGE_COUNT * RECORD.size
    records = []
    blobs = []
    summaries = []
    for entry in pages:
        map_off = cursor
        gfx_off = map_off + len(entry["tilemap"])
        cursor = gfx_off + len(entry["gfx"])
        if cursor > ATLAS_SIZE:
            raise BuildError(f"bank 50 overflow at page {entry['page']}: {cursor}")
        records.append(
            RECORD.pack(
                entry["slot"],
                entry["cinematic"],
                entry["row0"],
                entry["nrows"],
                entry["ntiles"],
                map_off,
                gfx_off,
                entry["cols"],
                entry["palette"],
                entry["first_tile"],
            )
        )
        blobs.append(entry["tilemap"] + entry["gfx"])
        summaries.append(
            {
                "page": entry["page"],
                "slot": entry["slot"],
                "row0": entry["row0"],
                "nrows": entry["nrows"],
                "ntiles": entry["ntiles"],
                "palette": entry["palette"],
                "first_tile": f"{entry['first_tile']:03X}",
                "last_tile": f"{entry['first_tile'] + entry['ntiles'] - 1:03X}",
                "map_off": f"{map_off:04X}",
                "gfx_off": f"{gfx_off:04X}",
            }
        )

    payload = (
        HEADER.pack(b"ECKO", 2, PAGE_COUNT, HEADER.size, cursor, 0x50, 0)
        + b"".join(records)
        + b"".join(blobs)
    )
    if len(payload) != cursor:
        raise BuildError(f"atlas cursor mismatch: {len(payload)} != {cursor}")
    if len(payload) != 62_424:
        raise BuildError(f"unexpected lifecycle atlas size: {len(payload)}")

    # All pages except page 17 must retain byte-exact map and graphics payloads.
    for entry in pages:
        record = RECORD.unpack_from(payload, entry["page"] * RECORD.size)
        got_map = payload[
            record[5] : record[5] + record[3] * record[7] * 2
        ]
        got_gfx = payload[record[6] : record[6] + record[4] * 32]
        if got_map != entry["tilemap"] or got_gfx != entry["gfx"]:
            raise BuildError(f"page {entry['page']} atlas reconstruction failed")
    return payload, summaries


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
        raise BuildError(f"parent drifted: {len(parent)} {sha256(parent)}")
    if len(parent_save) != SAVE_SIZE or sha256(parent_save) != EXPECTED_SAVE_SHA256:
        raise BuildError("parent SaveRAM drifted")
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP drifted")
    if len(live_save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("parent checksum invalid")

    if parent[IDLE_BRANCH_SITE : IDLE_BRANCH_SITE + 3] != EXPECTED_IDLE_BRANCH:
        raise BuildError("stage-0 idle branch drifted")
    if parent[PRELOAD_REDIRECT_SITE : PRELOAD_REDIRECT_SITE + 3] != EXPECTED_PRELOAD_REDIRECT:
        raise BuildError("preload redirect drifted")
    if parent[PAGE17_REDIRECT_SITE : PAGE17_REDIRECT_SITE + 3] != EXPECTED_PAGE17_REDIRECT:
        raise BuildError("page17 reload redirect drifted")
    if parent[END_BOUNDARY_SITE : END_BOUNDARY_SITE + 6] != EXPECTED_END_BOUNDARY:
        raise BuildError("END boundary site drifted")
    if (
        parent[STUB_REGION : STUB_REGION + len(EXPECTED_OLD_PRELOAD_HELPER)]
        != EXPECTED_OLD_PRELOAD_HELPER
    ):
        raise BuildError("old contiguous-clear helper drifted")
    if any(
        byte != 0xFF
        for byte in parent[
            STUB_REGION + len(EXPECTED_OLD_PRELOAD_HELPER) : STUB_REGION_END
        ]
    ):
        raise BuildError("lifecycle helper cave is no longer free FF")

    atlas, atlas_pages = rebuild_atlas(parent)
    preload_guard = build_preload_guard()
    end_helper = build_end_boundary_helper()
    if END_HELPER_IP < PRELOAD_HELPER_IP + len(preload_guard):
        raise BuildError("helper layout overlaps")
    if END_HELPER_IP + len(end_helper) > (STUB_REGION_END & 0xFFFF):
        raise BuildError("END helper exceeds reserved cave")

    candidate = bytearray(parent)
    candidate[ATLAS_BASE : ATLAS_BASE + ATLAS_SIZE] = b"\xFF" * ATLAS_SIZE
    candidate[ATLAS_BASE : ATLAS_BASE + len(atlas)] = atlas
    candidate[IDLE_BRANCH_SITE : IDLE_BRANCH_SITE + 3] = NEW_IDLE_BRANCH
    candidate[END_BOUNDARY_SITE : END_BOUNDARY_SITE + 6] = (
        near_call(0xD652, END_HELPER_IP) + b"\x90\x90\x90"
    )
    candidate[STUB_REGION:STUB_REGION_END] = b"\xFF" * (
        STUB_REGION_END - STUB_REGION
    )
    candidate[
        0xFE0000 + PRELOAD_HELPER_IP : 0xFE0000 + PRELOAD_HELPER_IP + len(preload_guard)
    ] = preload_guard
    candidate[
        0xFE0000 + END_HELPER_IP : 0xFE0000 + END_HELPER_IP + len(end_helper)
    ] = end_helper

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if not ws_checksum_valid(result):
        raise BuildError("candidate checksum update failed")

    allowed_ranges = (
        (ATLAS_BASE, ATLAS_BASE + ATLAS_SIZE),
        (IDLE_BRANCH_SITE, IDLE_BRANCH_SITE + 3),
        (END_BOUNDARY_SITE, END_BOUNDARY_SITE + 6),
        (STUB_REGION, STUB_REGION_END),
    )
    diffs = changed_offsets(parent, result)
    outside = [
        offset
        for offset in diffs
        if not any(lo <= offset < hi for lo, hi in allowed_ranges)
        and offset not in {ROM_SIZE - 2, ROM_SIZE - 1}
    ]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    # Existing one-shot sites and the shared overlay implementation are immutable.
    for lo, hi, label in (
        (0xFECB0E, 0xFECB11, "page17 reload redirect"),
        (0xFED4F1, 0xFED4F6, "page20 overlay call"),
        (0xFED5C0, 0xFED5C4, "page21 overlay redirect"),
        (0xFFFF18, 0xFFFFCC, "shared atlas overlay"),
    ):
        if result[lo:hi] != parent[lo:hi]:
            raise BuildError(f"{label} changed unexpectedly")
    if result[PRELOAD_REDIRECT_SITE : PRELOAD_REDIRECT_SITE + 3] != EXPECTED_PRELOAD_REDIRECT:
        raise BuildError("preload redirect was not preserved")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        raise BuildError("parent inputs changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)
    atomic_bytes(ATLAS_DUMP, atlas)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_galmuri11_bitmap_lifecycle_guard_candidate.py",
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
            "overlay_lifecycle": {
                "idle_site": "7E:CA6E",
                "old_target": "7E:CB0E recurring atlas upload",
                "new_target": "7E:CBD1 stock idle epilogue",
                "reload_path": "CB0B fall-through -> CB0E -> FD1E remains one-shot",
            },
            "preload_guard": {
                "site": "7E:CA71 -> 7E:FD50",
                "action": "reset timer and hide BG during stock writer",
                "removed": "contiguous CX=01F8 rep-stosw pseudo-full-clear",
            },
            "page17_map_ownership": {
                "old": "rows 0-17 including bank-0 upper blank tiles",
                "new": "rows 13-17 Korean bar only",
                "graphics_byte_exact": True,
                "tile_range": "06C-087",
            },
            "end_boundary_cleanup": {
                "site": "7E:D652 -> near call 7E:FD60",
                "timing": "after page21 hold 01C2, before END transition task registration",
                "destination": "BG map rows 12-17",
                "shape": "6 rows x 32 cells via stock 8000:7CC7",
                "fill_entry": "21F6 stable bank-1 blank",
                "preserves_replaced_instruction": "mov word [1B56],0F00",
            },
        },
        "atlas": {
            "bank": "50",
            "bytes": len(atlas),
            "free_bytes": ATLAS_SIZE - len(atlas),
            "records": PAGE_COUNT,
            "dump": rel(ATLAS_DUMP),
            "pages": atlas_pages,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "declared_ranges": [
                "500000-50FFFF bank 50 atlas",
                "FECA6E-FECA70 stage-0 idle branch",
                "FED652-FED657 END boundary call",
                "FEFD50-FEFD97 lifecycle helpers",
                "FFFFFE-FFFFFF checksum",
            ],
            "outside_declared_ranges": 0,
        },
        "preserved": {
            "galmuri11_bitmap_graphics": True,
            "cinematic_tile_ranges": True,
            "page20_and_page21_overlay_sites": True,
            "shared_overlay_byte_exact": True,
            "main_tip_unchanged": MAIN.read_bytes() == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save_before,
            "paired_saveram_byte_exact": OUT_SAVE.read_bytes() == parent_save,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "Tom Create is gone on the first visible page16-to-17 transition frame",
                "pages17-21 art, separator, and Galmuri11 bars remain intact",
                "page21 disappears before END graphics become visible",
                "END has no bottom Korean tile fragments",
            ],
            "savestate_note": (
                "Start from the paired SaveRAM and replay the ending. Old savestates restore "
                "stale VRAM and cannot validate boundary hooks."
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
