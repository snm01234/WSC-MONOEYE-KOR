#!/usr/bin/env python3
"""Split the unsafe P2 slot-0585 record writes into three isolated probes.

Runtime evidence:

* stage06 duplicate batch: good
* isolated 0585 full group: bad
* slot 0585 dictionary pointer/payload only: good
* slot-preserved known writes: bad

Therefore the fault is in one of the known record writes rather than the slot
0585 dictionary reassignment itself.  This builder applies, independently, the
three write classes that made up the slot-preserved probe:

1. three new target dialogue writes only;
2. nine external aux F585 -> F573 retargets only;
3. the single nested dictionary F585 -> F573 retarget at 5F:6DC0 only.

The main TIP and live SaveRAM are never modified.
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
OUT_REPORT = PATCH / "sig_id_p2_0585_write_split_probe_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STAGE06_SHA = "5e4208265d145ccb3706f71f57aa1f3a9d6e592ce23dbe5ebc59050a8b2eeef1"
MAIN_SHA = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"


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


def apply_row(candidate: bytearray, base: int, row: dict[str, Any], *, target: bool) -> None:
    logical = int(row["abs"] if target else row["token_abs"], 16)
    before = bytes.fromhex(row["before_hex"])
    after = bytes.fromhex(row["after_hex"])
    pos = base + logical
    if bytes(candidate[pos : pos + len(before)]) != before:
        raise BuildError(f"parent mismatch at {logical:06X}")
    candidate[pos : pos + len(after)] = after


def build_probe(
    *,
    name: str,
    stage06: bytes,
    target_rows: list[dict[str, Any]],
    external_rows: list[dict[str, Any]],
    nested_rows: list[dict[str, Any]],
) -> tuple[bytes, dict[str, Any]]:
    candidate = bytearray(stage06)
    base = stock_base(candidate)
    selected_targets: list[dict[str, Any]] = []
    selected_detach: list[dict[str, Any]] = []

    if name == "targets_only":
        selected_targets = target_rows
    elif name == "external_detach_only":
        selected_detach = external_rows
    elif name == "nested_detach_only":
        selected_detach = nested_rows
    else:
        raise BuildError(f"unknown probe {name}")

    for row in selected_targets:
        apply_row(candidate, base, row, target=True)
    for row in selected_detach:
        apply_row(candidate, base, row, target=False)

    update_ws_checksum(candidate)
    out = bytes(candidate)
    runs = diff_runs(stage06, out)
    return out, {
        "name": name,
        "target_records": [row["abs"] for row in selected_targets],
        "detachment_sites": [row["token_abs"] for row in selected_detach],
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
        "checksum": f"{int(ws_header(out)['checksum']):04X}",
        "checksum_valid": checksum_valid(out),
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
    target_rows = [
        row
        for row in report["apply_report"]["applied"]
        if row["dictionary_index"].upper() == "0585"
    ]
    detach_rows = [
        row
        for row in report["apply_report"]["detachment_writes"]
        if row["before_hex"].upper() == "F585"
    ]
    external_rows = [row for row in detach_rows if row.get("kind") != "nested_dictionary"]
    nested_rows = [row for row in detach_rows if row.get("kind") == "nested_dictionary"]
    if len(target_rows) != 3 or len(external_rows) != 9 or len(nested_rows) != 1:
        raise BuildError(
            f"unexpected split: targets={len(target_rows)}, external={len(external_rows)}, nested={len(nested_rows)}"
        )
    if nested_rows[0]["token_abs"].upper() != "5F6DC0":
        raise BuildError("unexpected nested token site")

    specs = [
        (
            "targets_only",
            "Tests only the three new dialogue record writes that use the original slot-0585 meaning.",
            "bad means at least one of the three target record rewrites corrupts the ID-command path",
        ),
        (
            "external_detach_only",
            "Tests only the nine aux text-consumer retargets F585 to F573.",
            "bad means one of the nine externally scanned aux sites is not a safe text consumer",
        ),
        (
            "nested_detach_only",
            "Tests only nested dictionary token 5F:6DC0 inside parent slot 0CCA.",
            "bad means the nested 0585 reference must remain exact even though 0585 and 0573 render alike in ordinary text",
        ),
    ]

    outputs: list[dict[str, Any]] = []
    for order, (name, purpose, bad_means) in enumerate(specs, start=1):
        rom, details = build_probe(
            name=name,
            stage06=stage06,
            target_rows=target_rows,
            external_rows=external_rows,
            nested_rows=nested_rows,
        )
        stem = f"sig_id_p2_0585_{name}_probe_candidate"
        rom_path = PATCH / f"{stem}.wsc"
        save_path = ROOT / f"sram/{stem}.sav"
        atomic_bytes(rom_path, rom)
        atomic_bytes(save_path, save_before)
        outputs.append(
            {
                "test_order": order,
                "rom": identity(rom_path, rom),
                "save": identity(save_path, save_before),
                "purpose": purpose,
                "bad_means": bad_means,
                "details": details,
            }
        )

    checks = {
        "write_population_exact": len(target_rows) == 3 and len(external_rows) == 9 and len(nested_rows) == 1,
        "nested_site_exact": nested_rows[0]["token_abs"].upper() == "5F6DC0",
        "all_checksums_valid": all(row["details"]["checksum_valid"] for row in outputs),
        "paired_saves_match_live_snapshot": all(
            (PATCH / Path(row["save"]["path"]).name).read_bytes() == save_before for row in outputs
        ),
        "main_tip_unchanged": MAIN.read_bytes() == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps(checks, ensure_ascii=False))

    result = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_id_p2_0585_write_split_probes.py",
        "ok": True,
        "published": False,
        "status": "slot_0585_known_write_split_probes_ready",
        "runtime_evidence": {
            "dictionary_only": "good",
            "slot_preserved_known_writes": "bad",
            "conclusion": "slot 0585 dictionary reassignment is safe; one of the 13 known record writes is unsafe",
        },
        "inputs": {
            "stage06_good": identity(STAGE06, stage06),
            "nested_report": identity(NESTED_REPORT),
            "main_tip": identity(MAIN, main_before),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "split": {
            "target_record_writes": len(target_rows),
            "external_detachment_writes": len(external_rows),
            "nested_detachment_writes": len(nested_rows),
            "nested_parent_index": nested_rows[0].get("parent_index"),
            "nested_site": nested_rows[0]["token_abs"],
            "nested_before": nested_rows[0]["before_hex"],
            "nested_after": nested_rows[0]["after_hex"],
        },
        "candidates": outputs,
        "test_protocol": {
            "order": ["targets_only", "external_detach_only", "nested_detach_only"],
            "instruction": "Test each isolated candidate with the same Sig Wedna(Z) ID-command action.",
            "observe": [
                "ID command activation",
                "dictionary text auto-advance",
                "Event Error occurrence",
                "both decimal error values",
            ],
            "interpretation": [
                "Only targets_only fails: bisect the three target records.",
                "Only external_detach_only fails: bisect the nine aux sites.",
                "Only nested_detach_only fails: preserve 5F:6DC0 as F585 and forbid nested-token canonicalization for slot 0585.",
                "More than one fails: each failing class contains an independent unsafe write.",
                "All three pass: the failure requires interaction among write classes; build pairwise probes.",
            ],
        },
        "checks": checks,
        "promotion": "blocked_pending_runtime_write_class_isolation",
    }
    atomic_json(OUT_REPORT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
