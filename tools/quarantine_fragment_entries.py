#!/usr/bin/env python3
"""
Move mid-word kana fragments out of the UI catalogs into a quarantine file.

These entries are not UI terms. They are compressor building blocks: the slot
holding ``ダメ`` is also the first half of ``ダメ－ジ``, so localizing it renders
``불가－ジ`` on 38 measured battle/UI records. ``リ－`` turns ``ジ－クフリ－ド``
into ``ジ－クフ리ド``; ``レイ`` turns ``プレイヤ－`` into ``プ레이ヤ－``.

Nothing is deleted. Removed rows are written to ``data/_quarantine_fragments.json``
with the reason, so a later pass can reinstate one together with whole-word entries
for every parent that composes it (which is the real fix — replacing a parent
slot's payload drops the child reference entirely).

Verified by re-running tools/scan_mixed_script_artifacts.py afterwards.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]

QUARANTINE = ROOT / "data/_quarantine_fragments.json"

# Kana fragments measured to corrupt longer words. Each entry records the word
# the fragment breaks, so the decision is auditable rather than a bare blocklist.
FRAGMENTS: Dict[str, str] = {
    "ダメ": "first half of ダメ－ジ → 불가－ジ (38 measured hits)",
    "クリス": "inside イクリス / クリスチ－ナ → イ크리스 (33 measured hits)",
    "レイ": "inside プレイヤ－ / レイラ / レイン → プ레이ヤ－ (13 measured hits)",
    "リ－": "inside ジ－クフリ－ド / フリ－ト / ハリ－ → ジ－クフ리ド",
    "ザビ": "inside サザビ－ / ザビ－ネ / ザビアロフ → 사자비－",
    "サラ": "inside longer names / サダラ－ン family",
    "ラン": "inside ロラン / ダカラン / ランチャ－ → メガバズ－カ란チャ－",
    "スン": "inside レッスン",
    "はい": "used as は+い characters in シグはいいの → シグ네いの",
    "マス": "inside longer katakana runs",
    "コア": "inside longer katakana runs",
    "カイ": "inside longer names",
    "セロ": "inside longer names",
    "オム": "inside longer names",
    "カツ": "inside longer names",
    "ステロ": "inside longer names",
    "シャル": "inside longer names",
    "ロナ": "inside longer names",
    "ス－ン": "inside longer names",
}

CATALOGS: Sequence[str] = (
    "unit_names_ko",
    "weapon_names_ko",
    "ui_system_ko",
    "ui_battle_terms_ko",
    "ui_menu_terms_ko",
    "ui_menu_terms2_ko",
    "ui_menu_terms3_ko",
)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run", action="store_true", help="report without writing catalogs"
    )
    args = ap.parse_args(argv)

    removed: List[dict] = []
    touched: List[str] = []

    for name in CATALOGS:
        path = ROOT / f"data/{name}.json"
        if not path.exists():
            continue
        spec = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=collections.OrderedDict,
        )
        changed = False
        for key in ("entries", "fragments"):
            rows = spec.get(key)
            if not rows:
                continue
            keep = []
            for row in rows:
                jp = row.get("jp")
                if jp in FRAGMENTS:
                    removed.append(
                        {
                            "catalog": name,
                            "section": key,
                            "jp": jp,
                            "ko": row.get("ko"),
                            "reason": FRAGMENTS[jp],
                        }
                    )
                    changed = True
                    continue
                keep.append(row)
            spec[key] = keep
        if changed:
            touched.append(name)
            if not args.dry_run:
                path.write_text(
                    json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

    payload = {
        "_note": (
            "Mid-word kana fragments removed from the UI catalogs. Not deleted: "
            "reinstate a row only together with whole-word entries for every "
            "parent slot that composes it."
        ),
        "generated_by": "tools/quarantine_fragment_entries.py",
        "removed_count": len(removed),
        "removed": removed,
    }
    if not args.dry_run:
        QUARANTINE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"{'DRY RUN ' if args.dry_run else ''}removed {len(removed)} row(s)")
    for row in removed:
        print(f"  {row['catalog']:20s} {row['jp']:6s} -> {row['ko']}")
    print(f"catalogs touched: {touched}")
    if not args.dry_run:
        print(f"quarantine → {QUARANTINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
