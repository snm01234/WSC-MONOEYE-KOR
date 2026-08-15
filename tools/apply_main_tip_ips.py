#!/usr/bin/env python3
"""Apply a main-TIP IPS directly onto a clean 8 MiB original ROM.

The IPS produced by ``tools/make_main_tip_ips.py`` grows the image to 16 MiB while
rewriting both halves, so no separate expand step is required.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from make_main_tip_ips import IpsError, apply_ips, sha256_bytes  # noqa: E402
from monoeye_rom import ROM_SIZE, ROM_SIZE_16MB, find_rom  # noqa: E402

DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_IPS = ROOT / "out/dist/monoeye_ko_expanded.ips"
DEFAULT_META = ROOT / "out/dist/monoeye_ko_expanded_ips.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=None)
    parser.add_argument("--ips", type=Path, default=DEFAULT_IPS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect-tip", type=Path, default=DEFAULT_TIP)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--allow-mismatch", action="store_true")
    args = parser.parse_args()

    original_path = args.original or find_rom(ROOT)
    if not original_path.is_file():
        raise SystemExit(f"original ROM missing: {original_path}")
    if not args.ips.is_file():
        raise SystemExit(f"IPS missing: {args.ips}")

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
        patched = apply_ips(original, args.ips.read_bytes())
    except IpsError as exc:
        raise SystemExit(str(exc)) from exc

    if len(patched) < ROM_SIZE_16MB:
        patched.extend(b"\x00" * (ROM_SIZE_16MB - len(patched)))
    if len(patched) != ROM_SIZE_16MB:
        raise SystemExit(f"patched size must be 16 MiB, got {len(patched)}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(f".{args.out.name}.{os.getpid()}.tmp")
    temporary.write_bytes(patched)
    os.replace(temporary, args.out)

    out_sha = sha256_bytes(patched)
    tip_sha = expected_output
    if tip_sha is None and args.expect_tip.is_file():
        tip_sha = sha256_bytes(args.expect_tip.read_bytes())
    match = None if tip_sha is None else out_sha == tip_sha
    if match is False and not args.allow_mismatch:
        raise SystemExit(f"output SHA-256 {out_sha} does not match expected {tip_sha}")

    print(
        json.dumps(
            {
                "ok": True if match is not False else False,
                "original": {"path": str(original_path), "sha256": sha256_bytes(original)},
                "ips": {"path": str(args.ips), "sha256": sha256_bytes(args.ips.read_bytes())},
                "output": {"path": str(args.out), "size": len(patched), "sha256": out_sha},
                "matches_main_tip": match,
                "expected_sha256": tip_sha,
                "applied_directly_to_8mib": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if match is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
