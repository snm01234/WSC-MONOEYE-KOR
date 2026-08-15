#!/usr/bin/env python3
"""Diagnostic ROM: XOR the bank-6B stream span of ending-credit page 0.

WRAM 0842/0844 during credits is a 32-bit playhead into stock ``6B:685D``.
This candidate inverts the 477 bytes up to the next captured playhead
(``6B:6A3A``, slot 1) so a fresh credit entry should garble 製作 / 메인 프로그램
if that stream is really the staff-roll source.

Does not touch the main TIP or live SaveRAM. Not for promotion.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT = ROOT / "out/patch/ending_credits_bank6b_xor_probe.wsc"
OUT_SAVE = ROOT / "sram/ending_credits_bank6b_xor_probe.sav"
REPORT = ROOT / "out/patch/ending_credits_bank6b_xor_probe_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
BANK = 0x6B
OFF_LO = 0x685D
OFF_HI = 0x6A3A  # exclusive; next captured playhead (slot 1)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict:
    raw = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(a)))
    return out


def main() -> int:
    if not MAIN.is_file() or not STOCK.is_file() or not SAVE.is_file():
        raise BuildError("missing parent ROM, stock ROM, or SaveRAM")
    parent = MAIN.read_bytes()
    stock = STOCK.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE:
        raise BuildError(f"parent is not 16 MiB: {len(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"SaveRAM size {len(save)}")
    base = stock_base(parent)
    logical = BANK * 0x10000 + OFF_LO
    length = OFF_HI - OFF_LO
    physical = base + logical
    source = bytes(parent[physical : physical + length])
    stock_src = bytes(stock[logical : logical + length])
    if source != stock_src:
        raise BuildError("6B:685D span is not stock-exact in the parent")
    if source == b"\x00" * length or source == b"\xFF" * length:
        raise BuildError("span is empty")

    patched = bytes(b ^ 0xFF for b in source)
    candidate = bytearray(parent)
    candidate[physical : physical + length] = patched
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if MAIN.read_bytes() != parent:
        raise BuildError("parent ROM mutated")
    if SAVE.read_bytes() != save:
        raise BuildError("live SaveRAM mutated")

    runs = diff_runs(parent, result)
    allow = {(physical, physical + length), (ROM_SIZE - 2, ROM_SIZE)}
    unexpected = [run for run in runs if run not in allow]
    if unexpected:
        raise BuildError(f"writes outside allowlist: {unexpected}")

    atomic_bytes(OUT, result)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    tmp_save = OUT_SAVE.with_name(f".{OUT_SAVE.name}.{os.getpid()}.tmp")
    shutil.copyfile(SAVE, tmp_save)
    os.replace(tmp_save, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_bank6b_xor_probe.py",
        "ok": True,
        "status": "diagnostic_probe_pending_user_runtime_test",
        "hypothesis": (
            "ending-credit pages are a compressed byte stream at stock 6B:685D+, "
            "with WRAM 0842/0844 as the 32-bit playhead (bank 6B, offset 685D on page 0)"
        ),
        "expect": (
            "fresh credit entry (not an old .state) should garble the first staff "
            "page(s). Later pages may also desync. If credits look unchanged, 6B is "
            "not the source and this probe is discarded."
        ),
        "parent": identity(MAIN, parent),
        "candidate": {**identity(OUT, result), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(OUT_SAVE),
        "patch": {
            "logical": f"{logical:06X}",
            "physical": f"{physical:08X}",
            "lo": f"{BANK:02X}:{OFF_LO:04X}",
            "hi_exclusive": f"{BANK:02X}:{OFF_HI:04X}",
            "bytes": length,
            "op": "xor_ff",
            "source_sha256": sha256(source),
            "target_sha256": sha256(patched),
        },
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(b - a for a, b in runs),
            "allowlist_clean": True,
        },
        "guards": {
            "main_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": SAVE.read_bytes() == save,
            "source_stock_exact": True,
        },
        "how_to_run": (
            "Open out/patch/ending_credits_bank6b_xor_probe.wsc in RetroArch "
            "Beetle WonderSwan. Paired SaveRAM is "
            "sram/ending_credits_bank6b_xor_probe.sav. Do not load "
            "monoeye_ko_expanded.state*: those restore old VRAM. Reach the "
            "ending credits from gameplay."
        ),
        "promotion": "blocked_diagnostic_only",
    }
    atomic_json(REPORT, report)
    print(json.dumps(
        {k: report[k] for k in ("ok", "status", "patch", "diff", "how_to_run", "promotion")},
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
