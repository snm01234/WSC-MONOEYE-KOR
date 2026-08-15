#!/usr/bin/env python3
"""Independently audit the self-contained static intermission BG candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_intermission_static_full_cleanup_candidate as common  # noqa: E402
from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base  # noqa: E402
from trace_intermission_focus_sprites import WSRAM_BYTES, WSRAM_CORE_OFFSET  # noqa: E402

EXPECTED_BASE_SHA = "2dcfb253b0488182ce061df7b4396918564e6049c31ecdce0d1a9f2a4dd834d7"
EXPECTED_CANDIDATE_SHA = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"
EXPECTED_SAV_SHA = "3866db7f9e03af72b666af3b89bd2f18c4e2e4ba3e7b3189eac857697ff537dd"
EXPECTED_RUNTIME_STATE_SHA = "78d358507d26529ec6d65eaacf3bcf376a8f5ae5c408a3b5071ad49698ac61c6"
EXPECTED_INITIAL_SCREEN_SHA = "886aa7dea481fdb2ac2b36f30e56085261c482daa52d863667fbcf82f83f22fe"
EXPECTED_FINAL_SCREEN_SHA = "9eb8dedf40bc4b7d68a4495f4ecf772514710d97740101921e6013d4a510cd4b"

TILE_BYTES = 0x20
TILEMAP_BASE = 0x3800
TILEMAP_BASES = (0x1800, 0x3000, 0x3800)
ATLAS_LO = 0x544400
ATLAS_HI = 0x54B780
FOCUS_ATLAS_LO = 0x542000
FOCUS_ATLAS_HI = 0x544400
CONFIRM_ATLAS_LO = 0x547CFC
CONFIRM_ATLAS_HI = 0x549A1C
TRANSITION_LO = 0x54B780
TRANSITION_HI = 0x550000
RUNTIME_HOOK_LO = 0x7A0600
RUNTIME_HOOK_HI = 0x7A1000
RENDER_FINAL_CALL = 0x789C4D
ORIGINAL_FINAL_CALL = bytes.fromhex("9A B5 DE 00 80")
WRAPPER_ROM = 0x78FCD3
WRAPPER_LIMIT = 0x790000
PRIVATE_PAYLOAD_ROM = 0x79FA8F
PRIVATE_PAYLOAD_LIMIT = 0x7A0000
PRIVATE_PAYLOAD_SEGMENT = 0x9000
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
ADVANCE_LEFT_CLEAR_PAD = 4


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def entry_at(ram: bytes, col: int, row: int, base: int = TILEMAP_BASE) -> int:
    off = base + (row * 32 + col) * 2
    return int.from_bytes(ram[off : off + 2], "little")


def slot_referenced(ram: bytes, bank: int, tid: int) -> bool:
    for base in TILEMAP_BASES:
        for map_row in range(32):
            for col in range(32):
                entry = entry_at(ram, col, map_row, base)
                entry_bank = 1 if entry & 0x2000 else 0
                if entry_bank == bank and (entry & 0x01FF) == tid:
                    return True
    return False


def raw_at(ram: bytes, col: int, row: int) -> bytes:
    value = entry_at(ram, col, row)
    tid = value & 0x01FF
    gfx = 0x8000 if value & 0x2000 else 0x4000
    return bytes(ram[gfx + tid * TILE_BYTES : gfx + (tid + 1) * TILE_BYTES])


def screen_from_ram(ram: bytes) -> list[list[int]]:
    screen = [[0] * common.SCREEN_W for _ in range(common.SCREEN_H)]
    for row in range(18):
        for col in range(28):
            value = entry_at(ram, col, row)
            stored = common.decode_tile(raw_at(ram, col, row))
            for y in range(8):
                sy = 7 - y if value & 0x8000 else y
                for x in range(8):
                    sx = 7 - x if value & 0x4000 else x
                    screen[row * 8 + y][col * 8 + x] = stored[sy][sx]
    return screen


def inverse_orient(grid: list[list[int]], value: int) -> list[list[int]]:
    stored = [[0] * 8 for _ in range(8)]
    for y in range(8):
        sy = 7 - y if value & 0x8000 else y
        for x in range(8):
            sx = 7 - x if value & 0x4000 else x
            stored[sy][sx] = grid[y][x]
    return stored


def raws_for_screen(
    screen: list[list[int]], map_ram: bytes
) -> dict[tuple[int, int], bytes]:
    rows = {}
    for row in range(18):
        for col in range(28):
            visible = [
                screen[row * 8 + y][col * 8 : col * 8 + 8] for y in range(8)
            ]
            rows[(col, row)] = common.encode_tile(
                inverse_orient(visible, entry_at(map_ram, col, row))
            )
    return rows


def compose_expected(
    before: list[list[int]],
    desired: list[list[int]],
    full_report: dict,
) -> tuple[list[list[int]], set[tuple[int, int]], list[dict]]:
    labels = full_report.get("labels") or []
    if len(labels) != 16:
        raise RuntimeError(f"full rebuild report labels={len(labels)}, expected 16")
    after = [line[:] for line in before]
    approved: set[tuple[int, int]] = set()
    rectangles = []
    for row in labels:
        name = str(row["name"])
        left, top, right, bottom = (
            int(value) for value in row["clean_core_bbox_xyxy"]
        )
        core = (left, top, right, bottom)
        if name in TOP_COLOR_PRESERVE_LABELS:
            if name == "advance":
                copy = (
                    max(0, left - ADVANCE_LEFT_CLEAR_PAD),
                    top,
                    right,
                    bottom,
                )
            else:
                copy = core
        else:
            copy = (
                max(0, left - STATIC_CLEAN_MARGIN),
                max(0, top - STATIC_CLEAN_MARGIN),
                min(common.SCREEN_W, right + STATIC_CLEAN_MARGIN),
                min(common.SCREEN_H, bottom + STATIC_CLEAN_MARGIN),
            )
        points = {
            (x, y)
            for y in range(copy[1], copy[3])
            for x in range(copy[0], copy[2])
        }
        approved |= points
        for x, y in points:
            in_core = left <= x < right and top <= y < bottom
            if name in TOP_COLOR_PRESERVE_LABELS:
                if in_core and desired[y][x] != 0:
                    value = desired[y][x]
                    if name in STATIC_YELLOW_LABELS:
                        value = STATIC_YELLOW_REMAP.get(value, value)
                else:
                    value = 0
            else:
                value = desired[y][x]
            after[y][x] = value
        rectangles.append(
            {
                "name": name,
                "core": core,
                "copy": copy,
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
    return after, approved, rectangles


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    out = ROOT / "out/patch/intermission_static_bg_focus_exact_candidate"
    full = ROOT / "out/patch/intermission_full_rebuild_clean16_candidate"
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=full / "intermission_full_rebuild_clean16_candidate.wsc",
    )
    ap.add_argument(
        "--candidate-rom",
        type=Path,
        default=out / "intermission_static_bg_focus_exact_candidate.wsc",
    )
    ap.add_argument(
        "--candidate-sav",
        type=Path,
        default=ROOT / "sram/intermission_static_bg_focus_exact_candidate.sav",
    )
    ap.add_argument(
        "--builder-report",
        type=Path,
        default=out / "static_bg_focus_exact_report.json",
    )
    ap.add_argument(
        "--full-report",
        type=Path,
        default=full / "full_rebuild_report.json",
    )
    ap.add_argument(
        "--focus-report",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json",
    )
    ap.add_argument(
        "--focus-sweep-report",
        type=Path,
        default=out / "focus_sweep_report.json",
    )
    ap.add_argument(
        "--map-state",
        type=Path,
        default=full
        / "runtime_static_probe/states/full_rebuild_reload_final.State",
    )
    ap.add_argument(
        "--desired-state",
        type=Path,
        default=full / "states/Mednafen.QuickSave1.State",
    )
    ap.add_argument(
        "--runtime-state",
        type=Path,
        default=out / "states/natural_reload_final.State",
    )
    ap.add_argument(
        "--initial-screen",
        type=Path,
        default=out / "captures/natural_reload/natural_reload_s00.png",
    )
    ap.add_argument(
        "--final-screen",
        type=Path,
        default=out / "captures/natural_reload/natural_reload_s04.png",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=out / "independent_audit.json",
    )
    args = ap.parse_args(argv)

    for path in (
        args.base_rom,
        args.candidate_rom,
        args.candidate_sav,
        args.builder_report,
        args.full_report,
        args.focus_report,
        args.focus_sweep_report,
        args.map_state,
        args.desired_state,
        args.runtime_state,
        args.initial_screen,
        args.final_screen,
        args.zstd_dll,
    ):
        if not path.is_file():
            raise SystemExit(f"missing: {path}")

    base_rom = args.base_rom.read_bytes()
    candidate = args.candidate_rom.read_bytes()
    base = stock_base(candidate)
    base_body = base_rom[base : base + 0x800000]
    body = candidate[base : base + 0x800000]
    builder = json.loads(args.builder_report.read_text(encoding="utf-8"))
    full_report = json.loads(args.full_report.read_text(encoding="utf-8"))
    focus_report = json.loads(args.focus_report.read_text(encoding="utf-8"))
    focus_sweep_report = json.loads(
        args.focus_sweep_report.read_text(encoding="utf-8")
    )
    wrapper_report = builder["renderer_wrapper"]
    wrapper_bytes = int(wrapper_report["wrapper_bytes"])
    payload_bytes = int(wrapper_report["private_payload_bytes"])

    diff = [
        index for index, (old, new) in enumerate(zip(base_rom, candidate)) if old != new
    ]
    allowed = {len(candidate) - 2, len(candidate) - 1}
    allowed.update(range(base + ATLAS_LO, base + ATLAS_HI))
    allowed.update(range(base + RENDER_FINAL_CALL, base + RENDER_FINAL_CALL + 5))
    allowed.update(range(base + WRAPPER_ROM, base + WRAPPER_ROM + wrapper_bytes))
    allowed.update(
        range(
            base + PRIVATE_PAYLOAD_ROM,
            base + PRIVATE_PAYLOAD_ROM + payload_bytes,
        )
    )
    outside_allowed = [offset for offset in diff if offset not in allowed]
    checksum = int.from_bytes(candidate[-2:], "little")
    computed_checksum = sum(candidate[:-2]) & 0xFFFF

    wrapper = body[WRAPPER_ROM : WRAPPER_ROM + wrapper_bytes]
    wrapper_tail = body[WRAPPER_ROM + wrapper_bytes : WRAPPER_LIMIT]
    private_payload = body[
        PRIVATE_PAYLOAD_ROM : PRIVATE_PAYLOAD_ROM + payload_bytes
    ]
    private_payload_tail = body[
        PRIVATE_PAYLOAD_ROM + payload_bytes : PRIVATE_PAYLOAD_LIMIT
    ]
    expected_hook = (
        b"\x9A"
        + (WRAPPER_ROM - 0x780000).to_bytes(2, "little")
        + bytes.fromhex("00 80")
    )

    zstd = Zstd(args.zstd_dll)
    map_core, _ = read_state_core(args.map_state, zstd)
    desired_core, _ = read_state_core(args.desired_state, zstd)
    runtime_core, _ = read_state_core(args.runtime_state, zstd)
    map_ram = map_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    desired_ram = desired_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    runtime_ram = runtime_core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
    before_screen = screen_from_ram(map_ram)
    desired_screen = screen_from_ram(desired_ram)
    runtime_screen = screen_from_ram(runtime_ram)
    expected_screen, approved_pixels, rectangles = compose_expected(
        before_screen, desired_screen, full_report
    )
    expected_raws = raws_for_screen(expected_screen, map_ram)
    before_raws = {
        (col, row): raw_at(map_ram, col, row)
        for row in range(18)
        for col in range(28)
    }
    changed_cells = {
        pos for pos in expected_raws if expected_raws[pos] != before_raws[pos]
    }
    runtime_mismatches = [
        list(pos)
        for pos in sorted(expected_raws, key=lambda p: (p[1], p[0]))
        if raw_at(runtime_ram, *pos) != expected_raws[pos]
    ]
    non_target_mismatches = [
        list(pos)
        for pos in sorted(expected_raws, key=lambda p: (p[1], p[0]))
        if pos not in changed_cells and raw_at(runtime_ram, *pos) != before_raws[pos]
    ]
    outside_pixel_changes = [
        [x, y]
        for y in range(common.SCREEN_H)
        for x in range(common.SCREEN_W)
        if before_screen[y][x] != expected_screen[y][x]
        and (x, y) not in approved_pixels
    ]
    ms_mismatches = [
        [x, y]
        for y in range(MS_PRESERVE_BOX[1], MS_PRESERVE_BOX[3])
        for x in range(MS_PRESERVE_BOX[0], MS_PRESERVE_BOX[2])
        if runtime_screen[y][x] != before_screen[y][x]
    ]
    top_rectangles = [
        rect for rect in rectangles if rect["name"] in TOP_COLOR_PRESERVE_LABELS
    ]
    # Non-glyph pixels inside each top clear window (advance includes a left JP
    # overhang pad) must stay index 0 so the 0x3000 green chrome shows through.
    top_color_mismatches = [
        [x, y]
        for rect in top_rectangles
        for y in range(rect["copy"][1], rect["copy"][3])
        for x in range(rect["copy"][0], rect["copy"][2])
        if desired_screen[y][x] == 0 and runtime_screen[y][x] != 0
    ]

    label_checks = []
    full_by_name = {str(row["name"]): row for row in full_report["labels"]}
    for rect in rectangles:
        left, top, right, bottom = rect["core"]
        mismatch = sum(
            runtime_screen[y][x] != expected_screen[y][x]
            for y in range(top, bottom)
            for x in range(left, right)
        )
        nonzero = {
            (x, y)
            for y in range(top, bottom)
            for x in range(left, right)
            if runtime_screen[y][x] != 0
        }
        expected_korean = int(full_by_name[rect["name"]]["final_korean_pixels"])
        if rect["name"] in TOP_COLOR_PRESERVE_LABELS:
            glyph_points = {
                (x, y)
                for y in range(top, bottom)
                for x in range(left, right)
                if desired_screen[y][x] != 0
            }
            glyph_ok = len(glyph_points) == expected_korean and all(
                runtime_screen[y][x] == expected_screen[y][x]
                for x, y in glyph_points
            )
            mask_ok = glyph_ok
        else:
            mask_ok = len(nonzero) == expected_korean
        label_checks.append(
            {
                "name": rect["name"],
                "core_bbox_xyxy": list(rect["core"]),
                "static_copy_bbox_xyxy": list(rect["copy"]),
                "runtime_vs_clean16_core_mismatch_pixels": mismatch,
                "runtime_nonzero_pixels_in_core": len(nonzero),
                "expected_korean_pixels": expected_korean,
                "nonzero_count_matches_korean_mask": mask_ok,
            }
        )

    lower_checks = []
    targets = focus_report.get("targets") or []
    if len(targets) != 12:
        raise RuntimeError(f"focus report targets={len(targets)}, expected 12")
    for target in targets:
        mask = common.focus_masks(target, zstd)
        palette = common.ink_palette(mask.name)
        if mask.name in STATIC_YELLOW_LABELS:
            palette = {"outline": 0x01, "fill": 0x0F}
        ko_mismatch = [
            [x, y]
            for (x, y), cls in mask.ko.items()
            if runtime_screen[y][x] != palette[cls]
        ]
        core = tuple(
            int(value)
            for value in full_by_name[mask.name]["clean_core_bbox_xyxy"]
        )
        core_nonzero = {
            (x, y)
            for y in range(core[1], core[3])
            for x in range(core[0], core[2])
            if runtime_screen[y][x] != 0
        }
        if mask.name in TOP_COLOR_PRESERVE_LABELS:
            core_equals_mask = set(mask.ko).issubset(core_nonzero) and not ko_mismatch
        else:
            core_equals_mask = core_nonzero == set(mask.ko)
        lower_checks.append(
            {
                "name": mask.name,
                "korean": mask.korean,
                "focus_origin_xy": list(mask.focus_origin),
                "focus_korean_pixels": len(mask.ko),
                "runtime_focus_pixel_mismatches": len(ko_mismatch),
                "runtime_focus_pixel_mismatch_sample": ko_mismatch[:12],
                "runtime_nonfocus_core_equals_focus_mask": core_equals_mask,
            }
        )

    private_slots = builder["static_atlas"]["private_slots"]

    def slot_vram(row: dict) -> int:
        if row.get("vram"):
            return int(row["vram"], 16)
        bank = int(row["bank"])
        tid = int(row["tid"], 16)
        return (0x8000 if bank else 0x4000) + tid * TILE_BYTES

    private_payload_by_key = {
        (int(row["bank"]), int(row["tid"], 16)): body[
            int(row["payload_rom"], 16) :
            int(row["payload_rom"], 16) + TILE_BYTES
        ]
        for row in private_slots
    }
    runtime_private_payload_mismatches = [
        {"bank": int(row["bank"]), "tid": row["tid"]}
        for row in private_slots
        if runtime_ram[slot_vram(row) : slot_vram(row) + TILE_BYTES]
        != private_payload_by_key[(int(row["bank"]), int(row["tid"], 16))]
    ]

    patch_checks = []
    for row in builder["static_atlas"]["tilemap_patches"]:
        pos = tuple(int(value) for value in row["pos"])
        old = int(row["old_entry"])
        new = int(row["new_entry"])
        actual = entry_at(runtime_ram, *pos)
        patch_checks.append(
            {
                "pos": list(pos),
                "old_entry": f"{old:04X}",
                "new_entry": f"{new:04X}",
                "runtime_entry": f"{actual:04X}",
                "runtime_exact": actual == new,
                "palette_and_flips_preserved": (old & 0xDE00) == (new & 0xDE00),
                "private_slot_bank": int(row["private_bank"]),
                "private_tid": row["private_tid"],
            }
        )

    anchor_checks = []
    for row in wrapper_report["guard_anchors"]:
        address = int(row["wsram_offset"])
        expected = int(row["entry"])
        actual = int.from_bytes(runtime_ram[address : address + 2], "little")
        anchor_checks.append(
            {
                "kind": row["kind"],
                "wsram_offset": f"{address:04X}",
                "expected": f"{expected:04X}",
                "runtime": f"{actual:04X}",
                "exact": actual == expected,
            }
        )

    fixture_rams = [map_ram]
    fixture_paths = []
    seen_fixture_paths: set[Path] = set()
    for target in targets:
        for kind in ("source_state", "test_state"):
            path = Path(target[kind]).resolve()
            if path in seen_fixture_paths:
                continue
            seen_fixture_paths.add(path)
            if not path.is_file():
                raise RuntimeError(f"missing focus liveness fixture: {path}")
            core, _ = read_state_core(path, zstd)
            fixture_rams.append(
                core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
            )
            fixture_paths.append(str(path))
    private_liveness_checks = []
    for row in private_slots:
        tid = int(row["tid"], 16)
        bank = int(row["bank"])
        vram = int(row.get("vram"), 16) if row.get("vram") else (
            (0x8000 if bank else 0x4000) + tid * TILE_BYTES
        )
        referenced = any(slot_referenced(ram, bank, tid) for ram in fixture_rams)
        variants = {
            bytes(ram[vram : vram + TILE_BYTES]) for ram in fixture_rams
        }
        private_liveness_checks.append(
            {
                "tid": row["tid"],
                "bank": bank,
                "tilemap_referenced": referenced,
                "fixture_payload_variants": len(variants),
                "safe": not referenced and len(variants) == 1,
            }
        )

    private_tids = [int(row["tid"], 16) for row in private_slots]
    expected_copy_prefix = (
        b"\xB8"
        + PRIVATE_PAYLOAD_SEGMENT.to_bytes(2, "little")
        + b"\x8E\xD8\xBE"
        + (PRIVATE_PAYLOAD_ROM & 0xFFFF).to_bytes(2, "little")
        + bytes.fromhex("FC")
        + b"\xBF"
        + slot_vram(private_slots[0]).to_bytes(2, "little")
        + b"\xB9"
        + (TILE_BYTES // 2).to_bytes(2, "little")
        + bytes.fromhex("F3 A5")
    )

    sweep_cases = focus_sweep_report.get("cases") or []
    focus_state_checks = []
    for case in sweep_cases:
        state_path = Path(case["state"])
        screenshot_path = Path(case["screenshot"])
        if not state_path.is_file() or not screenshot_path.is_file():
            raise RuntimeError(f"missing focus sweep evidence: {case['name']}")
        core, _ = read_state_core(state_path, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        cell_mismatches = [
            list(pos)
            for pos in sorted(expected_raws, key=lambda p: (p[1], p[0]))
            if raw_at(ram, *pos) != expected_raws[pos]
        ]
        payload_mismatches = [
            {"bank": int(row["bank"]), "tid": row["tid"]}
            for row in private_slots
            if ram[slot_vram(row) : slot_vram(row) + TILE_BYTES]
            != private_payload_by_key[(int(row["bank"]), int(row["tid"], 16))]
        ]
        tilemap_mismatches = [
            row["pos"]
            for row in builder["static_atlas"]["tilemap_patches"]
            if entry_at(ram, *row["pos"]) != int(row["new_entry"])
        ]
        focus_state_checks.append(
            {
                "name": str(case["name"]),
                "state": str(state_path),
                "state_sha256_matches_report": sha256_file(state_path)
                == case["state_sha256"],
                "screenshot": str(screenshot_path),
                "screenshot_sha256_matches_report": sha256_file(screenshot_path)
                == case["screenshot_sha256"],
                "background_cell_mismatches": len(cell_mismatches),
                "background_cell_mismatch_sample": cell_mismatches[:12],
                "private_payload_mismatches": payload_mismatches,
                "private_tilemap_mismatches": tilemap_mismatches[:12],
            }
        )

    checks = {
        "base_rom_hash_exact": sha256_file(args.base_rom) == EXPECTED_BASE_SHA,
        "candidate_rom_hash_exact": sha256_file(args.candidate_rom)
        == EXPECTED_CANDIDATE_SHA,
        "candidate_sav_hash_exact": sha256_file(args.candidate_sav)
        == EXPECTED_SAV_SHA,
        "candidate_sav_matches_base": args.candidate_sav.read_bytes()
        == (ROOT / "sram/monoeye_ko_expanded.sav").read_bytes(),
        "runtime_state_hash_exact": sha256_file(args.runtime_state)
        == EXPECTED_RUNTIME_STATE_SHA,
        "initial_screen_hash_exact": sha256_file(args.initial_screen)
        == EXPECTED_INITIAL_SCREEN_SHA,
        "final_screen_hash_exact": sha256_file(args.final_screen)
        == EXPECTED_FINAL_SCREEN_SHA,
        "checksum_valid": checksum == computed_checksum,
        "builder_candidate_hash_matches": builder["candidate_rom_sha256"]
        == sha256_file(args.candidate_rom),
        "focus_sweep_candidate_hash_matches": focus_sweep_report[
            "candidate_rom_sha256"
        ]
        == sha256_file(args.candidate_rom),
        "all_changes_within_static_hook_contract": not outside_allowed,
        "focus_atlas_unchanged": body[FOCUS_ATLAS_LO:FOCUS_ATLAS_HI]
        == base_body[FOCUS_ATLAS_LO:FOCUS_ATLAS_HI],
        "confirm_atlas_unchanged": body[CONFIRM_ATLAS_LO:CONFIRM_ATLAS_HI]
        == base_body[CONFIRM_ATLAS_LO:CONFIRM_ATLAS_HI],
        "transition_asset_unchanged": body[TRANSITION_LO:TRANSITION_HI]
        == base_body[TRANSITION_LO:TRANSITION_HI],
        "existing_runtime_hook_unchanged": body[RUNTIME_HOOK_LO:RUNTIME_HOOK_HI]
        == base_body[RUNTIME_HOOK_LO:RUNTIME_HOOK_HI],
        "renderer_hook_original_bound": base_body[
            RENDER_FINAL_CALL : RENDER_FINAL_CALL + 5
        ]
        == ORIGINAL_FINAL_CALL,
        "renderer_hook_points_to_wrapper": body[
            RENDER_FINAL_CALL : RENDER_FINAL_CALL + 5
        ]
        == expected_hook,
        "wrapper_hash_matches_report": sha256_bytes(wrapper)
        == wrapper_report["wrapper_sha256"],
        "wrapper_calls_original_then_retf": wrapper.startswith(ORIGINAL_FINAL_CALL)
        and wrapper.endswith(bytes.fromhex("07 1F 5F 5E 59 58 9D CB")),
        "wrapper_private_payload_copy_bound": expected_copy_prefix in wrapper,
        "unused_wrapper_tail_still_ff": all(value == 0xFF for value in wrapper_tail),
        "private_payload_hash_matches_report": sha256_bytes(private_payload)
        == wrapper_report["private_payload_sha256"],
        "unused_private_payload_tail_still_ff": all(
            value == 0xFF for value in private_payload_tail
        ),
        "guard_anchor_count_8": len(anchor_checks) == 8,
        "all_runtime_guard_anchors_exact": all(row["exact"] for row in anchor_checks),
        "private_slots_safe_selected": (
            len(private_slots) == len(private_tids)
            and builder["focus_liveness"]["selected_tids"]
            == [row["tid"] for row in private_slots]
            and all(
                int(row["bank"]) != 0 or int(row["tid"], 16) not in range(0x110, 0x134)
                for row in private_slots
            )
        ),
        "private_slots_safe_across_25_fixture_states": len(fixture_rams) == 25
        and all(row["safe"] for row in private_liveness_checks),
        "runtime_private_payload_exact": not runtime_private_payload_mismatches,
        "changed_screen_cells_match_report": len(changed_cells)
        == int(builder["static_atlas"]["changed_screen_cells"]),
        "runtime_all_visible_cells_exact": not runtime_mismatches,
        "runtime_non_target_cells_unchanged": not non_target_mismatches,
        "composition_outside_window_unchanged": not outside_pixel_changes,
        "connected_ms_preserved": not ms_mismatches,
        "top_four_core_holes_transparent": not top_color_mismatches,
        "sixteen_core_windows_exact": len(label_checks) == 16
        and all(
            row["runtime_vs_clean16_core_mismatch_pixels"] == 0
            and row["nonzero_count_matches_korean_mask"]
            for row in label_checks
        ),
        "lower_twelve_focus_pixels_and_positions_exact": len(lower_checks) == 12
        and all(
            row["runtime_focus_pixel_mismatches"] == 0
            and row["runtime_nonfocus_core_equals_focus_mask"]
            for row in lower_checks
        ),
        "all_private_tilemap_patches_runtime_exact": len(patch_checks)
        == int(builder["static_atlas"]["private_tilemap_patches"])
        and all(
            row["runtime_exact"]
            and row["palette_and_flips_preserved"]
            for row in patch_checks
        ),
        "focus_sweep_has_all_12_targets": len(focus_state_checks) == 12
        and {row["name"] for row in focus_state_checks}
        == {str(row["name"]) for row in targets},
        "focus_sweep_evidence_hashes_match": all(
            row["state_sha256_matches_report"]
            and row["screenshot_sha256_matches_report"]
            for row in focus_state_checks
        ),
        "focus_sweep_all_background_cells_exact": all(
            row["background_cell_mismatches"] == 0
            for row in focus_state_checks
        ),
        "focus_sweep_all_private_payloads_exact": all(
            not row["private_payload_mismatches"] for row in focus_state_checks
        ),
        "focus_sweep_all_private_tilemaps_exact": all(
            not row["private_tilemap_mismatches"] for row in focus_state_checks
        ),
    }
    audit = {
        "ok": all(checks.values()),
        "candidate_rom": str(args.candidate_rom),
        "candidate_rom_sha256": sha256_file(args.candidate_rom),
        "runtime_state": str(args.runtime_state),
        "runtime_state_sha256": sha256_file(args.runtime_state),
        "runtime_final_screen": str(args.final_screen),
        "runtime_final_screen_sha256": sha256_file(args.final_screen),
        "changed_bytes": len(diff),
        "outside_allowed_bytes": len(outside_allowed),
        "runtime_visible_cell_mismatches": len(runtime_mismatches),
        "runtime_non_target_cell_mismatches": len(non_target_mismatches),
        "runtime_private_payload_mismatches": runtime_private_payload_mismatches,
        "top_color_mismatches": top_color_mismatches,
        "focus_fixture_states": fixture_paths,
        "private_liveness_checks": private_liveness_checks,
        "focus_state_checks": focus_state_checks,
        "label_checks": label_checks,
        "lower_focus_checks": lower_checks,
        "tilemap_patch_checks": patch_checks,
        "guard_anchor_checks": anchor_checks,
        "checks": checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"audit     : {args.out}")
    print(f"ok        : {audit['ok']}")
    print(f"runtime   : {len(runtime_mismatches)} visible cell mismatches")
    print(
        "focus     : "
        f"{sum(row['runtime_focus_pixel_mismatches'] == 0 for row in lower_checks)}/12 exact"
    )
    print(
        "focus BG  : "
        f"{sum(row['background_cell_mismatches'] == 0 for row in focus_state_checks)}/12 exact"
    )
    if not audit["ok"]:
        failed = [name for name, passed in checks.items() if not passed]
        print(f"failed    : {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
