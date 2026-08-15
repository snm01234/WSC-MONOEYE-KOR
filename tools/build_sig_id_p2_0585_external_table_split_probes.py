#!/usr/bin/env python3
"""Split the nine slot-0585 external detachment writes around 5C:B5C2.

Runtime evidence:
- nested detachment 5F:6DC0 alone is good
- all nine external F585 -> F573 writes together produce Event Error

Static inspection shows 5C:B5C2 lies inside an ascending 16-bit value table:
    BF85 D285 E885 F585 0986 2986 3686 ...
It is therefore not a zstring token consumer.  This builder creates:
1) a probe changing only 5C:B5C2
2) a probe changing the other eight external sites

The exact stage06-good ROM is the parent.  Main TIP and live SaveRAM are never
modified.
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
STAGE06 = PATCH / "sig_id_p2_stage_06_duplicate_batch_candidate.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
NESTED_REPORT = PATCH / "p2_nested_duplicate_batch_report.json"
OUT_REPORT = PATCH / "sig_id_p2_0585_external_table_split_probe_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STAGE06_SHA = "5e4208265d145ccb3706f71f57aa1f3a9d6e592ce23dbe5ebc59050a8b2eeef1"
MAIN_SHA = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"
TABLE_SITE = "5CB5C2"
EXPECTED_EXTERNAL_SITES = {
    "5C036E", "5C1766", "5C2356", "5C5132", "5C51F8",
    "5C566E", "5C5CDD", "5C5D55", "5CB5C2",
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


def get_external_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in report["apply_report"]["detachment_writes"]
        if row.get("before_hex", "").upper() == "F585"
        and row.get("region") == "aux"
        and row.get("kind") == "zstring"
    ]
    sites = {row["token_abs"].upper() for row in rows}
    if sites != EXPECTED_EXTERNAL_SITES:
        raise BuildError(f"external site population drifted: {sorted(sites)}")
    return rows


def apply_rows(parent: bytes, rows: list[dict[str, Any]]) -> bytes:
    candidate = bytearray(parent)
    base = stock_base(candidate)
    for row in rows:
        pos = base + int(row["token_abs"], 16)
        before = bytes.fromhex(row["before_hex"])
        after = bytes.fromhex(row["after_hex"])
        if bytes(candidate[pos : pos + len(before)]) != before:
            raise BuildError(f"parent mismatch at {row['token_abs']}")
        candidate[pos : pos + len(after)] = after
    update_ws_checksum(candidate)
    return bytes(candidate)


def describe(name: str, parent: bytes, rom: bytes, rows: list[dict[str, Any]]) -> dict[str, Any]:
    runs = diff_runs(parent, rom)
    return {
        "name": name,
        "sites": [row["token_abs"].upper() for row in rows],
        "changed_bytes_vs_stage06": sum(hi - lo for lo, hi in runs),
        "diff_runs_vs_stage06": len(runs),
        "runs": [
            {
                "file_start": f"{lo:08X}",
                "file_end_exclusive": f"{hi:08X}",
                "length": hi - lo,
            }
            for lo, hi in runs
        ],
        "checksum": f"{int(ws_header(rom)['checksum']):04X}",
        "checksum_valid": checksum_valid(rom),
    }


def main() -> int:
    stage06 = STAGE06.read_bytes()
    main_before = MAIN.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    if len(stage06) != ROM_SIZE or sha(stage06) != STAGE06_SHA:
        raise BuildError("stage06 identity drifted")
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    report = json.loads(NESTED_REPORT.read_text(encoding="utf-8"))
    external = get_external_rows(report)
    table_rows = [row for row in external if row["token_abs"].upper() == TABLE_SITE]
    other_rows = [row for row in external if row["token_abs"].upper() != TABLE_SITE]
    if len(table_rows) != 1 or len(other_rows) != 8:
        raise BuildError("unexpected split population")

    specs = [
        {
            "name": "table_5cb5c2_only",
            "rows": table_rows,
            "purpose": "Tests only the clearly structured ascending 16-bit table entry at 5C:B5C2.",
            "bad_means": "5C:B5C2 is the exact ID-command regression write and must never be treated as text.",
        },
        {
            "name": "other_eight_only",
            "rows": other_rows,
            "purpose": "Tests the other eight aux sites while preserving the structured table at 5C:B5C2.",
            "bad_means": "At least one additional aux site is also a non-text structure or hidden ID-command record.",
        },
    ]

    outputs: list[dict[str, Any]] = []
    for order, spec in enumerate(specs, start=1):
        rom = apply_rows(stage06, spec["rows"])
        stem = f"sig_id_p2_0585_{spec['name']}_probe_candidate"
        rom_path = PATCH / f"{stem}.wsc"
        save_path = ROOT / f"sram/{stem}.sav"
        atomic_bytes(rom_path, rom)
        atomic_bytes(save_path, save_before)
        outputs.append(
            {
                "test_order": order,
                "rom": identity(rom_path, rom),
                "save": identity(save_path, save_before),
                "purpose": spec["purpose"],
                "bad_means": spec["bad_means"],
                "details": describe(spec["name"], stage06, rom, spec["rows"]),
            }
        )

    checks = {
        "external_population_exact": len(external) == 9,
        "table_context_is_ascending_u16": bytes.fromhex("BF85D285E885F5850986298636864A86")
        in stage06,
        "split_is_one_plus_eight": len(table_rows) == 1 and len(other_rows) == 8,
        "all_checksums_valid": all(row["details"]["checksum_valid"] for row in outputs),
        "paired_saves_match_live_snapshot": all(
            (PATCH / Path(row["save"]["path"]).name).read_bytes() == save_before
            for row in outputs
        ),
        "main_tip_unchanged": MAIN.read_bytes() == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps(checks, ensure_ascii=False))

    result = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_id_p2_0585_external_table_split_probes.py",
        "ok": True,
        "published": False,
        "status": "slot_0585_external_table_split_probes_ready",
        "runtime_evidence": {
            "nested_detach_only": "good",
            "external_nine_only": "Event Error",
            "remaining_interval": "one or more of nine bank-5C aux writes",
        },
        "static_diagnosis": {
            "site": "5C:B5C2",
            "before": "F585",
            "after": "F573",
            "context_u16_le": [
                "85BF", "85D2", "85E8", "85F5", "8609", "8629", "8636", "864A"
            ],
            "classification": "ascending structured 16-bit table, not zstring",
        },
        "inputs": {
            "stage06_good": identity(STAGE06, stage06),
            "nested_report": identity(NESTED_REPORT),
            "main_tip": identity(MAIN, main_before),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "candidates": outputs,
        "test_protocol": {
            "order": ["table_5cb5c2_only", "other_eight_only"],
            "instruction": "Test both isolated candidates with the same Sig Wedna(Z) ID-command action.",
            "observe": [
                "ID command activation",
                "Event Error occurrence",
                "both decimal error values",
            ],
            "interpretation": [
                "table-only bad and other-eight good: 5C:B5C2 is the sole causal write.",
                "table-only bad and other-eight bad: 5C:B5C2 plus at least one other site are independently unsafe.",
                "table-only good and other-eight bad: another aux site is causal despite the static table misclassification.",
                "both good: failure requires interaction among the external sites; split the other eight pairwise.",
            ],
        },
        "checks": checks,
        "promotion": "blocked_pending_runtime_exact_site_isolation",
    }
    atomic_json(OUT_REPORT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
