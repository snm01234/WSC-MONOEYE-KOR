#!/usr/bin/env python3
r"""
Repo-local BizHawk title/menu capture runner.

Replaces ``tools/run_menu_slices.ps1``, ``tools/run_menu_2k_slices.ps1`` and
``tools/run_menu_bisect_emu.py``, all of which hard-coded
``C:\Users\SangGeun\monoeye`` plus a WinGet BizHawk install that is not present.
Paths now come from :mod:`bizhawk_env`, which resolves the repository root and
the checked-in ``BizHawk-2.11.1-win-x64/EmuHawk.exe``, and runs it through an
isolated profile so the first-run onboarding never appears.

Scope is the **title and initial menu only** — the two screens the graphics hunt
needs. Reaching the intermission through Continue is deliberately not automated
here.

Determinism gate (``--runs 3``): the same ROM is run N times and each capture's
MD5 must match across runs. Nothing downstream (slice bisection, single-tile
mutation) means anything unless this holds, because the whole method reads
"screenshot hash changed => the mutated bytes feed that screen".

Examples::

    # baseline: stock ROM, three runs, record the reference hashes
    python tools/run_title_menu_capture.py --runs 3 --write-baseline

    # one candidate ROM against the recorded baseline
    python tools/run_title_menu_capture.py --rom out/patch/menu_bisect/X.wsc \
        --runs 1 --tag X --compare
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bizhawk_env as bz  # noqa: E402

BASELINE = bz.CAPTURE_DIR / "baseline_hashes.json"
LABELS = ("title", "menu")


def run_rom(
    rom: Path,
    out_dir: Path,
    tag: str,
    runs: int,
    timeout: float,
) -> dict:
    per_run = []
    for i in range(1, runs + 1):
        run_tag = f"{tag}_r{i}" if runs > 1 else tag
        res = bz.run_lua(bz.LUA_MENU_CAPTURE, rom, out_dir, run_tag, None, timeout=timeout)
        hashes = bz.png_hashes(res.shots)
        missing = [k for k in LABELS if k not in hashes]
        per_run.append(
            {
                "run": i,
                "tag": run_tag,
                "done": res.done,
                "exited_cleanly": res.exited,
                "seconds": res.seconds,
                "hashes": hashes,
                "missing": missing,
            }
        )
        status = "ok" if res.done and not missing else "INCOMPLETE"
        print(
            f"  [run {i}/{runs}] {status} {res.seconds}s "
            + " ".join(f"{k}={hashes[k]}" for k in LABELS if k in hashes)
        )
        if missing:
            print(f"    missing captures: {missing}")

    ref = {k: v for k, v in per_run[0]["hashes"].items() if k in LABELS}
    stable = all(
        {k: v for k, v in r["hashes"].items() if k in LABELS} == ref for r in per_run
    ) and all(not r["missing"] for r in per_run)
    return {
        "rom": str(rom),
        "rom_md5": bz.md5(rom, 32).lower(),
        "runs": runs,
        "deterministic": stable,
        "hashes": ref,
        "per_run": per_run,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rom", type=Path, default=bz.ORIG_ROM)
    ap.add_argument("--tag", default=None, help="file tag (default: ROM stem slug)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--out-dir", type=Path, default=bz.CAPTURE_DIR)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument(
        "--write-baseline",
        action="store_true",
        help=f"record the hashes as the reference in {BASELINE.name}",
    )
    ap.add_argument("--compare", action="store_true", help="diff against the baseline record")
    args = ap.parse_args(argv)

    rom = args.rom.resolve()
    if not rom.exists():
        raise SystemExit(f"missing ROM: {rom}")
    tag = args.tag or (
        "".join(c if c.isalnum() else "_" for c in rom.stem).strip("_")[:40]
    )

    emu = bz.ensure_profile(refresh_config=True)
    # No SaveRAM: the title and the initial menu must not depend on save state.
    bz.clear_saveram()
    print(f"profile : {bz.PROFILE_DIR}")
    print(f"emulator: {emu}")
    print(f"rom     : {rom}  (md5 {bz.md5(rom, 32).lower()})")

    report = run_rom(rom, args.out_dir, tag, args.runs, args.timeout)
    report["emulator"] = str(emu)
    bz.kill_emu()

    out_report = args.report or (args.out_dir / f"{tag}_capture_report.json")
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("\n== summary ==")
    print(
        f"deterministic={report['deterministic']} "
        + " ".join(f"{k}={v}" for k, v in report["hashes"].items())
    )
    print(f"report -> {out_report}")

    if args.compare:
        if not BASELINE.exists():
            print(f"\nno baseline at {BASELINE}", file=sys.stderr)
        else:
            base = json.loads(BASELINE.read_text(encoding="utf-8"))
            print("\n== vs baseline ==")
            for label in LABELS:
                got = report["hashes"].get(label, "NONE")
                want = base.get("hashes", {}).get(label, "NONE")
                print(f"{label}: {got} (baseline {want}) {'same' if got == want else 'CHANGED'}")

    if args.write_baseline:
        if not report["deterministic"]:
            print(
                "\nrefusing to write a baseline from a non-deterministic run",
                file=sys.stderr,
            )
            return 2
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline -> {BASELINE}")

    return 0 if report["deterministic"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
