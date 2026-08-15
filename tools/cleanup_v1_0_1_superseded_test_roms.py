#!/usr/bin/env python3
"""Archive superseded v1.0.1 test ROMs and paired SaveRAMs.

The current v1.0.1 main TIP, 8 MiB rebuild base, pre-ext3 baseline, live SaveRAM,
and rollback backups are never touched. Default mode is dry-run. Use ``--apply``
to move the exact allowlisted artifacts into ``legacy/v1_0_1_superseded_tests_20260816``.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out" / "patch"
SRAM = ROOT / "sram"
LEGACY = ROOT / "legacy" / "v1_0_1_superseded_tests_20260816"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
LIVE_SAVE = SRAM / "monoeye_ko_expanded.sav"
EXPECTED_MAIN_SHA256 = "c8ee51be9c5e33dfd88e7565453ff031a931aaf4948d9cd4aee35a7ec6892e86"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Exact, manually reviewed superseded top-level test ROMs. Baselines/main/backups
# are intentionally absent from this set.
ROM_NAMES = (
    "event_bank_false_replacement_cleanup_candidate.wsc",
    "event_cleanup_followup_guard_candidate.wsc",
    "event_cleanup_gato_5d1e3e_candidate.wsc",
    "event_cleanup_runtime_regression_guard_candidate.wsc",
    "monoeye_ko_expanded_z.wsc",
    "sanc_kingdom_tallgeese3_event1101_fix_candidate.wsc",
)

SAVE_NAMES = tuple(Path(name).with_suffix(".sav").name for name in ROM_NAMES) + (
    # No remaining ROM counterpart; belongs to the same superseded Sanc test family.
    "sanc_kingdom_tallgeese3_event1101_fix_candidate2.sav",
)

KEEP_ROM_NAMES = {
    "monoeye_ko_expanded.wsc",
    "monoeye_ko_expanded_8mb.wsc",
    "monoeye_ko_expanded.pre_ext3.wsc",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def validate() -> None:
    if not MAIN.is_file() or MAIN.stat().st_size != ROM_SIZE:
        raise SystemExit("REFUSED: current main TIP missing or wrong size")
    actual = sha256(MAIN)
    if actual != EXPECTED_MAIN_SHA256:
        raise SystemExit(f"REFUSED: main TIP SHA drifted: {actual}")
    if not LIVE_SAVE.is_file() or LIVE_SAVE.stat().st_size != SAVE_SIZE:
        raise SystemExit("REFUSED: live SaveRAM missing or wrong size")
    for name in KEEP_ROM_NAMES:
        path = PATCH / name
        if not path.is_file():
            raise SystemExit(f"REFUSED: protected ROM missing: {rel(path)}")


def collect() -> list[Path]:
    paths: list[Path] = []
    for name in ROM_NAMES:
        path = PATCH / name
        if path.is_file():
            paths.append(path)
    for name in SAVE_NAMES:
        path = SRAM / name
        if path.is_file():
            paths.append(path)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="move the allowlisted files into legacy")
    args = ap.parse_args()

    validate()
    main_before = sha256(MAIN)
    save_before = sha256(LIVE_SAVE)
    files = collect()
    total = sum(path.stat().st_size for path in files)

    mode = "ARCHIVE" if args.apply else "DRY-RUN"
    print(f"[{mode}] {len(files)} files, {total} bytes ({total / 1024 / 1024:.2f} MiB)")
    for path in files:
        target = LEGACY / path.relative_to(ROOT)
        print(f"  {rel(path)} -> {rel(target)}")

    if not args.apply:
        print("No files changed. Re-run with --apply to archive exactly this allowlist.")
        return 0

    for path in files:
        target = LEGACY / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise SystemExit(f"REFUSED: archive target already exists: {rel(target)}")
        shutil.move(str(path), str(target))

    validate()
    if sha256(MAIN) != main_before:
        raise SystemExit("FATAL: main TIP changed during cleanup")
    if sha256(LIVE_SAVE) != save_before:
        raise SystemExit("FATAL: live SaveRAM changed during cleanup")

    leftovers = [path for path in files if path.exists()]
    if leftovers:
        raise SystemExit("cleanup incomplete: " + ", ".join(rel(path) for path in leftovers))

    print(f"Archived {len(files)} files; reclaimed {total / 1024 / 1024:.2f} MiB from active locations.")
    print(f"Main preserved: {main_before.upper()}")
    print(f"Live SaveRAM preserved: {save_before.upper()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
