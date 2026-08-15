#!/usr/bin/env python3
"""Remove superseded unversioned v1.0.0 dist aliases after verified backup.

The v1.0.0 files were copied by the v1.0.1 hotfix promotion into the rollback
backup directory. This script leaves out/dist with only explicitly versioned
v1.0.1 release artifacts so GitHub release uploads are unambiguous.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "out/dist"
BACKUP = ROOT / "out/patch/backup/20260815_235351_pre_v1.0.1_hotfix/dist_v1.0.0"
FILES = (
    "monoeye_ko_expanded.xdelta",
    "monoeye_ko_expanded_xdelta.json",
    "monoeye_ko_expanded_XDELTA_README.md",
    "SHA256SUMS.txt",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for name in FILES:
        src = DIST / name
        bak = BACKUP / name
        if not src.is_file() or not bak.is_file():
            raise SystemExit(f"missing source/backup for {name}")
        if src.stat().st_size != bak.stat().st_size or sha(src) != sha(bak):
            raise SystemExit(f"backup mismatch for {name}")
    for name in FILES:
        (DIST / name).unlink()
    print("archived unversioned v1.0.0 dist aliases; backup verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
