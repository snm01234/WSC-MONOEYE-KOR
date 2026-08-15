#!/usr/bin/env python3
"""Build a fail-closed cleanup candidate for proven false replacements in event banks.

The current main contains six historical 09->10 changes inside structural event
bytes. Three of them (66:09DF/09F7/09FC) were runtime-proven by the Sanc Kingdom
Tallgeese III Event Error 3000:1101 and are already fixed in the focused test
candidate. The same historical replacement also changed three other live event
bytes:

- 65:1C25: sequential event/ID run 09 0A 0B 0C -> 10 0A 0B 0C
- 67:39DB: sequential event/ID run 09 0A 0B -> 10 0A 0B
- 67:69C3: event parameter FF 09 01 01 -> FF 10 01 01

This candidate restores all six bytes to the Japanese original and then requires
all remaining original-vs-candidate changes in banks 64-67 to belong to an exact
allowlist of intentional event-name/fixed-label localizations. Main TIP and live
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
from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_ROM = PATCH / "event_bank_false_replacement_cleanup_candidate.wsc"
OUT_SAVE = ROOT / "sram/event_bank_false_replacement_cleanup_candidate.sav"
OUT_REPORT = PATCH / "event_bank_false_replacement_cleanup_report.json"

MAIN_SHA = "d7543ad4a62d9e7a9687583e85005dc4ca137e6fa62238eb70e58492248985c9"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

PROVEN_FALSE_SITES = {
    0x651C25: "STG9 body: sequential ID 09 0A 0B 0C",
    0x6609DF: "STG15T body: far pointer FF 09 E6 00 -> 66:09FF",
    0x6609F7: "STG15T body: far pointer FF 09 E6 00 -> 66:09FF",
    0x6609FC: "STG15T body: far pointer FF 09 E6 00 -> 66:09FF",
    0x6739DB: "STG19N body: sequential ID 09 0A 0B",
    0x6769C3: "STG20T body: event parameter FF 09 01 01",
}

# Exact logical ranges that intentionally differ from stock after the six false
# replacements are removed. These are event-name/fixed-label strings, not body
# bytecode. AF01/C0EC remain quarantined as fixed event-name strings: their
# pointer starts and NUL terminators are preserved, but their dedicated consumer
# is not used as proof for arbitrary future writes.
INTENTIONAL_RANGES = {
    (0x643200, 0x643202): "覚醒 name token",
    (0x64500E, 0x645010): "覚醒 control-name token",
    (0x645019, 0x64501B): "覚醒 name token",
    (0x64501D, 0x64501F): "覚醒 turn-name token",
    (0x64B2B9, 0x64B2BB): "覚醒 name token",
    (0x651F16, 0x651F18): "激突戦宙域 fixed label token",
    (0x6649C4, 0x6649C6): "覚醒 name token",
    (0x66A145, 0x66A147): "ポゥ fixed event-name token",
    (0x66BB3B, 0x66BB3D): "ポゥ fixed event-name token",
    (0x66E004, 0x66E006): "ポゥ fixed event-name token",
    (0x66F18A, 0x66F18C): "覚醒 name token",
    (0x673E06, 0x673E08): "覚醒 name token",
    (0x673EA0, 0x673EA2): "防御 fixed event-name token",
    (0x67AF01, 0x67AF09): "ゲ－ムオ－バ－ event-name string (quarantine/known pointer target)",
    (0x67C0EC, 0x67C0F4): "ゲ－ムオ－バ－ event-name string (quarantine/known pointer target)",
    (0x67EBFB, 0x67EBFD): "防御 fixed event-name token",
    (0x67EC02, 0x67EC04): "防御 fixed event-name token",
    (0x67EC83, 0x67EC85): "防御 fixed event-name token",
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload).upper()}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def logical(data: bytes | bytearray, lo: int, hi: int) -> bytes:
    sb = stock_base(data)
    return bytes(data[sb + lo : sb + hi])


def logical_diff_runs(original: bytes, target: bytes, lo: int, hi: int) -> list[tuple[int, int]]:
    a = logical(original, lo, hi)
    b = logical(target, lo, hi)
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            out.append((lo + start, lo + i))
            start = None
    if start is not None:
        out.append((lo + start, hi))
    return out


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def main() -> int:
    main_before = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = MAIN_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if sha(original) != ORIGINAL_SHA:
        raise BuildError("original identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing/wrong size")

    sbm = stock_base(main_before)
    sbo = stock_base(original)
    out = bytearray(main_before)
    restored = []
    for logical_at, reason in PROVEN_FALSE_SITES.items():
        before = out[sbm + logical_at]
        source = original[sbo + logical_at]
        if before != 0x10 or source != 0x09:
            raise BuildError(f"{logical_at:06X}: expected main/original 10/09, got {before:02X}/{source:02X}")
        out[sbm + logical_at] = source
        restored.append({"logical": f"{logical_at:06X}", "before": "10", "after": "09", "reason": reason})

    update_ws_checksum(out)
    candidate = bytes(out)
    remaining = logical_diff_runs(original, candidate, 0x640000, 0x680000)
    unknown = [run for run in remaining if run not in INTENTIONAL_RANGES]

    pointer_name_checks = {
        "67_AE50_points_AF01": logical(candidate, 0x67AE50, 0x67AE54).hex().upper() == "01AFE700",
        "67_C03A_points_C0EC": logical(candidate, 0x67C03A, 0x67C03E).hex().upper() == "ECC0E700",
        "AF01_terminator_preserved": logical(candidate, 0x67AF09, 0x67AF0A) == b"\x00",
        "C0EC_terminator_preserved": logical(candidate, 0x67C0F4, 0x67C0F5) == b"\x00",
    }
    checks = {
        "six_proven_false_bytes_restored": all(candidate[sbm + x] == 0x09 for x in PROVEN_FALSE_SITES),
        "STG15T_runtime_proven_three_kept_restored": all(candidate[sbm + x] == 0x09 for x in (0x6609DF, 0x6609F7, 0x6609FC)),
        "remaining_event_bank_diffs_all_exact_allowlisted": not unknown,
        "event_name_pointer_and_terminator_checks": all(pointer_name_checks.values()),
        "checksum_valid": checksum_valid(candidate),
        "main_tip_unchanged": bytes(load_rom(MAIN)) == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "unknown": unknown}, ensure_ascii=False))

    atomic_bytes(OUT_ROM, candidate)
    atomic_bytes(OUT_SAVE, save_before)
    report = {
        "schema_version": 1,
        "ok": True,
        "status": "event_banks_64_67_fail_closed_static_clean_after_six_false_restores",
        "input": {"main": identity(MAIN, main_before), "original": identity(ORIGINAL, original), "save": identity(MAIN_SAVE, save_before)},
        "output": {"rom": identity(OUT_ROM, candidate), "save": identity(OUT_SAVE, save_before), "checksum": f"{ws_header(candidate)['checksum']:04X}"},
        "restored": restored,
        "remaining_diff_runs_64_67": [
            {"logical_start": f"{a:06X}", "logical_end_exclusive": f"{b:06X}", "reason": INTENTIONAL_RANGES[(a,b)]}
            for a,b in remaining
        ],
        "unknown_remaining_diff_runs": [{"start": f"{a:06X}", "end": f"{b:06X}"} for a,b in unknown],
        "event_name_quarantine_checks": pointer_name_checks,
        "checks": checks,
        "runtime_test_priority": [
            "STG9 駆け抜ける嵐: event progression around the ID table using 65:1C25",
            "STG19N 宇宙の渦: event progression around the ID table using 67:39DB",
            "STG20T 宇宙の渦（後編）: event progression using parameter at 67:69C3",
        ],
        "promotion": "blocked_pending_runtime_smoke_of_the_three_newly_restored_sites",
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["output"]["rom"], "checksum": report["output"]["checksum"], "remaining_allowlisted_runs": len(remaining), "report": rel(OUT_REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
