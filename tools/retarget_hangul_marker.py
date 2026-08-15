#!/usr/bin/env python3
"""
Move the Hangul run marker off a code that collides with a real character.

The font hook marks a Hangul run with a 2-byte sentinel and the shared dispatch
cave consumes it with ``cmp cx, marker``. The installed marker is ``E3DB``, which
is the code for the character ``映``. Measured in the original ROM: ``E3DB`` occurs
10 times in the text banks (``50``-``6F``, ``75``, ``76``) — ``全世界に向けて放映するのだ！``,
``映画「逆襲のシャア」に登場`` (six copies in bank ``5C``), ``ガンダムの戦いを映せ！！``
and two more. Every one of those makes the shared hook set the sticky Hangul flag
on a stock string, so the rest of the run is redirected to padding glyphs: garbled
frames and unrelated text in the unit/battle UI.

Zero dictionary phrases contain ``E3DB``, so the marker only ever appears inside
payloads this patch wrote, plus those 10 stock sites.

What this does:

1. rewrites the ``cmp cx, imm16`` operand in the installed dispatch caves,
2. replaces the marker inside **our** payloads — the whole expansion region, and
   the stock text banks except the original's own occurrences, which are verified
   byte-identical to the original before being skipped,
3. updates ``padding_store.marker_code`` in ``out/patch/hangul_char_map.json``.

The new code must satisfy: lead in ``0xE0``-``0xEF`` (so the length walker forms a
2-byte unit), outside the padding glyph range, not the ext3 magic, and zero
occurrences in the original text banks. ``--verify-new-code`` re-checks all of that
before writing.

``--dry-run`` is the default; ``--commit`` backs the target up first.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    load_rom,
    stock_base,
    update_ws_checksum,
    ws_header,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CHAR_MAP = ROOT / "out/patch/hangul_char_map.json"
DEFAULT_OUT = ROOT / "out/patch/retarget_hangul_marker.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

TEXT_BANKS = tuple(range(0x50, 0x70)) + (0x75, 0x76)
EXT3_MAGIC = 0xE518
CODE_BANKS = tuple(range(0x70, 0x80))

OLD_DEFAULT = 0xE3DB
NEW_DEFAULT = 0xEC80


def text_bank_blob(rom: bytes, base: int) -> bytes:
    return b"".join(
        rom[base + s * BANK_SIZE : base + (s + 1) * BANK_SIZE] for s in TEXT_BANKS
    )


def pad_range() -> Tuple[int, int]:
    if not CHAR_MAP.exists():
        return (0xE740, 0xE740 + 527)
    pad = json.loads(CHAR_MAP.read_text(encoding="utf-8")).get("padding_store") or {}
    base = int(pad.get("base_code", "E740"), 16)
    count = int(pad.get("count", 528))
    return (base, base + count - 1)


def check_new_code(jp: bytes, code: int) -> List[str]:
    problems: List[str] = []
    lead, trail = code >> 8, code & 0xFF
    if not 0xE0 <= lead <= 0xEF:
        problems.append(f"lead {lead:02X} is outside 0xE0-0xEF, the 2-byte unit range")
    if trail == 0x00:
        problems.append("trail 00 would collide with the zstring terminator")
    lo, hi = pad_range()
    if lo <= code <= hi:
        problems.append(f"code is inside the padding glyph range {lo:04X}-{hi:04X}")
    if code == EXT3_MAGIC:
        problems.append("code is the ext3 magic")
    hits = text_bank_blob(jp, stock_base(jp)).count(bytes([lead, trail]))
    if hits:
        problems.append(f"code occurs {hits} time(s) in the original text banks")
    return problems


def find_original_marker_sites(jp: bytes, old: int) -> List[int]:
    """Logical addresses of the original's own marker occurrences in text banks."""
    sj = stock_base(jp)
    needle = bytes([old >> 8, old & 0xFF])
    out: List[int] = []
    for seg in TEXT_BANKS:
        start, end = sj + seg * BANK_SIZE, sj + (seg + 1) * BANK_SIZE
        i = jp.find(needle, start, end)
        while i >= 0:
            out.append(i - sj)
            i = jp.find(needle, i + 1, end)
    return sorted(out)


