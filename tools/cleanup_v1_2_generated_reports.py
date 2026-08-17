#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out" / "patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
DIST_XDELTA = ROOT / "out" / "dist" / "monoeye_ko_expanded_v1.2.xdelta"
ARCHIVE = ROOT / "legacy" / "v1_2_generated_reports_20260817" / "out" / "patch"
MANIFEST = ROOT / "legacy" / "v1_2_generated_reports_20260817" / "manifest.json"

EXPECTED_MAIN_SHA = "c7bb4b5c936653888062f2389351c586fc483dedacdba209918b327e440e2131"
EXPECTED_XDELTA_SHA = "c26cf206528e33700aaee81807889ff5eecb9b08367306a6dccd169e19f91f28"
KEEP_JSON = {
    "exp_dictionary_meta.json",
    "ext3_dictionary_meta.json",
    "v1_2_release_validation_summary.json",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate() -> dict[str, str]:
    main_sha = sha256(MAIN)
    patch_sha = sha256(DIST_XDELTA)
    if main_sha != EXPECTED_MAIN_SHA:
        raise SystemExit(f"main TIP SHA drift: {main_sha}")
    if patch_sha != EXPECTED_XDELTA_SHA:
        raise SystemExit(f"v1.2 xdelta SHA drift: {patch_sha}")
    return {"main_sha256": main_sha, "xdelta_sha256": patch_sha}


def collect() -> list[Path]:
    return sorted(
        [p for p in PATCH.iterdir() if p.is_file() and p.suffix.lower() == ".json" and p.name not in KEEP_JSON],
        key=lambda p: p.name.lower(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive reproducible out/patch JSON reports for the v1.2 release workspace.")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    before = validate()
    targets = collect()
    total = sum(p.stat().st_size for p in targets)
    print(json.dumps({
        "mode": "commit" if args.commit else "dry_run",
        "targets": len(targets),
        "bytes": total,
        "mib": round(total / 1024 / 1024, 1),
        "kept_json": sorted(KEEP_JSON),
    }, ensure_ascii=False, indent=2))

    if not args.commit:
        for p in targets:
            print(p.relative_to(ROOT).as_posix())
        return 0

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    moved = []
    for src in targets:
        dst = ARCHIVE / src.name
        if dst.exists():
            if dst.is_file() and sha256(dst) == sha256(src):
                src.unlink()
                mode = "deduplicated_identical"
            else:
                raise SystemExit(f"archive collision: {dst}")
        else:
            shutil.move(str(src), str(dst))
            mode = "moved"
        moved.append({
            "source": src.relative_to(ROOT).as_posix(),
            "archive": dst.relative_to(ROOT).as_posix(),
            "size": dst.stat().st_size,
            "sha256": sha256(dst),
            "mode": mode,
        })

    after = validate()
    if before != after:
        raise SystemExit("release artifacts changed during cleanup")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/cleanup_v1_2_generated_reports.py",
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": after,
        "count": len(moved),
        "bytes": sum(int(x["size"]) for x in moved),
        "mib": round(sum(int(x["size"]) for x in moved) / 1024 / 1024, 1),
        "kept_json": sorted(KEEP_JSON),
        "policy": "Archive generated audit/report/runtime-contract JSON; retain only build metadata and compact v1.2 validation summary in out/patch.",
        "moved": moved,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"committed": len(moved), "archive": str(ARCHIVE.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
