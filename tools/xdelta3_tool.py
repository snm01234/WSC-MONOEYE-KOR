"""Locate or fetch a pinned xdelta3 binary for main-TIP delta patches.

xdelta 3.2.0 enables BLAKE3 armor and application metadata by default.
Distribution encodes with ``-a -S -A=``: armor off, secondary compression off,
and no VCDIFF application header. This keeps the stream as plain VCDIFF for
xdeltaUI / older xdelta3 frontends.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Sequence
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "tools" / "vendor"
VENDOR_EXE_WIN = VENDOR_DIR / "xdelta3.exe"

# Official jmacd/xdelta v3.2.0 Windows build (statically linked liblzma).
WINDOWS_RELEASE = {
    "version": "3.2.0",
    "url": (
        "https://github.com/jmacd/xdelta/releases/download/"
        "v3.2.0/xdelta3-3.2.0-windows-x86_64.zip"
    ),
    "zip_sha256": "af8ef036cb077a48df080c9a8ac1be4a6e7511c32d11f8bec89b6803a9e52576",
    "member": "xdelta3-3.2.0-windows-x86_64/xdelta3.exe",
    "exe_sha256": "53d90226615f217d3380c39892833311b4e24acd863e1ca01f14b5e772e2e6d0",
}

# Full original 8 MiB as one source window; full 16 MiB TIP as one target window.
SOURCE_WINDOW = 8 * 1024 * 1024
TARGET_WINDOW = 16 * 1024 * 1024


class XdeltaError(RuntimeError):
    pass


def sha256_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha256_bytes(payload),
    }


def _verify_file_sha(path: Path, expected: str) -> None:
    got = sha256_file(path)
    if got.lower() != expected.lower():
        raise XdeltaError(f"SHA-256 mismatch for {path}: got {got}, expected {expected}")


def fetch_windows_xdelta3(dest: Path = VENDOR_EXE_WIN) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(WINDOWS_RELEASE["url"], timeout=60) as response:
        raw = response.read()
    if sha256_bytes(raw) != WINDOWS_RELEASE["zip_sha256"]:
        raise XdeltaError(
            f"xdelta3 zip SHA-256 mismatch: got {sha256_bytes(raw)}, "
            f"expected {WINDOWS_RELEASE['zip_sha256']}"
        )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        payload = archive.read(WINDOWS_RELEASE["member"])
    if sha256_bytes(payload) != WINDOWS_RELEASE["exe_sha256"]:
        raise XdeltaError(
            f"xdelta3.exe SHA-256 mismatch: got {sha256_bytes(payload)}, "
            f"expected {WINDOWS_RELEASE['exe_sha256']}"
        )
    dest.write_bytes(payload)
    return dest


def resolve_xdelta3(explicit: Path | None = None, *, fetch: bool = True) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise XdeltaError(f"xdelta3 missing: {explicit}")
        return explicit

    env = os.environ.get("XDELTA3")
    if env:
        env_path = Path(env)
        if env_path.is_file():
            return env_path

    if sys.platform == "win32" and VENDOR_EXE_WIN.is_file():
        _verify_file_sha(VENDOR_EXE_WIN, WINDOWS_RELEASE["exe_sha256"])
        return VENDOR_EXE_WIN

    for name in ("xdelta3", "xdelta3.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)

    vendor_posix = VENDOR_DIR / "xdelta3"
    if vendor_posix.is_file():
        return vendor_posix

    if fetch and sys.platform == "win32":
        return fetch_windows_xdelta3()

    raise XdeltaError(
        "xdelta3 not found. Install xdelta3 on PATH, set XDELTA3, or place "
        f"the binary at {VENDOR_EXE_WIN}."
    )


def run_xdelta3(xdelta3: Path, args: Sequence[str], *, cwd: Path | None = None) -> str:
    command = [str(xdelta3), *args]
    completed = subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise XdeltaError(
            f"xdelta3 failed ({completed.returncode}): {' '.join(command)}\n{detail}"
        )
    return completed.stdout


DEFAULT_APP_HEADER = None


def encode_xdelta(
    xdelta3: Path,
    original: Path,
    tip: Path,
    out_path: Path,
    *,
    armor: bool = False,
    secondary: str | None = None,
    level: int = 9,
    app_header: str | None = DEFAULT_APP_HEADER,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-e",
        "-f",
        "-q",
        f"-{level}",
        "-B",
        str(SOURCE_WINDOW),
        "-W",
        str(TARGET_WINDOW),
        # Disable the application header entirely. Older xdeltaUI builds can
        # misinterpret a custom filename-only app header as external-compression
        # metadata and fail with "unrecognized external compression ID".
        "-A=",
    ]
    if app_header is not None:
        # Kept only as an opt-in escape hatch for development; distribution uses
        # no application header for maximum compatibility.
        args[-1:] = ["-A", app_header]
    if secondary is None:
        # xdelta3 3.2.0 enables LZMA secondary compression by default.  A bare
        # -S explicitly disables it; omitting -S would therefore still emit
        # VCD_SECONDARY and break older xdeltaUI/xdelta3 decoders.
        args.append("-S")
    else:
        args.extend(["-S", secondary])
    if not armor:
        args.append("-a")
    args.extend(["-s", str(original), str(tip), str(out_path)])
    run_xdelta3(xdelta3, args)


def decode_xdelta(
    xdelta3: Path,
    original: Path,
    patch: Path,
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_xdelta3(
        xdelta3,
        ["-d", "-f", "-q", "-s", str(original), str(patch), str(out_path)],
    )


def print_delta_info(xdelta3: Path, patch: Path) -> str:
    return run_xdelta3(xdelta3, ["printhdrs", str(patch)])
