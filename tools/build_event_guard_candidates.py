#!/usr/bin/env python3
"""
Candidates that undo the ext3 writes an event-record guard would have refused.

READ-ONLY with respect to the tip; candidates go to ``out/patch/ab/``.

Manual bisection narrowed the new-game event error (257 = ``0x0101``,
2049 = ``0x0801``) to the ext3 token writes in the dialogue banks: ``e1_hook``
(hook + expansion payload, no script references) boots, while ``e3_script61``
(``61``-``69`` added) fails.

``apply_3byte_seq_ko.py`` rewrites a record as ``prefix + token + 0x01 * pad``.
That is size preserving but only *meaning* preserving for a real dialogue record.
For an event record the interpreter then walks the ``0x01`` padding as opcodes,
which is what the in-game error reports. Requirement 2.5 of the spec demands the
applier run ``event_record_heuristics.looks_like_event_body`` fail-closed before
writing; that guard was never wired in, so this tool reconstructs what it would
have prevented.

Candidates:

``g1_no_event_bodies``  the tip with every record in ``60``-``69`` whose ORIGINAL
                        body trips ``looks_like_event_body`` reverted to
                        ``pre_ext3``. If this boots, the missing guard is the whole
                        story and wiring it in is the fix.
``g2_61_62`` / ``g3_63_65`` / ``g4_66_69``
                        plain address halving of the ``61``-``69`` range on top of
                        ``e2_dict5f``, as a fallback if the heuristic misses the
                        offending record.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_3byte_seq_ko import MAX_SAFE_PAD, MAX_SAFE_RECORD_LEN  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
    ws_header,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AB = ROOT / "out/patch/ab"
DEFAULT_OUT = ROOT / "out/patch/event_guard_candidates.json"

SCRIPT_LO = 0x600000
SCRIPT_HI = 0x69FFFF
BANK = 0x10000

HALVES: List[Tuple[str, int, int, str]] = [
    ("g2_61_62", 0x610000, 0x630000, "e2_dict5f + banks 61-62 only"),
    ("g3_63_65", 0x610000, 0x660000, "e2_dict5f + banks 61-65"),
    ("g4_66_69", 0x660000, 0x6A0000, "e2_dict5f + banks 66-69 only"),
]


def walk(jp: bytes, lo: int, hi: int) -> List[Tuple[int, int]]:
    """(logical_start, length_including_NUL) walked on the original."""
    sj = stock_base(jp)
    out: List[Tuple[int, int]] = []
    cursor = lo
    while cursor <= hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        term = got[1] - sj
        out.append((cursor, term - cursor + 1))
        cursor = term + 1
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE)
    ap.add_argument("--tip", type=Path, default=DEFAULT_TIP)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    for p in (args.jp, args.pre, args.tip):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    jp = bytes(load_rom(args.jp))
    pre = bytes(load_rom(args.pre))
    tip = bytes(load_rom(args.tip))
    sj, sp, st = stock_base(jp), stock_base(pre), stock_base(tip)
    AB.mkdir(parents=True, exist_ok=True)

    records = walk(jp, SCRIPT_LO, SCRIPT_HI)

    # g1: tip, minus every ext3-era write the applier's guard set would refuse.
    # Same three criteria as tools/apply_3byte_seq_ko.py (requirement 2.5):
    # event-looking original body, record >= MAX_SAFE_RECORD_LEN, pad > MAX_SAFE_PAD.
    rom = bytearray(tip)
    reverted: List[dict] = []
    reasons: dict = {}
    for start, n in records:
        original = jp[sj + start : sj + start + n]
        prefix, _body, _ = split_prefix_body(original[:-1])
        reason = None
        if looks_like_event_body(split_prefix_body(original[:-1])[1]):
            reason = "event_body"
        elif n - 1 >= MAX_SAFE_RECORD_LEN:
            reason = "record_len"
        elif (n - 1) - len(prefix) - 4 > MAX_SAFE_PAD:
            reason = "pad"
        if reason is None:
            continue
        cur = bytes(rom[st + start : st + start + n])
        want = pre[sp + start : sp + start + n]
        if cur == want:
            continue
        rom[st + start : st + start + n] = want
        reasons[reason] = reasons.get(reason, 0) + 1
        reverted.append(
            {
                "abs": f"{start:06X}",
                "len": n,
                "reason": reason,
                "tip": cur.hex(),
                "pre_ext3": want.hex(),
                "orig": original.hex(),
            }
        )
    cs = update_ws_checksum(rom)
    (AB / "g1_no_event_bodies.wsc").write_bytes(rom)
    cands = [
        {
            "name": "g1_no_event_bodies",
            "path": str(AB / "g1_no_event_bodies.wsc"),
            "note": "tip minus every ext3-era write on an event-looking record",
            "records_reverted": len(reverted),
            "bytes_reverted": sum(r["len"] for r in reverted),
            "by_reason": reasons,
            "checksum": f"{cs:04X}",
        }
    ]

    # g2-g4: address halving of 61-69 on top of e2_dict5f
    e2 = AB / "e2_dict5f.wsc"
    if e2.exists():
        base2 = bytes(load_rom(e2))
        sb2 = stock_base(base2)
        for name, lo, hi, note in HALVES:
            rom2 = bytearray(base2)
            rom2[sb2 + lo : sb2 + hi] = tip[st + lo : st + hi]
            cs2 = update_ws_checksum(rom2)
            (AB / f"{name}.wsc").write_bytes(rom2)
            cands.append(
                {
                    "name": name,
                    "path": str(AB / f"{name}.wsc"),
                    "note": note,
                    "range": [f"{lo:06X}", f"{hi - 1:06X}"],
                    "checksum": f"{cs2:04X}",
                }
            )
    else:
        cands.append({"name": "g2-g4", "error": f"missing {e2}; run build_ext3_bisect.py"})

    report = {
        "generated_by": "tools/build_event_guard_candidates.py",
        "established": "e1_hook boots; e3_script61 fails → the ext3 token writes in "
        "banks 61-69 introduce the fault",
        "hypothesis": "apply_3byte_seq_ko.py rewrote event records as "
        "prefix + token + 0x01 padding without the looks_like_event_body guard "
        "required by requirement 2.5; the interpreter walks the padding as opcodes",
        "records_walked": len(records),
        "event_like_records_reverted": len(reverted),
        "test_order": [c.get("name") for c in cands],
        "candidates": cands,
        "reverted_sites": reverted[:200],
        "tip_checksum": f"{ws_header(tip)['checksum']:04X}",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"records walked 600000-69FFFF : {len(records)}")
    print(f"event-like records reverted  : {len(reverted)} "
          f"({sum(r['len'] for r in reverted)} B)")
    for c in cands:
        if c.get("checksum"):
            print(f"  {c['name']:20s} checksum {c['checksum']}  {c['note']}")
        else:
            print(f"  {c.get('name')}: {c.get('error')}")
    for r in reverted[:10]:
        print(f"    {r['abs']} len={r['len']:>3} orig {r['orig'][:40]}")
        print(f"                    tip  {r['tip'][:40]}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
