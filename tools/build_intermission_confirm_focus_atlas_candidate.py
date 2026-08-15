#!/usr/bin/env python3
"""Patch the twelve bank-54 focus plates used only during confirmation animation."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_all_focus_clean import (  # noqa: E402
    cluster_grid,
    encode_unoriented,
    localize_grid,
)
from build_intermission_state_ab import (  # noqa: E402
    Zstd,
    read_state_core,
    write_state_with_core,
)
from build_intermission_supply_focus_ab import render_grid  # noqa: E402
from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402
from patch_intermission_labels_ko import FONT, Rasteriser, draw_strip  # noqa: E402
from trace_intermission_confirm_focus_atlases import NAMES  # noqa: E402
from trace_intermission_focus_sprites import (  # noqa: E402
    FOCUS_ATTR,
    SPRITE_COUNT_CORE_OFFSET,
    SPRITE_TABLE_CORE_OFFSET,
    WSRAM_BYTES,
    WSRAM_CORE_OFFSET,
    parse_sprites,
)

TILE_BYTES = 0x20
ATLAS_START = 0x547CFC
ATLAS_END = 0x549A1C
VRAM_TILE_START = 0x110


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=(
            ROOT
            / "out/patch/intermission_confirm_atlas_clean_candidate_16"
            / "intermission_static_full_cleanup_candidate.wsc"
        ),
    )
    ap.add_argument(
        "--base-sav",
        type=Path,
        default=(
            ROOT
            / "sram/intermission_static_full_cleanup_candidate.sav"
        ),
    )
    ap.add_argument(
        "--base-saveram",
        type=Path,
        default=(
            ROOT / "sram/intermission_static_full_cleanup_candidate.SaveRAM"
        ),
    )
    ap.add_argument(
        "--focus-report",
        type=Path,
        default=ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json",
    )
    ap.add_argument(
        "--trace-report",
        type=Path,
        default=(
            ROOT
            / "out/patch/intermission_confirm_atlas_clean_candidate_16"
            / "confirm_focus_atlas_trace/confirm_focus_atlas_trace.json"
        ),
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_confirm_atlas_clean_candidate_16_focus",
    )
    ap.add_argument("--scale", type=int, default=5)
    args = ap.parse_args()

    for path in (
        args.base_rom,
        args.base_sav,
        args.base_saveram,
        args.focus_report,
        args.trace_report,
        args.zstd_dll,
    ):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    base_rom = args.base_rom.read_bytes()
    base = stock_base(base_rom)
    focus_targets = {
        row["name"]: row
        for row in json.loads(args.focus_report.read_text(encoding="utf-8"))["targets"]
    }
    trace = json.loads(args.trace_report.read_text(encoding="utf-8"))
    traces = {row["name"]: row for row in trace["targets"]}
    if tuple(focus_targets) != NAMES or tuple(traces) != NAMES:
        raise RuntimeError("twelve-label ordering contract changed")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = args.out_dir / "previews"
    state_dir = args.out_dir / "states"
    preview_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    zstd = Zstd(args.zstd_dll)
    raster = Rasteriser(FONT, 13)

    patches: dict[int, tuple[bytes, str]] = {}
    constraints: dict[int, list[tuple[bytes, str]]] = collections.defaultdict(list)
    target_rows = []
    trace_source_checks = 0
    inferred_ambiguous_sources = []

    for name in NAMES:
        target = focus_targets[name]
        trace_target = traces[name]
        state_path = Path(trace_target["state"])
        core, core_name = read_state_core(state_path, zstd)
        ram = core[WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES]
        sprites = [
            sprite
            for sprite in parse_sprites(core, SPRITE_TABLE_CORE_OFFSET, SPRITE_COUNT_CORE_OFFSET)
            if sprite["attr"] == FOCUS_ATTR
        ]
        if len(sprites) != int(trace_target["focus_sprite_count"]):
            raise RuntimeError(f"{name}: sprite count drifted")
        original, bounds = cluster_grid(ram, sprites)
        korean_strip = draw_strip(
            target["korean"], int(target["strip_width"]), raster, 0x0F, 1, 1
        )
        origin_override = {
            "list": (1, 2),
            "load": (5, 2),
        }.get(name)
        localized, evidence = localize_grid(
            original, korean_strip, origin_override=origin_override
        )
        render_grid(original, preview_dir / f"{name}_before.png", args.scale)
        render_grid(localized, preview_dir / f"{name}_after.png", args.scale)

        trace_sprites = {int(row["index"]): row for row in trace_target["sprites"]}
        desired_by_ram: dict[int, bytes] = {}
        addresses_by_ram: dict[int, int] = {}
        positions_by_ram: dict[int, list[list[int]]] = collections.defaultdict(list)
        left, top, _, _ = bounds
        for sprite in sprites:
            ox, oy = sprite["x"] - left, sprite["y"] - top
            crop = [row[ox : ox + 8] for row in localized[oy : oy + 8]]
            desired = encode_unoriented(crop, sprite["flip_h"], sprite["flip_v"])
            trace_row = trace_sprites[int(sprite["index"])]
            traced = trace_row.get("rom_source")
            if traced is not None:
                address = int(traced, 16)
                trace_source_checks += 1
            else:
                aligned = [
                    int(hit, 16)
                    for hit in trace_row["bank54_exact_hits"]
                    if ATLAS_START <= int(hit, 16) < ATLAS_END
                    and (int(hit, 16) - ATLAS_START) % TILE_BYTES == 0
                ]
                if len(aligned) != 1:
                    raise RuntimeError(
                        f"{name}: ambiguous sprite {sprite['index']} has {len(aligned)} aligned atlas hits"
                    )
                address = aligned[0]
                inferred_ambiguous_sources.append(
                    {
                        "name": name,
                        "index": sprite["index"],
                        "address": f"{address:06X}",
                        "all_hits": trace_row["bank54_exact_hits"],
                    }
                )
            if not (ATLAS_START <= address < ATLAS_END):
                raise RuntimeError(f"{name}: traced address outside atlas {address:06X}")
            ram_off = int(sprite["wsram_offset"])
            if ram_off in desired_by_ram and desired_by_ram[ram_off] != desired:
                raise RuntimeError(f"{name}: reused live tile needs conflicting bytes")
            if ram_off in addresses_by_ram and addresses_by_ram[ram_off] != address:
                raise RuntimeError(f"{name}: live tile maps to two ROM addresses")
            desired_by_ram[ram_off] = desired
            addresses_by_ram[ram_off] = address
            positions_by_ram[ram_off].append([ox, oy])

        changed_tiles = []
        core_out = bytearray(core)
        for ram_off, desired in desired_by_ram.items():
            address = addresses_by_ram[ram_off]
            old = bytes(ram[ram_off : ram_off + TILE_BYTES])
            if base_rom[base + address : base + address + TILE_BYTES] != old:
                raise RuntimeError(f"{name}: ROM/source-state tile drift at {address:06X}")
            constraints[address].append((desired, name))
            if desired != old:
                changed_tiles.append(
                    {
                        "wsram": f"{ram_off:04X}",
                        "rom": f"{address:06X}",
                        "positions": positions_by_ram[ram_off],
                        "old_sha256": sha256_bytes(old),
                        "new_sha256": sha256_bytes(desired),
                    }
                )
            start = WSRAM_CORE_OFFSET + ram_off
            core_out[start : start + TILE_BYTES] = desired

        state_out = state_dir / f"{name}_confirm_clean.State"
        write_state_with_core(state_path, state_out, core_name, bytes(core_out), zstd, 3)
        verify, verify_name = read_state_core(state_out, zstd)
        if verify_name != core_name or verify != bytes(core_out):
            raise RuntimeError(f"{name}: state round trip failed")
        target_rows.append(
            {
                "name": name,
                "japanese": target["japanese"],
                "korean": target["korean"],
                "source_state": str(state_path),
                "clean_state": str(state_out),
                "bounds_xyxy": bounds,
                "sprite_count": len(sprites),
                "composition": evidence,
                "changed_tiles": changed_tiles,
                "before_preview": str(preview_dir / f"{name}_before.png"),
                "after_preview": str(preview_dir / f"{name}_after.png"),
            }
        )
        print(
            f"{name:18s} sprites={len(sprites):2d} changed={len(changed_tiles):2d} "
            f"jp={evidence['japanese_bbox_xyxy']} ko={evidence['korean_origin_xy']}"
        )

    merged_shared_tiles = []
    for address, demands in constraints.items():
        old = bytes(base_rom[base + address : base + address + TILE_BYTES])
        merged = bytearray(old)
        owners = sorted({owner for _, owner in demands})
        for index, old_value in enumerate(old):
            changed_values = {raw[index] for raw, _ in demands if raw[index] != old_value}
            if len(changed_values) > 1:
                raise RuntimeError(
                    f"transition tile {address:06X} byte {index:02X} has "
                    f"{len(changed_values)} incompatible non-original values from {owners}"
                )
            if changed_values:
                merged[index] = next(iter(changed_values))
        if bytes(merged) != old:
            patches[address] = (bytes(merged), "+".join(owners))
        if len({raw for raw, _ in demands}) > 1:
            merged_shared_tiles.append(
                {
                    "address": f"{address:06X}",
                    "owners": owners,
                    "demands": len(demands),
                    "merged_changed_bytes": sum(a != b for a, b in zip(old, merged)),
                }
            )

    candidate = bytearray(base_rom)
    for address, (raw, _) in patches.items():
        candidate[base + address : base + address + TILE_BYTES] = raw
    checksum = update_ws_checksum(candidate)
    rom_out = args.out_dir / "intermission_confirm_atlas_clean_candidate.wsc"
    sav_out = ROOT / "sram/intermission_confirm_atlas_clean_candidate.sav"
    saveram_out = ROOT / "sram/intermission_confirm_atlas_clean_candidate.SaveRAM"
    rom_out.write_bytes(candidate)
    shutil.copy2(args.base_sav, sav_out)
    shutil.copy2(args.base_saveram, saveram_out)

    reread = rom_out.read_bytes()
    if reread != bytes(candidate):
        raise RuntimeError("candidate ROM round trip failed")
    if (sum(reread[:-2]) & 0xFFFF) != int.from_bytes(reread[-2:], "little"):
        raise RuntimeError("candidate checksum failed")
    changed = [i for i, (left, right) in enumerate(zip(base_rom, reread)) if left != right]
    allowed = {
        base + address + delta
        for address in patches
        for delta in range(TILE_BYTES)
    }
    allowed.update((len(reread) - 2, len(reread) - 1))
    outside = [offset for offset in changed if offset not in allowed]
    if outside:
        raise RuntimeError(f"candidate changed outside allowlist at {outside[0]:07X}")

    report = {
        "purpose": "twelve confirmation-animation focus plates localized in the third bank-54 atlas",
        "base_rom": str(args.base_rom),
        "base_rom_sha256": sha256_bytes(base_rom),
        "candidate_rom": str(rom_out),
        "candidate_rom_sha256": sha256_file(rom_out),
        "candidate_sav": str(sav_out),
        "candidate_saveram": str(saveram_out),
        "checksum": f"{checksum:04X}",
        "atlas": {
            "start": f"{ATLAS_START:06X}",
            "end_exclusive": f"{ATLAS_END:06X}",
            "vram_tile_start": f"{VRAM_TILE_START:03X}",
        },
        "labels": target_rows,
        "unique_rom_tiles_patched": len(patches),
        "rom_changed_bytes_including_checksum": len(changed),
        "trace_source_checks": trace_source_checks,
        "inferred_ambiguous_sources": inferred_ambiguous_sources,
        "merged_shared_tiles": merged_shared_tiles,
        "verification": {
            "all_12_labels_processed": len(target_rows) == 12,
            "all_japanese_fill_removed": all(
                row["composition"]["orphan_fill_pixels"] == 0 for row in target_rows
            ),
            "all_changes_allowlisted": not outside,
            "full_16_label_asset_preserved": (
                base_rom[base + 0x54B780 : base + 0x54E7D3]
                == reread[base + 0x54B780 : base + 0x54E7D3]
            ),
            "focus_atlas_542000_544400_preserved": (
                base_rom[base + 0x542000 : base + 0x544400]
                == reread[base + 0x542000 : base + 0x544400]
            ),
            "saveram_byte_identical": (
                args.base_sav.read_bytes() == sav_out.read_bytes()
                and args.base_saveram.read_bytes() == saveram_out.read_bytes()
            ),
        },
    }
    report_path = args.out_dir / "confirm_focus_atlas_candidate_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    readme = args.out_dir / "README.md"
    readme.write_text(
        "# Intermission confirmation atlas clean candidate\n\n"
        "This test ROM includes the clean 16-label full-screen transition asset and "
        "localizes the separate 12-label highlighted sprite atlas loaded after A is pressed.\n\n"
        "The main TIP is not modified. Use the matching `.sav` when testing.\n",
        encoding="utf-8",
    )
    print(f"ROM tiles : {len(patches)}")
    print(f"ROM bytes : {len(changed)}")
    print(f"checksum  : {checksum:04X}")
    print(f"ROM       : {rom_out}")
    print(f"report    : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
