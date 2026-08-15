#!/usr/bin/env python3
"""
Build the name75 base lexicon by zipping an index-aligned Korean list onto the
exact Japanese base strings.

READ-ONLY with respect to ROMs.

Why index alignment. The base strings carry encoding quirks that are easy to
mistype: the long-vowel mark is ``－`` (not ``ー``), and several katakana words use
a hiragana ``べ``/``ぺ`` (``キュべレイ``, ``ガ－べラ・テトラ``, ``サ－ぺント``). A lexicon
keyed by hand-retyped Japanese would silently miss those rows — the composer would
just skip them and the record would stay Japanese with no error. So the Japanese
side is never retyped: ``--emit-bases`` writes the exact ordered list, and the
translator supplies a parallel array.

Modes
-----
``--emit-bases``   write the ordered base list (and a numbered .txt to translate against)
``--build``        zip ordered_ko onto that list → data/name75_base_ko.json

Length mismatch is a hard error: a shifted array would mislabel every unit after
the shift, which is worse than translating nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_WORKLIST = ROOT / "out/script/name75_untranslated.json"
DEFAULT_BASES = ROOT / "out/script/name75_bases_ordered.json"
DEFAULT_VALUES = ROOT / "data/name75_base_ko_values.json"
DEFAULT_OUT = ROOT / "data/name75_base_ko.json"
DEFAULT_CATALOG = ROOT / "data/name75_terms_ko.json"
DEFAULT_UNMATCHED = ROOT / "out/script/name75_unmatched_ordered.json"

TAG = re.compile(r"<[^>]*>")
BLOCK = "█"


def base_of(text: str) -> str:
    """Strip variant markers, parentheticals and control tags."""
    stripped = TAG.sub("", text)
    stripped = stripped.replace(BLOCK, "")
    stripped = re.sub(r"（[^）]*）", "", stripped)
    return stripped.strip()


def ordered_bases(worklist: Path) -> List[str]:
    rows = json.loads(worklist.read_text(encoding="utf-8"))["applicable_unique"]
    seen: Dict[str, None] = {}
    for row in rows:
        if "BADDICT" in row["jp"]:
            continue
        b = base_of(row["jp"])
        if b:
            seen.setdefault(b, None)
    return sorted(seen)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST)
    ap.add_argument("--bases", type=Path, default=DEFAULT_BASES)
    ap.add_argument("--values", type=Path, default=DEFAULT_VALUES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--emit-bases", action="store_true")
    ap.add_argument(
        "--emit-unmatched",
        action="store_true",
        help="write the ordered list of strings the composer could not build "
        "(control-tag variants, trailing Ｈ/Ｓ suffixes, mis-walked data) so an "
        "index-aligned 'unmatched_ko' array can supply full-string overrides",
    )
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--unmatched", type=Path, default=DEFAULT_UNMATCHED)
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args(argv)

    if args.emit_unmatched:
        blob = json.loads(args.catalog.read_text(encoding="utf-8"))
        rows = list(blob.get("unmatched") or [])
        args.unmatched.parent.mkdir(parents=True, exist_ok=True)
        args.unmatched.write_text(
            json.dumps(
                {
                    "_note": "exact strings the composer could not build; supply "
                    "a parallel 'unmatched_ko' array of the same length in "
                    "data/name75_base_ko_values.json (empty = leave Japanese)",
                    "count": len(rows),
                    "unmatched": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        args.unmatched.with_suffix(".txt").write_text(
            "\n".join(f"{i}\t{r}" for i, r in enumerate(rows)) + "\n",
            encoding="utf-8",
        )
        print(f"emitted {len(rows)} unmatched → {args.unmatched}")
        return 0

    if args.emit_bases:
        bases = ordered_bases(args.worklist)
        args.bases.parent.mkdir(parents=True, exist_ok=True)
        args.bases.write_text(
            json.dumps(
                {
                    "_note": "exact ordered base strings; supply a parallel "
                    "'ordered_ko' array of the same length",
                    "count": len(bases),
                    "bases": bases,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        txt = "\n".join(f"{i}\t{b}" for i, b in enumerate(bases))
        args.bases.with_suffix(".txt").write_text(txt + "\n", encoding="utf-8")
        print(f"emitted {len(bases)} bases → {args.bases}")
        print(f"numbered list → {args.bases.with_suffix('.txt')}")
        return 0

    if not args.build:
        raise SystemExit("pick --emit-bases or --build")

    bases = json.loads(args.bases.read_text(encoding="utf-8"))["bases"]
    spec = json.loads(args.values.read_text(encoding="utf-8"))
    ko_list = spec.get("ordered_ko") or []
    if len(ko_list) != len(bases):
        raise SystemExit(
            f"length mismatch: {len(bases)} bases vs {len(ko_list)} translations. "
            "A shifted array mislabels every entry after the shift — refusing."
        )

    pairs = {
        jp: ko.strip()
        for jp, ko in zip(bases, ko_list)
        if ko and ko.strip()
    }
    overrides = {k: v for k, v in (spec.get("overrides") or {}).items() if v}
    qualifiers = {k: v for k, v in (spec.get("qualifiers") or {}).items() if v}

    # Index-aligned overrides for the strings the composer cannot build.
    unmatched_ko = spec.get("unmatched_ko")
    if unmatched_ko is not None:
        if not args.unmatched.exists():
            raise SystemExit(
                f"unmatched_ko supplied but {args.unmatched} is missing; run "
                "--emit-unmatched first"
            )
        rows = json.loads(args.unmatched.read_text(encoding="utf-8"))["unmatched"]
        if len(unmatched_ko) != len(rows):
            raise SystemExit(
                f"length mismatch: {len(rows)} unmatched vs "
                f"{len(unmatched_ko)} translations — refusing"
            )
        for jp, ko in zip(rows, unmatched_ko):
            if ko and ko.strip():
                overrides[jp] = ko.strip()

    payload = {
        "description": (
            "name75 base lexicon. 'bases' maps a base string (variant markers, "
            "parentheticals and control tags removed) to Korean; "
            "tools/compose_name75_catalog.py re-attaches trailing █ markers. "
            "'overrides' are full-string translations for irregular forms."
        ),
        "generated_by": "tools/build_name75_lexicon.py --build",
        "base_count": len(bases),
        "translated": len(pairs),
        "untranslated": len(bases) - len(pairs),
        "override_count": len(overrides),
        "qualifier_count": len(qualifiers),
        "bases": pairs,
        "qualifiers": qualifiers,
        "overrides": overrides,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"lexicon: {len(pairs)} translated / {len(bases)} bases "
        f"({len(bases) - len(pairs)} left Japanese), {len(overrides)} overrides"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
