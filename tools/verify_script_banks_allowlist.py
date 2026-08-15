#!/usr/bin/env python3
"""
Verify banks 60–69: tip vs JP diffs must be ⊆ allowlisted pointer sites.

Also: JP records that look_like_event_body must remain byte-identical on tip.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from event_record_heuristics import looks_like_event_body  # noqa: E402
from monoeye_rom import load_rom, read_encoded_z_safe, stock_base  # noqa: E402

DIALOGUE_BANKS = range(0x60, 0x6A)


def _file_sites_covering(logical_sites: Iterable[int], rom: bytes) -> Set[int]:
    """Allowlist is logical abs of pointer *starts*; expand to covered file bytes."""
    sb = stock_base(rom)
    out: Set[int] = set()
    for a in logical_sites:
        # pointer forms are 2–4 bytes; allow ±0..3 from site
        for d in range(4):
            out.add(sb + a + d)
    return out


def verify_script_banks_allowlist(
    tip: bytes,
    jp: bytes,
    *,
    allowlist_logical: Sequence[int],
    body_abs: Sequence[int] | None = None,
    dialogue_banks: Iterable[int] = DIALOGUE_BANKS,
) -> dict:
    sb_t = stock_base(tip)
    sb_j = stock_base(jp)
    allowed_files = _file_sites_covering(allowlist_logical, tip)

    # Opening/seq dedicated rewrites: allow entire zstring record at each abs.
    for abs_off in body_abs or []:
        rj = read_encoded_z_safe(jp, sb_j + abs_off, max_len=0x400)
        length = len(rj[0]) + 1 if rj else 0  # payload + NUL
        if not length:
            rt = read_encoded_z_safe(tip, sb_t + abs_off, max_len=0x400)
            length = len(rt[0]) + 1 if rt else 4
        for d in range(max(length, 4)):
            allowed_files.add(sb_t + abs_off + d)

    illegal: list[dict] = []
    illegal_count = 0
    diff_bytes = 0
    for seg in dialogue_banks:
        for i in range(0x10000):
            logical = (seg << 16) | i
            ft = sb_t + logical
            fj = sb_j + logical
            if tip[ft] == jp[fj]:
                continue
            diff_bytes += 1
            if ft not in allowed_files:
                illegal_count += 1
                if len(illegal) < 80:
                    illegal.append(
                        {
                            "abs": f"{logical:06X}",
                            "tip": f"{tip[ft]:02X}",
                            "jp": f"{jp[fj]:02X}",
                        }
                    )

    event_breaks: list[dict] = []
    for seg in dialogue_banks:
        i = 0
        while i < 0x10000:
            logical = (seg << 16) | i
            rj = read_encoded_z_safe(jp, sb_j + logical, max_len=96)
            if not rj:
                i += 1
                continue
            payload, end = rj
            length = end - (sb_j + logical)
            if length <= 0:
                i += 1
                continue
            if looks_like_event_body(payload):
                jt = bytes(tip[sb_t + logical : sb_t + logical + length])
                if jt != payload:
                    event_breaks.append(
                        {
                            "abs": f"{logical:06X}",
                            "len": length,
                            "jp_hex": payload.hex()[:48],
                            "tip_hex": jt.hex()[:48],
                        }
                    )
            i = max(i + 1, end - sb_j - (seg << 16))

    ok = illegal_count == 0 and not event_breaks
    return {
        "ok": ok,
        "diff_bytes_60_69": diff_bytes,
        "allowlist_n": len(set(allowlist_logical)),
        "illegal_diff_count": illegal_count,
        "illegal_diffs": illegal,
        "illegal_diff_sites": illegal_count,
        "event_body_breaks": event_breaks[:40],
        "event_body_break_count": len(event_breaks),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tip", type=Path, required=True)
    ap.add_argument("--jp", type=Path, default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc")
    ap.add_argument(
        "--allowlist",
        type=Path,
        help="JSON report with pointer_allowlist: [\"60xxxx\", ...] or raw list",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "out/patch/verify_script_banks_allowlist.json")
    args = ap.parse_args()

    allow: list[int] = []
    if args.allowlist and args.allowlist.exists():
        data = json.loads(args.allowlist.read_text(encoding="utf-8"))
        raw = data.get("pointer_allowlist") or data.get("allowlist") or data
        if isinstance(raw, dict):
            raw = raw.get("sites") or []
        for x in raw:
            if isinstance(x, int):
                allow.append(x)
            else:
                allow.append(int(str(x), 16))

    tip = load_rom(args.tip)
    jp = load_rom(args.jp)
    report = verify_script_banks_allowlist(tip, jp, allowlist_logical=allow)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ok={report['ok']} diffs={report['diff_bytes_60_69']} "
          f"illegal_sites={report['illegal_diff_sites']} "
          f"event_breaks={report['event_body_break_count']}")
    print("→", args.out)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
