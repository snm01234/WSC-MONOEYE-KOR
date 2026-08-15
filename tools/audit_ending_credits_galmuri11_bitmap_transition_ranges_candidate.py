#!/usr/bin/env python3
"""Audit the non-overlapping Galmuri11 Bitmap cinematic range candidate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate"
PARENT = PARENT_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.wsc"
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_transition_ranges_candidate"
CANDIDATE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_transition_ranges_test.wsc"
)
BUILD_REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_transition_ranges_report.json"
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_transition_ranges_audit.json"
STATE_HELPER = ROOT / "out/patch/_analyze_beetle_status_vram.py"
STATE_DIR = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan")
NEW_BITMAP_STATE = (
    STATE_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.state1"
)
OLD_STATE_BASE = "monoeye_ko_expanded_ending_credits_cinematic_vram_reloc_test.state"

EXPECTED_PARENT_SHA256 = (
    "5f92d13d7ec071f133971dfeab3135151d98975f875363fa9a91d32fe70f713e"
)
ATLAS_BASE = 0x500000
RECORD = struct.Struct("<BBBBHHHHHH")
ROM_SIZE = 16_777_216

STATE_GROUPS = {
    17: (
        NEW_BITMAP_STATE,
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

EXPECTED_RANGES = {
    17: (8, 0x0DF, 0x0FA),
    18: (8, 0x091, 0x0B8),
    19: (8, 0x051, 0x07B),
    20: (10, 0x091, 0x0B7),
    21: (6, 0x0BD, 0x0D6),
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
    row0, nrows, map_off, cols, palette, first = (
        record[2],
        record[3],
        record[5],
        record[7],
        record[8],
        record[9],
    )
    source = atlas[map_off : map_off + nrows * cols * 2]
    score = 0
    for row in range(nrows):
        for col in range(cols):
            tile = struct.unpack_from("<H", source, (row * cols + col) * 2)[0]
            want = ((tile & 0x1FF) + first) | (palette << 9)
            got = struct.unpack_from(
                "<H", ram, 0x3000 + ((row0 + row) * 32 + col) * 2
            )[0]
            score += got == want
    return score, nrows * cols


def main() -> int:
    required = (PARENT, CANDIDATE, BUILD_REPORT, STATE_HELPER, NEW_BITMAP_STATE)
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

    helper = load_module("bitmap_transition_state", STATE_HELPER)
    atlas = candidate[ATLAS_BASE : ATLAS_BASE + 0x10000]
    records = {}
    range_rows = []
    for page, expected in EXPECTED_RANGES.items():
        record = RECORD.unpack_from(atlas, page * RECORD.size)
        palette, first, last = expected
        if (
            record[0],
            record[1],
            record[2],
            record[3],
            record[8],
            record[9],
            record[9] + record[4] - 1,
        ) != (page, 1, 13, 5, palette, first, last):
            raise AuditError(f"page {page} range contract failed: {record}")
        if first <= 0x090 <= last:
            raise AuditError(f"page {page} reaches separator tile 090")
        records[page] = record
        range_rows.append(
            {
                "rom_page": page,
                "ntiles": record[4],
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
        first, ntiles = record[9], record[4]
        for state_path in paths:
            if not state_path.is_file():
                raise AuditError(f"missing supplied state: {state_path}")
            ram, gfx = helper.parse_beetle_ram(state_path)
            # Rows 13-17 are replaced by the new bar.  The newly reported
            # page16 transition state is additionally audited over the full BG
            # map below, before that replacement occurs.
            bg = entries(ram, 0x3000, range(13), range(28))
            fg = entries(ram, 0x3800, range(32), range(32))
            obj = sprites(ram, gfx)
            bg_hits = collisions(bg, first, ntiles)
            fg_hits = collisions(fg, first, ntiles)
            obj_hits = collisions(obj, first, ntiles)
            total = len(bg_hits) + len(fg_hits) + len(obj_hits)
            total_collisions += total
            collision_rows.append(
                {
                    "rom_page": page,
                    "state": state_path.name,
                    "target_range": f"{first:03X}-{first + ntiles - 1:03X}",
                    "preserved_bg_collisions": len(bg_hits),
                    "full_fg_collisions": len(fg_hits),
                    "active_sprite_collisions": len(obj_hits),
                    "total": total,
                }
            )
    if total_collisions:
        raise AuditError(f"captured target collisions: {total_collisions}")

    transition_ram, transition_gfx = helper.parse_beetle_ram(NEW_BITMAP_STATE)
    page16_score, page16_cells = map_score(transition_ram, atlas, 16)
    if page16_score != page16_cells:
        raise AuditError(
            f"reported state no longer has the exact page16 map: {page16_score}/{page16_cells}"
        )
    full_bg = entries(transition_ram, 0x3000, range(18), range(28))
    old_page17_hits = collisions(full_bg, 0x051, 28)
    new_page17_hits = collisions(full_bg, records[17][9], records[17][4])
    if len(old_page17_hits) != 23 or new_page17_hits:
        raise AuditError(
            f"page16 transition overlap proof drifted: old={len(old_page17_hits)} "
            f"new={len(new_page17_hits)}"
        )

    # Only the three declared record bytes and checksum may differ.
    diffs = [index for index, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    expected_data_offsets = {
        ATLAS_BASE + 17 * RECORD.size + 14,
        ATLAS_BASE + 18 * RECORD.size + 14,
        ATLAS_BASE + 21 * RECORD.size + 14,
    }
    if any(
        offset not in expected_data_offsets
        and offset not in {ROM_SIZE - 2, ROM_SIZE - 1}
        for offset in diffs
    ):
        raise AuditError("candidate diff escaped declared first_tile/checksum bytes")
    if candidate[0xFE0000:0xFF0000] != parent[0xFE0000:0xFF0000]:
        raise AuditError("transition-guard code changed")
    if candidate[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise AuditError("shared overlay changed")

    report = {
        "schema_version": 1,
        "generated_by": (
            "tools/audit_ending_credits_galmuri11_bitmap_transition_ranges_candidate.py"
        ),
        "ok": True,
        "status": "supplied_state_and_static_audit_passed_runtime_pending",
        "candidate_sha256": sha256(candidate),
        "reported_state_proof": {
            "state": NEW_BITMAP_STATE.name,
            "display_control": f"{transition_gfx['DispControl'][0]:02X}",
            "page16_map_exact_cells": page16_score,
            "page16_map_total_cells": page16_cells,
            "old_page17_051_06C_live_bg_cells": len(old_page17_hits),
            "new_page17_0DF_0FA_live_bg_cells": len(new_page17_hits),
        },
        "ranges": range_rows,
        "consecutive_pairs": pair_rows,
        "captured_alias_audit": {
            "states_checked": len(collision_rows),
            "layers": [
                "preserved BG rows 0-12",
                "full 32x32 FG map",
                "active sprites",
            ],
            "target_collisions": total_collisions,
            "separator_tile_090_preserved": True,
            "states": collision_rows,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "only_first_tile_low_bytes_and_checksum": True,
        },
        "preserved": {
            "font_atlas_tilemaps_and_graphics_byte_exact": True,
            "transition_guard_code_byte_exact": True,
            "shared_overlay_byte_exact": True,
            "paired_saveram_byte_exact": build["preserved"]["paired_saveram_byte_exact"],
        },
        "conclusion": (
            "The reported page16 map has 23 cells in the old page17 upload range "
            "and zero cells in the new range.  All five target ranges are disjoint "
            "from their immediately preceding Korean page and have zero aliases in "
            "the supplied BG/FG/sprite inventories.  Runtime replay remains required."
        ),
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["status"],
                "candidate_sha256": report["candidate_sha256"],
                "reported_state_proof": report["reported_state_proof"],
                "consecutive_pair_overlaps": 0,
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
