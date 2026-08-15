#!/usr/bin/env python3
"""
Classify the ext3-era record rewrites inside a band, to target the next candidate.

READ-ONLY.

Manual bisection: banks ``61``-``62`` boot, banks ``66``-``69`` boot, banks
``61``-``65`` fail — so a rewrite in ``63``-``65`` breaks new game. Reverting every
record that ``looks_like_event_body`` flags did **not** help, so the offending
record looks like ordinary dialogue to that heuristic.

For each record whose bytes differ between ``pre_ext3`` and the target this reports
features of the **original** body that distinguish real prose from structured data:

``ctrl``        bytes below 0x20 that are not the known text controls
``has_0x08``    0x08 appears (record/box control in this engine's streams)
``kana_ratio``  share of units that decode to kana/kanji text
``tokens``      dictionary tokens present
``no_text``     nothing in the body decodes to a printable character
``ptr_site``    the record overlaps a free-space relocation pointer site
``pad_added``   padding bytes the rewrite introduced

Classes, most suspicious first:
  ``no_text``      original body decodes to nothing printable → almost certainly data
  ``ctrl_heavy``   many control bytes relative to length
  ``ptr_site``     record carries a relocation pointer the rewrite would bury
  ``prose``        everything else
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/changed_record_classes.json"

TEXT_CONTROLS = {0x00, 0x0A, 0x0D}
CTRL_HEAVY_RATIO = 0.5


def load_ptr_sites() -> set[int]:
    p = ROOT / "out/patch/free_space_pointer_allowlist.json"
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8"))
    return {int(s, 16) for s in data.get("pointer_allowlist", [])}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE)
    ap.add_argument("--tip", type=Path, default=DEFAULT_TIP)
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=0x630000)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=0x65FFFF)
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    jp = bytes(load_rom(args.jp))
    pre = bytes(load_rom(args.pre))
    tip = bytes(load_rom(args.tip))
    sj, sp, st = stock_base(jp), stock_base(pre), stock_base(tip)
    tbl = Tbl.load(args.tbl) if args.tbl.exists() else None
    jp_dict = Dictionary(jp)
    ptr_sites = load_ptr_sites()

    rows: List[dict] = []
    classes: Counter = Counter()

    cursor = args.lo
    while cursor <= args.hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        n = (got[1] - sj) - cursor + 1
        start = cursor
        cursor = (got[1] - sj) + 1

        before = pre[sp + start : sp + start + n]
        after = tip[st + start : st + start + n]
        if before == after:
            continue

        original = jp[sj + start : sj + start + n]
        prefix, body, _kind = split_prefix_body(original[:-1])

        ctrl = sum(1 for b in body if b < 0x20 and b not in TEXT_CONTROLS)
        has_08 = 0x08 in body
        tokens = sum(1 for i, b in enumerate(body) if is_dict_token(b))
        text = None
        if tbl is not None:
            try:
                text = jp_dict.expand(body, tbl)
            except Exception:  # pragma: no cover - informational
                text = None
        printable = 0
        if text:
            printable = sum(
                1 for ch in text if ch not in ("\u3000", "\x00") and ch.isprintable()
            )
        overlaps_ptr = any(start <= s < start + n for s in ptr_sites)

        if printable == 0:
            klass = "no_text"
        elif body and ctrl / len(body) >= CTRL_HEAVY_RATIO:
            klass = "ctrl_heavy"
        elif overlaps_ptr:
            klass = "ptr_site"
        else:
            klass = "prose"
        classes[klass] += 1

        rows.append(
            {
                "abs": f"{start:06X}",
                "len": n,
                "class": klass,
                "ctrl": ctrl,
                "body_len": len(body),
                "has_0x08": has_08,
                "tokens": tokens,
                "printable_chars": printable,
                "overlaps_ptr_site": overlaps_ptr,
                "orig_text": (text or "")[:48],
                "orig_hex": original.hex()[:64],
                "tip_hex": after.hex()[:64],
            }
        )

    report = {
        "generated_by": "tools/classify_changed_records.py",
        "read_only": True,
        "band": [f"{args.lo:06X}", f"{args.hi:06X}"],
        "changed_records": len(rows),
        "by_class": dict(classes),
        "class_order_most_suspicious_first": [
            "no_text",
            "ctrl_heavy",
            "ptr_site",
            "prose",
        ],
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"band {report['band'][0]}-{report['band'][1]}")
    print(f"changed records: {len(rows)}  {dict(classes)}")
    per_bank: Dict[str, Counter] = {}
    for r in rows:
        per_bank.setdefault(r["abs"][:2], Counter())[r["class"]] += 1
    for bank, c in sorted(per_bank.items()):
        print(f"  bank {bank}: {dict(c)}")
    for klass in ("no_text", "ctrl_heavy", "ptr_site"):
        sample = [r for r in rows if r["class"] == klass][:8]
        if sample:
            print(f"\n{klass}:")
            for r in sample:
                print(f"  {r['abs']} len={r['len']:>3} ctrl={r['ctrl']}/{r['body_len']} "
                      f"tok={r['tokens']} text={r['orig_text']!r}")
                print(f"    orig {r['orig_hex'][:48]}")
                print(f"    tip  {r['tip_hex'][:48]}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
