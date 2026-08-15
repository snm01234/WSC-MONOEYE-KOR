#!/usr/bin/env python3
"""
Build the aux BODY catalog — translations keyed by the text after the prefix.

READ-ONLY with respect to ROMs.

``build_aux_catalog.py`` keys translations by the whole record, which only works
for records that are provably text-initial. This one covers the records that
carry a non-text prefix (a speaker/portrait/control field), so the key is the
**body** and the prefix stays Japanese bytes in the ROM by design:

    record :  17 34 18 | いや、大したことじゃないんだが、
    key    :             いや、大したことじゃないんだが、

The record set and the split come from ``out/script/aux_prefix_rule.json``
(``measure_aux_prefix_rule.py`` on top of the per-record proofs in
``prove_aux_prefix.py``). Nothing is re-derived here; ``apply_aux_ko.py``
independently recomputes every prefix before it writes.

Same two rules as the rest of the pipeline, both learned the hard way:

* **the Japanese side is never retyped.** ``--emit`` writes the exact ordered
  unique bodies and the translator fills a parallel array in
  ``data/aux_body_ko_values.json``. A mistyped key fails silently — the long
  vowel is ``－`` (fullwidth minus) and some katakana words use hiragana ``べ``/``ぺ``
  — so a hand-copied key just leaves the line Japanese with no error. Length
  mismatch is a hard error, because a shifted array mistranslates everything
  after the shift.
* **encodability is checked before the catalog is written, not at apply time.**
  The name75 pass hit ``encode_fail`` on rows whose Korean used syllables absent
  from the patched font pool (숏 잭 륜 뱀 퀀). That is invisible to the eye, so
  ``--build`` proves every string against the installed TBL and marker and
  refuses the whole file if any row fails.

Also refused: a Korean string containing the long-vowel ``－``. Carrying the
Japanese elongation over produces ``이 녀석－－！！``, which
``scan_mixed_script_artifacts`` scores as ``broken_word`` and cannot tell from a
truncated katakana word.

Modes:
  --emit   → out/script/aux_body_ordered.json (+ .txt)
  --build  → data/aux_body_ko.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

DEFAULT_RULE = ROOT / "out/script/aux_prefix_rule.json"
DEFAULT_ORDERED = ROOT / "out/script/aux_body_ordered.json"
DEFAULT_VALUES = ROOT / "data/aux_body_ko_values.json"
DEFAULT_OUT = ROOT / "data/aux_body_ko.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"

LONG_VOWEL = "ー－"


def collect_unique(rule: dict) -> List[dict]:
    """Ordered unique body texts with the sites that hold them."""
    sites: Dict[str, List[str]] = collections.OrderedDict()
    banks: Dict[str, set] = collections.defaultdict(set)
    for bank, rows in rule.get("records", {}).items():
        for r in rows:
            body = r["body_jp"]
            sites.setdefault(body, []).append(r["abs"])
            banks[body].add(bank)
    return [
        {"jp": jp, "sites": s, "banks": sorted(banks[jp])} for jp, s in sites.items()
    ]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rule", type=Path, default=DEFAULT_RULE)
    ap.add_argument("--ordered", type=Path, default=DEFAULT_ORDERED)
    ap.add_argument("--values", type=Path, default=DEFAULT_VALUES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc")

    rule = json.loads(args.rule.read_text(encoding="utf-8"))
    if not rule.get("ok"):
        raise SystemExit(
            f"{args.rule} is not ok — rerun tools/measure_aux_prefix_rule.py"
        )
    rows = collect_unique(rule)

    if args.emit:
        args.ordered.parent.mkdir(parents=True, exist_ok=True)
        args.ordered.write_text(
            json.dumps(
                {
                    "_note": "exact ordered unique aux BODY texts (prefix already "
                    "removed); supply a parallel 'ordered_ko' array of the same "
                    "length in data/aux_body_ko_values.json (empty = leave "
                    "Japanese). Do not retype the Japanese.",
                    "count": len(rows),
                    "records_covered": sum(len(r["sites"]) for r in rows),
                    "texts": [r["jp"] for r in rows],
                    "sites": {r["jp"]: r["sites"] for r in rows},
                    "banks": {r["jp"]: r["banks"] for r in rows},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        args.ordered.with_suffix(".txt").write_text(
            "\n".join(
                f"{i}\t{len(r['sites'])}\t{','.join(r['banks'])}\t{r['jp']}"
                for i, r in enumerate(rows)
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"emitted {len(rows)} unique bodies covering "
            f"{sum(len(r['sites']) for r in rows)} records → {args.ordered}"
        )
        print(f"numbered list → {args.ordered.with_suffix('.txt')}")
        return 0

    if not args.build:
        raise SystemExit("pick --emit or --build")

    texts = json.loads(args.ordered.read_text(encoding="utf-8"))["texts"]
    spec = json.loads(args.values.read_text(encoding="utf-8"))
    ko_list = spec.get("ordered_ko") or []
    if len(ko_list) != len(texts):
        raise SystemExit(
            f"length mismatch: {len(texts)} texts vs {len(ko_list)} translations. "
            "A shifted array mistranslates every line after the shift — refusing."
        )

    tbl = Tbl.load(args.tbl)
    marker = marker_code()

    entries: List[dict] = []
    problems: List[dict] = []
    for i, (jp, ko) in enumerate(zip(texts, ko_list)):
        ko = (ko or "").strip()
        if not ko:
            continue
        if any(ch in ko for ch in LONG_VOWEL):
            problems.append(
                {"index": i, "jp": jp, "ko": ko, "problem": "long_vowel_in_korean"}
            )
            continue
        try:
            enc = try_encode_ko_text(
                normalize_ko_text(ko),
                tbl,
                hangul_marker_code=marker,
                hangul_marker_mode="run",
            )
        except Exception as exc:  # noqa: BLE001
            problems.append(
                {"index": i, "jp": jp, "ko": ko, "problem": f"encode_error:{exc}"}
            )
            continue
        if not enc:
            problems.append(
                {"index": i, "jp": jp, "ko": ko, "problem": "encode_fail"}
            )
            continue
        entries.append({"jp": jp, "ko": ko})

    if problems:
        print(f"REFUSING: {len(problems)} row(s) failed the pre-write checks")
        for p in problems[:20]:
            print(f"  [{p['index']}] {p['problem']}  {p['ko']!r}  (jp {p['jp'][:26]!r})")
        print(
            "\nencode_fail almost always means a Hangul syllable missing from the "
            "patched font pool (the name75 pass hit 숏 잭 륜 뱀 퀀). Substitute a "
            "syllable rather than extending the font."
        )
        return 1

    payload = {
        "description": (
            "Aux battle text BODIES — the sentence after a non-text prefix "
            "(speaker/portrait/control field). Keys are the body only; the prefix "
            "bytes stay in the ROM untouched. Banks 59 (mission dialogue), 5D/5E "
            "(pilot battle voice). Generated by tools/build_aux_body_catalog.py "
            "--build from data/aux_body_ko_values.json; do not hand-edit, edit the "
            "values file and rebuild."
        ),
        "_marker_note": (
            "marker is not declared here on purpose: the installed Hangul run "
            "marker is read from tools/hangul_marker.py."
        ),
        "generated_by": "tools/build_aux_body_catalog.py",
        "candidate_count": len(texts),
        "translated": len(entries),
        "untranslated": len(texts) - len(entries),
        "all_encodable_against": str(args.tbl),
        "marker": f"{marker:04X}",
        "entries": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"catalog: {len(entries)} translated / {len(texts)} bodies "
        f"({len(texts) - len(entries)} left Japanese) · all encodable"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
