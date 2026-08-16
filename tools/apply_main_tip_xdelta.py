#!/usr/bin/env python3
"""Apply a main-TIP xdelta3 patch onto a clean 8 MiB original ROM.

The patch produced by ``tools/make_main_tip_xdelta.py`` grows the image to 16 MiB.
The original ROM is the VCDIFF source (COPY); it is not embedded in the patch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import ROM_SIZE, ROM_SIZE_16MB, find_rom  # noqa: E402
from xdelta3_tool import (  # noqa: E402
    XdeltaError,
    decode_xdelta,
    resolve_xdelta3,
    sha256_bytes,
    sha256_file,
)

DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_XDELTA = ROOT / "out/dist/monoeye_ko_expanded_v1.1.xdelta"
DEFAULT_META = ROOT / "out/dist/monoeye_ko_expanded_v1.1_xdelta.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=None)
    parser.add_argument("--xdelta", type=Path, default=DEFAULT_XDELTA)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect-tip", type=Path, default=DEFAULT_TIP)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--xdelta3", type=Path, default=None)
    parser.add_argument("--allow-mismatch", action="store_true")
    args = parser.parse_args()

    original_path = args.original or find_rom(ROOT)
    if not original_path.is_file():
        raise SystemExit(f"original ROM missing: {original_path}")
    if not args.xdelta.is_file():
        raise SystemExit(f"xdelta missing: {args.xdelta}")

    original = original_path.read_bytes()
    if len(original) != ROM_SIZE:
        raise SystemExit(f"original must be 8 MiB, got {len(original)}")

    expected_original = None
    expected_output = None
    if args.meta.is_file():
        meta = json.loads(args.meta.read_text(encoding="utf-8"))
        expected_original = str((meta.get("original") or {}).get("sha256") or "").lower() or None
        expected_output = str((meta.get("main_tip") or {}).get("sha256") or "").lower() or None
        if expected_original and sha256_bytes(original) != expected_original:
            raise SystemExit(
                f"original SHA-256 mismatch: got {sha256_bytes(original)}, "
                f"expected {expected_original}"
            )

    try:
        xdelta3 = resolve_xdelta3(args.xdelta3)
    except XdeltaError as exc:
        raise SystemExit(str(exc)) from exc

    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(f".{args.out.name}.{os.getpid()}.tmp")
    try:
        decode_xdelta(xdelta3, original_path, args.xdelta, temporary)
        patched = temporary.read_bytes()
        os.replace(temporary, args.out)
    except XdeltaError as exc:
        if temporary.is_file():
            try:
                temporary.unlink()
            except OSError:
                pass
        raise SystemExit(str(exc)) from exc

    if len(patched) != ROM_SIZE_16MB:
        raise SystemExit(f"patched size must be 16 MiB, got {len(patched)}")

    out_sha = sha256_bytes(patched)
    tip_sha = expected_output
    if tip_sha is None and args.expect_tip.is_file():
        tip_sha = sha256_file(args.expect_tip)
    match = None if tip_sha is None else out_sha == tip_sha
    if match is False and not args.allow_mismatch:
        raise SystemExit(f"output SHA-256 {out_sha} does not match expected {tip_sha}")

    print(
        json.dumps(
            {
                "ok": True if match is not False else False,
                "original": {"path": str(original_path), "sha256": sha256_bytes(original)},
                "xdelta": {"path": str(args.xdelta), "sha256": sha256_file(args.xdelta)},
                "output": {"path": str(args.out), "size": len(patched), "sha256": out_sha},
                "matches_main_tip": match,
                "expected_sha256": tip_sha,
                "applied_directly_to_8mib": True,
                "embeds_original_rom": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if match is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
