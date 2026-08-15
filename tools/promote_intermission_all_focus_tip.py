#!/usr/bin/env python3
"""Promote the verified twelve-label intermission focus patch to main TIP."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402


TILE_BYTES = 0x20
ATLAS_LO = 0x542000
ATLAS_HI = 0x544400


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tip", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc"
    )
    ap.add_argument(
        "--candidate",
        type=Path,
        default=(
            ROOT
            / "out/patch/intermission_all_focus_clean/intermission_all_focus_clean.wsc"
        ),
    )
    ap.add_argument(
        "--candidate-report",
        type=Path,
        default=(
            ROOT / "out/patch/intermission_all_focus_clean/all_focus_clean_report.json"
        ),
    )
    ap.add_argument(
        "--report",
        type=Path,
        default=(
            ROOT / "out/patch/intermission_all_focus_clean/tip_promotion_report.json"
        ),
    )
    args = ap.parse_args(argv)

    for path in (args.tip, args.candidate, args.candidate_report):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    before = args.tip.read_bytes()
    candidate = args.candidate.read_bytes()
    if len(before) != len(candidate) or len(before) != 0x1000000:
        raise SystemExit("TIP/candidate must be equal-sized 16 MiB ROMs")
    base = stock_base(before)

    manifest = json.loads(args.candidate_report.read_text(encoding="utf-8"))
    if sha256(before) != manifest["base_rom_sha256"]:
        raise SystemExit("current TIP no longer matches the candidate base ROM")
    if sha256(candidate) != manifest["candidate_rom_sha256"]:
        raise SystemExit("candidate ROM hash no longer matches its report")
    if manifest.get("focus_labels") != 12:
        raise SystemExit("candidate report does not cover twelve focus labels")

    logical_tiles = {
        int(tile["rom"], 16)
        for target in manifest["targets"]
        for tile in target["changed_tiles"]
    }
    if len(logical_tiles) != manifest.get("unique_rom_tiles_patched"):
        raise SystemExit("candidate report unique tile count is inconsistent")
    if len(logical_tiles) != 202:
        raise SystemExit(f"expected 202 approved focus tiles, found {len(logical_tiles)}")
    for logical in logical_tiles:
        if not (ATLAS_LO <= logical < ATLAS_HI and logical % TILE_BYTES == 6):
            raise SystemExit(f"focus tile outside approved atlas: {logical:06X}")

    allowed = bytearray(len(before))
    for logical in logical_tiles:
        start = base + logical
        allowed[start : start + TILE_BYTES] = b"\x01" * TILE_BYTES
    allowed[-2:] = b"\x01\x01"

    differences = [i for i, (old, new) in enumerate(zip(before, candidate)) if old != new]
    outside = [i for i in differences if not allowed[i]]
    if outside:
        raise SystemExit(
            f"candidate differs outside approved focus tiles at {outside[0]:07X}"
        )
    if not any(i < len(before) - 2 for i in differences):
        raise SystemExit("candidate contains no focus atlas change")

    merged = bytearray(before)
    for index in differences:
        if index < len(merged) - 2:
            merged[index] = candidate[index]
    checksum = update_ws_checksum(merged)
    if bytes(merged) != candidate:
        raise SystemExit("allowlisted merge does not reproduce the verified candidate")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = (
        ROOT / "out/patch/backup" / f"{stamp}_pre_intermission_all_focus"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / args.tip.name
    shutil.copy2(args.tip, backup)

    args.tip.write_bytes(merged)
    reread = args.tip.read_bytes()
    if reread != bytes(merged):
        raise RuntimeError("TIP did not round-trip after write")
    if (sum(reread[:-2]) & 0xFFFF) != int.from_bytes(reread[-2:], "little"):
        raise RuntimeError("TIP checksum verification failed")

    report = {
        "purpose": "promote all twelve clean Korean intermission focus labels",
        "tip": str(args.tip),
        "candidate": str(args.candidate),
        "candidate_report": str(args.candidate_report),
        "backup": str(backup),
        "before_sha256": sha256(before),
        "after_sha256": sha256(reread),
        "candidate_sha256": sha256(candidate),
        "after_equals_candidate": reread == candidate,
        "focus_labels": 12,
        "approved_unique_tiles": len(logical_tiles),
        "changed_bytes_including_checksum": len(differences),
        "checksum": f"{checksum:04X}",
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"backup   : {backup}")
    print(f"tiles    : {len(logical_tiles)}")
    print(f"changed  : {len(differences)} bytes")
    print(f"checksum : {checksum:04X}")
    print(f"sha256   : {report['after_sha256']}")
    print(f"report   : {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