def patch_compare_operand(
    rom: bytearray, old: int, new: int
) -> List[dict]:
    """Rewrite ``cmp cx, old`` (81 F9 lo hi) wherever it is installed."""
    sb = stock_base(rom)
    pattern = b"\x81\xF9" + bytes([old & 0xFF, old >> 8])
    repl = b"\x81\xF9" + bytes([new & 0xFF, new >> 8])
    sites: List[dict] = []
    for seg in CODE_BANKS:
        start, end = sb + seg * BANK_SIZE, sb + (seg + 1) * BANK_SIZE
        i = rom.find(pattern, start, end)
        while i >= 0:
            rom[i : i + 4] = repl
            logical = i - sb
            sites.append(
                {"site": f"{logical >> 16:02X}:{logical & 0xFFFF:04X}", "insn": "cmp cx,imm16"}
            )
            i = rom.find(pattern, i + 1, end)
    return sites


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--old", type=lambda s: int(s, 16), default=OLD_DEFAULT)
    ap.add_argument("--new", type=lambda s: int(s, 16), default=NEW_DEFAULT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=None,
        help="write a copy here instead of modifying the target",
    )
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default")
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    for p in (args.jp, args.target):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    jp = bytes(load_rom(args.jp))
    rom = bytearray(load_rom(args.target))
    sj, st = stock_base(jp), stock_base(rom)

    problems = check_new_code(jp, args.new)
    if problems:
        for p in problems:
            print(f"REFUSE: {p}")
        raise SystemExit(f"new marker {args.new:04X} is not safe")

    old_b = bytes([args.old >> 8, args.old & 0xFF])
    new_b = bytes([args.new >> 8, args.new & 0xFF])

    original_sites = find_original_marker_sites(jp, args.old)
    protected: List[dict] = []
    for logical in original_sites:
        cur = bytes(rom[st + logical : st + logical + 2])
        protected.append(
            {
                "site": f"{logical >> 16:02X}:{logical & 0xFFFF:04X}",
                "target_bytes": cur.hex(),
                "identical_to_original": cur == old_b,
            }
        )
    dirty = [p for p in protected if not p["identical_to_original"]]
    if dirty:
        for p in dirty:
            print(f"REFUSE: original marker site {p['site']} is not intact "
                  f"({p['target_bytes']}) — repair the stock invasion first")
        raise SystemExit("original marker sites must be byte-identical before retargeting")

    skip = {logical for logical in original_sites}

    # 1) expansion region — everything there is ours
    exp_replaced = 0
    i = rom.find(old_b, 0, 0x800000)
    while i >= 0:
        rom[i : i + 2] = new_b
        exp_replaced += 1
        i = rom.find(old_b, i + 2, 0x800000)

    # 2) stock text banks — ours everywhere except the original's own sites
    stock_replaced = 0
    stock_sites: List[str] = []
    for seg in TEXT_BANKS:
        start, end = st + seg * BANK_SIZE, st + (seg + 1) * BANK_SIZE
        i = rom.find(old_b, start, end)
        while i >= 0:
            logical = i - st
            if logical in skip:
                i = rom.find(old_b, i + 1, end)
                continue
            rom[i : i + 2] = new_b
            stock_replaced += 1
            stock_sites.append(f"{logical >> 16:02X}:{logical & 0xFFFF:04X}")
            i = rom.find(old_b, i + 2, end)

    # 3) the dispatch compare operand
    compare_sites = patch_compare_operand(rom, args.old, args.new)

    checksum_before = f"{ws_header(load_rom(args.target))['checksum']:04X}"
    dest = None
    backup = None
    checksum_after = None
    if args.commit or args.out_rom:
        if args.out_rom:
            dest = args.out_rom
            dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest = args.target
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            (BACKUP_ROOT / stamp).mkdir(parents=True, exist_ok=True)
            backup = BACKUP_ROOT / stamp / args.target.name
            shutil.copy2(args.target, backup)
        checksum_after = f"{update_ws_checksum(rom):04X}"
        dest.write_bytes(rom)
        if args.commit and CHAR_MAP.exists():
            cm = json.loads(CHAR_MAP.read_text(encoding="utf-8"))
            cm.setdefault("padding_store", {})["marker_code"] = f"{args.new:04X}"
            cm["padding_store"]["marker_code_history"] = (
                cm["padding_store"].get("marker_code_history") or []
            ) + [
                {
                    "code": f"{args.old:04X}",
                    "retired_because": "collides with the character 映; occurs 10 "
                    "times in the original text banks, so the shared hook set the "
                    "sticky Hangul flag on stock strings",
                }
            ]
            CHAR_MAP.write_text(
                json.dumps(cm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

    report = {
        "ok": bool(compare_sites),
        "generated_by": "tools/retarget_hangul_marker.py",
        "mode": "commit" if args.commit else ("copy" if args.out_rom else "dry-run"),
        "old_marker": f"{args.old:04X}",
        "new_marker": f"{args.new:04X}",
        "new_marker_checks": "passed",
        "wrote": str(dest) if dest else None,
        "backup": str(backup) if backup else None,
        "revert": f"copy {backup} back" if backup else "no in-place write performed",
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "expansion_replacements": exp_replaced,
        "stock_text_bank_replacements": stock_replaced,
        "stock_text_bank_sites": stock_sites[:200],
        "compare_operand_sites": compare_sites,
        "protected_original_sites": protected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"marker {args.old:04X} → {args.new:04X}")
    print(f"  expansion replacements      : {exp_replaced}")
    print(f"  stock text-bank replacements: {stock_replaced}")
    print(f"  dispatch compare sites      : {[s['site'] for s in compare_sites]}")
    print(f"  protected original sites    : "
          f"{[p['site'] for p in protected]}")
    if not compare_sites:
        print("WARNING: no `cmp cx, marker` found — the hook may not be installed "
              "or uses a different encoding; the ROM would keep the old semantics")
    if dest:
        print(f"  wrote    : {dest}")
        if backup:
            print(f"  backup   : {backup}")
        print(f"  checksum : {checksum_before} → {checksum_after}")
    else:
        print("dry-run: nothing written. Use --out-rom for a copy or --commit.")
    print(f"→ {args.out}")
    return 0 if compare_sites else 1


if __name__ == "__main__":
    raise SystemExit(main())
