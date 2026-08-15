#!/usr/bin/env python3
"""Compare the three hand-made intermission focus savestates offline.

Expected meanings:

* QuickSave1: focus on Save
* QuickSave2: focus on 開発プラン
* QuickSave3: focus on 補給

The report intentionally keeps raw Core.bin offsets.  Cygne's serializer layout
is not fully documented, so identifying compact, repeatable change regions comes
before assigning them a WRAM/VRAM hardware address.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from build_intermission_state_ab import Zstd, read_state_core


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = (
    ROOT
    / "BizHawk-2.11.1-win-x64/WonderSwan/State"
    / "monoeye ko expanded.Cygne"
)
DEFAULT_OUT = ROOT / "out/patch/intermission_focus_trace"
MEANINGS = {1: "save", 2: "development_plan", 3: "supply"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def runs_from_offsets(offsets: list[int]) -> list[tuple[int, int]]:
    runs: list[list[int]] = []
    for off in offsets:
        if not runs or off != runs[-1][1] + 1:
            runs.append([off, off])
        else:
            runs[-1][1] = off
    return [(start, end) for start, end in runs]


def diff_overlay(a: Image.Image, b: Image.Image) -> tuple[Image.Image, int, tuple | None]:
    diff = ImageChops.difference(a, b)
    changed = sum(pixel != (0, 0, 0) for pixel in diff.getdata())
    out = a.copy()
    draw = ImageDraw.Draw(out)
    box = diff.getbbox()
    if box:
        draw.rectangle(box, outline=(255, 0, 255), width=1)
    return out, changed, box


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    args = ap.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    zstd = Zstd(args.zstd_dll)
    states = []
    for index in range(1, 4):
        path = args.state_dir / f"Mednafen.QuickSave{index}.State"
        if not path.exists():
            raise SystemExit(f"missing: {path}")
        core, core_name = read_state_core(path, zstd)
        with zipfile.ZipFile(path) as zf:
            framebuffer = Image.open(BytesIO(zf.read("Framebuffer.bmp"))).convert("RGB")
        png = args.out_dir / f"state{index}_{MEANINGS[index]}.png"
        framebuffer.save(png)
        states.append(
            {
                "index": index,
                "meaning": MEANINGS[index],
                "path": path,
                "state_sha256": sha256(path.read_bytes()),
                "core_name": core_name,
                "core": core,
                "core_sha256": sha256(core),
                "framebuffer": framebuffer,
                "framebuffer_png": png,
            }
        )

    if len({len(s["core"]) for s in states}) != 1:
        raise SystemExit("Core.bin lengths differ")

    report = {
        "states": [
            {
                key: (str(value) if isinstance(value, Path) else value)
                for key, value in state.items()
                if key not in {"core", "framebuffer"}
            }
            for state in states
        ],
        "core_bytes": len(states[0]["core"]),
        "pairs": [],
    }

    for a, b in itertools.combinations(states, 2):
        core_a, core_b = a["core"], b["core"]
        changed_offsets = [
            off for off, (left, right) in enumerate(zip(core_a, core_b)) if left != right
        ]
        runs = runs_from_offsets(changed_offsets)
        by_size = sorted(runs, key=lambda pair: pair[1] - pair[0], reverse=True)

        image_a, image_b = a["framebuffer"], b["framebuffer"]
        overlay, changed_pixels, pixel_box = diff_overlay(image_a, image_b)
        overlay_path = args.out_dir / f"diff_state{a['index']}_state{b['index']}.png"
        overlay.save(overlay_path)

        # Count differences per 32-byte Core.bin block for later state bisection.
        blocks = []
        for block_start in range(0, len(core_a), 32):
            block_end = min(block_start + 32, len(core_a))
            count = sum(
                core_a[off] != core_b[off] for off in range(block_start, block_end)
            )
            if count:
                blocks.append(
                    {
                        "start": f"{block_start:06X}",
                        "changed_bytes": count,
                    }
                )

        report["pairs"].append(
            {
                "a": a["index"],
                "b": b["index"],
                "a_meaning": a["meaning"],
                "b_meaning": b["meaning"],
                "core_changed_bytes": len(changed_offsets),
                "core_changed_runs": len(runs),
                "largest_runs": [
                    {
                        "start": f"{start:06X}",
                        "end": f"{end:06X}",
                        "length": end - start + 1,
                    }
                    for start, end in by_size[:64]
                ],
                "changed_32byte_blocks": blocks,
                "framebuffer_changed_pixels": changed_pixels,
                "framebuffer_diff_bbox": list(pixel_box) if pixel_box else None,
                "framebuffer_diff_overlay": str(overlay_path),
            }
        )

    report_path = args.out_dir / "focus_state_diff_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"states: {len(states)}")
    for pair in report["pairs"]:
        print(
            f"{pair['a']}->{pair['b']}: core {pair['core_changed_bytes']} B / "
            f"{pair['core_changed_runs']} runs; framebuffer "
            f"{pair['framebuffer_changed_pixels']} px"
        )
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
