#!/usr/bin/env python3
"""Probe: restore original JP name bytes for 제단의 문 and 사하라 사막 only.

The FF09 pointer restore in banks 64–67 did not change MAP SELECT enter
symptoms.  Remaining unique property of the two failing items on that screen:
5-byte KO bodies ``E5 18 xx yy 01``.  Working ext3 names are 4 or 6+ bytes.

This probe puts the original 5-byte Japanese payloads back (terminators stay)
so a single in-game test can tell whether the KO encoding is causal.  Main TIP
and live SaveRAM are never modified.  Korean labels will revert on those two
rows only.
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

from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT_ROM = PATCH / "scouting_map_select_name_jp_restore_probe.wsc"
OUT_SAVE = ROOT / "sram/scouting_map_select_name_jp_restore_probe.sav"
OUT_REPORT = PATCH / "scouting_map_select_name_jp_restore_probe_report.json"

ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768
MAIN_SHA = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"

SITES: tuple[tuple[int, bytes, bytes, str], ...] = (
    (0x75BDFA, bytes.fromhex("E518B62A01"), bytes.fromhex("F5EE05E1E8"), "zedan 제단의 문"),
    (0x75BDB2, bytes.fromhex("E518B60901"), bytes.fromhex("657E2EF4D1"), "sahara 사하라 사막"),
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data).upper()}


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


def diff_runs(left: bytes, right: bytes) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            rows.append((start, index))
            start = None
    if start is not None:
        rows.append((start, len(left)))
    return rows


def main() -> int:
    main_before = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = MAIN_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(original) != ORIGINAL_SIZE:
        raise BuildError("original ROM size drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    s_main = stock_base(main_before)
    s_orig = stock_base(original)
    candidate = bytearray(main_before)
    records: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []

    for logical, bad, good, label in SITES:
        dst = s_main + logical
        src = s_orig + logical
        if bytes(candidate[dst : dst + 5]) != bad:
            raise BuildError(f"{logical:06X}: TIP body {bytes(candidate[dst:dst+5]).hex()} != {bad.hex()}")
        if original[src : src + 5] != good:
            raise BuildError(f"{logical:06X}: original body drifted")
        if candidate[dst + 5] != 0 or original[src + 5] != 0:
            raise BuildError(f"{logical:06X}: terminator moved")
        candidate[dst : dst + 5] = good
        allowed.append((dst, dst + 5))
        records.append(
            {
                "logical": f"{logical:06X}",
                "label": label,
                "before": bad.hex().upper(),
                "restored": good.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    out = bytes(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    runs = diff_runs(main_before, out)
    unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not any(lo >= a0 and hi <= a1 for a0, a1 in allowed)
    ]
    checks = {
        "bodies_match_original": all(
            out[s_main + logical : s_main + logical + 5] == original[s_orig + logical : s_orig + logical + 5]
            for logical, _bad, _good, _label in SITES
        ),
        "terminators_kept": all(out[s_main + logical + 5] == 0 for logical, _b, _g, _l in SITES),
        "diffs_bounded": not unaccounted,
        "checksum_valid": checksum_valid(out),
        "main_tip_unchanged": bytes(load_rom(MAIN)) == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "unaccounted": unaccounted}, ensure_ascii=False))

    atomic_bytes(OUT_ROM, out)
    atomic_bytes(OUT_SAVE, save_before)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scouting_map_select_name_jp_restore_probe.py",
        "ok": True,
        "published": False,
        "status": "diagnostic_probe_not_a_korean_fix",
        "purpose": "If JP names enter normally, KO 5-byte E518+01 encoding is causal. If still fail, cause is outside the name records.",
        "input": {
            "main_tip": identity(MAIN, main_before),
            "original_rom": identity(ORIGINAL, original),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "output": {
            "rom": identity(OUT_ROM, out),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{checksum:04X}",
            "changed_bytes_vs_main": sum(hi - lo for lo, hi in runs),
        },
        "records": records,
        "checks": checks,
        "test_protocol": {
            "rom": rel(OUT_ROM),
            "expect_labels": ["ゼダンの門", "サハラ砂漠"],
            "observe": [
                "제단의 문 행이 일본어로 보이는지",
                "그 상태로 진입 시 12288 20 여부",
                "사하라 사막 행이 일본어로 보이는지",
                "그 상태로 진입 시 프리즈 여부",
            ],
        },
        "promotion": "blocked_diagnostic_only",
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({"ok": True, "rom": report["output"]["rom"], "checksum": report["output"]["checksum"], "report": rel(OUT_REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
