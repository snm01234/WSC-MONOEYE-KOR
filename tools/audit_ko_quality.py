#!/usr/bin/env python3
"""
P3: Audit / clean translation-sheet KO quality without touching ROM/font/ext_dict.

- Rank low-quality patterns that pollute frequency-based slot/glyph allocation
- Optionally blank low-quality KO and apply abs overrides
- Opening / stage-1 range gets priority overrides from data/ko_quality_overrides.json

Does not modify out/patch/*.wsc or font builders.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from normalize_ko_text import (  # noqa: E402
    hangul_count,
    is_low_quality_ko,
    normalize_ko_text,
)

csv.field_size_limit(10_000_000)

OPENING_ABS_MIN = 0x6040A5
OPENING_ABS_MAX = 0x605200  # opening narration → early stage-1 dialogue band


def load_lines(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        rows: list[dict] = []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                abs_raw = (row.get("abs") or "").strip()
                if not abs_raw:
                    continue
                try:
                    abs_off = int(abs_raw, 16)
                except ValueError:
                    continue
                rows.append(
                    {
                        "abs": f"{abs_off:06X}",
                        "jp": row.get("jp") or "",
                        "ko": row.get("ko") or "",
                        "id": row.get("id") or "",
                        "kind": row.get("kind") or "",
                    }
                )
        return rows
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload["lines"] if isinstance(payload, dict) else payload
    out: list[dict] = []
    for row in raw:
        abs_raw = str(row.get("abs") or "").strip()
        if not abs_raw:
            continue
        abs_off = int(abs_raw, 16)
        out.append(
            {
                "abs": f"{abs_off:06X}",
                "jp": row.get("jp") or "",
                "ko": row.get("ko") or "",
                "id": row.get("id") or "",
                "kind": row.get("kind") or "",
            }
        )
    return out


def load_overrides(path: Path) -> dict[int, str]:
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
        out[int(abs_raw, 16)] = normalize_ko_text(ko)
    return out


def classify_reason(ko: str) -> str:
    if not ko:
        return "empty"
    plain = ko.replace("　", " ")
    if is_low_quality_ko(ko):
        if "<FF>" in ko.upper() or "<BADDICT" in ko.upper() or ko.count("<") >= 2:
            return "control_tag"
        if re.search(r"[\u3040-\u30ff]", ko):
            return "kana"
        if ko.count("학교") >= 2:
            return "school_scaffold"
        if re.search(r"을（를）|는（은）|은（는）", ko):
            return "particle_scaffold"
        if re.match(r"^[을를은는]", ko):
            return "particle_lead"
        if hangul_count(ko) < 2:
            return "hn_lt2"
        if hangul_count(ko) <= 2 and not re.search(r"[가-힣]{2,}", ko):
            return "short_stub"
        if re.match(r"^[！？。・、，]", ko) or re.match(r"^[！？]", ko):
            return "punct_lead"
        if "해당" in ko or "문자" in ko or "의미" in ko or "번역" in plain:
            return "bing_meta"
        return "other_low"
    return "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out" / "script" / "translations_full.json",
    )
    ap.add_argument(
        "--overrides",
        type=Path,
        default=ROOT / "data" / "ko_quality_overrides.json",
    )
    ap.add_argument(
        "--report",
        type=Path,
        default=ROOT / "out" / "script" / "ko_quality_report.json",
    )
    ap.add_argument(
        "--write-cleaned",
        type=Path,
        default=None,
        help="Write cleaned {lines:[...]} JSON (low-quality KO blanked, overrides applied)",
    )
    ap.add_argument(
        "--blank-low",
        action="store_true",
        help="With --write-cleaned, blank KO that fails is_low_quality_ko",
    )
    args = ap.parse_args()

    lines = load_lines(args.sheet)
    overrides = load_overrides(args.overrides)

    reason_counts: Counter[str] = Counter()
    low_freq: Counter[str] = Counter()
    opening_stats = {"total": 0, "low": 0, "override": 0, "ok": 0}
    override_hits = 0
    cleaned: list[dict] = []

    for row in lines:
        abs_off = int(row["abs"], 16)
        ko_raw = (row.get("ko") or "").strip()
        ko = normalize_ko_text(ko_raw) if ko_raw else ""
        in_opening = OPENING_ABS_MIN <= abs_off < OPENING_ABS_MAX
        if in_opening:
            opening_stats["total"] += 1

        if abs_off in overrides:
            ko = overrides[abs_off]
            override_hits += 1
            if in_opening:
                opening_stats["override"] += 1

        reason = classify_reason(ko)
        reason_counts[reason] += 1
        low = reason != "ok"
        if low:
            low_freq[ko] += 1
            if in_opening:
                opening_stats["low"] += 1
        elif in_opening:
            opening_stats["ok"] += 1

        out_ko = ko
        if args.write_cleaned is not None and args.blank_low and low and abs_off not in overrides:
            out_ko = ""
        cleaned.append(
            {
                "abs": row["abs"],
                "jp": row.get("jp") or "",
                "ko": out_ko,
                "id": row.get("id") or "",
                "kind": row.get("kind") or "",
            }
        )

    top_low = [
        {"ko": k, "count": n}
        for k, n in sorted(low_freq.items(), key=lambda kv: (-kv[1], kv[0]))[:50]
    ]
    report = {
        "source": str(args.sheet).replace("\\", "/"),
        "line_count": len(lines),
        "override_file": str(args.overrides).replace("\\", "/") if overrides else None,
        "override_entries": len(overrides),
        "override_hits": override_hits,
        "reason_counts": dict(reason_counts.most_common()),
        "low_total": sum(v for k, v in reason_counts.items() if k != "ok"),
        "ok_total": reason_counts.get("ok", 0),
        "opening_range": {
            "abs_min": f"{OPENING_ABS_MIN:06X}",
            "abs_max": f"{OPENING_ABS_MAX:06X}",
            **opening_stats,
        },
        "top_low_frequency": top_low,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"lines={len(lines)} ok={report['ok_total']} low={report['low_total']} "
        f"overrides={override_hits}/{len(overrides)} → {args.report}"
    )
    print("reasons:", dict(reason_counts.most_common(12)))
    print(
        "opening:",
        opening_stats,
        f"[{OPENING_ABS_MIN:06X},{OPENING_ABS_MAX:06X})",
    )

    if args.write_cleaned is not None:
        payload = {
            "description": "P3 quality-cleaned translations (low KO blanked; overrides applied)",
            "source": str(args.sheet).replace("\\", "/"),
            "overrides": str(args.overrides).replace("\\", "/") if overrides else None,
            "line_count": len(cleaned),
            "lines": cleaned,
        }
        args.write_cleaned.parent.mkdir(parents=True, exist_ok=True)
        args.write_cleaned.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        nonempty = sum(1 for r in cleaned if (r.get("ko") or "").strip())
        print(f"wrote cleaned {args.write_cleaned} ko_nonempty={nonempty}")


if __name__ == "__main__":
    main()
