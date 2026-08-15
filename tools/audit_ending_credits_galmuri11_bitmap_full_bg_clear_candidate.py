#!/usr/bin/env python3
"""Audit the Galmuri11 Bitmap full-BG-clear cinematic candidate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
PARENT_DIR = (
    ROOT / "out/patch/ending_credits_galmuri11_bitmap_transition_ranges_candidate"
)
PARENT = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_transition_ranges_test.wsc"
)
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_full_bg_clear_candidate"
CANDIDATE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_full_bg_clear_test.wsc"
)
BUILD_REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_full_bg_clear_report.json"
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_full_bg_clear_audit.json"
SOURCE_PREVIEWS = (
    ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate/previews"
)
SPEC = ROOT / "data/ending_credits_ko.json"
STATE_HELPER = ROOT / "out/patch/_analyze_beetle_status_vram.py"
ATLAS_MODULE = ROOT / "tools/build_ending_credits_ko_page_atlas.py"
BUILDER_MODULE = (
    ROOT / "tools/build_ending_credits_galmuri11_bitmap_full_bg_clear_candidate.py"
)
STATE_DIR = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan")
FONT_STATE = (
    STATE_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.state1"
)
RANGE_STATE = (
    STATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_transition_ranges_test.state2"
)
OLD_STATE_BASE = "monoeye_ko_expanded_ending_credits_cinematic_vram_reloc_test.state"

EXPECTED_PARENT_SHA256 = (
    "cf08fd546c15d5b549b0bca42656904c4bddb05d48e243a74c1a2881b48af9b5"
)
ROM_SIZE = 16_777_216
ATLAS_BASE = 0x500000
ATLAS_SIZE = 0x10000
RECORD = struct.Struct("<BBBBHHHHHH")
PRELOAD_HELPER = 0xFEFD50
PRELOAD_HELPER_END = 0xFEFD6F

EXPECTED_RANGES = {
    17: (28, 8, 0x06C, 0x087),
    18: (40, 8, 0x091, 0x0B8),
    19: (43, 8, 0x051, 0x07B),
    20: (39, 10, 0x091, 0x0B7),
    21: (26, 6, 0x0BD, 0x0D6),
}

STATE_GROUPS = {
    17: (
        FONT_STATE,
        RANGE_STATE,
        STATE_DIR / "monoeye_ko_expanded.state17",
        STATE_DIR / OLD_STATE_BASE,
        STATE_DIR / f"{OLD_STATE_BASE}1",
    ),
    18: (STATE_DIR / "monoeye_ko_expanded.state18",),
    19: (
        STATE_DIR / "monoeye_ko_expanded.state19",
        STATE_DIR / f"{OLD_STATE_BASE}2",
    ),
    20: (
        STATE_DIR / "monoeye_ko_expanded.state20",
        STATE_DIR / "monoeye_ko_expanded.state23",
    ),
    21: (
        STATE_DIR / "monoeye_ko_expanded.state21",
        STATE_DIR / "monoeye_ko_expanded.state24",
    ),
}


class AuditError(RuntimeError):
    pass


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AuditError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def entries(ram: bytes, base: int, rows: range, cols: range) -> list[dict]:
    out = []
    for row in rows:
        for col in cols:
            raw = struct.unpack_from("<H", ram, base + (row * 32 + col) * 2)[0]
            out.append(
                {
                    "row": row,
                    "col": col,
                    "tile": raw & 0x1FF,
                    "bank": 1 if raw & 0x2000 else 0,
                }
            )
    return out


def sprites(ram: bytes, gfx: dict[str, bytes]) -> list[dict]:
    base = gfx["SPRBase"][0] << 9
    start = gfx["SpriteStart"][0]
    count = gfx["SpriteCount"][0]
    out = []
    for index in range(count):
        raw = struct.unpack_from(
            "<H", ram, base + ((start + index) & 0x7F) * 4
        )[0]
        out.append(
            {
                "tile": raw & 0x1FF,
                "bank": 1 if raw & 0x2000 else 0,
            }
        )
    return out


def collisions(rows: list[dict], first: int, ntiles: int) -> list[dict]:
    return [
        row
        for row in rows
        if row["bank"] == 0 and first <= row["tile"] < first + ntiles
    ]


def ranges_overlap(first_a: int, count_a: int, first_b: int, count_b: int) -> bool:
    return max(first_a, first_b) <= min(
        first_a + count_a - 1, first_b + count_b - 1
    )


def map_score(ram: bytes, atlas: bytes, page: int) -> tuple[int, int]:
    record = RECORD.unpack_from(atlas, page * RECORD.size)
    source = atlas[
        record[5] : record[5] + record[3] * record[7] * 2
    ]
    score = 0
    for row in range(record[3]):
        for col in range(record[7]):
            tile = struct.unpack_from(
                "<H", source, (row * record[7] + col) * 2
            )[0]
            want = ((tile & 0x1FF) + record[9]) | (record[8] << 9)
            got = struct.unpack_from(
                "<H",
                ram,
                0x3000 + ((record[2] + row) * 32 + col) * 2,
            )[0]
            score += got == want
    return score, record[3] * record[7]


def gfx_score(ram: bytes, atlas: bytes, page: int) -> tuple[int, int]:
    record = RECORD.unpack_from(atlas, page * RECORD.size)
    expected = atlas[record[6] : record[6] + record[4] * 32]
    actual = ram[
        0x4000 + record[9] * 32 : 0x4000 + (record[9] + record[4]) * 32
    ]
    score = sum(
        expected[index * 32 : (index + 1) * 32]
        == actual[index * 32 : (index + 1) * 32]
        for index in range(record[4])
    )
    return score, record[4]


def relevant_state_paths() -> list[Path]:
    paths = {
        path
        for path in STATE_DIR.glob("*ending_credits*.state*")
        if path.is_file() and not path.name.endswith(".png")
    }
    paths.update(
        path
        for index in range(17, 25)
        if (path := STATE_DIR / f"monoeye_ko_expanded.state{index}").is_file()
    )
    return sorted(paths)


def main() -> int:
    required = (
        PARENT,
        CANDIDATE,
        BUILD_REPORT,
        SPEC,
        STATE_HELPER,
        ATLAS_MODULE,
        BUILDER_MODULE,
        FONT_STATE,
        RANGE_STATE,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise AuditError(f"missing inputs: {missing}")
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise AuditError(f"parent drifted: {len(parent)} {sha256(parent)}")
    if len(candidate) != ROM_SIZE or sha256(candidate) != build["candidate"]["sha256"]:
        raise AuditError(f"candidate drifted: {len(candidate)} {sha256(candidate)}")
    if not ws_checksum_valid(candidate):
        raise AuditError("candidate checksum invalid")

    helper_mod = load_module("full_bg_clear_state", STATE_HELPER)
    atlas_mod = load_module("full_bg_clear_atlas", ATLAS_MODULE)
    builder_mod = load_module("full_bg_clear_builder", BUILDER_MODULE)
    expected_helper = builder_mod.build_preload_clear_helper()
    if candidate[PRELOAD_HELPER:PRELOAD_HELPER_END] != expected_helper:
        raise AuditError("preload full-BG-clear helper differs")
    if len(expected_helper) != 31:
        raise AuditError("preload helper size drifted")

    atlas = candidate[ATLAS_BASE : ATLAS_BASE + ATLAS_SIZE]
    header = struct.unpack_from("<4sHHHHHH", atlas, 0)
    if header[:4] != (b"ECKO", 2, 21, 16):
        raise AuditError(f"atlas header contract failed: {header}")
    if header[4] != 63_152:
        raise AuditError(f"atlas byte count drifted: {header[4]}")
    pages = json.loads(SPEC.read_text(encoding="utf-8"))["pages"]
    cursor = 16 + len(pages) * RECORD.size
    exact_rows = []
    for index, page_spec in enumerate(pages):
        page = index + 1
        slot = page_spec["slot"]
        record = RECORD.unpack_from(atlas, page * RECORD.size)
        source_path = SOURCE_PREVIEWS / f"slot{slot:02d}_ko.png"
        if not source_path.is_file():
            raise AuditError(f"missing source preview: {source_path}")
        image = Image.open(source_path).convert("RGB")
        if page == 17:
            ImageDraw.Draw(image).rectangle((0, 0, 223, 103), fill=(0, 0, 0))
        tilemap, gfx, ntiles = atlas_mod.page_atlas(image, record[2], record[3])
        if record[0] != slot or record[4] != ntiles or record[5] != cursor:
            raise AuditError(f"page {page} record/cursor contract failed: {record}")
        if record[6] != record[5] + len(tilemap):
            raise AuditError(f"page {page} gfx cursor failed")
        if atlas[record[5] : record[5] + len(tilemap)] != tilemap:
            raise AuditError(f"page {page} tilemap differs from preview")
        if atlas[record[6] : record[6] + len(gfx)] != gfx:
            raise AuditError(f"page {page} graphics differs from preview")
        cursor = record[6] + len(gfx)
        exact_rows.append(
            {
                "rom_page": page,
                "capture_slot": slot,
                "row0": record[2],
                "nrows": record[3],
                "ntiles": record[4],
                "preview_exact": True,
            }
        )
    if cursor != header[4]:
        raise AuditError(f"atlas final cursor mismatch: {cursor} != {header[4]}")
    if any(byte != 0xFF for byte in atlas[header[4] :]):
        raise AuditError("unused bank 50 tail is not FF")

    page17 = RECORD.unpack_from(atlas, 17 * RECORD.size)
    if (
        page17[0],
        page17[1],
        page17[2],
        page17[3],
        page17[4],
        page17[8],
        page17[9],
    ) != (17, 1, 0, 18, 28, 8, 0x06C):
        raise AuditError(f"page17 full-map contract failed: {page17}")
    page17_map = atlas[page17[5] : page17[5] + 18 * 28 * 2]
    upper_words = struct.unpack_from("<" + "H" * (13 * 28), page17_map)
    if any((word & 0x1FF) != 0 for word in upper_words):
        raise AuditError("page17 upper rows do not all reference blank relative tile 0")
    blank_gfx = atlas[page17[6] : page17[6] + 32]
    if blank_gfx != b"\xEE" * 32:
        raise AuditError(f"page17 blank atlas tile drifted: {blank_gfx.hex()}")

    range_rows = []
    records = {}
    for page, expected in EXPECTED_RANGES.items():
        record = RECORD.unpack_from(atlas, page * RECORD.size)
        ntiles, palette, first, last = expected
        if (
            record[4],
            record[8],
            record[9],
            record[9] + record[4] - 1,
        ) != expected:
            raise AuditError(f"page {page} range contract failed: {record}")
        if first <= 0x090 <= last:
            raise AuditError(f"page {page} reaches separator tile 090")
        records[page] = record
        range_rows.append(
            {
                "rom_page": page,
                "ntiles": ntiles,
                "palette": palette,
                "first_tile": f"{first:03X}",
                "last_tile": f"{last:03X}",
            }
        )

    page16 = RECORD.unpack_from(atlas, 16 * RECORD.size)
    sequence = {
        16: (page16[9], page16[4]),
        **{page: (records[page][9], records[page][4]) for page in range(17, 22)},
    }
    pair_rows = []
    for previous, current in zip(range(16, 21), range(17, 22)):
        previous_first, previous_count = sequence[previous]
        current_first, current_count = sequence[current]
        overlap = ranges_overlap(
            previous_first, previous_count, current_first, current_count
        )
        if overlap:
            raise AuditError(f"pages {previous}->{current} ranges overlap")
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

    collision_rows = []
    total_collisions = 0
    for page, paths in STATE_GROUPS.items():
        record = records[page]
        for state_path in paths:
            if not state_path.is_file():
                raise AuditError(f"missing supplied state: {state_path}")
            ram, gfx_state = helper_mod.parse_beetle_ram(state_path)
            # Page17 includes both reported transition states, so audit all 18
            # BG rows there.  Later pages replace their own bottom five rows.
            bg_rows = range(18) if page == 17 else range(13)
            bg = entries(ram, 0x3000, bg_rows, range(28))
            fg = entries(ram, 0x3800, range(32), range(32))
            obj = sprites(ram, gfx_state)
            bg_hits = collisions(bg, record[9], record[4])
            fg_hits = collisions(fg, record[9], record[4])
            obj_hits = collisions(obj, record[9], record[4])
            total = len(bg_hits) + len(fg_hits) + len(obj_hits)
            total_collisions += total
            collision_rows.append(
                {
                    "rom_page": page,
                    "state": state_path.name,
                    "target_range": f"{record[9]:03X}-{record[9] + record[4] - 1:03X}",
                    "bg_collisions": len(bg_hits),
                    "fg_collisions": len(fg_hits),
                    "active_sprite_collisions": len(obj_hits),
                    "total": total,
                }
            )
    if total_collisions:
        raise AuditError(f"captured target collisions: {total_collisions}")

    font_ram, font_gfx = helper_mod.parse_beetle_ram(FONT_STATE)
    range_ram, range_gfx = helper_mod.parse_beetle_ram(RANGE_STATE)
    font_map_score, font_map_total = map_score(font_ram, atlas, 16)
    range_map_score, range_map_total = map_score(range_ram, atlas, 16)
    range_gfx_score, range_gfx_total = gfx_score(range_ram, atlas, 16)
    if (font_map_score, font_map_total) != (336, 336):
        raise AuditError(f"font-state page16 map proof drifted: {font_map_score}/{font_map_total}")
    if (range_map_score, range_map_total, range_gfx_score, range_gfx_total) != (
        252,
        336,
        3,
        100,
    ):
        raise AuditError(
            "range-state stale page16 proof drifted: "
            f"map {range_map_score}/{range_map_total} gfx {range_gfx_score}/{range_gfx_total}"
        )
    range_full_bg = entries(range_ram, 0x3000, range(18), range(28))
    range_full_fg = entries(range_ram, 0x3800, range(32), range(32))
    range_obj = sprites(range_ram, range_gfx)
    old_rows = range_full_bg + range_full_fg + range_obj
    old_df_hits = collisions(old_rows, 0x0DF, 28)
    new_6c_hits = collisions(old_rows, 0x06C, 28)
    if len(old_df_hits) != 21 or new_6c_hits:
        raise AuditError(
            f"reported FG collision proof drifted: old={len(old_df_hits)} new={len(new_6c_hits)}"
        )

    blank_state_paths = relevant_state_paths()
    if not blank_state_paths:
        raise AuditError("no ending-credit states available for blank-tile audit")
    blank_hashes = set()
    for state_path in blank_state_paths:
        ram, _ = helper_mod.parse_beetle_ram(state_path)
        tile = ram[0x8000 + 0x1F6 * 32 : 0x8000 + (0x1F6 + 1) * 32]
        if tile != b"\x00" * 32:
            raise AuditError(f"bank-1 tile 1F6 is not blank in {state_path.name}")
        blank_hashes.add(sha256(tile))
    if len(blank_hashes) != 1:
        raise AuditError("blank tile 1F6 hash drifted across states")

    diffs = [index for index, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    outside = [
        offset
        for offset in diffs
        if not (ATLAS_BASE <= offset < ATLAS_BASE + ATLAS_SIZE)
        and not (PRELOAD_HELPER <= offset < PRELOAD_HELPER_END)
        and offset not in {ROM_SIZE - 2, ROM_SIZE - 1}
    ]
    if outside:
        raise AuditError(f"candidate diff leaked to {outside[0]:08X}")
    if candidate[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise AuditError("shared overlay changed")
    if candidate[0xFEFD1E:0xFEFD50] != parent[0xFEFD1E:0xFEFD50]:
        raise AuditError("cinematic page stubs changed")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ending_credits_galmuri11_bitmap_full_bg_clear_candidate.py",
        "ok": True,
        "status": "supplied_state_and_static_audit_passed_runtime_pending",
        "candidate_sha256": sha256(candidate),
        "early_clear": {
            "helper": "7E:FD50-FD6E",
            "helper_bytes": len(expected_helper),
            "fill_entry": "21F6",
            "words": 28 * 18,
            "blank_tile_states_checked": len(blank_state_paths),
            "bank1_tile_1F6_all_zero": True,
            "blank_tile_sha256": next(iter(blank_hashes)),
        },
        "page17_full_map": {
            "row0": page17[2],
            "nrows": page17[3],
            "upper_blank_cells": len(upper_words),
            "upper_cells_all_relative_tile_zero": True,
            "blank_atlas_tile_is_E_fill": True,
            "first_tile": "06C",
            "last_tile": "087",
        },
        "reported_states": {
            "tom_create_state": {
                "state": FONT_STATE.name,
                "display_control": f"{font_gfx['DispControl'][0]:02X}",
                "page16_map_exact": f"{font_map_score}/{font_map_total}",
                "candidate_action": "early 28x18 map clear while BG is disabled",
            },
            "upper_residue_state": {
                "state": RANGE_STATE.name,
                "page16_map_exact": f"{range_map_score}/{range_map_total}",
                "page16_gfx_exact": f"{range_gfx_score}/{range_gfx_total}",
                "old_DF_FA_layer_collisions": len(old_df_hits),
                "new_6C_87_layer_collisions": len(new_6c_hits),
                "candidate_action": "full page17 blank-upper map plus safe graphics range",
            },
        },
        "atlas": {
            "bank": "50",
            "bytes": header[4],
            "free_bytes": ATLAS_SIZE - header[4],
            "all_records_match_previews": True,
            "pages": exact_rows,
        },
        "ranges": range_rows,
        "consecutive_pairs": pair_rows,
        "captured_alias_audit": {
            "states_checked": len(collision_rows),
            "target_collisions": total_collisions,
            "states": collision_rows,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "outside_declared_ranges": 0,
        },
        "preserved": {
            "shared_overlay_byte_exact": True,
            "cinematic_page_stubs_byte_exact": True,
            "paired_saveram_byte_exact": build["preserved"]["paired_saveram_byte_exact"],
        },
        "conclusion": (
            "The early helper clears every stale BG cell with a blank tile proven "
            "zero across all available credit states.  Page17 then reapplies a "
            "complete blank-upper/Korean-bar map, and its 06C-087 graphics range "
            "has zero BG/FG/OBJ aliases in all supplied page17 timeline states. "
            "Runtime replay remains required."
        ),
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["status"],
                "candidate_sha256": report["candidate_sha256"],
                "early_clear": report["early_clear"],
                "page17_full_map": report["page17_full_map"],
                "reported_states": report["reported_states"],
                "captured_target_collisions": total_collisions,
                "report": str(REPORT),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
