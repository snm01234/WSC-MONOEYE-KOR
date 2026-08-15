#!/usr/bin/env python3
"""
Find aux text by contiguity instead of by guessing at pointer tables.

READ-ONLY. This tool never opens a .wsc for writing.

The idea
--------
Per-record filters fail here: a character-class test passes ~16,000 aux records
and most are graphics/table bytes that happen to decode to kana
(``アさ　機ュ``, ``たたたた…``). Pointer-table detection also failed twice — strings
are not stored in pointer order, and windows of "in-bank LE16 words addressing
coherent text" turn out to be repeated data bytes (``591A1A``, ``562525``).

But there is a structural property garbage does not have: **real strings tile.**
A block of game text is a run of NUL-terminated records laid end to end, so
walking forward from the start of the block yields coherent record after coherent
record. Garbage only appears when the walk starts inside non-text data, and it
does not sustain — a long unbroken chain of coherent decodes is very unlikely
unless the region really is a string table.

So: walk each aux bank sequentially and keep only maximal runs of consecutive
records that decode coherently, requiring at least ``--min-run`` in a row. A
single non-coherent record is allowed inside a run (``--max-gap``) because short
labels and numeric rows legitimately appear between sentences.

The coherence test is the one validated in find_aux_text_tables.coherent(): it
accepts 10 of 12 known-real aux sentences, rejecting only katakana-heavy ones.

Report: ``out/script/aux_text_blocks.json``.
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

from expand_dictionary import AUX_TOKEN_BANKS, _walk_zstring_range  # noqa: E402
from find_aux_text_tables import coherent  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    SEG_DICT,
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
)
from mixed_residual_models import identify_rom  # noqa: E402

DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/script/aux_text_blocks.json"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None, help="default: original")
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-run", type=int, default=6)
    ap.add_argument("--max-gap", type=int, default=1)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this scan is read-only")

    rom_path = args.rom or find_rom(ROOT)
    original_identity = identify_rom(rom_path, "original")
    rom = bytes(load_rom(rom_path))
    tbl = Tbl.load(args.tbl)
    d = Dictionary(rom)

    blocks: List[dict] = []

    for seg in AUX_TOKEN_BANKS:
        if seg == SEG_DICT:
            continue
        recs = list(
            _walk_zstring_range(
                rom, seg * BANK_SIZE, (seg + 1) * BANK_SIZE, region="aux", max_len=128
            )
        )
        decoded: List[tuple[int, bytes, str, bool]] = []
        for logical, payload, _k in recs:
            try:
                text = d.expand(payload, tbl)
            except Exception:
                text = ""
            decoded.append((logical, payload, text, coherent(text)))

        i = 0
        n = len(decoded)
        while i < n:
            if not decoded[i][3]:
                i += 1
                continue
            j = i
            gap = 0
            last_good = i
            while j + 1 < n:
                if decoded[j + 1][3]:
                    j += 1
                    last_good = j
                    gap = 0
                    continue
                gap += 1
                if gap > args.max_gap:
                    break
                j += 1
            run = decoded[i : last_good + 1]
            good = [r for r in run if r[3]]
            if len(good) >= args.min_run:
                last_start, last_payload = run[-1][0], run[-1][1]
                blocks.append(
                    {
                        "bank": f"{seg:02X}",
                        "start": f"{run[0][0]:06X}",
                        # Keep legacy ``end`` for existing read-only tools. New
                        # proven-record consumers use the explicit exclusive
                        # Original-derived boundary below.
                        "end": f"{last_start:06X}",
                        "end_exclusive": f"{last_start + len(last_payload) + 1:06X}",
                        "records": len(run),
                        "coherent": len(good),
                        "samples": [
                            {"abs": f"{a:06X}", "bytes": len(p), "text": t[:46]}
                            for a, p, t, ok in good[:6]
                        ],
                        "targets": [
                            {"abs": f"{a:06X}", "bytes": len(p), "jp": t}
                            for a, p, t, ok in good
                        ],
                    }
                )
            i = last_good + 1

    by_bank: collections.Counter = collections.Counter()
    total = 0
    for b in blocks:
        by_bank[b["bank"]] += b["coherent"]
        total += b["coherent"]

    report = {
        "schema_version": 2,
        "generated_by": "tools/find_aux_text_blocks.py",
        "read_only": True,
        "rom": str(rom_path),
        "original_rom_identity": original_identity.to_json_data(),
        "block_end_semantics": "end_exclusive",
        "min_run": args.min_run,
        "max_gap": args.max_gap,
        "blocks_found": len(blocks),
        "coherent_records": total,
        "records_by_bank": dict(sorted(by_bank.items())),
        "blocks": blocks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"blocks found     : {len(blocks)}")
        print(f"coherent records : {total}")
        print(f"by bank          : {dict(sorted(by_bank.items()))}")
        for b in sorted(blocks, key=lambda x: -x["coherent"])[:10]:
            print(
                f"\n  {b['bank']} {b['start']}-{b['end']}  "
                f"{b['coherent']}/{b['records']} coherent"
            )
            for s in b["samples"][:4]:
                print(f"      {s['abs']} {s['bytes']:3d}B  {s['text']}")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
