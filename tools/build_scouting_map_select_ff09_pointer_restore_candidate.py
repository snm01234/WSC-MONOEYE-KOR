#!/usr/bin/env python3
"""Restore six false FF09→FF10 bytes in stage event banks 64–67.

ALL_CLEAR MAP SELECT: 제단의 문 event-errors at 3000:0014, 사하라 사막 freezes.
Other ext3 names (월면/지구 위성/관측/달 궤도) enter normally, so the name
records themselves are not the cause.

A later 각성 token write (F3B3→FF09) also rewrote live event bytes that merely
looked like FF09:

* 66:09DF / 09F7 / 09FC — far pointers ``FF 09 E6 00`` (66:09FF) became
  ``FF 10 E6 00`` (66:10FF tile-like data) → jump-to-nowhere freeze
* 65:1C25 / 67:39DB — sequential ID runs ``09 0A 0B`` became ``10 0A 0B``
* 67:69C3 — ``FF 09 01 01`` event parameter became ``FF 10``

This builder copies those six original ``09`` bytes back, plus the WonderSwan
checksum.  Intentional event-name Hangul (F3B3→FF09 각성, F208, F34B, F2FE,
게임오버) and bank 60–63 dialogue are left untouched.  Main TIP and live
SaveRAM are never modified.
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
OUT_ROM = PATCH / "scouting_map_select_ff09_pointer_restore_candidate.wsc"
OUT_SAVE = ROOT / "sram/scouting_map_select_ff09_pointer_restore_candidate.sav"
OUT_REPORT = PATCH / "scouting_map_select_ff09_pointer_restore_report.json"

ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768
MAIN_SHA = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"

# Logical stock addresses. Each is a single byte 10 (TIP) that must be 09 (JP).
RESTORE_SITES: tuple[tuple[int, str], ...] = (
    (0x651C25, "STG9-adjacent sequential ID 09 0A 0B 0C"),
    (0x6609DF, "far pointer 05 FF 09 E6 00 -> 66:09FF"),
    (0x6609F7, "far pointer 15 19 FF 09 E6 00 -> 66:09FF"),
    (0x6609FC, "far pointer 05 FF 09 E6 00 -> 66:09FF"),
    (0x6739DB, "STG19N-adjacent sequential ID 09 0A 0B"),
    (0x6769C3, "event param FF 09 01 01"),
)

# Event-name Hangul that must remain (not part of this restore).
KEEP_FF09_NAMES: tuple[int, ...] = (
    0x643200,
    0x64500E,
    0x645019,
    0x64501D,
    0x64B2B9,
    0x6649C4,
    0x66F18A,
    0x673E06,
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

    for logical, reason in RESTORE_SITES:
        src = s_orig + logical
        dst = s_main + logical
        jp_b = original[src]
        ko_b = candidate[dst]
        if jp_b != 0x09:
            raise BuildError(f"{logical:06X}: original is {jp_b:02X}, expected 09")
        if ko_b != 0x10:
            raise BuildError(f"{logical:06X}: TIP is {ko_b:02X}, expected 10")
        candidate[dst] = 0x09
        allowed.append((dst, dst + 1))
        records.append(
            {
                "logical": f"{logical:06X}",
                "file": f"{dst:08X}",
                "before": f"{ko_b:02X}",
                "restored": "09",
                "reason": reason,
            }
        )

    for logical in KEEP_FF09_NAMES:
        dst = s_main + logical
        if bytes(candidate[dst : dst + 2]) != bytes.fromhex("FF09"):
            raise BuildError(f"{logical:06X}: expected kept FF09 event-name token")

    checksum = update_ws_checksum(candidate)
    out = bytes(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    runs = diff_runs(main_before, out)
    unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}", "length": hi - lo}
        for lo, hi in runs
        if not any(lo >= a0 and hi <= a1 for a0, a1 in allowed)
    ]

    # Name records for the two failing maps must be unchanged.
    zedan = s_main + 0x75BDFA
    sahara = s_main + 0x75BDB2
    name_unchanged = (
        main_before[zedan : zedan + 6] == out[zedan : zedan + 6]
        and main_before[sahara : sahara + 6] == out[sahara : sahara + 6]
    )

    checks = {
        "six_bytes_restored_to_original_09": all(
            out[s_main + logical] == 0x09 and original[s_orig + logical] == 0x09
            for logical, _ in RESTORE_SITES
        ),
        "far_pointer_66_09FF_restored": out[s_main + 0x6609DE : s_main + 0x6609E2]
        == bytes.fromhex("FF09E600"),
        "id_run_65_1C25_restored": out[s_main + 0x651C25 : s_main + 0x651C28]
        == bytes.fromhex("090A0B"),
        "id_run_67_39DB_restored": out[s_main + 0x6739DB : s_main + 0x6739DE]
        == bytes.fromhex("090A0B"),
        "awakening_event_names_kept_ff09": all(
            out[s_main + logical : s_main + logical + 2] == bytes.fromhex("FF09")
            for logical in KEEP_FF09_NAMES
        ),
        "map_select_names_unchanged": name_unchanged,
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
        "generated_by": "tools/build_scouting_map_select_ff09_pointer_restore_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "diagnosis": {
            "failing_maps": ["제단의 문 75BDFA event_error 12288 20", "사하라 사막 75BDB2 freeze"],
            "ruled_out": "ext3 name encoding E518 (월면/지구위성/관측/달궤도 OK)",
            "causal_class": "FF-page 각성 token FF09 falsely rewritten inside event pointers/ID runs",
            "restore_bytes": 6,
        },
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
            "diff_runs_vs_main": [
                {
                    "file_start": f"{lo:08X}",
                    "file_end_exclusive": f"{hi:08X}",
                    "length": hi - lo,
                }
                for lo, hi in runs
            ],
        },
        "records": records,
        "kept_ff09_event_names": [f"{logical:06X}" for logical in KEEP_FF09_NAMES],
        "checks": checks,
        "unaccounted_diff_runs": unaccounted,
        "test_protocol": {
            "rom": rel(OUT_ROM),
            "save": rel(OUT_SAVE),
            "observe": [
                "ALL_CLEAR 데이터 색적 MAP SELECT",
                "사하라 사막 진입: 프리즈/이벤트 오류 없이 맵 로드",
                "제단의 문 진입: 이벤트 오류 12288 20 없음",
                "홍콩·보급기지·월면·지구 위성·관측·달 궤도 회귀 없음",
            ],
        },
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(OUT_REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["output"]["rom"],
                "checksum": report["output"]["checksum"],
                "changed_bytes_vs_main": report["output"]["changed_bytes_vs_main"],
                "report": rel(OUT_REPORT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
