#!/usr/bin/env python3
"""Check palette-remapped copies of the *static* intermission label tiles.

This was an intermediate hypothesis check.  The later sprite-table trace in
``trace_intermission_focus_sprites.py`` proved that the active label is a
separate precomposited sprite plate, not a palette-remapped copy of these
background tiles.  Keep this scanner reproducible with the corrected Cygne
``Core.bin`` layout: wsRAM begins at Core offset 0x952.

The two domains intentionally represent the state of the failed A/B test:

* the Korean intermission A/B ROM, in which the direct bank-54 tiles are gone;
* the B initial Core.bin, in which those same 158 serialized tiles are Korean.

The negative result therefore applies only to the discarded palette-remapping
hypothesis; it is not evidence that the active label is generated procedurally.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_intermission_state_ab import Zstd, read_state_core  # noqa: E402
from find_screen_tile_in_rom import canon, rom_tile_4bpp  # noqa: E402


STATE_DIR = (
    ROOT
    / "BizHawk-2.11.1-win-x64/WonderSwan/State"
    / "monoeye ko expanded.Cygne"
)
AB_DIR = ROOT / "out/patch/intermission_state_ab"
DEFAULT_OUT = ROOT / "out/patch/intermission_focus_trace/palette_equiv_scan.json"

# Screen-correlated static-label wsRAM slots established from the three supplied
# states.  Earlier reports accidentally labelled Core offsets as wsRAM offsets;
# the values below subtract the real wsRAM Core base (0x952).
WSRAM_CORE_OFFSET = 0x952
WSRAM_BYTES = 0x10000
STATIC_LABEL_TILES = {
    "save": (1, [0x9700, 0x9720, 0x9740, 0x9760, 0x9780]),
    "development_plan": (
        2,
        [
            0x8920,
            0x8B20,
            0x8B40,
            0x8B60,
            0x8B80,
            0x8BA0,
            0x8BC0,
            0x8BE0,
            0x8C00,
            0x8C20,
        ],
    ),
    "supply": (3, [0x7B80, 0x7BA0, 0x7BC0]),
}


def nibbles(data: bytes) -> tuple[int, ...]:
    out: list[int] = []
    for value in data:
        out.extend((value >> 4, value & 0x0F))
    return tuple(out)


def nibble_column(data: np.ndarray, starts: np.ndarray, pixel: int) -> np.ndarray:
    values = data[starts + pixel // 2]
    return values >> 4 if pixel % 2 == 0 else values & 0x0F


def probe_positions(signature: tuple[int, ...], count: int = 28) -> list[int]:
    """Pick representatives plus spatially spread positions for a cheap filter."""
    labels = sorted(set(signature))
    chosen: list[int] = [signature.index(label) for label in labels]
    for pixel in np.linspace(0, 63, count, dtype=int).tolist():
        if pixel not in chosen:
            chosen.append(pixel)
    return chosen


def scan_signature(data: bytes, signature: tuple[int, ...], chunk: int) -> list[int]:
    arr = np.frombuffer(data, dtype=np.uint8)
    if len(arr) < 32:
        return []
    labels = sorted(set(signature))
    representative = {label: signature.index(label) for label in labels}
    probes = probe_positions(signature)
    hits: list[int] = []

    for base in range(0, len(arr) - 31, chunk):
        stop = min(len(arr) - 31, base + chunk)
        starts = np.arange(base, stop, dtype=np.int64)
        reps = {
            label: nibble_column(arr, starts, representative[label]) for label in labels
        }
        mask = np.ones(len(starts), dtype=bool)
        for left_i, left in enumerate(labels):
            for right in labels[left_i + 1 :]:
                mask &= reps[left] != reps[right]
        for pixel in probes:
            mask &= nibble_column(arr, starts, pixel) == reps[signature[pixel]]
            if not mask.any():
                break

        # The probes are only an accelerator; the canonical 64-pixel signature
        # remains the authority for every reported hit.
        for offset in starts[mask].tolist():
            if canon(nibbles(data[offset : offset + 32])) == signature:
                hits.append(offset)
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--chunk", type=int, default=1 << 20)
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    args = ap.parse_args(argv)

    zstd = Zstd(args.zstd_dll)
    focus_cores = {
        index: read_state_core(
            STATE_DIR / f"Mednafen.QuickSave{index}.State", zstd
        )[0]
        for index in (1, 2, 3)
    }
    b_core = read_state_core(AB_DIR / "B_patched_vram.State", zstd)[0]
    patched_rom = (AB_DIR / "A_intermission_ko_stock_vram.wsc").read_bytes()
    domains = {
        "b_initial_core": (b_core, 0),
        # Ignore the prepended expansion half. The stock body is where bank 54
        # and the commercial game's remaining assets live.
        "patched_rom_stock_body": (patched_rom[0x800000:], 0x800000),
    }

    targets: list[dict] = []
    for meaning, (state_index, ram_offsets) in STATIC_LABEL_TILES.items():
        ram = focus_cores[state_index][
            WSRAM_CORE_OFFSET : WSRAM_CORE_OFFSET + WSRAM_BYTES
        ]
        for ram_offset in ram_offsets:
            raw = bytes(ram[ram_offset : ram_offset + 32])
            signature = canon(rom_tile_4bpp(ram, ram_offset))
            target = {
                "meaning": meaning,
                "state": state_index,
                "ram_offset": f"{ram_offset:04X}",
                "raw": raw.hex().upper(),
                "palette_classes": len(set(signature)),
                "domains": {},
            }
            for domain_name, (domain, display_base) in domains.items():
                hits = scan_signature(domain, signature, args.chunk)
                target["domains"][domain_name] = [
                    f"{display_base + hit:07X}" for hit in hits
                ]
            targets.append(target)

    report = {
        "method": "all-byte-offset packed-4bpp palette-permutation invariant scan",
        "scope": "discarded static-label palette-remapping hypothesis",
        "wsram_core_offset": f"{WSRAM_CORE_OFFSET:06X}",
        "domains": {
            name: {"bytes": len(data), "display_base": f"{base:07X}"}
            for name, (data, base) in domains.items()
        },
        "targets": targets,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    total = sum(
        len(hits)
        for target in targets
        for hits in target["domains"].values()
    )
    print(f"targets: {len(targets)}")
    print(f"palette-equivalent hits: {total}")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
