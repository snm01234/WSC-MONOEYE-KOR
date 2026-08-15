#!/usr/bin/env python3
"""
Probe the opening / early-band records: room, current encoding, dict sharing.

READ-ONLY.

Answers the question behind the opening regression: the opening narration and the
early proof-of-concept lines were localized by re-pointing **stock 5F dictionary
slots**, which the intermission / HUD / help records also read. This tool reports,
per record, whether the standard path (a 4-byte ext3 token ``E5 18 xx yy``, the
same mechanism every other dialogue band uses) fits, so those lines can stop
touching the shared dictionary at all.

Columns:
  room      record length minus prefix length — a 4-byte ext3 token needs >= 4
  encoding  ext3 / stock_dict / ext_dict / plain
  index     dictionary index when the record carries a dict token
  shared    the index is also read by an aux (50-5E, 76) or name75 record
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import DEFAULT_REF_REGIONS, build_dict_token_locs  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    EXT3_INDEX_BASE,
    Tbl,
    dict_index_from_ext3_token,
    dict_index_from_token,
    is_dict_token,
    is_ext3_magic,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/opening_record_probe.json"

# Opening narration + the early proof-of-concept window, i.e. everything the
# dedicated opening tools touched instead of going through the normal band path.
DEFAULT_LO = 0x6040A5
DEFAULT_HI = 0x60456A

EXT3_TOKEN_LEN = 4


def classify_payload(payload: bytes) -> tuple[str, int | None]:
    """First token in a record body: encoding kind and dictionary index."""
    for i in range(len(payload) - 1):
        lead, trail = payload[i], payload[i + 1]
        if is_ext3_magic(lead, trail) and i + 3 < len(payload):
            return "ext3", dict_index_from_ext3_token(
                lead, trail, payload[i + 2], payload[i + 3]
            )
        if is_dict_token(lead):
            idx = dict_index_from_token(lead, trail)
            # A 2-byte token below the ext3 index base addresses the stock 5F
            # table (or the bank10 extension above the stock count).
            return ("stock_dict" if idx < EXT3_INDEX_BASE else "ext_dict"), idx
    return "plain", None


def probe(
    jp_path: Path, tgt_path: Path, lo: int, hi: int, *, tbl_path: Path | None
) -> dict:
    jp = bytes(load_rom(jp_path))
    tgt = bytes(load_rom(tgt_path))
    sj, st = stock_base(jp), stock_base(tgt)

    locs = build_dict_token_locs(jp, regions=DEFAULT_REF_REGIONS)
    non_dialogue_indices = {
        idx
        for idx, refs in locs.items()
        if any(r.region in ("aux", "name75") for r in refs)
    }

    tbl = Tbl.load(tbl_path) if tbl_path and tbl_path.exists() else None
    d_tgt = make_dictionary_ext3(
        tgt,
        load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )

    rows: List[dict] = []
    cursor = lo
    while cursor <= hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        original, term = got
        prefix, body, kind = split_prefix_body(original)
        room = len(original) - len(prefix)

        tgot = read_encoded_z_safe(tgt, st + cursor, max_len=256)
        tpayload = tgot[0] if tgot else b""
        enc, idx = classify_payload(tpayload)

        text = None
        if tbl is not None and tpayload:
            try:
                text = d_tgt.expand(split_prefix_body(tpayload)[1], tbl).rstrip("\u3000")
            except Exception:  # pragma: no cover - informational
                text = None

        rows.append(
            {
                "abs": f"{cursor:06X}",
                "record_len": len(original),
                "prefix_len": len(prefix),
                "room": room,
                "kind": kind,
                "ext3_fits": room >= EXT3_TOKEN_LEN,
                "target_encoding": enc,
                "dict_index": f"{idx:04X}" if idx is not None else None,
                "shared_with_non_dialogue": bool(
                    idx is not None and idx in non_dialogue_indices
                ),
                "target_text": text,
            }
        )
        cursor = (term - sj) + 1

    fits = [r for r in rows if r["ext3_fits"]]
    stock = [r for r in rows if r["target_encoding"] == "stock_dict"]
    shared = [r for r in rows if r["shared_with_non_dialogue"]]
    return {
        "generated_by": "tools/probe_opening_records.py",
        "read_only": True,
        "original": str(jp_path),
        "target": str(tgt_path),
        "band": [f"{lo:06X}", f"{hi:06X}"],
        "records": len(rows),
        "counts": {
            "ext3_token_fits": len(fits),
            "ext3_token_too_short": len(rows) - len(fits),
            "target_uses_stock_dict": len(stock),
            "target_uses_ext3": sum(
                1 for r in rows if r["target_encoding"] == "ext3"
            ),
            "target_uses_ext_dict": sum(
                1 for r in rows if r["target_encoding"] == "ext_dict"
            ),
            "target_plain": sum(1 for r in rows if r["target_encoding"] == "plain"),
            "shared_with_non_dialogue": len(shared),
        },
        "room_histogram": {
            str(k): sum(1 for r in rows if r["room"] == k)
            for k in sorted({r["room"] for r in rows})
        },
        "rows": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=DEFAULT_LO)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=DEFAULT_HI)
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")
    rep = probe(args.jp, args.target, args.lo, args.hi, tbl_path=args.tbl)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    c = rep["counts"]
    print(f"band    : {rep['band'][0]}–{rep['band'][1]}  records={rep['records']}")
    print(
        f"ext3 fit: {c['ext3_token_fits']} fit / {c['ext3_token_too_short']} too short "
        f"(needs room >= {EXT3_TOKEN_LEN})"
    )
    print(
        f"encoding: stock_dict={c['target_uses_stock_dict']} ext3={c['target_uses_ext3']} "
        f"ext_dict={c['target_uses_ext_dict']} plain={c['target_plain']}"
    )
    print(f"shared  : {c['shared_with_non_dialogue']} records use an index an "
          f"aux/name75 record also reads")
    print(f"room histogram: {rep['room_histogram']}")
    print("\nfirst 25 records:")
    for r in rep["rows"][:25]:
        print(
            f"  {r['abs']} len={r['record_len']:>3} prefix={r['prefix_len']} "
            f"room={r['room']:>3} fit={str(r['ext3_fits']):5s} "
            f"{r['target_encoding']:10s} idx={r['dict_index'] or '----'} "
            f"shared={str(r['shared_with_non_dialogue']):5s} {r['target_text']!r}"
        )
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
