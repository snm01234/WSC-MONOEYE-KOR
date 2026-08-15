#!/usr/bin/env python3
"""Build a self-contained static intermission BG candidate.

The transition overlay rebuilt at 0x54B780 is not the steady-state background
shown after returning from a submenu.  That screen reloads the 0x544400-
0x54B780 atlas and recreates a tilemap containing a few shared entries.

This builder copies only the sixteen approved clean text windows from the
full-rebuild state onto the live static background.  Safe atlas owners are
rewritten in place.  Conflicting cells receive private bank-1 tiles that are
unused and invariant across all twelve focus source/test states.  A small
guarded wrapper uploads those private payloads and retargets the cells after
the normal background renderer completes.  Non-tile attributes (palette and
flips) are preserved.  The twelve leaf labels therefore use the exact approved
focus sprite pixels and screen coordinates without changing the focus atlas.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_intermission_static_full_cleanup_candidate as common  # noqa: E402
from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from render_bank_tiles import GREYS_16  # noqa: E402
from trace_intermission_focus_sprites import WSRAM_BYTES, WSRAM_CORE_OFFSET  # noqa: E402

EXPECTED_BASE_SHA = "2dcfb253b0488182ce061df7b4396918564e6049c31ecdce0d1a9f2a4dd834d7"
EXPECTED_MAP_STATE_SHA = "2f34037ba543e6d9e9c14955c77b89b72563fd5f1ab8bb29ddeb133f9653bbaf"
EXPECTED_DESIRED_STATE_SHA = "7582d009e6546c0ec8d05b8b158ff6fb39ecbba0031fae86407d6900ac4a2d07"

TILE_BYTES = 0x20
# Text/label screen. Bright-green top chrome lives on a second screen at 0x3000
# (palette 5). Private bank-1 slot scans must include every live tilemap, or the
# green plate's bank-1 tiles (historically 0x11A+) get stolen and look "broken".
TILEMAP_BASE = 0x3800
TILEMAP_BASES = (0x1800, 0x3000, 0x3800)
ATLAS_LO = 0x544400
ATLAS_HI = 0x54B780
FOCUS_ATLAS_LO = 0x542000
FOCUS_ATLAS_HI = 0x544400
CONFIRM_ATLAS_LO = 0x547CFC
CONFIRM_ATLAS_HI = 0x549A1C
TRANSITION_HEADER = 0x54B780
RUNTIME_HOOK_LO = 0x7A0600
RUNTIME_HOOK_HI = 0x7A1000
FOCUS_RESERVED_BANK0 = set(range(0x110, 0x134))
STATIC_CLEAN_MARGIN = 8
MS_PRESERVE_BOX = (12, 69, 47, 85)
TOP_COLOR_PRESERVE_LABELS = {
    "parent_operation",
    "mission_status",
    "scouting",
    "advance",
}
STATIC_YELLOW_LABELS = {"mission_status", "scouting"}
STATIC_YELLOW_REMAP = {0x03: 0x01, 0x0E: 0x0F}
# Non-focus 진격 keeps JP ink left of clean_core x=182 (x=178..181).
# Clear-only pad; glyph stamp still uses the report core so desired ink is not copied.
ADVANCE_LEFT_CLEAR_PAD = 4

# The renderer's final far call is wrapped.  The wrapper lives in the verified
# all-FF tail of the same mapped 64 KiB code bank.
RENDER_FINAL_CALL = 0x789C4D
ORIGINAL_FINAL_CALL = bytes.fromhex("9A B5 DE 00 80")
WRAPPER_ROM = 0x78FCD3
WRAPPER_BANK_BASE = 0x780000
WRAPPER_LIMIT = 0x790000
PRIVATE_PAYLOAD_ROM = 0x79FA8F
PRIVATE_PAYLOAD_LIMIT = 0x7A0000
PRIVATE_PAYLOAD_SEGMENT = 0x9000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def entry_at(ram: bytes, col: int, row: int, base: int = TILEMAP_BASE) -> int:
    off = base + (row * 32 + col) * 2
    return int.from_bytes(ram[off : off + 2], "little")


def bank1_tids_in_tilemaps(ram: bytes) -> set[int]:
    used: set[int] = set()
    for base in TILEMAP_BASES:
        for row in range(32):
            for col in range(32):
                entry = entry_at(ram, col, row, base)
                if entry & 0x2000:
                    used.add(entry & 0x01FF)
    return used


def raw_for_entry(ram: bytes, entry: int) -> bytes:
    tid = entry & 0x01FF
    gfx = 0x8000 if entry & 0x2000 else 0x4000
    return bytes(ram[gfx + tid * TILE_BYTES : gfx + (tid + 1) * TILE_BYTES])


def raw_at(ram: bytes, col: int, row: int) -> bytes:
    return raw_for_entry(ram, entry_at(ram, col, row))


def oriented_grid(ram: bytes, col: int, row: int) -> list[list[int]]:
    value = entry_at(ram, col, row)
    raw_grid = common.decode_tile(raw_at(ram, col, row))
    return [
        [
            raw_grid[7 - y if value & 0x8000 else y][
                7 - x if value & 0x4000 else x
            ]
            for x in range(8)
        ]
        for y in range(8)
    ]


def inverse_orient(grid: list[list[int]], entry: int) -> list[list[int]]:
    out = [[0] * 8 for _ in range(8)]
    for y in range(8):
        sy = 7 - y if entry & 0x8000 else y
        for x in range(8):
            sx = 7 - x if entry & 0x4000 else x
            out[sy][sx] = grid[y][x]
    return out


def screen_from_ram(ram: bytes) -> list[list[int]]:
    screen = [[0] * common.SCREEN_W for _ in range(common.SCREEN_H)]
    for row in range(18):
        for col in range(28):
            grid = oriented_grid(ram, col, row)
            for y in range(8):
                screen[row * 8 + y][col * 8 : col * 8 + 8] = grid[y]
    return screen


def render_screen(screen: list[list[int]], path: Path, scale: int) -> None:
    image = Image.new("RGB", (common.SCREEN_W, common.SCREEN_H), GREYS_16[0])
    pixels = image.load()
    for y in range(common.SCREEN_H):
        for x in range(common.SCREEN_W):
            pixels[x, y] = GREYS_16[screen[y][x]]
    if scale > 1:
        image = image.resize(
            (common.SCREEN_W * scale, common.SCREEN_H * scale), Image.NEAREST
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def target_rectangles(report: dict) -> list[dict]:
    rows = report.get("labels") or []
    if len(rows) != 16:
        raise RuntimeError(f"full rebuild report has {len(rows)} labels, expected 16")
    names = [str(row["name"]) for row in rows]
    if len(names) != len(set(names)):
        raise RuntimeError("full rebuild report contains duplicate label names")
    out = []
    for row in rows:
        name = str(row["name"])
        core_rect = tuple(int(v) for v in row["clean_core_bbox_xyxy"])
        if len(core_rect) != 4 or not (
            0 <= core_rect[0] < core_rect[2] <= common.SCREEN_W
            and 0 <= core_rect[1] < core_rect[3] <= common.SCREEN_H
        ):
            raise RuntimeError(f"{row['name']}: invalid clean rectangle {core_rect}")
        # Top green chrome is a second screen. Only clear text-layer holes inside
        # the core glyph box — an 8px margin would punch through the blue UI under
        # the operation circle. Advance alone also clears a left JP overhang pad.
        if name in TOP_COLOR_PRESERVE_LABELS:
            if name == "advance":
                rect = (
                    max(0, core_rect[0] - ADVANCE_LEFT_CLEAR_PAD),
                    core_rect[1],
                    core_rect[2],
                    core_rect[3],
                )
                policy = "transparent_plate_plus_korean_glyphs_advance_left_clear"
            else:
                rect = core_rect
                policy = "transparent_plate_plus_korean_glyphs"
        else:
            rect = (
                max(0, core_rect[0] - STATIC_CLEAN_MARGIN),
                max(0, core_rect[1] - STATIC_CLEAN_MARGIN),
                min(common.SCREEN_W, core_rect[2] + STATIC_CLEAN_MARGIN),
                min(common.SCREEN_H, core_rect[3] + STATIC_CLEAN_MARGIN),
            )
            policy = "core_plus_8px_cleanup"
        out.append(
            {
                "name": name,
                "core_rect": core_rect,
                "rect": rect,
                "copy_policy": policy,
            }
        )
    return out


def compose_static_screen(
    before: list[list[int]],
    desired_source: list[list[int]],
    rectangles: list[dict],
) -> tuple[list[list[int]], set[tuple[int, int]], list[dict]]:
    after = [line[:] for line in before]
    approved: set[tuple[int, int]] = set()
    rows = []
    for item in rectangles:
        left, top, right, bottom = item["rect"]
        core_left, core_top, core_right, core_bottom = item["core_rect"]
        points = {
            (x, y) for y in range(top, bottom) for x in range(left, right)
        }
        approved |= points
        changed = 0
        recolored = 0
        cleared = 0
        glyphs = 0
        for x, y in points:
            in_core = (
                core_left <= x < core_right and core_top <= y < core_bottom
            )
            desired = desired_source[y][x]
            if item["name"] in TOP_COLOR_PRESERVE_LABELS:
                # Green chrome is SCR @ 0x3000 / palette 5. Keep text-screen
                # holes at index 0 so that layer shows through; only stamp glyphs.
                if in_core and desired != 0:
                    if item["name"] in STATIC_YELLOW_LABELS:
                        remapped = STATIC_YELLOW_REMAP.get(desired, desired)
                        recolored += remapped != desired
                        desired = remapped
                    value = desired
                    glyphs += 1
                else:
                    value = 0
                    cleared += before[y][x] != 0
            else:
                value = desired
            if after[y][x] != value:
                changed += 1
            after[y][x] = value
        rows.append(
            {
                "name": item["name"],
                "clean_core_bbox_xyxy": list(item["core_rect"]),
                "static_copy_bbox_xyxy": list(item["rect"]),
                "copy_policy": item["copy_policy"],
                "copied_pixels": len(points),
                "changed_pixels": changed,
                "yellow_remapped_pixels": recolored,
                "korean_glyph_pixels": glyphs,
                "cleared_to_transparent_pixels": cleared,
            }
        )
    ms_points = {
        (x, y)
        for y in range(MS_PRESERVE_BOX[1], MS_PRESERVE_BOX[3])
        for x in range(MS_PRESERVE_BOX[0], MS_PRESERVE_BOX[2])
    }
    for x, y in ms_points:
        after[y][x] = before[y][x]
    approved -= ms_points
    outside = {
        (x, y)
        for y in range(common.SCREEN_H)
        for x in range(common.SCREEN_W)
        if before[y][x] != after[y][x] and (x, y) not in approved
    }
    if outside:
        raise RuntimeError(f"static composition escaped text windows: {sorted(outside)[:8]}")
    return after, approved, rows


def raw_grids_for_screen(
    screen: list[list[int]], ram: bytes
) -> dict[tuple[int, int], bytes]:
    out = {}
    for row in range(18):
        for col in range(28):
            oriented = [
                screen[row * 8 + y][col * 8 : col * 8 + 8] for y in range(8)
            ]
            out[(col, row)] = common.encode_tile(
                inverse_orient(oriented, entry_at(ram, col, row))
            )
    return out


def build_raw_index(body: bytes) -> dict[bytes, list[int]]:
    result: dict[bytes, list[int]] = collections.defaultdict(list)
    for address in range(ATLAS_LO, ATLAS_HI, TILE_BYTES):
        result[bytes(body[address : address + TILE_BYTES])].append(address)
    return result


def resolve_entry_owners(
    ram: bytes, body: bytes, raw_index: dict[bytes, list[int]]
) -> dict[int, int]:
    owners: dict[int, int] = {}
    entries = {
        entry_at(ram, col, row) for row in range(32) for col in range(32)
    }
    for value in entries:
        raw = raw_for_entry(ram, value)
        tid = value & 0x01FF
        if not (value & 0x2000):
            linear = ATLAS_LO + (tid - 0x15E) * TILE_BYTES
            if (
                ATLAS_LO <= linear < ATLAS_HI
                and bytes(body[linear : linear + TILE_BYTES]) == raw
            ):
                owners[value] = linear
                continue
        hits = raw_index.get(raw, [])
        if len(hits) == 1:
            owners[value] = hits[0]
    return owners


def load_focus_fixture_rams(
    report_path: Path, zstd: Zstd
) -> tuple[list[dict], list[bytes]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    targets = report.get("targets") or []
    if len(targets) != 12:
        raise RuntimeError(f"focus report has {len(targets)} targets, expected 12")
    names = [str(row["name"]) for row in targets]
    if len(names) != len(set(names)):
        raise RuntimeError("focus report contains duplicate target names")

    states = []
    rams = []
    seen: set[Path] = set()
    for row in targets:
        for kind in ("source_state", "test_state"):
            path = Path(row[kind]).resolve()
            if path in seen:
                continue
            if not path.is_file():
                raise RuntimeError(f"missing focus fixture: {path}")
            seen.add(path)
            core, member = read_state_core(path, zstd)
            rams.append(
                core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
            )
            states.append(
                {
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "core_member": member,
                }
            )
    return states, rams


def find_private_slots(
    map_ram: bytes, focus_rams: list[bytes], needed: int
) -> tuple[list[dict], dict]:
    """Pick private VRAM tiles that no live tilemap references.

    Bright-green intermission chrome uses bank-1 tiles via the 0x3000 screen, so
    bank-1 is nearly full. Prefer payload-invariant bank-0 tiles outside the
    focus-sprite reserved band; allow a non-contiguous selection.
    """
    rams = [map_ram, *focus_rams]
    used_bank0: set[int] = set()
    used_bank1: set[int] = set()
    for ram in rams:
        for base in TILEMAP_BASES:
            for row in range(32):
                for col in range(32):
                    entry = entry_at(ram, col, row, base)
                    tid = entry & 0x01FF
                    if entry & 0x2000:
                        used_bank1.add(tid)
                    else:
                        used_bank0.add(tid)
    used_bank0 |= FOCUS_RESERVED_BANK0

    def dynamic_tids(bank: int) -> set[int]:
        base = 0x8000 if bank == 1 else 0x4000
        return {
            tid
            for tid in range(0x200)
            if len(
                {
                    bytes(
                        ram[base + tid * TILE_BYTES : base + (tid + 1) * TILE_BYTES]
                    )
                    for ram in rams
                }
            )
            > 1
        }

    dynamic_bank0 = dynamic_tids(0)
    dynamic_bank1 = dynamic_tids(1)
    safe_bank0 = [
        tid
        for tid in range(0x200)
        if tid not in used_bank0 and tid not in dynamic_bank0
    ]
    safe_bank1 = [
        tid
        for tid in range(0x200)
        if tid not in used_bank1 and tid not in dynamic_bank1
    ]
    # Prefer longer bank-0 runs first so the wrapper copies denser ranges when
    # possible, then fall back to remaining safe bank-0 / bank-1 tiles.
    def runs_of(tids: list[int]) -> list[tuple[int, int]]:
        if not tids:
            return []
        out = []
        start = prev = tids[0]
        for tid in tids[1:]:
            if tid == prev + 1:
                prev = tid
                continue
            out.append((start, prev))
            start = prev = tid
        out.append((start, prev))
        return sorted(out, key=lambda pair: -(pair[1] - pair[0] + 1))

    selected: list[dict] = []
    for start, end in runs_of(safe_bank0):
        for tid in range(start, end + 1):
            if len(selected) >= needed:
                break
            raw = bytes(
                map_ram[0x4000 + tid * TILE_BYTES : 0x4000 + (tid + 1) * TILE_BYTES]
            )
            selected.append(
                {
                    "bank": 0,
                    "tid": tid,
                    "entry": tid,
                    "vram": 0x4000 + tid * TILE_BYTES,
                    "old_sha256": sha256_bytes(raw),
                }
            )
        if len(selected) >= needed:
            break
    if len(selected) < needed:
        for tid in safe_bank1:
            if len(selected) >= needed:
                break
            raw = bytes(
                map_ram[0x8000 + tid * TILE_BYTES : 0x8000 + (tid + 1) * TILE_BYTES]
            )
            selected.append(
                {
                    "bank": 1,
                    "tid": tid,
                    "entry": 0x2000 | tid,
                    "vram": 0x8000 + tid * TILE_BYTES,
                    "old_sha256": sha256_bytes(raw),
                }
            )
    if len(selected) < needed:
        raise RuntimeError(
            f"no private slot pool: need {needed}, "
            f"safe bank0={len(safe_bank0)} bank1={len(safe_bank1)}"
        )
    selected = selected[:needed]
    return selected, {
        "fixture_state_count": len(rams),
        "focus_fixture_state_count": len(focus_rams),
        "tilemap_bases_scanned": [f"{base:04X}" for base in TILEMAP_BASES],
        "used_bank0_slots": len(used_bank0),
        "used_bank1_slots": len(used_bank1),
        "dynamic_bank0_slots": len(dynamic_bank0),
        "dynamic_bank1_slots": len(dynamic_bank1),
        "safe_bank0_slots": len(safe_bank0),
        "safe_bank1_slots": len(safe_bank1),
        "selected_banks": sorted({row["bank"] for row in selected}),
        "selected_tids": [f"{row['tid']:03X}" for row in selected],
        "selected_run": [
            f"{selected[0]['tid']:03X}",
            f"{selected[-1]['tid']:03X}",
        ],
    }


def emit_guarded_wrapper(
    patches: list[dict],
    anchors: list[dict],
    private_slots: list[dict],
) -> tuple[bytes, list[int]]:
    if not private_slots:
        raise RuntimeError("private slot list is empty")
    payload_bytes = len(private_slots) * TILE_BYTES
    if payload_bytes % 2:
        raise RuntimeError("private payload must contain whole words")

    code = bytearray(ORIGINAL_FINAL_CALL)
    # pushf, ax, cx, si, di, ds, es; ES=0 for WSRAM guards/writes.
    code += bytes.fromhex("9C 50 51 56 57 1E 06 31 C0 8E C0")
    done_jumps: list[int] = []
    for row in anchors:
        address = int(row["wsram_offset"])
        value = int(row["entry"])
        code += b"\x26\x81\x3E"
        code += address.to_bytes(2, "little")
        code += value.to_bytes(2, "little")
        code += b"\x74\x03"  # equal: skip the near jump
        code += b"\xE9\x00\x00"
        done_jumps.append(len(code) - 2)
    # DS:SI = adjacent fixed ROM bank payload; copy one tile at a time so the
    # destination VRAM slots need not be contiguous.
    code += b"\xB8" + PRIVATE_PAYLOAD_SEGMENT.to_bytes(2, "little")
    code += b"\x8E\xD8"
    code += b"\xBE" + (PRIVATE_PAYLOAD_ROM & 0xFFFF).to_bytes(2, "little")
    code += bytes.fromhex("FC")  # cld
    for row in private_slots:
        code += b"\xBF" + int(row["vram"]).to_bytes(2, "little")
        code += b"\xB9" + (TILE_BYTES // 2).to_bytes(2, "little")
        code += bytes.fromhex("F3 A5")  # rep movsw
    for row in patches:
        code += b"\x26\xC7\x06"
        code += int(row["wsram_offset"]).to_bytes(2, "little")
        code += int(row["new_entry"]).to_bytes(2, "little")
    done = len(code)
    code += bytes.fromhex("07 1F 5F 5E 59 58 9D CB")
    for displacement_at in done_jumps:
        relative = done - (displacement_at + 2)
        if not -0x8000 <= relative <= 0x7FFF:
            raise RuntimeError("wrapper guard branch exceeds rel16")
        code[displacement_at : displacement_at + 2] = (
            relative & 0xFFFF
        ).to_bytes(2, "little")
    return bytes(code), done_jumps


def contiguous_runs(offsets: list[int]) -> list[dict]:
    if not offsets:
        return []
    rows = []
    start = previous = offsets[0]
    for value in offsets[1:]:
        if value == previous + 1:
            previous = value
            continue
        rows.append({"start": start, "end": previous + 1, "bytes": previous + 1 - start})
        start = previous = value
    rows.append({"start": start, "end": previous + 1, "bytes": previous + 1 - start})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT
        / "out/patch/intermission_full_rebuild_clean16_candidate"
        / "intermission_full_rebuild_clean16_candidate.wsc",
    )
    ap.add_argument(
        "--base-sav",
        type=Path,
        default=ROOT / "sram/monoeye_ko_expanded.sav",
    )
    ap.add_argument(
        "--map-state",
        type=Path,
        default=ROOT
        / "out/patch/intermission_full_rebuild_clean16_candidate/runtime_static_probe/states"
        / "full_rebuild_reload_final.State",
    )
    ap.add_argument(
        "--desired-state",
        type=Path,
        default=ROOT
        / "out/patch/intermission_full_rebuild_clean16_candidate/states"
        / "Mednafen.QuickSave1.State",
    )
    ap.add_argument(
        "--full-report",
        type=Path,
        default=ROOT
        / "out/patch/intermission_full_rebuild_clean16_candidate"
        / "full_rebuild_report.json",
    )
    ap.add_argument(
        "--focus-report",
        type=Path,
        default=ROOT
        / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_static_bg_focus_exact_candidate",
    )
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args(argv)

    for path in (
        args.base_rom,
        args.base_sav,
        args.map_state,
        args.desired_state,
        args.full_report,
        args.focus_report,
        args.zstd_dll,
    ):
        if not path.is_file():
            raise SystemExit(f"missing: {path}")
    if sha256_file(args.base_rom) != EXPECTED_BASE_SHA:
        raise RuntimeError("full-rebuild base ROM hash drifted")
    if sha256_file(args.map_state) != EXPECTED_MAP_STATE_SHA:
        raise RuntimeError("static reload map-state hash drifted")
    if sha256_file(args.desired_state) != EXPECTED_DESIRED_STATE_SHA:
        raise RuntimeError("clean16 desired-state hash drifted")

    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    body = base_rom[base : base + 0x800000]
    if body[RENDER_FINAL_CALL : RENDER_FINAL_CALL + 5] != ORIGINAL_FINAL_CALL:
        raise RuntimeError("renderer final-call hook site drifted")
    if any(value != 0xFF for value in body[WRAPPER_ROM:WRAPPER_LIMIT]):
        raise RuntimeError("renderer wrapper tail is no longer all-FF")
    if any(
        value != 0xFF
        for value in body[PRIVATE_PAYLOAD_ROM:PRIVATE_PAYLOAD_LIMIT]
    ):
        raise RuntimeError("private payload tail is no longer all-FF")

    zstd = Zstd(args.zstd_dll)
    map_core, map_member = read_state_core(args.map_state, zstd)
    desired_core, desired_member = read_state_core(args.desired_state, zstd)
    focus_fixture_states, focus_fixture_rams = load_focus_fixture_rams(
        args.focus_report, zstd
    )
    map_ram = map_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    desired_ram = desired_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    before_screen = screen_from_ram(map_ram)
    desired_source = screen_from_ram(desired_ram)
    rectangles = target_rectangles(
        json.loads(args.full_report.read_text(encoding="utf-8"))
    )
    after_screen, approved_pixels, label_rows = compose_static_screen(
        before_screen, desired_source, rectangles
    )
    before_raws = {
        (col, row): raw_at(map_ram, col, row)
        for row in range(18)
        for col in range(28)
    }
    after_raws = raw_grids_for_screen(after_screen, map_ram)
    changed_cells = {
        pos for pos in after_raws if after_raws[pos] != before_raws[pos]
    }
    if not changed_cells:
        raise RuntimeError("static composition produced no changed cells")
    if any(
        not any(
            left < (pos[0] + 1) * 8
            and pos[0] * 8 < right
            and top < (pos[1] + 1) * 8
            and pos[1] * 8 < bottom
            for left, top, right, bottom in (row["rect"] for row in rectangles)
        )
        for pos in changed_cells
    ):
        raise RuntimeError("changed static cell does not intersect a text window")

    raw_index = build_raw_index(body)
    owners = resolve_entry_owners(map_ram, body, raw_index)
    positions_by_entry: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for row in range(32):
        for col in range(32):
            positions_by_entry[entry_at(map_ram, col, row)].append((col, row))
    consumers_by_rom: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for value, positions in positions_by_entry.items():
        if value in owners:
            consumers_by_rom[owners[value]].extend(positions)

    atlas_updates: dict[int, bytes] = {}
    private_requests: list[tuple[tuple[int, int], bytes, str]] = []
    touched_roms = {
        owners[entry_at(map_ram, *pos)]
        for pos in changed_cells
        if entry_at(map_ram, *pos) in owners
    }
    for rom in sorted(touched_roms):
        groups: dict[bytes, list[tuple[int, int]]] = collections.defaultdict(list)
        for pos in consumers_by_rom[rom]:
            desired = (
                after_raws[pos]
                if pos in changed_cells
                else raw_at(map_ram, *pos)
            )
            groups[desired].append(pos)
        original = bytes(body[rom : rom + TILE_BYTES])
        keeper = (
            original
            if original in groups
            else max(groups.items(), key=lambda item: len(item[1]))[0]
        )
        if keeper != original:
            atlas_updates[rom] = keeper
        for desired, positions in groups.items():
            if desired == keeper:
                continue
            for pos in positions:
                if pos in changed_cells:
                    private_requests.append((pos, desired, "shared_rom_variant"))

    owned_changed = {
        pos
        for pos in changed_cells
        if entry_at(map_ram, *pos) in owners
    }
    for pos in sorted(changed_cells - owned_changed, key=lambda p: (p[1], p[0])):
        private_requests.append((pos, after_raws[pos], "unresolved_atlas_owner"))

    requested_positions = [pos for pos, _, _ in private_requests]
    if len(requested_positions) != len(set(requested_positions)):
        raise RuntimeError("duplicate private request for one screen cell")
    private_unique_raws = list(
        dict.fromkeys(desired for _, desired, _ in private_requests)
    )
    slots, slot_liveness = find_private_slots(
        map_ram, focus_fixture_rams, len(private_unique_raws)
    )
    private_by_raw: dict[bytes, dict] = {}
    slot_index = 0
    patches = []
    private_updates = []
    private_payloads = []
    for pos, desired, reason in private_requests:
        slot = private_by_raw.get(desired)
        if slot is None:
            if slot_index >= len(slots):
                raise RuntimeError(
                    f"private slot pool exhausted: need >{slot_index}, found {len(slots)}"
                )
            slot = slots[slot_index]
            slot_index += 1
            private_by_raw[desired] = slot
            private_payloads.append(desired)
            private_updates.append(
                {
                    "bank": int(slot["bank"]),
                    "tid": f"{slot['tid']:03X}",
                    "entry": f"{slot['entry']:04X}",
                    "vram": f"{slot['vram']:04X}",
                    "payload_rom": f"{PRIVATE_PAYLOAD_ROM + (slot_index - 1) * TILE_BYTES:06X}",
                    "old_sha256": slot["old_sha256"],
                    "new_sha256": sha256_bytes(desired),
                }
            )
        old = entry_at(map_ram, *pos)
        new = (old & ~0x21FF) | int(slot["entry"])
        patches.append(
            {
                "pos": list(pos),
                "wsram_offset": TILEMAP_BASE + (pos[1] * 32 + pos[0]) * 2,
                "old_entry": old,
                "new_entry": new,
                "old_entry_hex": f"{old:04X}",
                "new_entry_hex": f"{new:04X}",
                "private_bank": int(slot["bank"]),
                "private_tid": f"{slot['tid']:03X}",
                "private_vram": f"{slot['vram']:04X}",
                "private_payload_rom": next(
                    row["payload_rom"]
                    for row in private_updates
                    if row["tid"] == f"{slot['tid']:03X}"
                    and int(row["bank"]) == int(slot["bank"])
                ),
                "reason": reason,
            }
        )

    patch_positions = {tuple(row["pos"]) for row in patches}
    anchor_candidates = [
        (0, 0),
        (27, 0),
        (0, 17),
        (27, 17),
        (6, 1),
        (6, 17),
        (19, 17),
        (13, 8),
        (22, 2),
        (2, 15),
    ]
    anchors = []
    for pos in anchor_candidates:
        if pos in patch_positions:
            continue
        anchors.append(
            {
                "kind": "steady_tilemap",
                "pos": list(pos),
                "wsram_offset": TILEMAP_BASE + (pos[1] * 32 + pos[0]) * 2,
                "entry": entry_at(map_ram, *pos),
                "entry_hex": f"{entry_at(map_ram, *pos):04X}",
            }
        )
        if len(anchors) == 8:
            break
    if len(anchors) != 8:
        raise RuntimeError("could not select eight stable static-map anchors")
    wrapper, branch_offsets = emit_guarded_wrapper(
        patches, anchors, slots
    )
    if WRAPPER_ROM + len(wrapper) > WRAPPER_LIMIT:
        raise RuntimeError("renderer wrapper exceeds same-bank FF tail")
    private_payload = b"".join(private_payloads)
    if PRIVATE_PAYLOAD_ROM + len(private_payload) > PRIVATE_PAYLOAD_LIMIT:
        raise RuntimeError("private payload exceeds adjacent-bank FF tail")

    candidate = bytearray(base_rom)
    for address, raw in atlas_updates.items():
        candidate[base + address : base + address + TILE_BYTES] = raw
    candidate[
        base + PRIVATE_PAYLOAD_ROM :
        base + PRIVATE_PAYLOAD_ROM + len(private_payload)
    ] = private_payload
    wrapper_offset = WRAPPER_ROM - WRAPPER_BANK_BASE
    hook_call = b"\x9A" + wrapper_offset.to_bytes(2, "little") + bytes.fromhex("00 80")
    candidate[
        base + RENDER_FINAL_CALL : base + RENDER_FINAL_CALL + len(hook_call)
    ] = hook_call
    candidate[base + WRAPPER_ROM : base + WRAPPER_ROM + len(wrapper)] = wrapper
    checksum = update_ws_checksum(candidate)
    out = bytes(candidate)

    # Static model: owned rewrites update every consumer; wrapper patches then
    # select the private payload.  All visible target cells must match the
    # composed clean screen, and every non-target cell must remain unchanged.
    predicted = dict(before_raws)
    for value, rom in owners.items():
        if rom not in atlas_updates:
            continue
        for pos in positions_by_entry[value]:
            if 0 <= pos[0] < 28 and 0 <= pos[1] < 18:
                predicted[pos] = atlas_updates[rom]
    private_payload_by_key = {
        (int(row["bank"]), int(row["tid"], 16)): bytes(
            out[
                base + int(row["payload_rom"], 16) :
                base + int(row["payload_rom"], 16)
                + TILE_BYTES
            ]
        )
        for row in private_updates
    }
    for row in patches:
        pos = tuple(row["pos"])
        predicted[pos] = private_payload_by_key[
            (int(row["private_bank"]), int(row["private_tid"], 16))
        ]
    target_mismatches = [
        list(pos)
        for pos in sorted(changed_cells, key=lambda p: (p[1], p[0]))
        if predicted[pos] != after_raws[pos]
    ]
    outside_mismatches = [
        [col, row]
        for row in range(18)
        for col in range(28)
        if (col, row) not in changed_cells
        and predicted[(col, row)] != before_raws[(col, row)]
    ]
    if target_mismatches or outside_mismatches:
        raise RuntimeError(
            f"static model mismatch target={target_mismatches[:8]} "
            f"outside={outside_mismatches[:8]}"
        )

    diff_offsets = [
        index for index, (old, new) in enumerate(zip(base_rom, out)) if old != new
    ]
    allowed = {len(out) - 2, len(out) - 1}
    allowed.update(
        base + address + delta
        for address in atlas_updates
        for delta in range(TILE_BYTES)
    )
    allowed.update(
        range(
            base + RENDER_FINAL_CALL,
            base + RENDER_FINAL_CALL + len(hook_call),
        )
    )
    allowed.update(range(base + WRAPPER_ROM, base + WRAPPER_ROM + len(wrapper)))
    allowed.update(
        range(
            base + PRIVATE_PAYLOAD_ROM,
            base + PRIVATE_PAYLOAD_ROM + len(private_payload),
        )
    )
    outside_allowed = [offset for offset in diff_offsets if offset not in allowed]
    if outside_allowed:
        raise RuntimeError(f"candidate changed bytes outside contract: {outside_allowed[:8]}")
    focus_delta = sum(
        out[base + i] != base_rom[base + i]
        for i in range(FOCUS_ATLAS_LO, FOCUS_ATLAS_HI)
    )
    transition_header_delta = sum(
        out[base + i] != base_rom[base + i]
        for i in range(TRANSITION_HEADER, TRANSITION_HEADER + 18)
    )
    runtime_hook_delta = sum(
        out[base + i] != base_rom[base + i]
        for i in range(RUNTIME_HOOK_LO, RUNTIME_HOOK_HI)
    )
    if focus_delta or transition_header_delta or runtime_hook_delta:
        raise RuntimeError(
            "preservation guard failed "
            f"focus={focus_delta} transition={transition_header_delta} "
            f"runtime={runtime_hook_delta}"
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rom_out = args.out_dir / "intermission_static_bg_focus_exact_candidate.wsc"
    sav_out = ROOT / "sram/intermission_static_bg_focus_exact_candidate.sav"
    rom_out.write_bytes(out)
    shutil.copy2(args.base_sav, sav_out)
    previews = args.out_dir / "previews"
    render_screen(before_screen, previews / "01_static_before.png", args.scale)
    render_screen(desired_source, previews / "02_clean16_reference.png", args.scale)
    render_screen(after_screen, previews / "03_static_focus_exact_expected.png", args.scale)

    report = {
        "purpose": (
            "self-contained 0x544xxx static BG cleanup; lower twelve labels "
            "match approved focus pixels and coordinates"
        ),
        "base_rom": str(args.base_rom),
        "base_rom_sha256": sha256_file(args.base_rom),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": sha256_file(rom_out),
        "candidate_sav": str(sav_out),
        "candidate_sav_sha256": sha256_file(sav_out),
        "checksum": f"{checksum:04X}",
        "map_state": {
            "path": str(args.map_state),
            "sha256": sha256_file(args.map_state),
            "core_member": map_member,
        },
        "desired_state": {
            "path": str(args.desired_state),
            "sha256": sha256_file(args.desired_state),
            "core_member": desired_member,
        },
        "top_green_plate": {
            "screen_tilemap": "0x3000",
            "palette": 5,
            "policy": (
                "bright green chrome is a second BG screen; private bank-1 slots "
                "must not collide with any TILEMAP_BASES reference, and top text "
                "windows keep index-0 holes so that green layer shows through"
            ),
        },
        "focus_liveness": {
            "report": str(args.focus_report),
            "report_sha256": sha256_file(args.focus_report),
            "fixture_states": focus_fixture_states,
            **slot_liveness,
            "policy": (
                "private slots must be unreferenced in tilemaps "
                f"{[f'{b:04X}' for b in TILEMAP_BASES]} and payload-invariant "
                "across the static map plus every source/test focus state; "
                "bank-0 outside the focus-sprite reserved band is preferred "
                "because bank-1 hosts the green chrome screen"
            ),
        },
        "static_atlas": {
            "range": [f"{ATLAS_LO:06X}", f"{ATLAS_HI:06X}"],
            "owned_entries": len(owners),
            "changed_screen_cells": len(changed_cells),
            "changed_screen_cell_positions": [
                list(pos) for pos in sorted(changed_cells, key=lambda p: (p[1], p[0]))
            ],
            "changed_unique_rom_tiles": len(atlas_updates),
            "owned_or_keeper_updates": len(atlas_updates),
            "private_unique_tiles": len(private_updates),
            "private_tilemap_patches": len(patches),
            "private_slots": private_updates,
            "tilemap_patches": patches,
        },
        "renderer_wrapper": {
            "hook_call_rom": f"{RENDER_FINAL_CALL:06X}",
            "original_call_hex": ORIGINAL_FINAL_CALL.hex(),
            "new_call_hex": hook_call.hex(),
            "wrapper_rom": f"{WRAPPER_ROM:06X}",
            "wrapper_bytes": len(wrapper),
            "wrapper_sha256": sha256_bytes(wrapper),
            "guard_anchor_count": len(anchors),
            "guard_anchors": anchors,
            "guard_branch_displacements": branch_offsets,
            "private_payload_rom": f"{PRIVATE_PAYLOAD_ROM:06X}",
            "private_payload_bytes": len(private_payload),
            "private_payload_sha256": sha256_bytes(private_payload),
            "private_payload_segment": f"{PRIVATE_PAYLOAD_SEGMENT:04X}",
            "private_vram_slots": [
                {
                    "bank": int(row["bank"]),
                    "tid": f"{row['tid']:03X}",
                    "vram": f"{row['vram']:04X}",
                }
                for row in slots
            ],
            "policy": (
                "after eight steady-state tilemap anchors match, copy each private "
                "tile payload into its safe VRAM slot and then retarget the patched cells"
            ),
        },
        "labels": label_rows,
        "pixel_contract": {
            "approved_window_pixels": len(approved_pixels),
            "static_cleanup_margin_pixels": STATIC_CLEAN_MARGIN,
            "top_color_preserve_labels": sorted(TOP_COLOR_PRESERVE_LABELS),
            "static_yellow_labels": sorted(STATIC_YELLOW_LABELS),
            "static_yellow_remap": {
                f"{old:X}": f"{new:X}"
                for old, new in STATIC_YELLOW_REMAP.items()
            },
            "ms_preserve_box_xyxy": list(MS_PRESERVE_BOX),
            "ms_preserve_mismatch_pixels": sum(
                before_screen[y][x] != after_screen[y][x]
                for y in range(MS_PRESERVE_BOX[1], MS_PRESERVE_BOX[3])
                for x in range(MS_PRESERVE_BOX[0], MS_PRESERVE_BOX[2])
            ),
            "changed_pixels": sum(
                before_screen[y][x] != after_screen[y][x]
                for x, y in approved_pixels
            ),
            "outside_window_pixel_changes": 0,
            "upper_four_source": "full_rebuild canonical 13px parent masks",
            "lower_twelve_source": "approved focus-state exact masks and origins",
        },
        "diff": {
            "changed_bytes": len(diff_offsets),
            "changed_runs": [
                {
                    "start": f"{row['start']:08X}",
                    "end": f"{row['end']:08X}",
                    "bytes": row["bytes"],
                }
                for row in contiguous_runs(diff_offsets)
            ],
            "outside_allowed_bytes": len(outside_allowed),
        },
        "verification": {
            "static_model_target_cells_exact": not target_mismatches,
            "static_model_non_target_cells_unchanged": not outside_mismatches,
            "focus_atlas_unchanged": focus_delta == 0,
            "confirm_atlas_unchanged": out[
                base + CONFIRM_ATLAS_LO : base + CONFIRM_ATLAS_HI
            ]
            == base_rom[base + CONFIRM_ATLAS_LO : base + CONFIRM_ATLAS_HI],
            "transition_overlay_header_unchanged": transition_header_delta == 0,
            "existing_runtime_hook_unchanged": runtime_hook_delta == 0,
            "private_slots_outside_focus_reserved_bank0": all(
                int(row["bank"]) != 0
                or int(row["tid"], 16) not in FOCUS_RESERVED_BANK0
                for row in private_updates
            ),
            "private_slots_focus_state_safe": (
                slot_liveness["selected_tids"]
                == [row["tid"] for row in private_updates]
            ),
            "private_entries_preserve_palette_and_flips": all(
                (row["old_entry"] & 0xDE00) == (row["new_entry"] & 0xDE00)
                for row in patches
            ),
            "all_changes_within_contract": not outside_allowed,
        },
    }
    report_path = args.out_dir / "static_bg_focus_exact_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.out_dir / "private_tilemap_patches.json").write_text(
        json.dumps(
            {
                "note": "These patches are applied by the in-ROM guarded renderer wrapper.",
                "patches": patches,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.out_dir / "README.md").write_text(
        "# Static BG focus-exact intermission candidate\n\n"
        "This test ROM combines the clean16 transition overlay with a rebuilt live "
        "`0x544400..0x54B780` steady-state atlas. The lower twelve labels copy the "
        "approved focus-sprite geometry and coordinates. Shared static cells are "
        "split into bank-1 slots proven unused and invariant across all focus "
        "source/test states. An eight-anchor in-ROM wrapper uploads their payloads "
        "before retargeting the map; no Lua memory patch is required. The main TIP "
        "is not modified.\n",
        encoding="utf-8",
    )
    print(f"ROM       : {rom_out}")
    print(f"SHA-256   : {report['candidate_rom_sha256']}")
    print(f"cells     : {len(changed_cells)}")
    print(f"atlas     : {len(atlas_updates)} tiles")
    print(f"private   : {len(private_updates)} tiles / {len(patches)} map patches")
    print(f"wrapper   : {len(wrapper)} bytes at {WRAPPER_ROM:06X}")
    print(f"report    : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
