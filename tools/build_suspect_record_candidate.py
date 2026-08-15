#!/usr/bin/env python3
"""
Candidate that reverts only the *structurally suspect* record rewrites in a band.

READ-ONLY with respect to the tip; candidates go to ``out/patch/ab/``.

Bisection reached bank ``64``: ``h2_640000_64FFFF`` fails new game while banks 63
and 65 alone are fine. ``looks_like_event_body`` does not flag the offender, so a
different discriminator is needed.

The one used here: a genuine dialogue record in this engine almost always carries
at least one dictionary token, and its body decodes to coherent text. A record with
**no dictionary token** and a high share of control bytes that decodes to nonsense
is structured data the extraction mistook for prose — e.g. ``64:0505``, whose
original body is 13 bytes with 9 control bytes, no tokens, and decodes to
``をん－の買の－な買はと``.

Selectors (any match makes a record suspect, all judged on the ORIGINAL body):

``--nul-in-body``   a ``00`` byte inside the body. This one is not a heuristic: a
                    zstring payload cannot contain its own terminator, so such a
                    "record" is misparsed data. Measured example ``64:0860``, body
                    ``15 19 6b 08 e4 00 05 72 08 e4 00 08 04 80 08``.
``--no-tokens``     no dictionary token in the body. Suggestive but **not** safe on
                    its own: real prose written in raw codes also has none, e.g.
                    ``60:5786`` -> ``艦を近づけろ！``. Restrict it with ``--banks``.
``--banks``         only consider records in these banks (hex, comma separated).

A control-byte ratio was tried and discarded: in this encoding bytes below 0x20 are
ordinary text characters (``60:A9A2`` -> ``確か、ご幼少のころに`` is 5/9 "control"),
so the ratio does not separate prose from data.

The candidate keeps every other rewrite in the band, so if it boots the suspect
set contains the offender and the list is small enough to inspect directly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)

AB = ROOT / "out/patch/ab"
DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_BASE = AB / "h2_640000_64FFFF.wsc"
DEFAULT_OUT = ROOT / "out/patch/suspect_record_candidate.json"

TEXT_CONTROLS = {0x00, 0x0A, 0x0D}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE)
    ap.add_argument(
        "--base",
        type=Path,
        default=DEFAULT_BASE,
        help="failing candidate to strip suspects out of",
    )
    ap.add_argument("--lo", type=lambda s: int(s, 16), default=0x640000)
    ap.add_argument("--hi", type=lambda s: int(s, 16), default=0x64FFFF)
    ap.add_argument(
        "--nul-in-body",
        dest="nul",
        action="store_true",
        default=True,
        help="refuse records whose original body contains a 00 byte (default on)",
    )
    ap.add_argument("--no-nul-in-body", dest="nul", action="store_false")
    ap.add_argument(
        "--no-tokens",
        action="store_true",
        help="also refuse records with no dictionary token — pair with --banks",
    )
    ap.add_argument(
        "--banks",
        default="",
        help="restrict --no-tokens to these banks, e.g. 64,65,66,67,68,69",
    )
    ap.add_argument("--min-len", type=int, default=1)
    ap.add_argument("--name", default="j1_no_suspects")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    for p in (args.jp, args.pre, args.base):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    jp = bytes(load_rom(args.jp))
    pre = bytes(load_rom(args.pre))
    rom = bytearray(load_rom(args.base))
    sj, sp, sb = stock_base(jp), stock_base(pre), stock_base(rom)
    tbl = Tbl.load(args.tbl) if args.tbl.exists() else None
    jp_dict = Dictionary(jp)
    no_token_banks = {
        int(b, 16) for b in (s.strip() for s in args.banks.split(",")) if b
    }

    reverted: List[dict] = []
    cursor = args.lo
    while cursor <= args.hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        n = (got[1] - sj) - cursor + 1
        start = cursor
        cursor = (got[1] - sj) + 1

        cur = bytes(rom[sb + start : sb + start + n])
        want = pre[sp + start : sp + start + n]
        if cur == want:
            continue

        original = jp[sj + start : sj + start + n]
        _prefix, body, _ = split_prefix_body(original[:-1])
        if len(body) < args.min_len:
            continue
        tokens = sum(1 for b in body if is_dict_token(b))
        ctrl = sum(1 for b in body if b < 0x20 and b not in TEXT_CONTROLS)
        ratio = ctrl / len(body) if body else 0.0

        reason = None
        if args.nul and 0 in body:
            reason = "nul_in_body"
        elif args.no_tokens and tokens == 0 and (start >> 16) in no_token_banks:
            reason = "no_dict_token"
        if reason is None:
            continue

        rom[sb + start : sb + start + n] = want
        text = None
        if tbl is not None:
            try:
                text = jp_dict.expand(body, tbl)
            except Exception:  # pragma: no cover
                text = None
        reverted.append(
            {
                "abs": f"{start:06X}",
                "len": n,
                "reason": reason,
                "tokens": tokens,
                "ctrl": ctrl,
                "body_len": len(body),
                "ctrl_ratio": round(ratio, 3),
                "orig_text": (text or "")[:40],
                "orig_hex": original.hex(),
                "was": cur.hex(),
            }
        )

    cs = update_ws_checksum(rom)
    dest = AB / f"{args.name}.wsc"
    dest.write_bytes(rom)

    report = {
        "generated_by": "tools/build_suspect_record_candidate.py",
        "base": str(args.base),
        "band": [f"{args.lo:06X}", f"{args.hi:06X}"],
        "selectors": {
            "nul_in_body": bool(args.nul),
            "no_tokens": bool(args.no_tokens),
            "no_token_banks": sorted(f"{b:02X}" for b in no_token_banks),
            "min_len": args.min_len,
        },
        "by_reason": {
            r: sum(1 for x in reverted if x["reason"] == r)
            for r in sorted({x["reason"] for x in reverted})
        },
        "candidate": {"name": args.name, "path": str(dest), "checksum": f"{cs:04X}"},
        "reverted": len(reverted),
        "bytes_reverted": sum(r["len"] for r in reverted),
        "sites": reverted,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"base {args.base.name}  band {report['band'][0]}-{report['band'][1]}")
    print(f"reverted {len(reverted)} suspect records ({report['bytes_reverted']} B)")
    print(f"candidate {args.name} checksum {cs:04X} → {dest}")
    for r in reverted[:15]:
        print(f"  {r['abs']} len={r['len']:>3} tok={r['tokens']} "
              f"ctrl={r['ctrl']}/{r['body_len']} text={r['orig_text']!r}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
