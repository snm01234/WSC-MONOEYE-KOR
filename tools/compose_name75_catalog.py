#!/usr/bin/env python3
"""
Expand a hand-authored name75 base lexicon into a full jp→ko catalog.

READ-ONLY with respect to ROMs.

The bank-75 display table stores many mechanical variants of the same name:
``ガザＣ``, ``ガザＣ█``, ``ガザＣ██``, ``ガザＣ███``. The ``█`` glyphs are rank /
variant markers, not text. Authoring all 852 strings by hand would mean writing
the same unit name four times, so the lexicon holds the ~675 base names and this
tool re-attaches the markers.

Only ONE pattern is expanded automatically: ``base`` followed by trailing ``█``
markers. Anything else — a parenthetical like ``（ハイパ－）``, an embedded control
tag like ``<E62F>``, a trailing ``Ｈ``/``Ｓ`` suffix — must be given as an explicit
full-string override. That keeps the mechanical path trivially correct and makes
every irregular string a deliberate decision.

Unmatched strings are simply omitted, which means the record keeps its Japanese
text. Skipping is always safe; guessing is not. Several worklist rows are not text
at all but mis-walked table data (``コ　にすす``, ``の………　…　풰ラふか`` — that one
even contains Hangul bled in from an already-patched region), so silently leaving
anything unauthored alone is the required behaviour, not a limitation.

Output: ``data/name75_terms_ko.json`` (an ``entries`` catalog that
``tools/apply_name75_ko.py --names`` accepts).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

DEFAULT_LEXICON = ROOT / "data/name75_base_ko.json"
DEFAULT_WORKLIST = ROOT / "out/script/name75_untranslated.json"
DEFAULT_OUT = ROOT / "data/name75_terms_ko.json"

BLOCK = "█"

# base [markers] （qualifier） [markers]
PAREN = re.compile(
    r"^(?P<base>.*?)(?P<b1>█*)（(?P<qual>[^）]*)）(?P<b2>█*)$"
)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON)
    ap.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc")

    lex = json.loads(args.lexicon.read_text(encoding="utf-8"))
    bases: Dict[str, str] = lex.get("bases") or {}
    overrides: Dict[str, str] = lex.get("overrides") or {}
    quals: Dict[str, str] = lex.get("qualifiers") or {}

    work = json.loads(args.worklist.read_text(encoding="utf-8"))
    rows = work.get("applicable_unique") or []

    entries: List[dict] = []
    by_override = 0
    by_base = 0
    by_paren = 0
    unmatched: List[str] = []

    for row in rows:
        jp = row["jp"]
        ko = overrides.get(jp)
        if ko:
            by_override += 1
        else:
            core = jp.rstrip(BLOCK)
            markers = jp[len(core) :]
            hit = bases.get(core)
            if hit:
                ko = hit + markers
                by_base += 1
            else:
                # base [markers]（qualifier）[markers] — also mechanical, as long
                # as both the base and the qualifier are known.
                m = PAREN.match(jp)
                if not m:
                    unmatched.append(jp)
                    continue
                base_ko = bases.get(m.group("base"))
                qual_ko = quals.get(m.group("qual"))
                if not base_ko or not qual_ko:
                    unmatched.append(jp)
                    continue
                ko = (
                    base_ko
                    + m.group("b1")
                    + "（"
                    + qual_ko
                    + "）"
                    + m.group("b2")
                )
                by_paren += 1
        entries.append({"jp": jp, "ko": ko, "sites": len(row.get("sites") or [])})

    payload = {
        "description": (
            "name75 unit / weapon / pilot / terrain / skill / voice-line strings, "
            "composed by tools/compose_name75_catalog.py from "
            "data/name75_base_ko.json. Do not hand-edit: edit the lexicon and "
            "re-run the composer."
        ),
        "_marker_note": (
            "marker is not declared here on purpose: the installed Hangul run "
            "marker is read from tools/hangul_marker.py."
        ),
        "generated_by": "tools/compose_name75_catalog.py",
        "lexicon": str(args.lexicon.relative_to(ROOT)),
        "entry_count": len(entries),
        "from_override": by_override,
        "from_base_plus_markers": by_base,
        "from_base_plus_qualifier": by_paren,
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "entries": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(
            f"composed {len(entries)} entries (override {by_override}, "
            f"base+markers {by_base}, base+qualifier {by_paren}) | "
            f"unmatched {len(unmatched)} left Japanese"
        )
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
