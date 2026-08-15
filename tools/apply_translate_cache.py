#!/usr/bin/env python3
"""
Merge excel_translate_cache.json into translation_sheet.csv.

Safe to run while translation is in progress (resume-friendly) or after it finishes.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
csv.field_size_limit(10_000_000)

from normalize_ko_text import is_low_quality_ko, normalize_ko_text  # noqa: E402
from script_translation_scope import translation_exclusion_reason  # noqa: E402
from translation_source_policy import reject_legacy_generator  # noqa: E402


def normalize(text: str) -> str:
    return normalize_ko_text(text)


def load_cache(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
        entries = raw["entries"]
    else:
        entries = raw
    out: dict[str, str] = {}
    for jp, ko in entries.items():
        if jp == "engine" or not str(ko).strip():
            continue
        nk = normalize(str(ko))
        if is_low_quality_ko(nk):
            continue
        out[str(jp)] = nk
    return out


def load_seed(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        line["jp"]: normalize(line["ko"])
        for line in payload.get("lines", [])
        if line.get("jp") and line.get("ko")
    }


def load_abs_overrides(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    lines = payload.get("lines", payload if isinstance(payload, list) else [])
    out: dict[int, str] = {}
    for row in lines:
        abs_raw = str(row.get("abs") or "").strip()
        ko = (row.get("ko") or "").strip()
        if not abs_raw or not ko:
            continue
        out[int(abs_raw, 16)] = normalize(ko)
    return out


def main() -> None:
    reject_legacy_generator(Path(__file__))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out" / "script" / "translation_sheet.csv",
    )
    ap.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "out" / "script" / "excel_translate_cache.json",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data" / "translations_seed.json",
    )
    ap.add_argument(
        "--overrides",
        type=Path,
        default=ROOT / "data" / "ko_quality_overrides.json",
        help="Abs-keyed KO overrides (opening/stage1); win over cache/seed jp map",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "script" / "translation_sheet.csv",
    )
    ap.add_argument(
        "--drop-low-existing",
        action="store_true",
        help="Blank existing sheet KO that fails the quality filter (unless overridden)",
    )
    ap.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Replace existing non-empty KO with cache/seed values (default preserves latest sheet KO)",
    )
    args = ap.parse_args()

    cache = load_cache(args.cache) if args.cache.exists() else {}
    seed = load_seed(args.seed)
    overrides = load_abs_overrides(args.overrides)
    mapping = dict(cache)
    mapping.update(seed)  # seed wins over cache (by jp)

    with args.sheet.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    filled = 0
    missing = 0
    overridden = 0
    dropped = 0
    excluded_cleared = 0
    existing_preserved = 0
    for row in rows:
        jp = row.get("jp") or ""
        abs_raw = (row.get("abs") or "").strip()
        abs_off: int | None
        try:
            abs_off = int(abs_raw, 16) if abs_raw else None
        except ValueError:
            abs_off = None

        exclusion = translation_exclusion_reason(abs_off) if abs_off is not None else None
        if exclusion:
            row["ko"] = ""
            if "notes" in row:
                row["notes"] = exclusion
            excluded_cleared += 1
            continue

        if abs_off is not None and abs_off in overrides:
            row["ko"] = overrides[abs_off]
            overridden += 1
            filled += 1
            continue

        existing = normalize((row.get("ko") or "").strip()) if row.get("ko") else ""
        if args.drop_low_existing and existing and is_low_quality_ko(existing):
            row["ko"] = ""
            existing = ""
            dropped += 1
        if existing and not args.overwrite_existing:
            row["ko"] = existing
            existing_preserved += 1
            filled += 1
            continue

        ko = mapping.get(jp, "")
        if ko:
            row["ko"] = ko
            filled += 1
        elif existing:
            row["ko"] = existing
            filled += 1
        else:
            missing += 1

    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        f"Wrote {args.out} | filled={filled} missing={missing} "
        f"cache_kept={len(cache)} seed={len(seed)} "
        f"overrides={overridden}/{len(overrides)} dropped_low={dropped} "
        f"excluded_cleared={excluded_cleared} existing_preserved={existing_preserved} "
        f"overwrite_existing={args.overwrite_existing}"
    )


if __name__ == "__main__":
    main()
