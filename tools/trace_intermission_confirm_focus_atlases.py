#!/usr/bin/env python3
"""Map all twelve confirmation-focus sprite plates back to bank-54 ROM tiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from monoeye_rom import stock_base  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    FOCUS_ATTR,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    compact_runs,
    parse_sprites,
    render_cluster,
    sha256,
)

NAMES = (
    "mission_status", "scouting", "advance",
    "supply", "list", "assignment",
    "development_plan", "remodel", "disassemble",
    "save", "load", "library",
)


def all_hits(haystack: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        hit = haystack.find(needle, start)
        if hit < 0:
            return out
        out.append(hit)
        start = hit + 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--state-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--prefix", default="confirm_focus_atlas_")
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument("--scale", type=int, default=6)
    args = ap.parse_args()

    rom = args.rom.read_bytes()
    base = stock_base(rom)
    zstd = Zstd(args.zstd_dll)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = {"rom": str(args.rom), "rom_sha256": sha256(rom), "targets": []}
    for name in NAMES:
        state = args.state_dir / f"{args.prefix}{name}_confirm.State"
        if not state.exists():
            raise SystemExit(f"missing: {state}")
        core, _ = read_state_core(state, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        focus = [
            sprite
            for sprite in parse_sprites(core, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET)
            if sprite["attr"] == FOCUS_ATTR
        ]
        if not focus:
            raise RuntimeError(f"{name}: no attr={FOCUS_ATTR:02X} sprites")
        preview, bounds = render_cluster(ram, focus, args.scale)
        preview_path = args.out_dir / f"{name}.png"
        preview.save(preview_path)

        rows = []
        preferred = []
        unresolved = 0
        for sprite in focus:
            off = sprite["wsram_offset"]
            raw = bytes(ram[off : off + 0x20])
            normalized = [
                hit - base
                for hit in all_hits(rom, raw)
                if base <= hit < base + 0x800000
            ]
            bank54 = [hit for hit in normalized if 0x540000 <= hit < 0x550000]
            source = bank54[0] if len(bank54) == 1 else None
            if source is None:
                unresolved += 1
            else:
                preferred.append(source)
            rows.append(
                {
                    **sprite,
                    "tile": f"{sprite['tile']:03X}",
                    "attr": f"{sprite['attr']:02X}",
                    "wsram_offset": f"{off:04X}",
                    "rom_source": f"{source:06X}" if source is not None else None,
                    "bank54_exact_hits": [f"{hit:06X}" for hit in bank54],
                    "tile_sha256": sha256(raw),
                }
            )
        target = {
            "name": name,
            "state": str(state),
            "state_sha256": sha256(state.read_bytes()),
            "focus_sprite_count": len(focus),
            "focus_bounds_xyxy": bounds,
            "unique_source_tiles": len(set(preferred)),
            "unresolved_sprites": unresolved,
            "source_runs": compact_runs(preferred),
            "preview": str(preview_path),
            "sprites": rows,
        }
        report["targets"].append(target)
        print(
            name, len(focus), bounds,
            "unique", len(set(preferred)), "unresolved", unresolved,
            target["source_runs"],
        )

    report_path = args.out_dir / "confirm_focus_atlas_trace.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
