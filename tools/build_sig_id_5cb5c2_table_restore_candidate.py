#!/usr/bin/env python3
"""Restore the single causal ID-command table byte at logical 5C:B5C2.

Runtime isolation proved that changing only logical 5C:B5C2 from F585 to F573
is sufficient to trigger the Sig Wedna(Z) Event Error.  The bytes are part of
an ascending little-endian 16-bit table, not text:

    85BF, 85D2, 85E8, 85F5, 8609, 8629, 8636, 864A, ...

P2 misclassified raw bytes F5 85 as dictionary token F585 and rewrote them to
F5 73, corrupting table value 85F5 into 73F5.  This builder starts from the
current main TIP and restores only F573 -> F585 at that site, plus checksum.
It never modifies the main TIP or live SaveRAM.
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

from monoeye_rom import stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_ROM = PATCH / "sig_id_5cb5c2_table_restore_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_id_5cb5c2_table_restore_candidate.sav"
OUT_REPORT = PATCH / "sig_id_5cb5c2_table_restore_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MAIN_SHA = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"
LOGICAL_SITE = 0x5CB5C2
BAD = bytes.fromhex("F573")
GOOD = bytes.fromhex("F585")
EXPECTED_CONTEXT_BAD = bytes.fromhex("BF85D285E885F5730986298636864A86")
EXPECTED_CONTEXT_GOOD = bytes.fromhex("BF85D285E885F5850986298636864A86")


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
    main_before = MAIN.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    candidate = bytearray(main_before)
    base = stock_base(candidate)
    site = base + LOGICAL_SITE
    context_start = site - 6
    context_end = site + 10
    if bytes(candidate[site : site + 2]) != BAD:
        raise BuildError(f"unexpected bytes at 5C:B5C2: {candidate[site:site+2].hex()}")
    if bytes(candidate[context_start:context_end]) != EXPECTED_CONTEXT_BAD:
        raise BuildError("structured table context drifted")

    candidate[site : site + 2] = GOOD
    if bytes(candidate[context_start:context_end]) != EXPECTED_CONTEXT_GOOD:
        raise BuildError("table restoration failed")
    update_ws_checksum(candidate)
    out = bytes(candidate)
    runs = diff_runs(main_before, out)

    checks = {
        "main_identity_exact": sha(main_before) == MAIN_SHA,
        "bad_table_entry_present_before": main_before[site : site + 2] == BAD,
        "good_table_entry_present_after": out[site : site + 2] == GOOD,
        "context_restored_exact": out[context_start:context_end] == EXPECTED_CONTEXT_GOOD,
        "only_site_and_checksum_changed": len(runs) == 2
        and runs[0] == (site + 1, site + 2)
        and runs[1][0] >= ROM_SIZE - 2,
        "checksum_valid": checksum_valid(out),
        "main_tip_unchanged": MAIN.read_bytes() == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps(checks, ensure_ascii=False))

    atomic_bytes(OUT_ROM, out)
    atomic_bytes(OUT_SAVE, save_before)

    result = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_id_5cb5c2_table_restore_candidate.py",
        "ok": True,
        "published": False,
        "status": "runtime_confirmation_required",
        "runtime_evidence": {
            "stage06_parent": "good",
            "table_5cb5c2_only_probe": "Event Error",
            "causal_write": "5C:B5C2 F585 -> F573 is sufficient to trigger the regression",
        },
        "diagnosis": {
            "logical_site": "5C:B5C2",
            "file_site": f"{site:08X}",
            "bad_bytes": BAD.hex().upper(),
            "restored_bytes": GOOD.hex().upper(),
            "bad_u16_le": "73F5",
            "restored_u16_le": "85F5",
            "classification": "ascending structured 16-bit table entry, not text",
        },
        "input": {
            "main_tip": identity(MAIN, main_before),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "output": {
            "rom": identity(OUT_ROM, out),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{int(ws_header(out)['checksum']):04X}",
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
        "checks": checks,
        "test_protocol": {
            "rom": rel(OUT_ROM),
            "observe": [
                "Sig Wedna(Z) ID command activation",
                "expected dialogue flow",
                "dictionary text auto-advance absence",
                "Event Error absence",
            ],
        },
        "promotion": "blocked_pending_runtime_confirmation",
    }
    atomic_json(OUT_REPORT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
