#!/usr/bin/env python3
"""
Single source of truth for running BizHawk out of this repository.

Why this exists
---------------
The older menu-hunting runners (``tools/run_menu_slices.ps1``,
``tools/run_menu_2k_slices.ps1``, ``tools/run_menu_bisect_emu.py``) hard-coded

* ``C:\\Users\\SangGeun\\monoeye`` as the repo root, and
* the WinGet install of BizHawk as the emulator,

neither of which exists here any more. This module resolves both from the
repository itself (``d:\\monoeye`` + ``BizHawk-2.11.1-win-x64/EmuHawk.exe``) so a
runner never contains an absolute machine path again.

It also solves two problems that made the previous emulator runs unreliable.

1. **Onboarding overlay.** The bundled ``config.ini`` has ``FirstBoot: true``,
   so a fresh EmuHawk shows the first-run dialogs and eats input. BizHawk 2.11
   has no ``--config=`` switch (verified against the strings in ``EmuHawk.exe``
   and ``dll/BizHawk.Client.Common.dll``: only ``--lua``, ``--load-state``,
   ``--load-slot``, ``--userdata``, ``--socket*``, ``--url*``, ``--mmf``,
   ``--fullscreen``, ``--chromeless``, ``--audiosync``, ``--luaconsole``), and it
   always reads ``config.ini`` from the directory holding the executable. So we
   build a **separate portable instance** under ``out/bizhawk_profile`` whose
   own ``config.ini`` has onboarding, updates and throttling disabled. The heavy
   payload directories are directory junctions back into the checked-in BizHawk,
   so the profile costs ~6 MB rather than 148 MB.

2. **Stray SaveRAM.** The title and initial menu must not depend on save state,
   so :func:`clear_saveram` wipes the profile's SaveRAM directory before a run
   and ``BackupSaveram``/``AutosaveSaveRAM`` are off. That keeps the two captured
   screens a pure function of the ROM bytes.

Scope note: the Continue -> intermission path is intentionally **not** automated
here. It was tried and the initial menu did not respond to the pulsed X3/Start
inputs, so no verified script exists; rather than leave an unproven runner
behind, that path stays manual.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]

#: Checked-in BizHawk build. Never launched directly; see :func:`ensure_profile`.
BIZHAWK_DIR = ROOT / "BizHawk-2.11.1-win-x64"

#: Isolated portable instance with its own config.ini (onboarding disabled).
PROFILE_DIR = ROOT / "out" / "bizhawk_profile"
PROFILE_EMU = PROFILE_DIR / "EmuHawk.exe"

#: Stock ROM and its saves.
ORIG_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
ORIG_SAV = ROOT / "SD Gundam G Generation Mono-Eye Gundams.sav"
BACKUP_SAV = ROOT / "savebackup" / "monoeye_ko_expanded_1to2.sav"

#: Default capture directory for the title/menu/intermission runners.
CAPTURE_DIR = ROOT / "out" / "title_menu_capture"

LUA_MENU_CAPTURE = ROOT / "tools" / "menu_capture.lua"

#: Subdirectories of the BizHawk build that the profile links instead of copying.
_LINKED_DIRS = (
    "dll",
    "Firmware",
    "gamedb",
    "Gameboy",
    "NES",
    "overlay",
    "Shaders",
    "Tools",
    "ExternalTools",
    "Lua",
)

#: Files copied into the profile.
_COPIED_FILES = ("EmuHawk.exe", "EmuHawk.exe.config", "defctrl.json")

#: config.ini overrides that make an unattended run deterministic.
CONFIG_OVERRIDES: Mapping[str, object] = {
    # Onboarding / nagging
    "FirstBoot": False,
    "UpdateAutoCheckEnabled": False,
    "SkipSuperuserPrivsCheck": True,
    "SkipOutdatedOsCheck": True,
    "SkipRATelemetryWarning": True,
    "SuppressAskSave": True,
    "SingleInstanceMode": False,
    # Window / OSD: keep the framebuffer clean so PNG hashes are stable
    "SaveWindowPosition": False,
    "MainWindowPosition": "64, 64",
    "MainWindowMaximized": False,
    "DisplayMessages": False,
    "DisplayFps": False,
    "DisplayFrameCounter": False,
    "DisplayInput": False,
    "DisplayLagCounter": False,
    "DisplayRerecordCount": False,
    "DisplaySubtitles": False,
    "ScreenshotCaptureOsd": False,
    "PauseWhenMenuActivated": False,
    "StartPaused": False,
    # Timing: Lua drives frameadvance, so let it run as fast as it can
    "ClockThrottle": False,
    "VSyncThrottle": False,
    "SoundThrottle": False,
    "Unthrottled": True,
    "AutoMinimizeSkipping": False,
    "RunInBackground": True,
    "SoundEnabled": False,
    "SoundEnabledNormal": False,
    # Saves: no stray .bak files, no autosave racing our injected SRAM
    "BackupSaveram": False,
    "AutosaveSaveRAM": False,
    "AutoSaveLastSaveSlot": False,
    "AutoLoadLastSaveSlot": False,
}


class ProfileError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# profile construction
# --------------------------------------------------------------------------
def _make_junction(link: Path, target: Path) -> None:
    if link.exists() or link.is_symlink():
        return
    # Junctions do not need elevation on Windows.
    res = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise ProfileError(
            f"could not junction {link} -> {target}: {res.stdout}{res.stderr}"
        )


def build_profile_config() -> dict:
    """Stock config.ini with :data:`CONFIG_OVERRIDES` applied."""
    src = BIZHAWK_DIR / "config.ini"
    if not src.exists():
        raise ProfileError(f"missing stock config: {src}")
    cfg = json.loads(src.read_text(encoding="utf-8"))
    unknown = [k for k in CONFIG_OVERRIDES if k not in cfg]
    if unknown:
        # A renamed key would silently stop taking effect; fail loudly instead.
        raise ProfileError(f"config keys absent from BizHawk 2.11.1: {unknown}")
    cfg.update(CONFIG_OVERRIDES)
    return cfg


def ensure_profile(refresh_config: bool = True) -> Path:
    """Create/refresh the isolated EmuHawk instance and return its executable."""
    if not BIZHAWK_DIR.is_dir():
        raise ProfileError(f"missing BizHawk build: {BIZHAWK_DIR}")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    for name in _COPIED_FILES:
        src = BIZHAWK_DIR / name
        dst = PROFILE_DIR / name
        if not src.exists():
            continue
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)

    for name in _LINKED_DIRS:
        src = BIZHAWK_DIR / name
        if src.is_dir():
            _make_junction(PROFILE_DIR / name, src)

    # Real (non-linked) directories so writes stay inside the profile.
    for rel in ("WonderSwan/SaveRAM", "WonderSwan/State", "WonderSwan/Screenshots"):
        (PROFILE_DIR / rel).mkdir(parents=True, exist_ok=True)

    cfg_path = PROFILE_DIR / "config.ini"
    if refresh_config or not cfg_path.exists():
        cfg_path.write_text(
            json.dumps(build_profile_config(), indent=2) + "\n", encoding="utf-8"
        )

    if not PROFILE_EMU.exists():
        raise ProfileError(f"profile executable missing: {PROFILE_EMU}")
    return PROFILE_EMU


# --------------------------------------------------------------------------
# SaveRAM
# --------------------------------------------------------------------------
SAVERAM_DIR = PROFILE_DIR / "WonderSwan" / "SaveRAM"

#: A Cygne/Mednafen WonderSwan .SaveRAM is cartridge SRAM followed by the 1 KiB
#: internal EEPROM. Measured on the file BizHawk itself wrote:
#: 33,792 B = 32,768 + 1,024, with the EEPROM part all zero.
SRAM_SIZE = 32768
EEPROM_SIZE = 1024


def saveram_names(rom: Path) -> list[str]:
    """Names BizHawk may pick for this ROM's .SaveRAM file.

    Measured: ``monoeye_ko_expanded.wsc`` -> ``monoeye ko expanded.SaveRAM``, i.e.
    underscores become spaces. Other capitalisations have been observed, so all
    plausible spellings are written; the emulator reads whichever it wants.
    """
    stem = rom.stem
    spaced = stem.replace("_", " ")
    variants = [stem, spaced, spaced.title(), spaced.lower()]
    seen, out = set(), []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(f"{v}.SaveRAM")
    return out


def install_saveram(rom: Path, sav: Path) -> list[Path]:
    """Install a raw 32 KiB .sav as BizHawk SaveRAM for ``rom``."""
    data = sav.read_bytes()
    if len(data) == SRAM_SIZE:
        blob = data + bytes(EEPROM_SIZE)
    elif len(data) == SRAM_SIZE + EEPROM_SIZE:
        blob = data
    else:
        raise ProfileError(
            f"{sav} is {len(data)} B; expected {SRAM_SIZE} or {SRAM_SIZE + EEPROM_SIZE}"
        )
    SAVERAM_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name in saveram_names(rom):
        dst = SAVERAM_DIR / name
        dst.write_bytes(blob)
        written.append(dst)
    return written


def clear_saveram() -> None:
    """Drop any SaveRAM so a capture depends only on the ROM."""
    if SAVERAM_DIR.is_dir():
        for p in SAVERAM_DIR.iterdir():
            if p.is_file():
                p.unlink()


# --------------------------------------------------------------------------
# process control
# --------------------------------------------------------------------------
def kill_emu() -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Stop-Process -Name EmuHawk -Force -ErrorAction SilentlyContinue",
        ],
        capture_output=True,
        check=False,
    )
    time.sleep(0.5)


@dataclass
class RunResult:
    tag: str
    log: Path
    done: bool
    exited: bool
    seconds: float
    shots: dict  # label -> Path


def run_lua(
    lua: Path,
    rom: Path,
    out_dir: Path,
    tag: str,
    extra_env: Mapping[str, str] | None = None,
    timeout: float = 180.0,
    done_marker: str = "DONE",
) -> RunResult:
    """Run one Lua script against one ROM in the isolated profile.

    Waits for ``done_marker`` in ``<out_dir>/<tag>.log`` and then for the process
    to exit on its own (the scripts call ``client.exit()``); kills it if it
    overstays.
    """
    emu = ensure_profile(refresh_config=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob(f"{tag}_*"):
        stale.unlink(missing_ok=True)
    log = out_dir / f"{tag}.log"
    log.unlink(missing_ok=True)

    env = dict(os.environ)
    env["MONOEYE_OUT"] = str(out_dir)
    env["MONOEYE_TAG"] = tag
    if extra_env:
        env.update(extra_env)

    kill_emu()
    started = time.time()
    proc = subprocess.Popen(
        [str(emu), f"--lua={lua}", str(rom)],
        cwd=str(PROFILE_DIR),
        env=env,
    )
    done = False
    deadline = started + timeout
    while time.time() < deadline:
        if log.exists() and done_marker in log.read_text(
            encoding="utf-8", errors="replace"
        ):
            done = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    # Give client.exit() a moment, then force it down.
    grace = time.time() + 15
    while time.time() < grace and proc.poll() is None:
        time.sleep(0.3)
    exited = proc.poll() is not None
    if not exited:
        proc.kill()
        kill_emu()
    time.sleep(0.4)

    shots = {}
    for png in sorted(out_dir.glob(f"{tag}_*.png")):
        label = png.stem[len(tag) + 1 :]
        shots[label] = png
    return RunResult(
        tag=tag,
        log=log,
        done=done,
        exited=exited,
        seconds=round(time.time() - started, 1),
        shots=shots,
    )


def md5(path: Path, digits: int = 12) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:digits].upper()


def png_hashes(shots: Mapping[str, Path]) -> dict:
    return {label: md5(p) for label, p in sorted(shots.items())}


def parse_log_fields(log: Path, prefix: str) -> list[str]:
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix):
            out.append(line[len(prefix) :].strip())
    return out


__all__ = [
    "ROOT",
    "BIZHAWK_DIR",
    "PROFILE_DIR",
    "PROFILE_EMU",
    "ORIG_ROM",
    "ORIG_SAV",
    "BACKUP_SAV",
    "CAPTURE_DIR",
    "LUA_MENU_CAPTURE",
    "CONFIG_OVERRIDES",
    "ProfileError",
    "RunResult",
    "build_profile_config",
    "ensure_profile",
    "install_saveram",
    "saveram_names",
    "clear_saveram",
    "kill_emu",
    "run_lua",
    "md5",
    "png_hashes",
    "parse_log_fields",
]
