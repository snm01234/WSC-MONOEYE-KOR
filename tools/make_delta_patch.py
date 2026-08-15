#!/usr/bin/env python3
"""
Create a distribution patch (IPS and/or raw xdelta-like copy helper metadata)
from an original WonderSwan ROM to a patched ROM.

IPS is written with a pure-Python encoder (no external tools required).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def iter_diff_runs(original: bytes, patched: bytes):
    if len(original) != len(patched):
        raise ValueError("ROM sizes must match for IPS generation")
    index = 0
    size = len(original)
    while index < size:
        if original[index] == patched[index]:
            index += 1
            continue
        start = index
        while index < size and original[index] != patched[index]:
            index += 1
            # IPS records are limited to 0xFFFF bytes.
            if index - start >= 0xFFFF:
                break
        yield start, patched[start:index]


def write_ips(original: bytes, patched: bytes, out_path: Path) -> dict:
    records = 0
    changed = 0
    with out_path.open("wb") as handle:
        handle.write(b"PATCH")
        for offset, data in iter_diff_runs(original, patched):
            if not data:
                continue
            if offset > 0xFFFFFF:
                raise ValueError(f"IPS offset too large: {offset:#x}")
            handle.write(struct.pack(">I", offset)[1:])  # 24-bit BE
            handle.write(struct.pack(">H", len(data)))
            handle.write(data)
            records += 1
            changed += len(data)
        handle.write(b"EOF")
    return {"records": records, "changed_bytes": changed, "path": str(out_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--original",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
    )
    ap.add_argument(
        "--patched",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_seed.wsc",
    )
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out" / "dist")
    ap.add_argument("--name", type=str, default="monoeye_ko_seed")
    args = ap.parse_args()

    if not args.original.exists():
        raise SystemExit(f"Original ROM not found: {args.original}")
    if not args.patched.exists():
        raise SystemExit(f"Patched ROM not found: {args.patched}")

    original = args.original.read_bytes()
    patched = args.patched.read_bytes()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    ips_path = args.out_dir / f"{args.name}.ips"
    ips_info = write_ips(original, patched, ips_path)

    meta = {
        "name": args.name,
        "original": str(args.original.name),
        "patched": str(args.patched.name),
        "original_sha1": sha1_file(args.original),
        "patched_sha1": sha1_file(args.patched),
        "original_size": len(original),
        "patched_size": len(patched),
        "ips": ips_info,
        "apply": [
            "Keep a clean backup of the original ROM.",
            f"Verify original SHA1 == {sha1_file(args.original)}",
            f"Apply `{ips_path.name}` with any IPS patcher (Floating IPS, Lunar IPS, etc.).",
        ],
    }
    meta_path = args.out_dir / f"{args.name}_patch.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = args.out_dir / f"{args.name}_README.md"
    readme.write_text(
        "\n".join(
            [
                f"# {args.name} patch",
                "",
                f"- Original: `{args.original.name}`",
                f"- Original SHA1: `{meta['original_sha1']}`",
                f"- Patch: `{ips_path.name}`",
                f"- Changed bytes: **{ips_info['changed_bytes']}** in **{ips_info['records']}** records",
                "",
                "## Apply",
                "",
                *[f"{i+1}. {line}" for i, line in enumerate(meta["apply"])],
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"Wrote {ips_path}")
    print(f"Wrote {meta_path}")
    print(f"Wrote {readme}")


if __name__ == "__main__":
    main()
