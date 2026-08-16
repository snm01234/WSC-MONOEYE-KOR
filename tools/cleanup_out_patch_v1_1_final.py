#!/usr/bin/env python3
"""Clean out/patch after the final v1.1 promotion.

Only top-level files in out/patch are removed. The rollback directory and the
current v1.1 main/TBL/metadata/final audit set are protected explicitly.

Dry-run by default. Pass --apply to delete the listed files.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"

PROTECTED = {
    "monoeye_ko_expanded.wsc",
    "hangul_patch_pad3.tbl",
    "exp_dictionary_meta.json",
    "ext3_dictionary_meta.json",
    "diana_original_control_restore_v1_1_promotion_report.json",
    "v1_1_final_20cell_audit.json",
    "v1_1_final_name_audit.json",
    "v1_1_final_runtime_contracts.json",
    "v1_1_final_structural_audit.json",
    "v1_1_final_terminology_audit.json",
}

EXPECTED_MAIN_SHA256 = "f62f14d15f3d76ad2eb33e2b55531ab4781d230cb63bcc2348bbd703d8c39be3"
EXPECTED_TBL_SHA256 = "9d31d1fd1a5022b54f83866d285f4d656461b30d4046ef2b6261a9441abca914"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete instead of dry-run")
    args = parser.parse_args()

    main_rom = PATCH / "monoeye_ko_expanded.wsc"
    tbl = PATCH / "hangul_patch_pad3.tbl"
    if sha256(main_rom) != EXPECTED_MAIN_SHA256:
        raise SystemExit("refuse cleanup: main TIP SHA drifted")
    if sha256(tbl) != EXPECTED_TBL_SHA256:
        raise SystemExit("refuse cleanup: active TBL SHA drifted")
    if not (PATCH / "backup").is_dir():
        raise SystemExit("refuse cleanup: rollback backup directory missing")

    victims = sorted(
        p for p in PATCH.iterdir()
        if p.is_file() and p.name not in PROTECTED
    )
    total = sum(p.stat().st_size for p in victims)

    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"protected_files={len(PROTECTED)}")
    print(f"remove_count={len(victims)}")
    print(f"remove_bytes={total}")
    for p in victims:
        print(f"REMOVE {p.relative_to(ROOT)} {p.stat().st_size}")

    if args.apply:
        for p in victims:
            p.unlink()
        print("cleanup_complete=true")
        print(f"main_sha256={sha256(main_rom)}")
        print(f"tbl_sha256={sha256(tbl)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
