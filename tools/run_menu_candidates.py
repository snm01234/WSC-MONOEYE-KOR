#!/usr/bin/env python3
r"""
Run a batch of candidate ROMs through the title/menu capture and localise changes.

For each ROM: capture title + menu once, compare the MD5s with the recorded
baseline, and for any capture that changed, report which 8x8 framebuffer blocks
moved. The block report is the part that matters -- "the hash changed" alone
cannot distinguish "this ROM byte draws that button" from "the ROM is subtly
broken and the whole screen shifted".

Usage::

    python tools/run_menu_candidates.py out/patch/menu_bisect/PLATE_*.wsc
    python tools/run_menu_candidates.py --glob "out/patch/menu_bisect/TILE_*.wsc"
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bizhawk_env as bz  # noqa: E402
from diff_capture_tiles import block_diff  # noqa: E402
from PIL import Image  # noqa: E402

BASELINE = bz.CAPTURE_DIR / "baseline_hashes.json"
LABELS = ("title", "menu")


def baseline_pngs(tag: str = "orig") -> dict:
    """Baseline PNGs written by run_title_menu_capture.py --runs 3."""
    out = {}
    for label in LABELS:
        for cand in (
            bz.CAPTURE_DIR / f"{tag}_r1_{label}.png",
            bz.CAPTURE_DIR / f"{tag}_{label}.png",
        ):
            if cand.exists():
                out[label] = cand
                break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roms", nargs="*", type=Path)
    ap.add_argument("--glob", default=None)
    ap.add_argument("--out-dir", type=Path, default=bz.CAPTURE_DIR / "candidates")
    ap.add_argument("--baseline-tag", default="orig")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--overlays", action="store_true", help="write highlighted diff PNGs")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)

    roms = list(args.roms)
    if args.glob:
        roms += [Path(p) for p in sorted(globmod.glob(args.glob))]
    if not roms:
        raise SystemExit("no ROMs given")
    if not BASELINE.exists():
        raise SystemExit(
            f"missing {BASELINE}; run tools/run_title_menu_capture.py --runs 3 --write-baseline"
        )
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    base_hashes = base["hashes"]
    base_png = baseline_pngs(args.baseline_tag)
    missing_png = [l for l in LABELS if l not in base_png]
    if missing_png:
        raise SystemExit(f"baseline PNGs not found for {missing_png} in {bz.CAPTURE_DIR}")

    bz.ensure_profile(refresh_config=True)
    bz.clear_saveram()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for rom in roms:
        rom = rom.resolve()
        tag = rom.stem
        res = bz.run_lua(bz.LUA_MENU_CAPTURE, rom, args.out_dir, tag, None, timeout=args.timeout)
        hashes = bz.png_hashes(res.shots)
        row = {"tag": tag, "rom": str(rom), "done": res.done, "captures": {}}
        summary = []
        for label in LABELS:
            got = hashes.get(label, "NONE")
            want = base_hashes.get(label, "NONE")
            entry = {"hash": got, "baseline": want, "changed": got != want}
            if entry["changed"] and label in res.shots:
                blocks, total, size = block_diff(
                    Image.open(base_png[label]), Image.open(res.shots[label])
                )
                cols = [b["col"] for b in blocks]
                rowsi = [b["row"] for b in blocks]
                entry["blocks"] = len(blocks)
                entry["pixels"] = total
                entry["bbox"] = (
                    {"col": [min(cols), max(cols)], "row": [min(rowsi), max(rowsi)]}
                    if blocks
                    else None
                )
                entry["block_list"] = [[b["col"], b["row"]] for b in blocks]
                if args.overlays:
                    ov = args.out_dir / f"{tag}_{label}_diff.png"
                    from diff_capture_tiles import main as diff_main

                    diff_main([str(base_png[label]), str(res.shots[label]), "--overlay", str(ov)])
                    entry["overlay"] = str(ov.relative_to(ROOT))
                summary.append(
                    f"{label}=CHANGED({len(blocks)}blk/{total}px "
                    f"col{min(cols)}-{max(cols)} row{min(rowsi)}-{max(rowsi)})"
                    if blocks
                    else f"{label}=CHANGED(0blk)"
                )
            else:
                summary.append(f"{label}={'CHANGED' if entry['changed'] else 'same'}")
            row["captures"][label] = entry
        rows.append(row)
        print(f"{tag}: " + "  ".join(summary))

    bz.kill_emu()
    out = args.report or (args.out_dir / "candidate_results.json")
    out.write_text(
        json.dumps({"baseline": base_hashes, "results": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
