#!/usr/bin/env python3
"""Scan BizHawk Cygne savestate Core.bin for Hangul tag / index patterns.

Cygne Lua domains are ROM/SRAM/iEEPROM only, so WRAM 1A6E / flag 19FF cannot be
read live. Savestates still embed WRAM; this heuristic searches for clusters of
glyph indices in [0x820,0x820+N) and tagged (bit15) forms.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import zipfile
import zstandard
from pathlib import Path

HANGUL_LO = 0x820
HANGUL_COUNT = 96
HANGUL_HI = HANGUL_LO + HANGUL_COUNT


def load_core_bin(state_path: Path) -> bytes | None:
    raw = state_path.read_bytes()
    # BizHawk .State is often a zip; Core.bin may be zstd-compressed.
    if raw[:2] == b"PK":
        with zipfile.ZipFile(state_path) as zf:
            names = zf.namelist()
            for cand in ("Core.bin.zst", "Core.bin"):
                if cand in names:
                    data = zf.read(cand)
                    if cand.endswith(".zst"):
                        return zstandard.ZstdDecompressor().decompress(data)
                    return data
            # fallback: any Core*
            for name in names:
                if "Core" in name:
                    data = zf.read(name)
                    if name.endswith(".zst"):
                        return zstandard.ZstdDecompressor().decompress(data)
                    return data
        return None
    if raw[:4] == b"\x28\xb5\x2f\xfd":  # zstd magic
        return zstandard.ZstdDecompressor().decompress(raw)
    return raw


def scan_indices(blob: bytes) -> dict:
    """Find runs of LE u16 values looking like glyph index buffers."""
    tagged_hits = 0
    range_hits = 0
    best_run = {"tagged": 0, "range": 0, "off": None, "sample": []}
    # Sliding window of 24 words
    words = memoryview(blob)
    n = len(blob) - 1
    for off in range(0, n - 48, 2):
        tagged = 0
        in_range = 0
        sample = []
        for i in range(24):
            (v,) = struct.unpack_from("<H", words, off + i * 2)
            raw = v & 0x7FFF
            is_tag = bool(v & 0x8000)
            is_h = HANGUL_LO <= raw < HANGUL_HI
            if is_tag and is_h:
                tagged += 1
            if is_h:
                in_range += 1
            if v != 0:
                sample.append(f"{v:04X}")
        if tagged > best_run["tagged"] or (
            tagged == best_run["tagged"] and in_range > best_run["range"]
        ):
            best_run = {
                "tagged": tagged,
                "range": in_range,
                "off": off,
                "sample": sample[:16],
            }
        tagged_hits += tagged
        range_hits += in_range
    # Also count exact flag byte 01 near plausible DS images — weak signal only.
    return {
        "best_window": best_run,
        "total_tagged_h_pairs_over_windows": tagged_hits,
        "note": "Heuristic only; Cygne Core.bin layout not fully mapped",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "out" / "bizhawk" / "hyp1",
    )
    args = ap.parse_args()
    states = sorted(args.dir.glob("*.State"))
    report = {"states": [], "summary": {}}
    any_tag = False
    any_range = False
    for st in states:
        try:
            core = load_core_bin(st)
        except Exception as exc:  # noqa: BLE001
            report["states"].append({"file": st.name, "error": str(exc)})
            continue
        if not core:
            report["states"].append({"file": st.name, "error": "no Core.bin"})
            continue
        scan = scan_indices(core)
        bw = scan["best_window"]
        if bw["tagged"] > 0:
            any_tag = True
        if bw["range"] > 0:
            any_range = True
        report["states"].append(
            {
                "file": st.name,
                "core_len": len(core),
                "best_tagged": bw["tagged"],
                "best_range": bw["range"],
                "best_off": bw["off"],
                "sample": bw["sample"],
            }
        )
    if any_tag:
        verdict = "COREBIN_TAG_CANDIDATES_FOUND"
    elif any_range:
        verdict = "COREBIN_RANGE_ONLY_NO_TAG"
    else:
        verdict = "COREBIN_NO_CLEAR_INDEX_WINDOW"
    report["summary"] = {
        "verdict": verdict,
        "state_count": len(states),
        "any_tag": any_tag,
        "any_range": any_range,
    }
    out = args.dir / "corebin_scan.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
