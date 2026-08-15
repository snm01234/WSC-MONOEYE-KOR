#!/usr/bin/env python3
"""Repair the sample-54 next-sample terminator overwritten by the Hangul cave.

Root cause:
- Stock PCM sample table entry 54 occupies 7F:FC46-FC4D.
- The original next-sample word at 7F:FC4C is FFFF (stop playback).
- The historical Hangul primary cave started at 7F:FC4C and overwrote FFFF
  with its first bytes 9A F1. The PCM ISR interpreted that as next sample
  index F19A after the valid ID-command effect, then streamed arbitrary data.

Repair:
- Move the currently installed 53-byte pad3-aware primary from FC4C to FC4E.
- Retarget the sole trampoline at 7A:FFB5 from F000:FC4C to F000:FC4E.
- Restore sample entry 54 exactly to the original eight bytes.
- Preserve the fixed ext_dict helper at FC8C and every later runtime cave.

This builds a test candidate only. It never modifies the main TIP or SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OUT_ROM = ROOT / "out/patch/id_command_audio_sample54_table_repair_candidate.wsc"
OUT_SAVE = ROOT / "sram/id_command_audio_sample54_table_repair_candidate.sav"
REPORT = ROOT / "out/patch/id_command_audio_sample54_table_repair_report.json"

EXPECTED_MAIN_SHA256 = "ed44538a78491a1bd93022930ff6c3ec67da0b03b9e5fb5666dd1ef4df05b692"
EXPECTED_ORIGINAL_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

TRAMPOLINE = 0x7AFFB5
TRAMPOLINE_OLD = bytes.fromhex("EA4CFC00F0")
TRAMPOLINE_NEW = bytes.fromhex("EA4EFC00F0")

SAMPLE_TABLE = 0x7FFA96
SAMPLE_COUNT = 55
SAMPLE_ENTRY_SIZE = 8
SAMPLE54 = SAMPLE_TABLE + 54 * SAMPLE_ENTRY_SIZE  # 7F:FC46
SAMPLE54_ORIGINAL = bytes.fromhex("30BAEF009715FFFF")
SAMPLE54_BROKEN = bytes.fromhex("30BAEF0097159AF1")

PRIMARY_OLD = 0x7FFC4C
PRIMARY_NEW = 0x7FFC4E
PRIMARY_LEN = 53
EXPECTED_PRIMARY = bytes.fromhex(
    "9AF1FC00F0"
    "F7C30080"
    "7420"
    "81E3FF7F"
    "81EB2008"
    "81FB6000"
    "730D"
    "C1E304"
    "BAF8F9"
    "03D3"
    "EA2B0500A0"
    "EAABFC00F0"
    "C1E304"
    "03D3"
    "EA2B0500A0"
)

DICT_HELPER = 0x7FFC8C
RUNTIME_END = 0x7FFD10


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(data), "sha256": sha256(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise BuildError("ROM size changed")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    left, right = run
    return any(a <= left and right <= b for a, b in allowed)


def occurrences(data: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    cursor = 0
    while True:
        found = data.find(needle, cursor)
        if found < 0:
            return out
        out.append(found)
        cursor = found + 1


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if sha256(original) != EXPECTED_ORIGINAL_SHA256:
        raise BuildError("original ROM identity drifted")
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    psb = stock_base(parent)
    osb = stock_base(original)
    def p(logical: int) -> int:
        return psb + logical
    def o(logical: int) -> int:
        return osb + logical

    if parent[p(TRAMPOLINE):p(TRAMPOLINE)+5] != TRAMPOLINE_OLD:
        raise BuildError("Hangul trampoline identity drifted")
    if parent[p(SAMPLE54):p(SAMPLE54)+8] != SAMPLE54_BROKEN:
        raise BuildError("broken sample-54 entry identity drifted")
    if original[o(SAMPLE54):o(SAMPLE54)+8] != SAMPLE54_ORIGINAL:
        raise BuildError("original sample-54 entry identity drifted")
    old_primary = parent[p(PRIMARY_OLD):p(PRIMARY_OLD)+PRIMARY_LEN]
    if old_primary != EXPECTED_PRIMARY:
        raise BuildError(
            "installed pad3 primary identity drifted: " + old_primary.hex().upper()
        )
    if PRIMARY_NEW + PRIMARY_LEN > DICT_HELPER:
        raise BuildError("relocated primary would overlap ext_dict helper")

    protected_runtime = parent[p(DICT_HELPER):p(RUNTIME_END)]
    original_sample_table = original[
        o(SAMPLE_TABLE):o(SAMPLE_TABLE)+SAMPLE_COUNT*SAMPLE_ENTRY_SIZE
    ]

    candidate = bytearray(parent)
    # Save source first because source and destination overlap by 51 bytes.
    candidate[p(PRIMARY_NEW):p(PRIMARY_NEW)+PRIMARY_LEN] = old_primary
    candidate[p(PRIMARY_OLD):p(PRIMARY_OLD)+2] = b"\xFF\xFF"
    candidate[p(TRAMPOLINE):p(TRAMPOLINE)+5] = TRAMPOLINE_NEW
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    repaired_sample_table = candidate_bytes[
        p(SAMPLE_TABLE):p(SAMPLE_TABLE)+SAMPLE_COUNT*SAMPLE_ENTRY_SIZE
    ]
    checks = {
        "main_identity_exact": sha256(parent) == EXPECTED_MAIN_SHA256,
        "sample54_entry_exact_original": (
            candidate_bytes[p(SAMPLE54):p(SAMPLE54)+8] == SAMPLE54_ORIGINAL
        ),
        "all_55_sample_entries_exact_original": repaired_sample_table == original_sample_table,
        "primary_relocated_exact": (
            candidate_bytes[p(PRIMARY_NEW):p(PRIMARY_NEW)+PRIMARY_LEN] == old_primary
        ),
        "trampoline_retargeted_exact": (
            candidate_bytes[p(TRAMPOLINE):p(TRAMPOLINE)+5] == TRAMPOLINE_NEW
        ),
        "ext_dict_and_later_runtime_exact": (
            candidate_bytes[p(DICT_HELPER):p(RUNTIME_END)] == protected_runtime
        ),
        "old_target_reference_removed": not occurrences(
            candidate_bytes, bytes.fromhex("EA4CFC00F0")
        ),
        "new_target_reference_exactly_one": len(
            occurrences(candidate_bytes, bytes.fromhex("EA4EFC00F0"))
        ) == 1,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_MAIN_SHA256,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_snapshot,
    }

    runs = diff_runs(parent, candidate_bytes)
    allowed = [
        (p(TRAMPOLINE), p(TRAMPOLINE) + 5),
        (p(PRIMARY_OLD), p(PRIMARY_NEW) + PRIMARY_LEN),
        (len(parent) - 2, len(parent)),
    ]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    checks["diffs_bounded"] = not unaccounted
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {"checks": checks, "unaccounted": unaccounted},
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_audio_sample54_table_repair_candidate.py",
        "ok": True,
        "canonical": False,
        "promotion_allowed": False,
        "status": "static_verified_pending_user_runtime_test",
        "root_cause": {
            "sample": 54,
            "sample_table_entry": "7F:FC46-FC4D",
            "original_entry": SAMPLE54_ORIGINAL.hex().upper(),
            "broken_entry": SAMPLE54_BROKEN.hex().upper(),
            "overwritten_field": "next_sample word at 7F:FC4C-4D",
            "original_next_sample": "FFFF (stop)",
            "broken_next_sample": "F19A (invalid chained sample index)",
            "overwriter": "Hangul primary runtime cave formerly starting at 7F:FC4C",
            "symptom_chain": (
                "sample 54 plays its valid ID-command effect; on completion the PCM ISR "
                "treats F19A as a chained sample index and streams arbitrary ROM data as noise"
            ),
        },
        "parent": identity(MAIN, parent),
        "original": identity(ORIGINAL, original),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE, save_snapshot),
            "policy": "test-only snapshot; never promote SaveRAM",
        },
        "repair": {
            "primary_move": {
                "from": "7F:FC4C",
                "to": "7F:FC4E",
                "length": PRIMARY_LEN,
                "payload_sha256": sha256(old_primary),
            },
            "trampoline": {
                "site": "7A:FFB5",
                "before": TRAMPOLINE_OLD.hex().upper(),
                "after": TRAMPOLINE_NEW.hex().upper(),
            },
            "sample54_entry": {
                "site": "7F:FC46",
                "before": SAMPLE54_BROKEN.hex().upper(),
                "after": SAMPLE54_ORIGINAL.hex().upper(),
            },
            "protected_runtime": "7F:FC8C-FD0F exact",
        },
        "checks": checks,
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(right-left for left, right in runs),
            "checksum": f"{checksum:04X}",
            "stored_checksum_bytes": candidate_bytes[-2:].hex().upper(),
            "unaccounted_runs": unaccounted,
        },
        "runtime_test": [
            "Activate an ID command and confirm the intended activation effect remains.",
            "Confirm the subsequent approximately five-second noise is gone.",
            "Check ordinary UI, battle, weapon, and character sound effects.",
            "Check Hangul text in pad1, pad2, and pad3 glyph ranges.",
            "Save, fully close the emulator, restart, and load the paired SaveRAM.",
        ],
        "promotion": "blocked_pending_user_runtime_validation",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["status"],
                "candidate": report["candidate"],
                "candidate_save": report["candidate_save"],
                "root_cause": report["root_cause"],
                "checks": checks,
                "diff": report["diff"],
                "report": str(REPORT.relative_to(ROOT)).replace("\\", "/"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
