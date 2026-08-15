#!/usr/bin/env python3
"""Trace all twelve focusable intermission leaf labels from captured states."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    FOCUS_ATTR,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    all_hits,
    compact_runs,
    parse_sprites,
    render_cluster,
)


TARGETS = [
    ("mission_status", "임무/전황", ROOT / "out/patch/intermission_focus_sweep/input_probe/button_X1.State"),
    ("scouting", "색적", ROOT / "out/patch/intermission_focus_sweep/input_probe_top/button_X2.State"),
    ("advance", "진격", ROOT / "out/patch/intermission_focus_sweep/input_probe_top/button_X4.State"),
    (
        "supply",
        "보급",
        ROOT
        / "BizHawk-2.11.1-win-x64/WonderSwan/State"
        / "monoeye ko expanded.Cygne/Mednafen.QuickSave3.State",
    ),
    ("list", "목록", ROOT / "out/patch/intermission_focus_sweep/input_probe/button_X2.State"),
    ("assignment", "배속", ROOT / "out/patch/intermission_focus_sweep/input_probe/button_X4.State"),
    (
        "development_plan",
        "개발 플랜",
        ROOT
        / "BizHawk-2.11.1-win-x64/WonderSwan/State"
        / "monoeye ko expanded.Cygne/Mednafen.QuickSave2.State",
    ),
    ("remodel", "개조", ROOT / "out/patch/intermission_focus_sweep/input_probe_q2/button_X2.State"),
    ("disassemble", "분해", ROOT / "out/patch/intermission_focus_sweep/input_probe_q2/button_X4.State"),
    (
        "save",
        "세이브",
        ROOT
        / "BizHawk-2.11.1-win-x64/WonderSwan/State"
        / "monoeye ko expanded.Cygne/Mednafen.QuickSave1.State",
    ),
    ("load", "로드", ROOT / "out/patch/intermission_focus_sweep/input_probe_q1/button_X2.State"),
    ("library", "도감", ROOT / "out/patch/intermission_focus_sweep/input_probe_q1/button_X4.State"),
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_trace",
    )
    ap.add_argument("--scale", type=int, default=5)
    args = ap.parse_args(argv)

    rom = args.rom.read_bytes()
    zstd = Zstd(args.zstd_dll)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = {"rom": str(args.rom), "rom_sha256": sha256(rom), "targets": []}
    for name, korean, state in TARGETS:
        core, _ = read_state_core(state, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        current = parse_sprites(core, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET)
        focus = [sprite for sprite in current if sprite["attr"] == FOCUS_ATTR]
        preview, bounds = render_cluster(ram, focus, args.scale)
        preview_path = args.out_dir / f"{name}.png"
        preview.save(preview_path)

        rows = []
        unique_bank54 = []
        for sprite in focus:
            off = sprite["wsram_offset"]
            raw = bytes(ram[off : off + 0x20])
            hits = all_hits(rom, raw)
            bank54 = [hit for hit in hits if 0x540000 <= hit < 0x550000]
            if len(bank54) == 1:
                unique_bank54.append(bank54[0])
            rows.append(
                {
                    "index": sprite["index"],
                    "tile": f"{sprite['tile']:03X}",
                    "x": sprite["x"],
                    "y": sprite["y"],
                    "wsram_offset": f"{off:04X}",
                    "rom_bank54_hits": [f"{hit:06X}" for hit in bank54],
                    "raw_sha256": sha256(raw),
                }
            )
        report["targets"].append(
            {
                "name": name,
                "korean": korean,
                "state": str(state),
                "preview": str(preview_path),
                "focus_sprite_count": len(focus),
                "bounds_xyxy": bounds,
                "unique_bank54_source_runs": compact_runs(unique_bank54),
                "sprites": rows,
            }
        )
        print(f"{name:18s} sprites={len(focus):2d} bounds={bounds} -> {preview_path.name}")

    path = args.out_dir / "all_focus_trace.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
