#!/usr/bin/env python3
"""Restore the bank-5E battle-quote index and keep native 5D Heero bodies.

The metadata-5D native-only candidate rewrote Heero quote bodies, but the
LE16 index at 5EC33D still pointed at 5E002A (NUL).  Runtime therefore never
read the patched records: Sig fallback portrait + empty box.

This candidate starts from that native-only ROM and copies original
5EC33D–5EC3AF (114 bytes) back.  Main TIP and live SaveRAM are not written.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
    ws_header,
)

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
NATIVE = PATCH / "battle_metadata5d_native_only_candidate.wsc"
OUT_ROM = PATCH / "battle_heero_quote_index_restore_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_heero_quote_index_restore_candidate.sav"
OUT_REPORT = PATCH / "battle_heero_quote_index_restore_candidate_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MAIN_SHA = "528f28e1050257e9f3698f27cf9aa577b217c67cd8951d6030cc5592fc6e0e85"
NATIVE_SHA = "419271adc8b0885e6bc25e173f3ec9d7ae03b6f8cdb44e47f4e1d4a744696e1c"
TABLE_START = 0x5EC33D
TABLE_END = 0x5EC3AF  # exclusive; 114 bytes / 57 LE16 words
TABLE_SLICE_SHA = "29e4293ecd6dc7168a8b1ab1c7a776dcbfcc6ae881140f5d693854276e6066c0"
HEERO_PTR_SITE = 0x5EC37D  # original 00C8 -> 5E00C8
HEERO_PTR_VALUE = 0x00C8
HEERO_RECORD = 0x5E00C8
HEERO_NATIVE_PREFIX = bytes.fromhex("5DF56F")
HEERO_PTRS = {
    0x5EC37D: 0x00C8,
    0x5EC37F: 0x00DA,
    0x5EC383: 0x00EE,
    0x5EC385: 0x00F5,
    0x5EC387: 0x0109,
    0x5EC38F: 0x0143,
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def le16_at(rom: bytes, logical: int) -> int:
    start = stock_base(rom) + logical
    return int.from_bytes(rom[start : start + 2], "little")


def main() -> int:
    main_before = MAIN.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    original = bytes(load_rom(ORIGINAL))
    native = NATIVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(native) != ROM_SIZE or sha(native) != NATIVE_SHA:
        raise BuildError("native-only body candidate identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    orig_sb = stock_base(original)
    native_sb = stock_base(native)
    good = bytes(original[orig_sb + TABLE_START : orig_sb + TABLE_END])
    if len(good) != TABLE_END - TABLE_START or sha(good) != TABLE_SLICE_SHA:
        raise BuildError("original quote-index slice drifted")
    if bytes(native[native_sb + HEERO_RECORD : native_sb + HEERO_RECORD + 3]) != HEERO_NATIVE_PREFIX:
        raise BuildError("native Heero body at 5E00C8 drifted")
    if le16_at(native, HEERO_PTR_SITE) != 0x002A:
        raise BuildError("native-only candidate unexpectedly already restored 5EC37D")

    candidate = bytearray(native)
    site = native_sb + TABLE_START
    before_slice = bytes(candidate[site : site + len(good)])
    candidate[site : site + len(good)] = good
    update_ws_checksum(candidate)
    out = bytes(candidate)

    vs_native = diff_runs(native, out)
    vs_main = diff_runs(main_before, out)
    table_extent = (site, site + len(good))
    checksum_extent = (len(out) - 2, len(out))
    vs_native_unaccounted = [
        run for run in vs_native if not covered(run, [table_extent, checksum_extent])
    ]

    ptrs = {f"{addr:06X}": f"{le16_at(out, addr):04X}" for addr in HEERO_PTRS}
    ptr_ok = all(le16_at(out, addr) == value for addr, value in HEERO_PTRS.items())
    got = read_encoded_z_safe(out, stock_base(out) + HEERO_RECORD, max_len=32)
    heero_live = bytes(got[0]) if got else b""

    checks = {
        "main_identity_exact": sha(main_before) == MAIN_SHA,
        "native_identity_exact": sha(native) == NATIVE_SHA,
        "original_slice_sha_exact": sha(good) == TABLE_SLICE_SHA,
        "table_restored_exact": bytes(out[site : site + len(good)]) == good,
        "table_changed_from_native": before_slice != good,
        "heero_ptr_00c8": le16_at(out, HEERO_PTR_SITE) == HEERO_PTR_VALUE,
        "heero_ptrs_exact": ptr_ok,
        "heero_body_native_kept": heero_live.startswith(HEERO_NATIVE_PREFIX),
        "vs_native_only_table_and_checksum": not vs_native_unaccounted,
        "checksum_valid": checksum_valid(out),
        "main_tip_unchanged": MAIN.read_bytes() == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "ptrs": ptrs, "vs_native": vs_native_unaccounted}, ensure_ascii=False))

    atomic_bytes(OUT_ROM, out)
    atomic_bytes(OUT_SAVE, save_before)

    result = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_heero_quote_index_restore_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_requires_runtime_test_not_promoted",
        "diagnosis": {
            "table": f"{TABLE_START:06X}-{TABLE_END:06X}",
            "bytes": len(good),
            "heero_index_site": f"{HEERO_PTR_SITE:06X}",
            "original_pointer": f"{HEERO_PTR_VALUE:04X}",
            "broken_pointer": "002A",
            "broken_target": "5E002A",
            "heero_record": f"{HEERO_RECORD:06X}",
            "native_body_prefix": HEERO_NATIVE_PREFIX.hex().upper(),
            "classification": "bank-5E battle-quote LE16 index, not zstring text",
        },
        "parent": {
            "main_tip": identity(MAIN, main_before),
            "native_body_layer": identity(NATIVE, native),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "candidate": identity(OUT_ROM, out),
        "saveram": {
            **identity(OUT_SAVE, save_before),
            "copied_from_live": True,
        },
        "checksum": f"{int(ws_header(out)['checksum']):04X}",
        "heero_pointers": ptrs,
        "table_before_hex": before_slice.hex().upper(),
        "table_after_hex": good.hex().upper(),
        "diff_runs_vs_native": [
            {"start": f"{lo:08X}", "end": f"{hi:08X}", "length": hi - lo}
            for lo, hi in vs_native
        ],
        "diff_runs_vs_main_count": len(vs_main),
        "checks": checks,
        "test_protocol": {
            "rom": rel(OUT_ROM),
            "save": rel(OUT_SAVE),
            "observe": [
                "Reset, then Heero Yuy / Wing Zero Custom battle quote",
                "Heero portrait, not Sig Wedner",
                "Non-empty Korean battle line",
            ],
        },
        "promotion": "blocked_pending_runtime_confirmation",
    }
    atomic_json(OUT_REPORT, result)
    print(
        json.dumps(
            {
                "ok": True,
                "rom": rel(OUT_ROM),
                "sha256": sha(out),
                "checksum": result["checksum"],
                "heero_pointers": ptrs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
