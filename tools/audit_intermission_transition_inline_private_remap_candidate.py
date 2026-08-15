#!/usr/bin/env python3
"""Audit the conservative QuickSave5 intermission transition remapper."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from trace_intermission_focus_sprites import WSRAM_BYTES, WSRAM_CORE_OFFSET  # noqa: E402

PATCH = ROOT / "out/patch"
CANDIDATE_DIR = PATCH / "intermission_transition_inline_private_remap_candidate"
RUNTIME_DIR = CANDIDATE_DIR / "runtime_frames"
FOCUS_DIR = CANDIDATE_DIR / "focus_sweep"
FOCUS_STATE_DIR = CANDIDATE_DIR / "focus_states"
BASELINE_RUNTIME_DIR = PATCH / "intermission_transition_live_trace_current/long_frames"
BASELINE_STATE_DIR = PATCH / "intermission_transition_live_trace_current/dynamic_write_states"
SOURCE_REPORT = PATCH / "intermission_advance_left_residue_clear_build_report.json"
BUILD_REPORT = CANDIDATE_DIR / "build_report.json"
ROM = CANDIDATE_DIR / "intermission_transition_inline_private_remap_candidate.wsc"
SAVE = ROOT / "sram/intermission_transition_inline_private_remap_candidate.sav"
REPORT = CANDIDATE_DIR / "runtime_audit.json"
ZSTD_DLL = ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll"

EXPECTED_ROM_SHA256 = "48320a9336346bf6c6b230b7199426197a7a6321a16d4caed9989aa29c6d9c13"
EXPECTED_WRAPPER_SHA256 = "3c756fc5e099939d79979d73d0e17c669eff41977b4f5ebbe39179168d4f9ae1"
STATE_FRAMES = (1848, 1849, 1850, 1851, 1863, 1866, 1875)
EXPECTED_BASELINE_MISMATCHES = {
    1848: 25,
    1849: 34,
    1850: 35,
    1851: 0,
    1863: 0,
    1866: 25,
    1875: 34,
}
FOCUS_NAMES = (
    "mission_status",
    "scouting",
    "advance",
    "supply",
    "list",
    "assignment",
    "development_plan",
    "remodel",
    "disassemble",
    "save",
    "load",
    "library",
)
FOCUS_PHASE_BOXES = {
    "supply": (88, 28, 144, 64),
    "development_plan": (40, 76, 144, 124),
}
FRAME_PATTERN = re.compile(r"_(\d+)_f(\d+)\.png$")
LOG_PATTERN = re.compile(
    r"^FRAME .*? emu=(\d+) map0=(\d+).*?3856=([0-9A-F]{4}) "
    r"3C5C=([0-9A-F]{4}) 3C60=([0-9A-F]{4})",
    flags=re.MULTILINE,
)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def framebuffer(path: Path) -> Image.Image:
    result = Image.open(path).convert("RGB")
    if result.size != (224, 144):
        raise RuntimeError(f"unexpected framebuffer size: {path}: {result.size}")
    return result


def changed_pixels(left: Image.Image, right: Image.Image) -> tuple[int, list[int] | None]:
    difference = ImageChops.difference(left, right)
    count = sum(pixel != (0, 0, 0) for pixel in difference.get_flattened_data())
    box = difference.getbbox()
    return count, list(box) if box else None


def changed_outside_box(left: Image.Image, right: Image.Image, box: tuple[int, int, int, int]) -> int:
    difference = ImageChops.difference(left, right)
    x0, y0, x1, y1 = box
    return sum(
        difference.getpixel((x, y)) != (0, 0, 0)
        for y in range(144)
        for x in range(224)
        if not (x0 <= x < x1 and y0 <= y < y1)
    )


def palette_partition(source: Image.Image, box: tuple[int, int, int, int]) -> list[int]:
    mapping: dict[tuple[int, int, int], int] = {}
    result = []
    for pixel in source.crop(box).get_flattened_data():
        if pixel not in mapping:
            mapping[pixel] = len(mapping)
        result.append(mapping[pixel])
    return result


def black_mask(source: Image.Image, box: tuple[int, int, int, int]) -> list[bool]:
    return [pixel == (0, 0, 0) for pixel in source.crop(box).get_flattened_data()]


def frame_files(directory: Path, tag: str) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for path in directory.glob(f"{tag}_*_f*.png"):
        match = FRAME_PATTERN.search(path.name)
        if match:
            result[int(match.group(2))] = path
    return result


def state_file(directory: Path, tag: str, frame: int) -> Path:
    matches = list(directory.glob(f"{tag}_*_f{frame}.State"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {tag} state for frame {frame}, got {matches}")
    return matches[0]


def state_ram(path: Path, zstd: Zstd) -> bytes:
    core, _ = read_state_core(path, zstd)
    return core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]


def map_mismatches(ram: bytes, patches: list[dict]) -> list[dict]:
    rows = []
    for patch in patches:
        offset = int(patch["wsram_offset"])
        actual = int.from_bytes(ram[offset : offset + 2], "little")
        expected = int(patch["new_entry"])
        if actual != expected:
            rows.append(
                {
                    "wsram_offset": f"{offset:04X}",
                    "expected": f"{expected:04X}",
                    "actual": f"{actual:04X}",
                }
            )
    return rows


def anchors_match(ram: bytes, anchors: list[dict]) -> bool:
    return all(
        int.from_bytes(
            ram[int(row["wsram_offset"]) : int(row["wsram_offset"]) + 2], "little"
        )
        == int(row["entry"])
        for row in anchors
    )


def main() -> int:
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    source = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    patches = source["static_atlas"]["tilemap_patches"]
    anchors = source["renderer_wrapper"]["guard_anchors"]
    zstd = Zstd(ZSTD_DLL)

    candidate_states = {}
    baseline_states = {}
    for frame in STATE_FRAMES:
        candidate_ram = state_ram(state_file(RUNTIME_DIR, "candidate", frame), zstd)
        baseline_ram = state_ram(state_file(BASELINE_STATE_DIR, "current", frame), zstd)
        candidate_mismatches = map_mismatches(candidate_ram, patches)
        baseline_mismatches = map_mismatches(baseline_ram, patches)
        candidate_states[frame] = {
            "private_entry_mismatches": candidate_mismatches,
            "all_eight_anchors_match": anchors_match(candidate_ram, anchors),
        }
        baseline_states[frame] = {
            "private_entry_mismatch_count": len(baseline_mismatches),
            "all_eight_anchors_match": anchors_match(baseline_ram, anchors),
        }

    runtime_log = (RUNTIME_DIR / "candidate.log").read_text(encoding="utf-8")
    log_rows = {
        int(match.group(1)): {
            "map_changes": int(match.group(2)),
            "probes": [match.group(3), match.group(4), match.group(5)],
        }
        for match in LOG_PATTERN.finditer(runtime_log)
    }
    visible_rows = {frame: log_rows[frame] for frame in range(1848, 2001)}

    candidate_frames = frame_files(RUNTIME_DIR, "candidate")
    baseline_frames = frame_files(BASELINE_RUNTIME_DIR, "current")
    clean_transition_reference = digest_file(baseline_frames[1845])
    early_visible_hashes = {
        frame: digest_file(candidate_frames[frame]) for frame in (1848, 1849, 1850, 1851)
    }
    baseline_hashes = {digest_file(path) for path in baseline_frames.values()}
    settled_hash = digest_file(candidate_frames[2000])

    focus_log = (FOCUS_DIR / "focus_sweep.log").read_text(encoding="utf-8")
    focus_rows = {}
    for name in FOCUS_NAMES:
        match = re.search(
            rf"^{re.escape(name)} .*LOAD=(true|false) SHOT=(true|false) SAVE=(true|false)",
            focus_log,
            flags=re.MULTILINE,
        )
        candidate_image = framebuffer(FOCUS_DIR / f"{name}.png")
        baseline_image = framebuffer(
            PATCH / "intermission_transition_live_trace_current/focus_sweep" / f"{name}.png"
        )
        whole_count, difference_box = changed_pixels(candidate_image, baseline_image)
        phase_box = FOCUS_PHASE_BOXES.get(name)
        focus_rows[name] = {
            "load": bool(match and match.group(1) == "true"),
            "shot": bool(match and match.group(2) == "true"),
            "save": bool(match and match.group(3) == "true"),
            "whole_screen_changed_pixels": whole_count,
            "difference_bbox": difference_box,
            "allowed_focus_phase_box": list(phase_box) if phase_box else None,
            "changed_pixels_outside_allowed_focus_phase": (
                changed_outside_box(candidate_image, baseline_image, phase_box)
                if phase_box
                else whole_count
            ),
            "focus_phase_palette_partition_exact": (
                palette_partition(candidate_image, phase_box)
                == palette_partition(baseline_image, phase_box)
                if phase_box
                else whole_count == 0
            ),
            "focus_phase_black_pixel_mask_exact": (
                black_mask(candidate_image, phase_box)
                == black_mask(baseline_image, phase_box)
                if phase_box
                else whole_count == 0
            ),
        }

    baseline_counts = {
        frame: row["private_entry_mismatch_count"] for frame, row in baseline_states.items()
    }
    checks = {
        "builder_report_passed": bool(build.get("ok")) and all(build["checks"].values()),
        "candidate_rom_hash_bound": digest_file(ROM) == EXPECTED_ROM_SHA256,
        "candidate_saveram_matches_build_snapshot": digest_file(SAVE)
        == build["candidate_saveram"]["sha256"],
        "existing_final_wrapper_is_parent_byte_identical": build["existing_wrapper"][
            "byte_identical_to_parent"
        ]
        and build["existing_wrapper"]["sha256"] == EXPECTED_WRAPPER_SHA256,
        "all_candidate_state_snapshots_have_all_35_private_entries": all(
            not row["private_entry_mismatches"] for row in candidate_states.values()
        ),
        "all_candidate_state_snapshots_match_all_eight_anchors": all(
            row["all_eight_anchors_match"] for row in candidate_states.values()
        ),
        "restored_main_reproduces_exact_25_34_35_overwrite_sequence_and_recurrence": baseline_counts
        == EXPECTED_BASELINE_MISMATCHES,
        "runtime_frame_coverage_1818_through_2000": set(candidate_frames)
        == set(range(1818, 2001)),
        "quicksave5_loaded_and_trace_completed": "LOAD=true" in runtime_log
        and "DONE frame=2001" in runtime_log,
        "every_visible_frame_logged": set(visible_rows) == set(range(1848, 2001)),
        "tilemap_has_zero_changes_from_first_visible_frame_through_2000": all(
            row["map_changes"] == 0 for row in visible_rows.values()
        ),
        "key_private_entries_stay_exact_from_first_visible_frame_through_2000": all(
            row["probes"] == ["00E8", "06E9", "06EA"] for row in visible_rows.values()
        ),
        "frames_1848_through_1851_equal_pre_overwrite_clean_wrapper_raster": all(
            value == clean_transition_reference for value in early_visible_hashes.values()
        ),
        "settled_candidate_raster_occurs_exactly_in_restored_main": settled_hash
        in baseline_hashes,
        "all_12_focus_replays_loaded_captured_and_saved": all(
            all((row["load"], row["shot"], row["save"])) for row in focus_rows.values()
        ),
        "all_12_focus_pngs_and_states_exist": all(
            (FOCUS_DIR / f"{name}.png").is_file()
            and (FOCUS_STATE_DIR / f"{name}.State").is_file()
            for name in FOCUS_NAMES
        ),
        "all_focus_differences_are_confined_to_selected_focus_phase": all(
            row["changed_pixels_outside_allowed_focus_phase"] == 0
            for row in focus_rows.values()
        ),
        "all_focus_black_pixel_topologies_are_exact_despite_palette_phase": all(
            row["focus_phase_black_pixel_mask_exact"] for row in focus_rows.values()
        ),
        "ten_focus_screens_are_full_frame_byte_exact": sum(
            row["whole_screen_changed_pixels"] == 0 for row in focus_rows.values()
        )
        == 10,
    }
    if not all(checks.values()):
        raise RuntimeError(f"runtime audit failed: {checks}; focus={focus_rows}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/audit_intermission_transition_inline_private_remap_candidate.py",
        "ok": True,
        "fixture_policy": (
            "The unmodified user-requested QuickSave5 pre-intermission fixture was used; "
            "no playthrough from the beginning was performed."
        ),
        "candidate_rom": {
            "path": relative(ROM),
            "sha256": digest_file(ROM),
        },
        "candidate_saveram": {
            "path": relative(SAVE),
            "sha256": digest_file(SAVE),
        },
        "transition": {
            "captured_frames": [1818, 2000],
            "first_visible_frame": 1848,
            "candidate_state_checks": candidate_states,
            "restored_main_private_entry_mismatch_counts": baseline_counts,
            "early_visible_frame_hashes": early_visible_hashes,
            "clean_pre_overwrite_reference_hash": clean_transition_reference,
            "settled_candidate_hash": settled_hash,
        },
        "focus_replay": {
            "allowed_focus_phase_boxes_xyxy": {
                name: list(box) for name, box in FOCUS_PHASE_BOXES.items()
            },
            "cases": focus_rows,
        },
        "checks": checks,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
