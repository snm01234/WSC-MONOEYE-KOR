#!/usr/bin/env python3
"""SUPERSEDED - do not run. Use tools/run_menu_candidates.py.

This pointed at ``C:\\Users\\SangGeun\\monoeye`` and the WinGet BizHawk install,
neither of which exists here, and at ``out/title_trace6/menu_capture.lua``, which
was lost. The replacement resolves everything from the repository:

    python tools/run_title_menu_capture.py --runs 3 --write-baseline
    python tools/run_menu_candidates.py --glob "out/patch/menu_bisect/*.wsc"

It also reports *where* a capture changed in 8x8 framebuffer blocks, which this
hash-only driver could not do.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMU_DIR = Path(
    r"C:\Users\SangGeun\AppData\Local\Microsoft\WinGet\Packages"
    r"\TASEmulators.BizHawk_Microsoft.Winget.Source_8wekyb3d8bbwe"
)
EMU = EMU_DIR / "EmuHawk.exe"
LUA = ROOT / "out" / "title_trace6" / "menu_capture.lua"
OUT = ROOT / "out" / "title_trace6"
REF_TITLE = "BF8FD8CD1554"
REF_MENU = "D144B003D040"


def kill_emu() -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Stop-Process -Name EmuHawk -Force -ErrorAction SilentlyContinue"],
        check=False,
    )
    time.sleep(0.4)


def md5_12(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:12].upper()


def run_one(tag: str, rom: Path, timeout: float = 55.0) -> dict:
    kill_emu()
    for p in OUT.glob(f"{tag}*"):
        p.unlink(missing_ok=True)
    env = os.environ.copy()
    env["MENU_TAG"] = tag
    proc = subprocess.Popen(
        [str(EMU), f"--lua={LUA}", str(rom)],
        cwd=str(EMU_DIR),
        env=env,
    )
    log = OUT / f"{tag}.log"
    deadline = time.time() + timeout
    while time.time() < deadline and proc.poll() is None:
        if log.exists() and "DONE" in log.read_text(encoding="utf-8", errors="replace"):
            break
        time.sleep(0.4)
    extra = time.time() + 10
    while time.time() < extra and proc.poll() is None:
        time.sleep(0.3)
    if proc.poll() is None:
        proc.kill()
    time.sleep(0.5)
    title = next(OUT.glob(f"{tag}_title_*.png"), None)
    menu = next(OUT.glob(f"{tag}_menu_*.png"), None)
    th = md5_12(title) if title else "NONE"
    mh = md5_12(menu) if menu else "NONE"
    changed = th != REF_TITLE or mh != REF_MENU
    row = {
        "tag": tag,
        "title": th,
        "menu": mh,
        "changed": changed,
        "title_bytes": title.stat().st_size if title else 0,
        "menu_bytes": menu.stat().st_size if menu else 0,
    }
    print(
        f"{tag}: title={th} menu={mh} "
        f"{'CHANGED' if changed else 'same'} "
        f"tb={row['title_bytes']} mb={row['menu_bytes']}",
        flush=True,
    )
    return row


def main() -> None:
    bisect = ROOT / "out" / "patch" / "menu_bisect"
    tags = [
        "SLICE_70_lo",
        "SLICE_70_hi",
        "SLICE_71_lo",
        "SLICE_71_hi",
        "SLICE_72_lo",
        "SLICE_72_hi",
        "SLICE_73_lo",
        "SLICE_73_hi",
    ]
    # Ensure slice ROMs exist
    if not (bisect / "SLICE_70_lo.wsc").exists():
        print("Missing slice ROMs; run slice builder first", file=sys.stderr)
        sys.exit(1)
    rows = []
    for tag in tags:
        rows.append(run_one(tag, bisect / f"{tag}.wsc"))
    kill_emu()
    print("--- summary ---")
    for r in rows:
        print(r)


if __name__ == "__main__":
    print(__doc__, file=sys.stderr)
    sys.exit(1)
