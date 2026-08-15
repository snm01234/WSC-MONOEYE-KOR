#!/usr/bin/env python3
"""
Locate the LE16 pointer tables that address real text inside the aux banks.

READ-ONLY. This tool never opens a .wsc for writing.

Why this exists
---------------
Walking an aux bank as NUL-terminated strings finds ~60,000 "records", and a
character-class filter still passes ~16,000 of them — but most are graphics or
fixed tables whose bytes happen to decode to valid kana. Measured examples:
``アさ　機ュ``, ``たたたたたた…``, ``がおでヤぇ試　な``. Writing tokens over those is the
bank 64-69 failure mode again (padding walked as opcodes → event error 257/2049).

So do not guess which records are text. Find the code's own index: a run of LE16
values that (a) all land inside the same bank, (b) rise monotonically, and
(c) point at byte sequences that decode to coherent text. That triple is a
pointer table, and the strings it addresses are exactly the ones the game draws.

Coherence is judged on the DECODED text, not on the bytes:
  * hiragana ratio in a prose-like band — real Japanese here is hiragana-heavy,
    while table noise is katakana/kanji-heavy with stray hiragana
  * no ideographic space except trailing padding — garbage records are riddled
    with mid-string ``　``
  * no single character dominating, which is what ``たるそるレるドる…`` looks like

Report: ``out/script/aux_text_tables.json``.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from expand_dictionary import AUX_TOKEN_BANKS  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    SEG_DICT,
    Dictionary,
    Tbl,
    find_rom,
    le16,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/script/aux_text_tables.json"

HIRAGANA = re.compile(r"[\u3041-\u309f]")
KATAKANA = re.compile(r"[\u30a0-\u30ff]")
KANJI = re.compile(r"[\u4e00-\u9fff]")
JP = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
IDEO_SPACE = "\u3000"


def coherent(text: str) -> bool:
    """Does this decode look like a sentence the game would print?"""
    body = text.rstrip(IDEO_SPACE + " ")
    if len(body) < 5:
        return False
    if "<" in body:  # BADDICT / TRUNC / raw code escape
        return False
    if IDEO_SPACE in body:  # mid-string ideographic space = table noise
        return False
    if not JP.search(body):
        return False
    jp_chars = [c for c in body if JP.match(c)]
    if len(jp_chars) < 4:
        return False
    hira = sum(1 for c in body if HIRAGANA.match(c))
    ratio = hira / len(body)
    if not 0.25 <= ratio <= 0.95:
        return False
    counts = collections.Counter(body)
    if counts.most_common(1)[0][1] / len(body) > 0.25:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None, help="default: original")
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-run", type=int, default=16, help="pointers per table")
    ap.add_argument(
        "--min-coherent",
        type=float,
        default=0.6,
        help="fraction of a run's targets that must decode coherently",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this scan is read-only")

    rom = bytes(load_rom(args.rom or find_rom(ROOT)))
    tbl = Tbl.load(args.tbl)
    d = Dictionary(rom)
    sb = stock_base(rom)

    text_cache: Dict[int, str] = {}

    def text_at(logical: int) -> str:
        hit = text_cache.get(logical)
        if hit is not None:
            return hit
        out = ""
        got = read_encoded_z_safe(rom, sb + logical, max_len=128)
        if got:
            try:
                out = d.expand(got[0], tbl)
            except Exception:
                out = ""
        text_cache[logical] = out
        return out

    tables: List[dict] = []

    for seg in AUX_TOKEN_BANKS:
        if seg == SEG_DICT:
            continue
        base = sb + seg * BANK_SIZE
        bank = rom[base : base + BANK_SIZE]
        # Strings are NOT stored in pointer order here, so a monotonically
        # rising run finds nothing (measured: 0 tables). The real signal is
        # simply "a window of consecutive LE16 words that all land in-bank
        # and mostly address coherent text".
        i = 0
        accepted: List[tuple[int, int]] = []  # (table_off, end_off)
        while i + args.min_run * 2 <= BANK_SIZE:
            vals = [le16(bank, i + k * 2) for k in range(args.min_run)]
            if any(v < 0x100 or v >= BANK_SIZE for v in vals):
                i += 2
                continue
            texts = [text_at(seg * BANK_SIZE + v) for v in vals]
            frac = sum(1 for t in texts if coherent(t)) / len(texts)
            if frac < args.min_coherent:
                i += 2
                continue
            # grow the window forward while it keeps addressing text
            end = i + args.min_run * 2
            while end + 1 < BANK_SIZE:
                v = le16(bank, end)
                if v < 0x100 or v >= BANK_SIZE:
                    break
                if not coherent(text_at(seg * BANK_SIZE + v)):
                    break
                end += 2
            accepted.append((i, end))
            i = end

        for start, end in accepted:
            vals = [le16(bank, o) for o in range(start, end, 2)]
            logicals = [seg * BANK_SIZE + v for v in vals]
            texts = [text_at(x) for x in logicals]
            good = sum(1 for t in texts if coherent(t))
            uniq = sorted(set(logicals))
            tables.append(
                {
                    "bank": f"{seg:02X}",
                    "table_at": f"{seg:02X}:{start:04X}",
                    "pointers": len(vals),
                    "unique_targets": len(uniq),
                    "coherent_fraction": round(good / len(texts), 3),
                    "samples": [
                        {"abs": f"{a:06X}", "text": t[:48]}
                        for a, t in list(zip(logicals, texts))[:5]
                    ],
                    "targets": [f"{a:06X}" for a in uniq],
                }
            )

    by_bank: collections.Counter = collections.Counter()
    total_targets = 0
    for t in tables:
        by_bank[t["bank"]] += t["pointers"]
        total_targets += t["pointers"]

    report = {
        "generated_by": "tools/find_aux_text_tables.py",
        "read_only": True,
        "rom": str(args.rom or find_rom(ROOT)),
        "min_run": args.min_run,
        "min_coherent": args.min_coherent,
        "tables_found": len(tables),
        "addressed_records": total_targets,
        "pointers_by_bank": dict(sorted(by_bank.items())),
        "tables": tables,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"tables found     : {len(tables)}")
        print(f"records addressed: {total_targets}")
        print(f"by bank          : {dict(sorted(by_bank.items()))}")
        for t in tables[:12]:
            print(
                f"\n  {t['table_at']}  {t['pointers']} ptrs  "
                f"coherent {t['coherent_fraction']}"
            )
            for s in t["samples"][:3]:
                print(f"      {s['abs']}  {s['text']}")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
