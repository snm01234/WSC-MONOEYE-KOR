#!/usr/bin/env python3
"""Archive redundant v1.2 test/candidate artifacts without touching the current main TIP.

Default is dry-run. ``--commit`` moves selected top-level ``out/patch`` artifacts
under ``legacy/v1_2_test_artifacts_20260817/out/patch``. Nothing is deleted.

Policy:
- keep the current main TIP, active TBL/ext3 metadata and compact v1.2 release validation summary;
- archive every top-level candidate/probe/test WSC other than the main TIP;
- archive reproducible audit/report/review/worklist/runtime-contract JSONs;
- never traverse or alter ``out/patch/backup``.
"""
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
EXPECTED_MAIN_SHA = "c7bb4b5c936653888062f2389351c586fc483dedacdba209918b327e440e2131"
EXPECTED_MAIN_SIZE = 16_777_216
ARCHIVE = ROOT / "legacy" / "v1_2_test_artifacts_20260817"
MANIFEST = ARCHIVE / "manifest.json"

KEEP = {
    "monoeye_ko_expanded.wsc",
    "hangul_patch_pad3.tbl",
    "exp_dictionary_meta.json",
    "ext3_dictionary_meta.json",
    "v1_2_release_validation_summary.json",
}

GENERATED_JSON_MARKERS = (
    "candidate", "probe", "audit", "report", "runtime", "review", "worklist",
    "matrix", "risk", "speaker", "terminology", "battle", "exact", "structural",
    "postpromotion", "prepromotion", "current_", "leading18", "test",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_main() -> dict[str, object]:
    if not MAIN.is_file() or MAIN.stat().st_size != EXPECTED_MAIN_SIZE:
        raise SystemExit("current main TIP missing or wrong size")
    digest = sha256(MAIN)
    if digest != EXPECTED_MAIN_SHA:
        raise SystemExit(f"main TIP SHA drift: {digest}")
    return {"size": MAIN.stat().st_size, "sha256": digest}


def referenced_json_basenames() -> set[str]:
    refs: set[str] = set()
    for tool in (ROOT / "tools").glob("*.py"):
        try:
            text = tool.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for path in PATCH.glob("*.json"):
            if path.name in text:
                refs.add(path.name)
    return refs


def collect() -> tuple[list[Path], set[str]]:
    refs = referenced_json_basenames()
    rows: list[Path] = []
    for path in sorted((p for p in PATCH.iterdir() if p.is_file()), key=lambda p: p.name.lower()):
        name = path.name
        low = name.lower()
        if name in KEEP:
            continue
        if path.suffix.lower() in {".wsc", ".wsc_bak"}:
            rows.append(path)
            continue
        if path.suffix.lower() == ".json":
            if name in refs:
                continue
            if name.startswith("v1_1_final_") or any(marker in low for marker in GENERATED_JSON_MARKERS):
                rows.append(path)
                continue
        if low.endswith("candidate.sav") or "probe" in low or "test" in low:
            rows.append(path)
    return rows, refs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    before = validate_main()
    rows, refs = collect()
    total = sum(p.stat().st_size for p in rows)
    summary = {
        "mode": "commit" if args.commit else "dry_run",
        "main": before,
        "selected_files": len(rows),
        "selected_bytes": total,
        "selected_mib": round(total / 1024 / 1024, 1),
        "selected_wsc": sum(p.suffix.lower() in {".wsc", ".wsc_bak"} for p in rows),
        "selected_json": sum(p.suffix.lower() == ".json" for p in rows),
        "tool_referenced_json_kept": len(refs),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.commit:
        for p in rows:
            print(p.relative_to(ROOT).as_posix())
        return 0

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, object]] = []
    for src in rows:
        dst = ARCHIVE / src.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
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
            "target": dst.relative_to(ROOT).as_posix(),
            "bytes": dst.stat().st_size,
            "mode": mode,
        })

    after = validate_main()
    if before != after:
        raise SystemExit("main TIP changed during cleanup")
    payload = {
        "schema_version": 1,
        "generated_by": "tools/cleanup_v1_2_test_artifacts.py",
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_before": before,
        "main_after": after,
        "summary": summary,
        "keep": sorted(KEEP),
        "tool_referenced_json_kept": sorted(refs),
        "moved": moved,
        "restore": "Move each archived target back to its source path if needed.",
    }
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"committed": len(moved), "archive": ARCHIVE.relative_to(ROOT).as_posix()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
