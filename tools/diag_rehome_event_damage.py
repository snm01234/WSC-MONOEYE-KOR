#!/usr/bin/env python3
"""
Find records the ext3 re-homing pass rewrote that are actually event bodies.

READ-ONLY.

``apply_3byte_seq_ko.py`` performs a size-preserving rewrite: ``prefix + token +
0x01 * pad``. That is safe for a dialogue record but destroys an **event** record,
because the event interpreter then walks the ``0x01`` padding as opcodes — which is
what an in-game "event error 257 / 2049" (``0x0101`` / ``0x0801``) reports.

Requirement 2.5 of the bugfix spec demands the applier run
``event_record_heuristics.looks_like_event_body`` fail-closed before writing. That
guard is not wired in yet (task 6), so this tool reconstructs the damage after the
fact: it diffs a before/after pair inside the dialogue banks, resolves each changed
byte back to its record start, and tests the ORIGINAL body of that record against
the heuristic.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from event_record_heuristics import looks_like_event_body  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import load_rom, read_encoded_z_safe, stock_base  # noqa: E402

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_OUT = ROOT / "out/patch/rehome_event_damage.json"

SCRIPT_LO = 0x600000
SCRIPT_HI = 0x69FFFF
PAD = 0x01
MAX_LISTED = 300


def record_starts(jp: bytes, lo: int, hi: int) -> List[tuple[int, int]]:
    """(logical_start, logical_end_inclusive_of_NUL) walked on the original."""
    sj = stock_base(jp)
    out: List[tuple[int, int]] = []
    cursor = lo
    while cursor <= hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        term = got[1] - sj
        out.append((cursor, term))
        cursor = term + 1
    return out


def analyze(jp_path: Path, before_path: Path, after_path: Path) -> dict:
    jp = bytes(load_rom(jp_path))
    before = bytes(load_rom(before_path))
    after = bytes(load_rom(after_path))
    sj, sb, sa = stock_base(jp), stock_base(before), stock_base(after)

    records = record_starts(jp, SCRIPT_LO, SCRIPT_HI)

    changed: List[dict] = []
    for start, term in records:
        n = term - start + 1
        b = before[sb + start : sb + start + n]
        a = after[sa + start : sa + start + n]
        if a == b:
            continue
        original = jp[sj + start : sj + start + n]
        body_orig = split_prefix_body(original[:-1])[1] if n else b""
        prefix, body_after, _ = split_prefix_body(a[:-1])
        pad = 0
        for byte in reversed(body_after):
            if byte != PAD:
                break
            pad += 1
        entry = {
            "abs": f"{start:06X}",
            "record_len": n,
            "orig_hex": original.hex(),
            "before_hex": b.hex(),
            "after_hex": a.hex(),
            "orig_body_is_event": looks_like_event_body(body_orig),
            "before_was_pad_rewritten": PAD in b[:-1],
            "pad_bytes_after": pad,
        }
        changed.append(entry)

    event_hits = [c for c in changed if c["orig_body_is_event"]]
    newly = [c for c in changed if not c["before_was_pad_rewritten"]]
    newly_event = [c for c in newly if c["orig_body_is_event"]]

    return {
        "ok": not event_hits,
        "generated_by": "tools/diag_rehome_event_damage.py",
        "read_only": True,
        "original": str(jp_path),
        "before": str(before_path),
        "after": str(after_path),
        "band": [f"{SCRIPT_LO:06X}", f"{SCRIPT_HI:06X}"],
        "records_walked": len(records),
        "counts": {
            "records_changed": len(changed),
            "orig_body_looks_like_event": len(event_hits),
            "changed_that_were_untouched_before": len(newly),
            "of_those_event_bodies": len(newly_event),
        },
        "event_bodies": event_hits[:MAX_LISTED],
        "changed_sample": changed[:50],
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")
    rep = analyze(args.jp, args.before, args.after)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    c = rep["counts"]
    print(f"records walked            : {rep['records_walked']}")
    print(f"records changed           : {c['records_changed']}")
    print(f"  original body is EVENT   : {c['orig_body_looks_like_event']}")
    print(f"  untouched before this run: {c['changed_that_were_untouched_before']}")
    print(f"    of those, event bodies : {c['of_those_event_bodies']}")
    for e in rep["event_bodies"][:20]:
        print(f"  {e['abs']} len={e['record_len']:>3} pad={e['pad_bytes_after']:>3}")
        print(f"    orig   {e['orig_hex'][:56]}")
        print(f"    after  {e['after_hex'][:56]}")
    print(f"\n→ {args.out}")
    print(f"ok={rep['ok']}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
