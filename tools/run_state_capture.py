#!/usr/bin/env python3
r"""
Capture native 224x144 framebuffers from a hand-made savestate.

Reaching the intermission by script is impossible here: the initial menu responds
only to Start and A (measured, see tools/run_bizhawk_input_probe.py), so the
cursor cannot be moved onto Continue. A savestate saved by hand replaces that
whole input sequence and is deterministic on top.

Caveat worth knowing before drawing conclusions from a mutated ROM: a savestate
restores VRAM too. Tiles the game had already uploaded stay as they were when the
state was saved, so a ROM edit is only visible after the game re-uploads them.
Use ``--seq`` to walk into a submenu and back if a redraw is needed.

The default state path is the one BizHawk writes for the tip; note the core name
``Cygne/Mednafen`` contains a slash, so BizHawk turns it into a directory:
``WonderSwan/State/<rom>.Cygne/Mednafen.QuickSave1.State``.

Usage::

    python tools/run_state_capture.py --tag intermission
    python tools/run_state_capture.py --tag inter_redraw --seq "A;w120;B;w120"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bizhawk_env as bz  # noqa: E402

LUA = bz.ROOT / "tools" / "bizhawk_state_capture.lua"
DEFAULT_STATE = (
    bz.BIZHAWK_DIR
    / "WonderSwan"
    / "State"
    / "monoeye ko expanded.Cygne"
    / "Mednafen.QuickSave1.State"
)
DEFAULT_ROM = bz.ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc"
DEFAULT_OUT = bz.CAPTURE_DIR / "state"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--sav", type=Path, default=None, help="raw .sav to install as SaveRAM")
    ap.add_argument("--tag", default="state")
    ap.add_argument("--settle", type=int, default=4)
    ap.add_argument("--seq", default=None, help="semicolon steps after the first shot")
    ap.add_argument("--hold", type=int, default=8)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--runs", type=int, default=1, help=">1 checks the capture is deterministic")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument(
        "--save-final",
        type=Path,
        default=None,
        help="optional savestate path written after the last --seq step",
    )
    args = ap.parse_args(argv)

    rom, state = args.rom.resolve(), args.state.resolve()
    out_dir = args.out_dir.resolve()
    for p in (rom, state):
        if not p.exists():
            raise SystemExit(f"missing: {p}")

    bz.ensure_profile(refresh_config=True)
    bz.clear_saveram()
    if args.sav:
        for p in bz.install_saveram(rom, args.sav.resolve()):
            print(f"saveram: {p.name}")

    # BizHawk resolves its state directory relative to the executable, so mirror
    # the state into the profile as well; the Lua load uses the absolute path, but
    # keeping both consistent avoids surprises if a run saves over it.
    mirror = bz.PROFILE_DIR / "WonderSwan" / "State" / state.parent.name / state.name
    mirror.parent.mkdir(parents=True, exist_ok=True)
    if not mirror.exists() or mirror.stat().st_mtime < state.stat().st_mtime:
        shutil.copy2(state, mirror)

    env = {"MONOEYE_STATE": str(state), "MONOEYE_SETTLE": str(args.settle), "MONOEYE_HOLD": str(args.hold)}
    if args.seq:
        env["MONOEYE_SEQ"] = args.seq
    if args.save_final:
        final_state = args.save_final.resolve()
        final_state.parent.mkdir(parents=True, exist_ok=True)
        env["MONOEYE_SAVE_FINAL"] = str(final_state)

    print(f"rom   : {rom.name} (md5 {bz.md5(rom, 32).lower()})")
    print(f"state : {state}")

    hashes = []
    for i in range(1, args.runs + 1):
        tag = f"{args.tag}_r{i}" if args.runs > 1 else args.tag
        res = bz.run_lua(LUA, rom, out_dir, tag, env, timeout=args.timeout)
        loaded = [l for l in bz.parse_log_fields(res.log, "LOADSTATE")]
        saved = [l for l in bz.parse_log_fields(res.log, "SAVESTATE")]
        sizes = [l for l in bz.parse_log_fields(res.log, "SHOT")]
        print(f"  run {i}: done={res.done} {res.seconds}s  load={loaded[0] if loaded else 'n/a'}")
        for s in sizes:
            print(f"    SHOT {s}")
        for s in saved:
            print(f"    SAVESTATE {s}")
        h = bz.png_hashes(res.shots)
        hashes.append(h)
        print("    " + " ".join(f"{k}={v}" for k, v in h.items()))
    bz.kill_emu()

    if args.runs > 1:
        same = all(h == hashes[0] for h in hashes)
        print(f"\ndeterministic={same}")
        return 0 if same else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
