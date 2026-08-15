#!/usr/bin/env python3
"""Rewrite only BizVersion.txt so older BizHawk 2.11 states load unattended in 2.11.1."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def rewrite(source: Path, output: Path, version: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "BizVersion.txt":
                data = version
            clone = zipfile.ZipInfo(info.filename, info.date_time)
            clone.compress_type = info.compress_type
            clone.comment = info.comment
            clone.extra = info.extra
            clone.create_system = info.create_system
            clone.external_attr = info.external_attr
            clone.internal_attr = info.internal_attr
            clone.flag_bits = info.flag_bits
            dst.writestr(clone, data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--glob", default="*_clean.State")
    ap.add_argument("--version", default="Version 2.11.1\r\n")
    args = ap.parse_args()

    paths = sorted(args.input_dir.glob(args.glob))
    if not paths:
        raise SystemExit(f"no states matched {args.input_dir / args.glob}")
    version = args.version.encode("ascii")
    for source in paths:
        output = args.out_dir / source.name
        rewrite(source, output, version)
        print(f"{source} -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
