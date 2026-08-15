#!/usr/bin/env python3
r"""
Driver for tools/bizhawk_input_probe.lua: find out which buttons a screen accepts.

Why this is a keeper rather than a throwaway: the initial menu turned out to
ignore every direction key, and that is only believable with a control run in the
same harness. The probe reaches a screen through a scripted prelude, takes a
savestate, then branches once per candidate button so all candidates start from
byte-identical machine state. Screenshots are grouped by MD5, so "this button did
nothing" is a measurement instead of an impression.

Result on the stock and tip ROMs, initial menu, 2026-07-27: only ``Start``
(title -> menu) and ``A`` (confirm) change the framebuffer. ``X1``-``X4``,
``Y1``-``Y4`` for P1 and P2, holds of 2-30 frames, up to four repeats, settles up
to 900 frames and a ``Rotate`` toggle all leave it unchanged.

Examples::

    # which buttons do anything on the title?
    python tools/run_bizhawk_input_probe.py --tag title --prelude "w600"

    # ... and on the initial menu, with a save installed
    python tools/run_bizhawk_input_probe.py --tag menu --prelude "w600;Start;w200" \
        --sav savebackup/monoeye_ko_expanded_1to2.sav

    # control test: identical prelude length, only the direction differs, then
    # compare what A does afterwards
    python tools/run_bizhawk_input_probe.py --tag ctl_x3 \
        --prelude "w600;Start;w200;X3;w60" --keys A --after 240
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bizhawk_env as bz  # noqa: E402

PROBE = bz.ROOT / "tools" / "bizhawk_input_probe.lua"
DEFAULT_OUT = bz.CAPTURE_DIR / "input_probe"
DEFAULT_KEYS = "NONE,Start,A,B,X1,X2,X3,X4,Y1,Y2,Y3,Y4"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=bz.ORIG_ROM)
    ap.add_argument("--sav", type=Path, default=None, help="raw 32 KiB .sav to install as SaveRAM")
    ap.add_argument("--tag", default="probe")
    ap.add_argument(
        "--prelude",
        default="w600",
        help="semicolon steps run before the branch savestate; w<n> waits, else a button",
    )
    ap.add_argument("--keys", default=DEFAULT_KEYS, help="candidate buttons; NONE is the control")
    ap.add_argument("--hold", type=int, default=8, help="frames to hold a button")
    ap.add_argument("--after", type=int, default=120, help="frames between the press and the shot")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    rom = args.rom.resolve()
    if not rom.exists():
        raise SystemExit(f"missing ROM: {rom}")

    bz.ensure_profile(refresh_config=True)
    bz.clear_saveram()
    if args.sav:
        sav = args.sav.resolve()
        for p in bz.install_saveram(rom, sav):
            print(f"saveram: {p.name}")

    res = bz.run_lua(
        PROBE,
        rom,
        args.out_dir,
        args.tag,
        {
            "MONOEYE_PRELUDE": args.prelude,
            "MONOEYE_KEYS": args.keys,
            "MONOEYE_HOLD": str(args.hold),
            "MONOEYE_AFTER": str(args.after),
        },
        timeout=args.timeout,
    )
    print(f"rom={rom.name} prelude={args.prelude!r} done={res.done} {res.seconds}s")
    for line in res.log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(("KEYS", "PRELUDE", "PRESS", "SRAM_INJECT")):
            print("  " + line)

    groups: dict[str, list[str]] = collections.defaultdict(list)
    for label, p in sorted(res.shots.items()):
        groups[bz.md5(p)].append(label)
    print("\nscreenshot groups (same hash = the button changed nothing relative to each other):")
    control = next((h for h, ls in groups.items() if "NONE" in ls), None)
    for h, labels in groups.items():
        mark = "  <- control" if h == control else ""
        print(f"  {h}  {labels}{mark}")
    if control is not None:
        inert = [l for l in groups[control] if l not in ("NONE", "title")]
        if inert:
            print(f"\nno effect: {inert}")
    bz.kill_emu()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
