#!/usr/bin/env python3
"""
Automated new-game bisection driver (runs where BizHawk is installed).

The bisection of the event error (257 = ``0x0101``, 2049 = ``0x0801``) has been
manual so far, which costs one round trip per candidate. This driver does the same
thing automatically: it composes candidates from a working base plus the tip's
bytes over an address range, runs each in BizHawk through
``tools/bizhawk_newgame_probe.lua``, and binary-searches the range until the
smallest failing span is found.

Verdict rule: a candidate *passes* when its per-checkpoint framebuffer digests
match the reference run of a ROM known to reach the opening narration. Digests are
sampled on a pixel grid, so a text screen and an error screen separate cleanly
while small text differences (Korean vs Japanese glyphs) also register — which is
why the reference must be the **same** base ROM with the range excluded, not an
unrelated build.

Usage::

    python tools/run_newgame_bisect.py --emu "C:\\path\\to\\EmuHawk.exe" \\
        --base out/patch/ab/e2_dict5f.wsc --lo 630000 --hi 65FFFF

Nothing is written to the tip; candidates and logs land in ``out/patch/ab/`` and
``out/bizhawk/newgame_bisect/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum  # noqa: E402

AB = ROOT / "out/patch/ab"
PROBE_LUA = ROOT / "tools/bizhawk_newgame_probe.lua"
OUT_DIR = ROOT / "out/bizhawk/newgame_bisect"
DEFAULT_REPORT = ROOT / "out/patch/newgame_bisect.json"

CP_RE = re.compile(
    r"^CP (\S+) frame=(\d+) "
    r"(?:digest=(-?\d+) nonblank=(\d+)|shot=(\S+))$"
)


def kill_emu() -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Stop-Process -Name EmuHawk -Force -ErrorAction SilentlyContinue",
        ],
        check=False,
    )
    time.sleep(0.4)


def compose(base: bytes, tip: bytes, lo: int, hi: int, dest: Path) -> Path:
    rom = bytearray(base)
    sb, stp = stock_base(base), stock_base(tip)
    rom[sb + lo : sb + hi + 1] = tip[stp + lo : stp + hi + 1]
    update_ws_checksum(rom)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(rom)
    return dest


def run_probe(emu: Path, rom: Path, tag: str, timeout: int) -> Dict[str, tuple]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / f"{tag}.log"
    if log_path.exists():
        log_path.unlink()
    env = dict(os.environ, PROBE_OUT=str(OUT_DIR), PROBE_TAG=tag)
    kill_emu()
    proc = subprocess.Popen(
        [str(emu), f"--lua={PROBE_LUA}", str(rom)],
        cwd=str(emu.parent),
        env=env,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(1.0)
    if proc.poll() is None:
        kill_emu()
        proc.wait(timeout=10)
    if not log_path.exists():
        raise RuntimeError(f"probe produced no log for {tag} ({rom})")
    cps: Dict[str, tuple] = {}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = CP_RE.match(line.strip())
        if m:
            if m.group(5):
                shot = Path(m.group(5))
                if not shot.exists():
                    raise RuntimeError(f"probe screenshot missing for {tag}: {shot}")
                # The core has no framebuffer/pixel API; hash the exact PNG
                # emitted by client.screenshot instead.
                digest = int(hashlib.md5(shot.read_bytes()).hexdigest()[:15], 16)
                nonblank = shot.stat().st_size
            else:
                digest = int(m.group(3))
                nonblank = int(m.group(4))
            cps[m.group(1)] = (digest, nonblank)
    if not cps:
        raise RuntimeError(f"probe log for {tag} has no checkpoints")
    return cps


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emu", type=Path, required=True, help="D:\\monoeye\\BizHawk-2.11.1-win-x64\\EmuHawk.exe")
    ap.add_argument("--base", type=Path, default=AB / "e2_dict5f.wsc")
    ap.add_argument("--tip", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--lo", type=lambda s: int(s, 16), default=0x630000)
    ap.add_argument("--hi", type=lambda s: int(s, 16), default=0x65FFFF)
    ap.add_argument("--timeout", type=int, default=180, help="seconds per run")
    ap.add_argument(
        "--min-span",
        type=lambda s: int(s, 16),
        default=0x40,
        help="stop narrowing below this span (hex bytes)",
    )
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = ap.parse_args(argv)

    if not args.emu.exists():
        raise SystemExit(f"EmuHawk not found: {args.emu}")
    if not PROBE_LUA.exists():
        raise SystemExit(f"missing probe: {PROBE_LUA}")
    for p in (args.base, args.tip):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    base = bytes(load_rom(args.base))
    tip = bytes(load_rom(args.tip))
    if len(base) != len(tip):
        raise SystemExit("base and tip must be the same size")

    trace: List[dict] = []

    ref_rom = AB / "bisect_ref.wsc"
    shutil.copy2(args.base, ref_rom)
    reference = run_probe(args.emu, ref_rom, "ref", args.timeout)
    trace.append({"tag": "ref", "range": None, "checkpoints": reference, "pass": True})
    print(f"reference checkpoints: {sorted(reference)}")

    def verdict(cps: Dict[str, tuple]) -> bool:
        return all(cps.get(k) == v for k, v in reference.items())

    full = compose(base, tip, args.lo, args.hi, AB / "bisect_full.wsc")
    full_cps = run_probe(args.emu, full, "full", args.timeout)
    if verdict(full_cps):
        print("the full range matches the reference — nothing to bisect here")
        trace.append({"tag": "full", "range": [args.lo, args.hi], "pass": True})
        args.report.write_text(
            json.dumps({"result": "no_failure_in_range", "trace": trace}, indent=2),
            encoding="utf-8",
        )
        return 0
    trace.append({"tag": "full", "range": [args.lo, args.hi], "pass": False})

    lo, hi = args.lo, args.hi
    round_i = 0
    while hi - lo + 1 > args.min_span:
        mid = lo + (hi - lo) // 2
        round_i += 1
        tag = f"r{round_i}_{lo:06X}_{mid:06X}"
        cand = compose(base, tip, lo, mid, AB / f"{tag}.wsc")
        cps = run_probe(args.emu, cand, tag, args.timeout)
        ok = verdict(cps)
        trace.append({"tag": tag, "range": [lo, mid], "pass": ok})
        print(f"  {tag}: {'pass' if ok else 'FAIL'}")
        if ok:
            lo = mid + 1  # lower half is innocent; fault is above mid
        else:
            hi = mid

    result = {
        "result": "narrowed",
        "failing_span": [f"{lo:06X}", f"{hi:06X}"],
        "span_bytes": hi - lo + 1,
        "base": str(args.base),
        "tip": str(args.tip),
        "trace": trace,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nfailing span: {lo:06X}-{hi:06X} ({hi - lo + 1} bytes)")
    print(f"→ {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
