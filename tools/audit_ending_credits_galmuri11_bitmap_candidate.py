#!/usr/bin/env python3
"""Static audit for the Galmuri11 Bitmap Regular ending-credit candidate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PARENT_DIR = ROOT / "out/patch/ending_credits_cinematic_transition_guard_candidate"
PARENT = (
    PARENT_DIR / "monoeye_ko_expanded_ending_credits_cinematic_transition_guard_test.wsc"
)
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate"
CANDIDATE = OUT_DIR / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.wsc"
BUILD_REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_report.json"
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_audit.json"
PREVIEWS = OUT_DIR / "previews"
SPEC = ROOT / "data/ending_credits_ko.json"
NATIVE = ROOT / "out/patch/ending_credits"
STATE_DIR = Path(r"C:\RetroArch-Win64\states\Beetle WonderSwan")
STATE_HELPER = ROOT / "out/patch/_analyze_beetle_status_vram.py"
ATLAS_MODULE = ROOT / "tools/build_ending_credits_ko_page_atlas.py"

ATLAS_BASE = 0x500000
ATLAS_SIZE = 0x10000
RECORD = struct.Struct("<BBBBHHHHHH")
EXPECTED_PARENT_SHA256 = (
    "a8be5f53b4d3c45365ff7ec267f7c9c2590229e0b1229efd1a967bc1a62085fa"
)

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


def nonblack_tile_rows(img: Image.Image) -> list[int]:
    rgb = img.convert("RGB")
    pixels = rgb.load()
    return [
        row
        for row in range(18)
        if any(
            pixels[x, y] != (0, 0, 0)
            for y in range(row * 8, row * 8 + 8)
            for x in range(224)
        )
    ]


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


def collision_count(rows: list[dict], first: int, ntiles: int) -> int:
    return sum(
        row["bank"] == 0 and first <= row["tile"] < first + ntiles
        for row in rows
    )


def main() -> int:
    required = (PARENT, CANDIDATE, BUILD_REPORT, SPEC, STATE_HELPER, ATLAS_MODULE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise AuditError(f"missing inputs: {missing}")
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    if sha256(parent) != EXPECTED_PARENT_SHA256:
        raise AuditError(f"parent drifted: {sha256(parent)}")
    if sha256(candidate) != build["candidate"]["sha256"]:
        raise AuditError(f"candidate drifted: {sha256(candidate)}")
    if not ws_checksum_valid(candidate):
        raise AuditError("candidate checksum invalid")
    if candidate[:ATLAS_BASE] != parent[:ATLAS_BASE]:
        raise AuditError("bytes before bank 50 changed")
    if candidate[ATLAS_BASE + ATLAS_SIZE : -2] != parent[ATLAS_BASE + ATLAS_SIZE : -2]:
        raise AuditError("bytes after bank 50 changed outside checksum")

    atlas_mod = load_module("bitmap_candidate_atlas", ATLAS_MODULE)
    state_helper = load_module("bitmap_candidate_state", STATE_HELPER)
    pages = json.loads(SPEC.read_text(encoding="utf-8"))["pages"]
    header = struct.unpack_from("<4sHHHHHH", candidate, ATLAS_BASE)
    if header[:4] != (b"ECKO", 2, len(pages), 16):
        raise AuditError(f"atlas header contract failed: {header}")
    atlas_bytes = header[4]
    if atlas_bytes != build["atlas"]["bytes"]:
        raise AuditError(f"atlas size drifted: {atlas_bytes}")
    if any(candidate[ATLAS_BASE + atlas_bytes : ATLAS_BASE + ATLAS_SIZE]):
        # The builder intentionally fills the unused bank tail with FF.
        tail = candidate[ATLAS_BASE + atlas_bytes : ATLAS_BASE + ATLAS_SIZE]
        if any(byte != 0xFF for byte in tail):
            raise AuditError("unused bank 50 tail is not FF")

    cursor = 16 + len(pages) * RECORD.size
    page_rows = []
    for index, page in enumerate(pages):
        rom_page = index + 1
        slot = page["slot"]
        record = RECORD.unpack_from(candidate, ATLAS_BASE + rom_page * RECORD.size)
        if record[0] != slot or record[7] != 28:
            raise AuditError(f"page {rom_page} record identity failed: {record}")
        png = PREVIEWS / f"slot{slot:02d}_ko.png"
        if not png.is_file():
            raise AuditError(f"missing preview: {png}")
        image = Image.open(png).convert("RGB")
        tilemap, gfx, ntiles = atlas_mod.page_atlas(image, record[2], record[3])
        if record[4] != ntiles or record[5] != cursor:
            raise AuditError(f"page {rom_page} tile count/map cursor failed")
        if record[6] != record[5] + len(tilemap):
            raise AuditError(f"page {rom_page} gfx cursor failed")
        map_bytes = candidate[
            ATLAS_BASE + record[5] : ATLAS_BASE + record[5] + len(tilemap)
        ]
        gfx_bytes = candidate[
            ATLAS_BASE + record[6] : ATLAS_BASE + record[6] + len(gfx)
        ]
        if map_bytes != tilemap or gfx_bytes != gfx:
            raise AuditError(f"page {rom_page} atlas differs from preview")
        cursor = record[6] + len(gfx)

        if page.get("art"):
            if (record[1], record[2], record[3]) != (1, 13, 5):
                raise AuditError(f"page {rom_page} cinematic row contract failed")
            row_coverage = True
        else:
            if record[1] != 0:
                raise AuditError(f"page {rom_page} standard flag failed")
            native = Image.open(NATIVE / f"slot{slot:02d}_native.png").convert("RGB")
            used = sorted(
                set(nonblack_tile_rows(native)) | set(nonblack_tile_rows(image))
            )
            row_coverage = bool(used) and all(
                record[2] <= row < record[2] + record[3] for row in used
            )
            if not row_coverage:
                raise AuditError(f"page {rom_page} does not erase every stock row")
        page_rows.append(
            {
                "rom_page": rom_page,
                "capture_slot": slot,
                "row0": record[2],
                "nrows": record[3],
                "ntiles": record[4],
                "palette": record[8],
                "first_tile": f"{record[9]:03X}",
                "last_tile": f"{record[9] + record[4] - 1:03X}",
                "preview_exact": True,
                "stock_and_korean_rows_covered": row_coverage,
            }
        )
    if cursor != atlas_bytes:
        raise AuditError(f"atlas final cursor mismatch: {cursor} != {atlas_bytes}")

    expected_cinematic = {
        17: (8, 0x051, 0x06C),
        18: (8, 0x051, 0x078),
        19: (8, 0x051, 0x07B),
        20: (10, 0x091, 0x0B7),
        21: (6, 0x091, 0x0AA),
    }
    collision_rows = []
    total_collisions = 0
    for page, expected in expected_cinematic.items():
        record = RECORD.unpack_from(candidate, ATLAS_BASE + page * RECORD.size)
        palette, first, last = expected
        if (record[8], record[9], record[9] + record[4] - 1) != expected:
            raise AuditError(f"page {page} cinematic range drifted: {record}")
        if first <= 0x090 <= last:
            raise AuditError(f"page {page} reaches separator tile 090")
        for state_path in STATE_GROUPS[page]:
            if not state_path.is_file():
                raise AuditError(f"missing supplied timeline state: {state_path}")
            ram, gfx = state_helper.parse_beetle_ram(state_path)
            bg = entries(ram, 0x3000, range(13), range(28))
            fg = entries(ram, 0x3800, range(32), range(32))
            obj = sprites(ram, gfx)
            bg_count = collision_count(bg, first, record[4])
            fg_count = collision_count(fg, first, record[4])
            obj_count = collision_count(obj, first, record[4])
            total = bg_count + fg_count + obj_count
            total_collisions += total
            collision_rows.append(
                {
                    "rom_page": page,
                    "state": state_path.name,
                    "target_range": f"{first:03X}-{last:03X}",
                    "preserved_bg_collisions": bg_count,
                    "full_fg_collisions": fg_count,
                    "active_sprite_collisions": obj_count,
                    "total": total,
                }
            )
    if total_collisions:
        raise AuditError(f"captured target collisions: {total_collisions}")

    if candidate[0xFE0000:0xFF0000] != parent[0xFE0000:0xFF0000]:
        raise AuditError("transition guard code changed")
    if candidate[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise AuditError("shared overlay changed")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ending_credits_galmuri11_bitmap_candidate.py",
        "ok": True,
        "status": "static_and_supplied_state_audit_passed_runtime_pending",
        "candidate_sha256": sha256(candidate),
        "atlas": {
            "bank": "50",
            "bytes": atlas_bytes,
            "free_bytes": ATLAS_SIZE - atlas_bytes,
            "records": len(pages),
            "all_records_match_previews": True,
            "all_stock_and_korean_rows_covered": True,
            "unused_tail_ff": True,
            "pages": page_rows,
        },
        "cinematic_alias_audit": {
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
        "preserved": {
            "transition_guard_code_byte_exact": True,
            "shared_overlay_byte_exact": True,
            "parent_saveram_byte_exact": build["preserved"]["paired_saveram_byte_exact"],
        },
        "conclusion": (
            "All 21 Galmuri11 Bitmap records are byte-exact to their previews, "
            "the trimmed standard-page spans cover every stock and Korean visible "
            "row, and the five cinematic upload ranges have zero aliases in all "
            "supplied BG/FG/sprite state inventories. Emulator replay is still required."
        ),
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["status"],
                "candidate_sha256": report["candidate_sha256"],
                "atlas_bytes": atlas_bytes,
                "atlas_free_bytes": ATLAS_SIZE - atlas_bytes,
                "records_exact": len(pages),
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
