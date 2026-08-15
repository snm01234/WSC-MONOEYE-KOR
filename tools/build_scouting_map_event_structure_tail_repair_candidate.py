#!/usr/bin/env python3
"""Restore remaining bank-62 scouting event structures after 62:D675.

2026-08-03 repaired four ``15 19`` far-pointer slots at ``62:D675–D6CF``
(false ext3 ``E5 18 A0 33–36``).  The same table continues through the rest
of bank 62.  MAP SELECT names are innocent (JP-name probe still died);
Zedan ``12288 20`` and Sahara freeze match executing ``E5 18`` where the
original stream has ``15 19 oo oo E2 00`` event far pointers.

This candidate copies original bytes over every stock-bank-62 difference
from ``62:D800`` to bank end.  That window has no ``17 34 18`` dialogue
prefix on the Japanese side — only event pointers, ``16`` far calls, and
binary headers (``F863F685``, ``E0CA103F…``) that were eaten as text.

Main TIP and live SaveRAM are never modified.
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
OUT_ROM = PATCH / "scouting_map_event_structure_tail_repair_candidate.wsc"
OUT_SAVE = ROOT / "sram/scouting_map_event_structure_tail_repair_candidate.sav"
OUT_REPORT = PATCH / "scouting_map_event_structure_tail_repair_report.json"

ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768
MAIN_SHA = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"

# After the 2026-08-03 four-slot repair (D675–D6CF) and the identical 04-fill.
RESTORE_LOGICAL_START = 0x62D800
RESTORE_LOGICAL_END = 0x630000
PRIOR_REPAIR = (0x62D675, 0x62D6CF)
DIALOGUE_PREFIX = bytes.fromhex("173418")
EXPECTED_RUNS = 92
EXPECTED_BYTES = 671


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


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    lo, hi = run
    return any(lo >= a0 and hi <= a1 for a0, a1 in allowed)


def logical_runs(jp_bank: bytes, ko_bank: bytes, start: int, end: int) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    i = start
    while i < end:
        if jp_bank[i] == ko_bank[i]:
            i += 1
            continue
        j = i
        while j < end and jp_bank[j] != ko_bank[j]:
            j += 1
        rows.append((i, j))
        i = j
    return rows


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(original) != ORIGINAL_SIZE:
        raise BuildError("original ROM size drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    s_parent = stock_base(parent)
    s_orig = stock_base(original)
    jp_bank = original[s_orig + 0x620000 : s_orig + 0x630000]
    ko_bank = parent[s_parent + 0x620000 : s_parent + 0x630000]
    if jp_bank[PRIOR_REPAIR[0] & 0xFFFF : PRIOR_REPAIR[1] & 0xFFFF] != ko_bank[
        PRIOR_REPAIR[0] & 0xFFFF : PRIOR_REPAIR[1] & 0xFFFF
    ]:
        raise BuildError("prior D675–D6CF repair drifted; do not stack this candidate")

    bank_lo = RESTORE_LOGICAL_START & 0xFFFF
    bank_hi = RESTORE_LOGICAL_END & 0xFFFF or 0x10000
    runs = logical_runs(jp_bank, ko_bank, bank_lo, bank_hi)
    nbytes = sum(hi - lo for lo, hi in runs)
    if len(runs) != EXPECTED_RUNS or nbytes != EXPECTED_BYTES:
        raise BuildError(f"tail diff shape drifted: runs={len(runs)} bytes={nbytes}")

    for lo, hi in runs:
        pre = jp_bank[max(0, lo - 16) : lo]
        if DIALOGUE_PREFIX in pre or DIALOGUE_PREFIX in jp_bank[lo:hi]:
            raise BuildError(f"dialogue prefix in restore window 62:{lo:04X}")

    candidate = bytearray(parent)
    records: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for lo, hi in runs:
        logical = 0x620000 + lo
        dst0 = s_parent + logical
        dst1 = s_parent + 0x620000 + hi
        src0 = s_orig + logical
        src1 = s_orig + 0x620000 + hi
        before = bytes(candidate[dst0:dst1])
        source = bytes(original[src0:src1])
        if before == source:
            raise BuildError(f"{logical:06X} already matches original")
        candidate[dst0:dst1] = source
        allowed.append((dst0, dst1))
        records.append(
            {
                "logical_start": f"{logical:06X}",
                "logical_end_exclusive": f"{0x620000 + hi:06X}",
                "length": hi - lo,
                "before_hex": before.hex().upper(),
                "restored_hex": source.hex().upper(),
                "had_false_ext3": before.startswith(bytes.fromhex("E518"))
                or bytes.fromhex("E518") in before,
            }
        )

    checksum = update_ws_checksum(candidate)
    out = bytes(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    file_runs = diff_runs(parent, out)
    unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in file_runs
        if not covered((lo, hi), allowed)
    ]
    restored_bank = out[s_parent + 0x620000 : s_parent + 0x630000]
    checks = {
        "tail_matches_original": restored_bank[0xD800:] == jp_bank[0xD800:],
        "prior_repair_kept": restored_bank[0xD675:0xD6CF] == jp_bank[0xD675:0xD6CF],
        "early_bank62_unchanged": restored_bank[:0xD800] == ko_bank[:0xD800],
        "diffs_bounded": not unaccounted,
        "checksum_valid": checksum_valid(out),
        "main_tip_unchanged": bytes(load_rom(MAIN)) == parent,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
        "false_ext3_removed": all(
            bytes.fromhex("E518") not in out[s_parent + 0x620000 + lo : s_parent + 0x620000 + hi]
            for lo, hi in runs
        ),
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "unaccounted": unaccounted}, ensure_ascii=False))

    atomic_bytes(OUT_ROM, out)
    atomic_bytes(OUT_SAVE, save_before)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scouting_map_event_structure_tail_repair_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "purpose": (
            "Restore leftover scouting/map-enter event far pointers in bank 62 "
            "after 62:D675. Names stay Korean. Test ALL_CLEAR MAP SELECT 제단의 문 "
            "and 사하라 사막."
        ),
        "input": {
            "main_tip": identity(MAIN, parent),
            "original_rom": identity(ORIGINAL, original),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "output": {
            "rom": identity(OUT_ROM, out),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{checksum:04X}",
            "changed_bytes_vs_main": sum(hi - lo for lo, hi in file_runs),
            "restore_runs": len(runs),
            "restore_bytes": nbytes,
        },
        "range": {
            "logical_start": f"{RESTORE_LOGICAL_START:06X}",
            "logical_end_exclusive": f"{RESTORE_LOGICAL_END:06X}",
            "prior_repair_kept": [f"{PRIOR_REPAIR[0]:06X}", f"{PRIOR_REPAIR[1]:06X}"],
        },
        "records": records,
        "checks": checks,
        "test_protocol": {
            "rom": rel(OUT_ROM),
            "save": rel(OUT_SAVE),
            "steps": [
                "ALL_CLEAR 데이터 → 색적 MAP SELECT",
                "제단의 문: 이벤트 오류 12288 20 없이 진입",
                "사하라 사막: 프리즈 없이 진입",
                "홍콩·보급기지 등 기존 정상 맵 재확인",
            ],
        },
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({"ok": True, "rom": rel(OUT_ROM), "checksum": f"{checksum:04X}", "bytes": nbytes, "runs": len(runs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
