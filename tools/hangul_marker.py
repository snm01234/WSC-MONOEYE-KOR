#!/usr/bin/env python3
"""
Single source of truth for the Hangul run marker code.

The marker is a 2-byte sentinel the font hook's dispatch cave consumes with
``cmp cx, marker`` to start a Hangul run. It is also embedded in every Hangul
payload this patch writes, and mapped to the empty string in the patched TBL so it
does not render.

It used to be hardcoded as ``E3DB`` in roughly twenty tools. That code is the code
for the character ``映`` and occurs 10 times in the original text banks, so the
shared hook set the sticky Hangul flag on stock strings and garbled the unit and
battle UI. Because the constant was duplicated everywhere, moving it required
touching every caller — hence this module: read the value, do not copy it.

The installed value lives in ``out/patch/hangul_char_map.json`` under
``padding_store.marker_code``, which ``tools/patch_font_hangul_hook.py`` writes and
``tools/retarget_hangul_marker.py`` updates.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAR_MAP = ROOT / "out/patch/hangul_char_map.json"

# The current checked-in runtime uses EC8D (mapped to the empty string in
# hangul_patch_pad3.tbl).  The generated char-map is intentionally absent from
# some release workspaces, so falling back to the historic E3DB glyph silently
# disables every raw-marker guard.  A writer still prefers the installed map
# when present, but the release fallback must match the shipped runtime.
FALLBACK = 0xEC8D


def marker_code(char_map: Path | None = None) -> int:
    """The installed marker code, from the char map when it exists."""
    path = char_map or CHAR_MAP
    if not path.exists():
        return FALLBACK
    try:
        pad = json.loads(path.read_text(encoding="utf-8")).get("padding_store") or {}
    except (OSError, json.JSONDecodeError):
        return FALLBACK
    code = pad.get("marker_code")
    return int(code, 16) if code else FALLBACK


def marker_pair(char_map: Path | None = None) -> bytes:
    """The marker as its two ROM bytes, big-endian as it appears in a payload."""
    code = marker_code(char_map)
    return bytes([code >> 8, code & 0xFF])


if __name__ == "__main__":
    print(f"{marker_code():04X}")


def resolve_marker(declared: str | int | None, *, source: str = "catalog") -> int:
    """
    The marker to actually write, ignoring any stale value a data file declares.

    Data catalogs under ``data/`` carry a ``"marker"`` field that was frozen when
    the catalog was authored. When the installed marker is retargeted those fields
    go stale, and a writer that trusts them emits the OLD code — which is a real
    character (``E3DB`` = ``映``), so the text renders as that character and the
    Hangul run flag is never raised. The declared value is therefore advisory
    only: it is compared, reported, and discarded.

    Returns the installed marker. Prints a warning when ``declared`` disagrees.
    """
    installed = marker_code()
    if declared is None:
        return installed
    value = int(declared, 16) if isinstance(declared, str) else int(declared)
    if value != installed:
        print(
            f"WARNING: {source} declares marker {value:04X} but the installed "
            f"marker is {installed:04X}; using {installed:04X}. "
            f"Update the {source} or leave its marker field out."
        )
    return installed
