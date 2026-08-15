#!/usr/bin/env python3
"""Audit the second cinematic ending-credit candidate against timeline states."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE_DIR = ROOT / "out/patch/ending_credits_cinematic_transition_guard_candidate"
CANDIDATE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_cinematic_transition_guard_test.wsc"
)
REPORT = CANDIDATE_DIR / "ending_credits_cinematic_transition_guard_audit.json"
STATE_DIR = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan")
STATE_HELPER = ROOT / "out/patch/_analyze_beetle_status_vram.py"

EXPECTED_MAIN = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
EXPECTED_CANDIDATE = "a8be5f53b4d3c45365ff7ec267f7c9c2590229e0b1229efd1a967bc1a62085fa"
ATLAS_BASE = 0x500000
RECORD = struct.Struct("<BBBBHHHHHH")

NEW_STATE_BASE = "monoeye_ko_expanded_ending_credits_cinematic_vram_reloc_test.state"
STATE_GROUPS = {
    17: (
        STATE_DIR / "monoeye_ko_expanded.state17",
        STATE_DIR / NEW_STATE_BASE,
        STATE_DIR / f"{NEW_STATE_BASE}1",
    ),
    18: (STATE_DIR / "monoeye_ko_expanded.state18",),
    19: (
        STATE_DIR / "monoeye_ko_expanded.state19",
        STATE_DIR / f"{NEW_STATE_BASE}2",
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def load_state_helper():
    spec = importlib.util.spec_from_file_location("ending_state_helper", STATE_HELPER)
    if spec is None or spec.loader is None:
        raise AuditError("cannot load RetroArch state helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
                    "palette": (raw >> 9) & 0xF,
                    "raw": raw,
                }
            )
    return out


def sprites(ram: bytes, gfx: dict[str, bytes]) -> list[dict]:
    base = gfx["SPRBase"][0] << 9
    start = gfx["SpriteStart"][0]
    count = gfx["SpriteCount"][0]
    out = []
    for index in range(count):
        raw, y, x = struct.unpack_from(
            "<HBB", ram, base + ((start + index) & 0x7F) * 4
        )
        out.append(
            {
                "tile": raw & 0x1FF,
                "bank": 1 if raw & 0x2000 else 0,
                "x": x,
                "y": y,
            }
        )
    return out


def collisions(rows: list[dict], first: int, ntiles: int) -> list[dict]:
    return [
        row
        for row in rows
        if row["bank"] == 0 and first <= row["tile"] < first + ntiles
    ]


def map_score(ram: bytes, atlas: bytes, page: int) -> int:
    rec = RECORD.unpack_from(atlas, page * 16)
    row0, nrows, map_off, cols, palette, first = (
        rec[2], rec[3], rec[5], rec[7], rec[8], rec[9]
    )
    source = atlas[map_off : map_off + nrows * cols * 2]
    score = 0
    for row in range(nrows):
        for col in range(cols):
            tile = struct.unpack_from("<H", source, (row * cols + col) * 2)[0] & 0x1FF
            want = tile + first | palette << 9
            got = struct.unpack_from(
                "<H", ram, 0x3000 + ((row0 + row) * 32 + col) * 2
            )[0]
            score += got == want
    return score


def palette_word(ram: bytes, palette: int, index: int) -> int:
    return struct.unpack_from("<H", ram, 0xFE00 + palette * 32 + index * 2)[0]


def jump_target(ip: int, code: bytes) -> int:
    if len(code) != 3 or code[0] != 0xE9:
        raise AuditError(f"not a near jump at {ip:04X}: {code.hex()}")
    disp = struct.unpack_from("<h", code, 1)[0]
    return (ip + 3 + disp) & 0xFFFF


def main() -> int:
    helper = load_state_helper()
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if sha256(parent) != EXPECTED_MAIN:
        raise AuditError(f"main TIP drifted: {sha256(parent)}")
    if sha256(candidate) != EXPECTED_CANDIDATE:
        raise AuditError(f"candidate drifted: {sha256(candidate)}")
    if not ws_checksum_valid(candidate):
        raise AuditError("candidate checksum is invalid")
    atlas = candidate[ATLAS_BASE : ATLAS_BASE + 0x10000]

    expected_records = {
        17: (8, 0x080, 0x097),
        18: (8, 0x080, 0x0A1),
        19: (8, 0x080, 0x0A4),
        20: (10, 0x091, 0x0B2),
        21: (6, 0x091, 0x0A6),
    }
    record_rows = []
    for page, (palette, first, last) in expected_records.items():
        rec = RECORD.unpack_from(atlas, page * 16)
        if (rec[0], rec[1], rec[2], rec[3], rec[8], rec[9], rec[9] + rec[4] - 1) != (
            page, 1, 13, 5, palette, first, last
        ):
            raise AuditError(f"page {page} record contract failed: {rec}")
        record_rows.append(
            {
                "rom_page": page,
                "palette": palette,
                "first_tile": f"{first:03X}",
                "last_tile": f"{last:03X}",
                "ntiles": rec[4],
            }
        )

    # Verify exact control-flow placement of the BG lifecycle helpers.
    preload_jump = candidate[0xFECA71 : 0xFECA74]
    page17_jump = candidate[0xFECB0E : 0xFECB11]
    page21_jump = candidate[0xFED5C0 : 0xFED5C3]
    if jump_target(0xCA71, preload_jump) != 0xFD50:
        raise AuditError("preload redirect target is wrong")
    if jump_target(0xCB0E, page17_jump) != 0xFD1E:
        raise AuditError("page17 redirect target is wrong")
    if jump_target(0xD5C0, page21_jump) != 0xFD40:
        raise AuditError("page21 redirect target is wrong")
    if candidate[0xFEFD50:0xFEFD5D] != bytes.fromhex(
        "C7040000E40024FEE600E918CD"
    ):
        raise AuditError("BG-disable preload helper differs")
    if candidate[0xFEFD1E:0xFEFD2E] != bytes.fromhex(
        "E4000C01E6008A059A30FF00F0E9A3CE"
    ):
        raise AuditError("BG-enable/page17 helper differs")
    if candidate[0xFEFD40:0xFEFD4C] != bytes.fromhex(
        "B0159A30FF00F032D2E993D8"
    ):
        raise AuditError("relocated page21 helper differs")
    if candidate[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise AuditError("shared overlay drifted")

    collision_rows = []
    total_collisions = 0
    for page, state_paths in STATE_GROUPS.items():
        rec = RECORD.unpack_from(atlas, page * 16)
        first, ntiles = rec[9], rec[4]
        for state_path in state_paths:
            if not state_path.is_file():
                raise AuditError(f"missing timeline state: {state_path}")
            ram, gfx = helper.parse_beetle_ram(state_path)
            bg = entries(ram, 0x3000, range(13), range(28))
            fg = entries(ram, 0x3800, range(32), range(32))
            obj = sprites(ram, gfx)
            bg_hits = collisions(bg, first, ntiles)
            fg_hits = collisions(fg, first, ntiles)
            obj_hits = collisions(obj, first, ntiles)
            count = len(bg_hits) + len(fg_hits) + len(obj_hits)
            total_collisions += count
            collision_rows.append(
                {
                    "rom_page": page,
                    "state": state_path.name,
                    "target_range": f"{first:03X}-{first + ntiles - 1:03X}",
                    "preserved_bg_collisions": len(bg_hits),
                    "full_fg_collisions": len(fg_hits),
                    "active_sprite_collisions": len(obj_hits),
                    "total": count,
                }
            )
    if total_collisions != 0:
        raise AuditError(f"candidate ranges retain {total_collisions} captured aliases")

    state0_path = STATE_DIR / NEW_STATE_BASE
    state1_path = STATE_DIR / f"{NEW_STATE_BASE}1"
    state2_path = STATE_DIR / f"{NEW_STATE_BASE}2"
    state0_ram, _ = helper.parse_beetle_ram(state0_path)
    state1_ram, _ = helper.parse_beetle_ram(state1_path)
    state2_ram, state2_gfx = helper.parse_beetle_ram(state2_path)

    # State0: the full page-16 Korean map survived while every page-16 tile had
    # already been replaced.  The new BG-off interval surrounds that loader.
    page16 = RECORD.unpack_from(atlas, 16 * 16)
    page16_map_score = map_score(state0_ram, atlas, 16)
    page16_expected = atlas[
        page16[6] : page16[6] + page16[4] * 32
    ]
    page16_actual = state0_ram[
        0x4000 + page16[9] * 32 : 0x4000 + (page16[9] + page16[4]) * 32
    ]
    page16_exact_tiles = sum(
        page16_expected[i * 32 : (i + 1) * 32]
        == page16_actual[i * 32 : (i + 1) * 32]
        for i in range(page16[4])
    )
    if page16_map_score != 504 or page16_exact_tiles != 0:
        raise AuditError("state0 page16 mismatch proof drifted")

    # State1: palette 8 is ready, while palette 10 is still the bad gradient.
    palette_proof = {
        "palette8_ink": f"{palette_word(state1_ram, 8, 1):03X}",
        "palette8_background": f"{palette_word(state1_ram, 8, 0xE):03X}",
        "palette10_ink": f"{palette_word(state1_ram, 10, 1):03X}",
        "palette10_background": f"{palette_word(state1_ram, 10, 0xE):03X}",
    }
    if palette_proof != {
        "palette8_ink": "FFF",
        "palette8_background": "000",
        "palette10_ink": "007",
        "palette10_background": "CCF",
    }:
        raise AuditError(f"state1 palette proof drifted: {palette_proof}")

    # State2: later visible FG cells use B5-BC.  Page19 now ends at A4.
    fg = entries(state2_ram, 0x3800, range(32), range(32))
    scroll_x = state2_gfx["FGXScroll"][0]
    visible_columns = {((scroll_x + x) // 8) & 31 for x in range(224)}
    dynamic_fg = sorted(
        {
            row["tile"]
            for row in fg
            if row["row"] == 0
            and row["col"] in visible_columns
            and row["bank"] == 0
            and 0x0B5 <= row["tile"] <= 0x0BC
        }
    )
    if dynamic_fg != list(range(0x0B5, 0x0BD)):
        raise AuditError(f"state2 dynamic FG proof drifted: {dynamic_fg}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ending_credits_cinematic_transition_guard_candidate.py",
        "ok": True,
        "status": "timeline_state_static_audit_passed_runtime_pending",
        "inputs": {
            "main_sha256": sha256(parent),
            "candidate_sha256": sha256(candidate),
            "timeline_states": [
                state0_path.name,
                state1_path.name,
                state2_path.name,
            ],
        },
        "record_contracts": record_rows,
        "code_contracts": {
            "preload_redirect": "7E:CA71 -> 7E:FD50 -> 7E:CA75",
            "bg_disable": "in 00; and FE; out 00 before CAD1",
            "bg_enable": "in 00; or 01; out 00 after CB06",
            "page17_redirect": "7E:CB0E -> 7E:FD1E -> 7E:CBD1",
            "page21_redirect": "7E:D5C0 -> 7E:FD40 -> 7E:D5DF",
            "shared_overlay_unchanged": True,
        },
        "timeline_proofs": {
            "state0": {
                "page16_korean_map_cells": page16_map_score,
                "page16_korean_gfx_exact_tiles": page16_exact_tiles,
                "candidate_action": "BG hidden across the mismatched loader interval",
            },
            "state1": {
                **palette_proof,
                "candidate_action": "pages 17-19 select palette 8",
            },
            "state2": {
                "visible_dynamic_fg_tiles": [f"{tile:03X}" for tile in dynamic_fg],
                "candidate_page19_range": "080-0A4",
                "candidate_action": "page19 no longer writes the dynamic FG range",
            },
        },
        "captured_alias_audit": {
            "states_checked": len(collision_rows),
            "layers": [
                "preserved BG rows 0-12",
                "full 32x32 FG map",
                "active sprites",
            ],
            "target_collisions": total_collisions,
            "states": collision_rows,
        },
        "conclusion": (
            "All three supplied timeline failure mechanisms are covered by explicit "
            "candidate contracts, and the page-specific tile ranges have zero aliases "
            "in the captured maps/sprites. Emulator replay remains required."
        ),
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["status"],
                "timeline_proofs": report["timeline_proofs"],
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
