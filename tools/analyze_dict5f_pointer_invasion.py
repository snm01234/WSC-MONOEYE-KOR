#!/usr/bin/env python3
"""
Analyze the stock 5F dictionary pointer invasion (requirements 2.6, 3.2, 3.10).

READ-ONLY. This tool never opens a .wsc for writing.

The stock dictionary in bank ``5F`` is shared: one index is expanded by dialogue
records, by the intermission / battle-HUD / help zstrings in the aux banks
(``50–5F``, ``76``) and by the name75 unit/weapon tables. Moving the pointer for
an index therefore changes the text every consumer of that index renders — which
is hypothesis A3 of the design (intermission UI corruption).

For every pointer that differs from the original this tool answers three things:

1. who consumes the index in the ORIGINAL ROM — dialogue only, or also a
   non-dialogue (aux / name75) record,
2. whether restoring the original pointer is even possible, i.e. whether the
   original phrase bytes still sit at the original offset in the target,
3. what the pointer match rate would become if only the non-dialogue-consumed
   indices were restored (requirement 3.10 floor is 3,802 / 3,831).

Report: ``out/patch/dict5f_pointer_analysis.json``.
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

from expand_dictionary import DEFAULT_REF_REGIONS, build_dict_token_locs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_END,
    DICT_PTR_START,
    SEG_DICT,
    Tbl,
    Dictionary,
    le16,
    load_rom,
    stock_base,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/dict5f_pointer_analysis.json"

PTR_GATE_MIN = 3802
NON_DIALOGUE_REGIONS = ("aux", "name75")
MAX_LISTED = 400


def ptr_base(rom: bytes) -> int:
    return stock_base(rom) + SEG_DICT * BANK_SIZE + DICT_PTR_START


def pointer_count() -> int:
    return (DICT_PTR_END - DICT_PTR_START + 1) // 2


def read_pointers(rom: bytes, n: int) -> List[int]:
    base = ptr_base(rom)
    return [le16(rom, base + i * 2) for i in range(n)]


def phrase_at(rom: bytes, off: int, limit: int = 256) -> bytes:
    """NUL-terminated phrase bytes at a bank-local 5F offset."""
    base = stock_base(rom) + SEG_DICT * BANK_SIZE
    end = off
    while end < BANK_SIZE and end - off < limit and rom[base + end] != 0:
        end += 1
    return bytes(rom[base + off : base + end])


def analyze(
    jp_path: Path, pre_path: Path, tgt_path: Path, *, tbl_path: Path | None
) -> dict:
    jp = bytes(load_rom(jp_path))
    pre = bytes(load_rom(pre_path))
    tgt = bytes(load_rom(tgt_path))

    n = pointer_count()
    p_jp = read_pointers(jp, n)
    p_pre = read_pointers(pre, n)
    p_tgt = read_pointers(tgt, n)

    # Consumers are enumerated on the ORIGINAL: that is the reference set of
    # records the game reads, and the target cannot hide one by corrupting it.
    locs = build_dict_token_locs(jp, regions=DEFAULT_REF_REGIONS)

    tbl = None
    jp_dict = tgt_dict = None
    if tbl_path and tbl_path.exists():
        try:
            tbl = Tbl.load(tbl_path)
            jp_dict = Dictionary(jp)
            tgt_dict = Dictionary(tgt)
        except Exception:  # pragma: no cover - decoding is informational
            tbl = None

    changed: List[dict] = []
    cls_counter: Counter = Counter()
    restorable_counter: Counter = Counter()

    for idx in range(n):
        if p_jp[idx] == p_tgt[idx]:
            continue
        refs = locs.get(idx, [])
        regions = sorted({r.region for r in refs})
        non_dialogue = [r for r in refs if r.region in NON_DIALOGUE_REGIONS]
        dialogue = [r for r in refs if r.region == "script"]

        if non_dialogue:
            klass = "must_restore_non_dialogue_consumer"
        elif dialogue:
            klass = "dialogue_only"
        else:
            klass = "no_consumer_in_original"
        cls_counter[klass] += 1

        # Can the original pointer even be restored? Only if the original phrase
        # bytes still sit at the original offset in the target.
        jp_phrase = phrase_at(jp, p_jp[idx])
        tgt_at_jp_off = phrase_at(tgt, p_jp[idx])
        restorable = jp_phrase == tgt_at_jp_off
        restorable_counter[(klass, restorable)] += 1

        entry = {
            "index": f"{idx:04X}",
            "index_dec": idx,
            "ptr_original": f"{p_jp[idx]:04X}",
            "ptr_pre_ext3": f"{p_pre[idx]:04X}",
            "ptr_target": f"{p_tgt[idx]:04X}",
            "changed_by": (
                "PRE" if p_pre[idx] == p_tgt[idx] else
                ("EXT3" if p_pre[idx] == p_jp[idx] else "BOTH")
            ),
            "classification": klass,
            "consumer_regions": regions,
            "consumers": {
                "aux": sum(1 for r in refs if r.region == "aux"),
                "name75": sum(1 for r in refs if r.region == "name75"),
                "script": len(dialogue),
            },
            "non_dialogue_sites": [
                f"{r.abs >> 16:02X}:{r.abs & 0xFFFF:04X}" for r in non_dialogue[:8]
            ],
            "original_phrase_intact_at_original_offset": restorable,
        }
        if tbl is not None:
            try:
                entry["text_original"] = jp_dict.expand(jp_dict.raw_entry(idx), tbl)
                entry["text_target"] = tgt_dict.expand(tgt_dict.raw_entry(idx), tbl)
            except Exception:  # pragma: no cover
                pass
        changed.append(entry)

    must = [c for c in changed if c["classification"].startswith("must_restore")]
    must_restorable = [
        c for c in must if c["original_phrase_intact_at_original_offset"]
    ]
    match_now = n - len(changed)
    match_after_must = match_now + len(must_restorable)
    match_after_all = n - len(
        [c for c in changed if not c["original_phrase_intact_at_original_offset"]]
    )

    return {
        "ok": match_after_must >= PTR_GATE_MIN,
        "generated_by": "tools/analyze_dict5f_pointer_invasion.py",
        "read_only": True,
        "original": str(jp_path),
        "pre_ext3": str(pre_path),
        "target": str(tgt_path),
        "pointer_table": f"5F:{DICT_PTR_START:04X}-{DICT_PTR_END:04X}",
        "pointer_count": n,
        "gate_min_match": PTR_GATE_MIN,
        "match": {
            "now": match_now,
            "changed": len(changed),
            "if_only_non_dialogue_restored": match_after_must,
            "if_every_restorable_restored": match_after_all,
            "gate_reachable_by_non_dialogue_restore_only": match_after_must
            >= PTR_GATE_MIN,
        },
        "classification_counts": dict(sorted(cls_counter.items())),
        "changed_by_counts": dict(
            sorted(Counter(c["changed_by"] for c in changed).items())
        ),
        "restorability": {
            f"{k[0]}|phrase_intact={k[1]}": v
            for k, v in sorted(restorable_counter.items())
        },
        "must_restore": {
            "count": len(must),
            "restorable": len(must_restorable),
            "not_restorable": len(must) - len(must_restorable),
            "sites": must[:MAX_LISTED],
        },
        # Full list, always complete — repair_dict5f_pointers.py consumes this.
        "changed_all": [
            {
                "index": c["index"],
                "index_dec": c["index_dec"],
                "ptr_original": c["ptr_original"],
                "ptr_target": c["ptr_target"],
                "classification": c["classification"],
                "consumer_regions": c["consumer_regions"],
                "changed_by": c["changed_by"],
                "original_phrase_intact_at_original_offset": c[
                    "original_phrase_intact_at_original_offset"
                ],
            }
            for c in changed
        ],
        "changed_sample": changed[:MAX_LISTED],
        "note": "consumers enumerated on the ORIGINAL ROM via "
        "expand_dictionary.build_dict_token_locs(regions=script|name75|aux). "
        "'must_restore' = the index is read by a non-dialogue (aux/name75) record, "
        "so moving its pointer changes intermission / HUD / help text.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data/monoeye.tbl")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")
    for p in (args.jp, args.pre, args.target):
        if not p.exists():
            raise SystemExit(f"missing input ROM: {p}")

    rep = analyze(args.jp, args.pre, args.target, tbl_path=args.tbl)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    m = rep["match"]
    print(f"target        : {rep['target']}")
    print(f"pointer table : {rep['pointer_table']}  ({rep['pointer_count']} pointers)")
    print(
        f"match now     : {m['now']}/{rep['pointer_count']} "
        f"(changed {m['changed']}, gate min {rep['gate_min_match']})"
    )
    print("classification of changed pointers:")
    for k, v in rep["classification_counts"].items():
        print(f"  {k:38s} {v:>5}")
    print("changed by:")
    for k, v in rep["changed_by_counts"].items():
        print(f"  {k:38s} {v:>5}")
    print("restorability (original phrase still at original offset):")
    for k, v in rep["restorability"].items():
        print(f"  {k:52s} {v:>5}")
    mr = rep["must_restore"]
    print(
        f"must_restore  : {mr['count']} indices with a non-dialogue consumer "
        f"({mr['restorable']} restorable, {mr['not_restorable']} not)"
    )
    print(
        f"projected     : non-dialogue-only restore → {m['if_only_non_dialogue_restored']}"
        f"/{rep['pointer_count']}  |  every restorable → "
        f"{m['if_every_restorable_restored']}/{rep['pointer_count']}"
    )
    print(f"\n→ {args.out}")
    print(f"gate reachable by non-dialogue restore only = {rep['ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
