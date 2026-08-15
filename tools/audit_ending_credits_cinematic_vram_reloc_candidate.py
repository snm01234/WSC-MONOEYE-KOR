#!/usr/bin/env python3
"""Audit cinematic ending-credit VRAM relocation against captured states."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE_DIR = ROOT / "out/patch/ending_credits_cinematic_vram_reloc_candidate"
CANDIDATE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_cinematic_vram_reloc_test.wsc"
)
REPORT = CANDIDATE_DIR / "ending_credits_cinematic_vram_reloc_state_audit.json"
STATE_DIR = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan")
STATE_HELPER = ROOT / "out/patch/_analyze_beetle_status_vram.py"

EXPECTED_MAIN = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
EXPECTED_CANDIDATE = "83efce31c847c7c9bf79ab2b5f718fcea4eb81ee26b6ff4d9ed5f19270e1b474"
ATLAS_BASE = 0x500000
RECORD = struct.Struct("<BBBBHHHHHH")
OLD_FIRST = 0x080
NEW_FIRST = 0x098
PAGE_STATES = {
    17: (17, 22),
    18: (18,),
    19: (19,),
    20: (20, 23),
    21: (21, 24),
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
        raise AuditError(f"cannot load state helper {STATE_HELPER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def map_entries(ram: bytes, base: int, rows: range, cols: range) -> list[dict]:
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
                }
            )
    return out


def sprite_entries(ram: bytes, gfx: dict[str, bytes]) -> list[dict]:
    base = gfx["SPRBase"][0] << 9
    start = gfx["SpriteStart"][0]
    count = gfx["SpriteCount"][0]
    out = []
    for index in range(count):
        off = base + ((start + index) & 0x7F) * 4
        raw, y, x = struct.unpack_from("<HBB", ram, off)
        out.append(
            {
                "tile": raw & 0x1FF,
                "bank": 1 if raw & 0x2000 else 0,
                "x": x,
                "y": y,
            }
        )
    return out


def collisions(entries: list[dict], first: int, ntiles: int) -> list[dict]:
    last = first + ntiles
    return [
        row
        for row in entries
        if row["bank"] == 0 and first <= row["tile"] < last
    ]


def ws_checksum_valid(rom: bytes) -> bool:
    stored = struct.unpack_from("<H", rom, len(rom) - 2)[0]
    return stored == (sum(rom[:-2]) & 0xFFFF)


def main() -> int:
    helper = load_state_helper()
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if sha256(parent) != EXPECTED_MAIN:
        raise AuditError(f"main TIP drifted: {sha256(parent)}")
    if sha256(candidate) != EXPECTED_CANDIDATE:
        raise AuditError(f"candidate drifted: {sha256(candidate)}")
    if not ws_checksum_valid(candidate):
        raise AuditError("candidate WonderSwan checksum is invalid")

    state_rows = []
    total_target_collisions = 0
    source_page20_21_bg_row12_collisions = 0
    for page, state_slots in PAGE_STATES.items():
        parent_record = RECORD.unpack_from(parent, ATLAS_BASE + page * 16)
        candidate_record = RECORD.unpack_from(candidate, ATLAS_BASE + page * 16)
        if parent_record[:-1] != candidate_record[:-1]:
            raise AuditError(f"page {page} changed outside first_tile")
        if parent_record[-1] != OLD_FIRST or candidate_record[-1] != NEW_FIRST:
            raise AuditError(f"page {page} first_tile contract failed")
        ntiles = candidate_record[4]

        for state_slot in state_slots:
            state_path = STATE_DIR / f"monoeye_ko_expanded.state{state_slot}"
            if not state_path.is_file():
                raise AuditError(f"missing captured state {state_path}")
            ram, gfx = helper.parse_beetle_ram(state_path)
            bg = map_entries(ram, 0x3000, range(13), range(28))
            fg = map_entries(ram, 0x3800, range(32), range(32))
            sprites = sprite_entries(ram, gfx)

            target_bg = collisions(bg, NEW_FIRST, ntiles)
            target_fg = collisions(fg, NEW_FIRST, ntiles)
            target_sprites = collisions(sprites, NEW_FIRST, ntiles)
            source_bg = collisions(bg, OLD_FIRST, ntiles)
            source_fg = collisions(fg, OLD_FIRST, ntiles)
            source_sprites = collisions(sprites, OLD_FIRST, ntiles)
            target_count = len(target_bg) + len(target_fg) + len(target_sprites)
            total_target_collisions += target_count

            source_row12 = [row for row in source_bg if row["row"] == 12]
            if page in (20, 21) and state_slot in (20, 21):
                source_page20_21_bg_row12_collisions += len(source_row12)

            state_rows.append(
                {
                    "rom_page": page,
                    "state_slot": state_slot,
                    "ntiles": ntiles,
                    "source_range": f"{OLD_FIRST:03X}-{OLD_FIRST + ntiles - 1:03X}",
                    "target_range": f"{NEW_FIRST:03X}-{NEW_FIRST + ntiles - 1:03X}",
                    "source_collisions": {
                        "preserved_bg": len(source_bg),
                        "preserved_bg_row12": len(source_row12),
                        "fg_32x32": len(source_fg),
                        "active_sprites": len(source_sprites),
                    },
                    "target_collisions": {
                        "preserved_bg": len(target_bg),
                        "fg_32x32": len(target_fg),
                        "active_sprites": len(target_sprites),
                        "total": target_count,
                    },
                }
            )

    alias_proofs = []
    for page, original_slot, corrupted_slot in ((20, 20, 23), (21, 21, 24)):
        original_ram, _ = helper.parse_beetle_ram(
            STATE_DIR / f"monoeye_ko_expanded.state{original_slot}"
        )
        corrupted_ram, _ = helper.parse_beetle_ram(
            STATE_DIR / f"monoeye_ko_expanded.state{corrupted_slot}"
        )
        record = RECORD.unpack_from(parent, ATLAS_BASE + page * 16)
        ntiles, gfx_off = record[4], record[6]
        tile_index = 0x090 - OLD_FIRST
        if not 0 <= tile_index < ntiles:
            raise AuditError(f"page {page} does not cover source tile 0x090")
        expected_corrupt = parent[
            ATLAS_BASE + gfx_off + tile_index * 32 :
            ATLAS_BASE + gfx_off + (tile_index + 1) * 32
        ]
        original_tile = original_ram[0x4000 + 0x090 * 32 : 0x4000 + 0x091 * 32]
        corrupted_tile = corrupted_ram[0x4000 + 0x090 * 32 : 0x4000 + 0x091 * 32]
        proof = {
            "rom_page": page,
            "original_state": original_slot,
            "corrupted_state": corrupted_slot,
            "separator_tile": "090",
            "original_tile_is_solid_E": original_tile == bytes([0xEE]) * 32,
            "corrupted_tile_matches_atlas_glyph": corrupted_tile == expected_corrupt,
            "candidate_target_excludes_tile_090": not (
                NEW_FIRST <= 0x090 < NEW_FIRST + ntiles
            ),
        }
        if not all(proof[key] for key in proof if key not in {
            "rom_page", "original_state", "corrupted_state", "separator_tile"
        }):
            raise AuditError(f"page {page} alias proof failed: {proof}")
        alias_proofs.append(proof)

    if total_target_collisions != 0:
        raise AuditError(f"relocated range still has {total_target_collisions} collisions")
    if source_page20_21_bg_row12_collisions != 56:
        raise AuditError(
            "expected 56 captured source collisions in page 20-21 row 12, got "
            f"{source_page20_21_bg_row12_collisions}"
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ending_credits_cinematic_vram_reloc_candidate.py",
        "ok": True,
        "status": "captured_state_collision_audit_passed_runtime_pending",
        "inputs": {
            "main_sha256": sha256(parent),
            "candidate_sha256": sha256(candidate),
            "state_directory": str(STATE_DIR),
        },
        "summary": {
            "captured_states_checked": sum(len(v) for v in PAGE_STATES.values()),
            "source_page20_21_bg_row12_collisions": source_page20_21_bg_row12_collisions,
            "target_collisions_all_checked_layers": total_target_collisions,
            "layers": [
                "preserved BG rows 0-12",
                "full 32x32 scrolling FG map",
                "active sprites",
            ],
        },
        "alias_proofs": alias_proofs,
        "states": state_rows,
        "conclusion": (
            "The candidate excludes tile 0x090 and has zero target-range aliases in "
            "all captured BG, FG, and active-sprite inventories. Runtime transition "
            "timing still requires emulator validation."
        ),
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": report["ok"],
        "status": report["status"],
        "summary": report["summary"],
        "report": str(REPORT),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
