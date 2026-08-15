#!/usr/bin/env python3
"""Reconstruct exact historical P2 stage ROMs for the Sig ID-command regression.

User runtime evidence establishes this regression window:

* last good: backup/20260802_183444_pre_p2_fix0208
* first bad: backup/20260802_191414_pre_preopening_ext3

The first-bad ROM is byte-identical to the promoted cumulative P2 fix0208 ROM.
All intermediate candidate ROM files were removed after promotion, but their
candidate SHA-256 values and exact approved change extents remain in the stage
reports.  Starting from the first-bad ROM and restoring later-stage extents from
the last-good ROM reproduces the historical intermediate ROMs byte-exactly.

This builder creates a small reverse-bisection set.  It never modifies the main
TIP or the live main SaveRAM.  Every ROM is paired with a snapshot of the current
main SaveRAM, per project policy.
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

from monoeye_rom import stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
GOOD = PATCH / "backup/20260802_183444_pre_p2_fix0208/monoeye_ko_expanded.wsc"
BAD = PATCH / "backup/20260802_191414_pre_preopening_ext3/monoeye_ko_expanded.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = PATCH / "sig_id_p2_stage_bisect_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
GOOD_SHA = "ec37720a93cadd8cd91bb1ffcb490d4d89b05eb49363a38c05ed6be46d29a9cb"
BAD_SHA = "0c6fd5c71d7ebb1f27204ebd2cff9bf889406fc483b4bd4c5b2e9156e51b8a6b"
MAIN_SHA = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"

STAGES = [
    {
        "id": "01_exact_reuse",
        "report": "p2_exact_reuse_report.json",
        "historical_sha256": "ae31419d80c408d4b67c02d8d946f62581d5467de19433632f037850e5b91966",
        "cumulative_targets": 28,
        "summary": "existing dictionary tokens reused; no dictionary writes",
    },
    {
        "id": "02_true_free",
        "report": "p2_true_free_report.json",
        "historical_sha256": "b6296bd2c001108a2b02ec7c38c2774d1acc4c38e1a680068b34bf8c8a90c569",
        "cumulative_targets": 46,
        "summary": "18 targets using true-free expansion dictionary slots",
    },
    {
        "id": "03_stock_spill",
        "report": "p2_stock_spill_report.json",
        "historical_sha256": "c3664b043a2ea888845c2dffad5a6d3cc507d3e7ff46b9275f7e8cca268c8d83",
        "cumulative_targets": 58,
        "summary": "12 targets using true-free stock bank-5F slots",
    },
    {
        "id": "04_duplicate_detach1",
        "report": "p2_duplicate_detach_report.json",
        "historical_sha256": "b733fb5e3b489b84dbf05b3d5d9b599a9c4197fd8b368816fab10da28345b1e7",
        "cumulative_targets": 62,
        "summary": "first duplicate-payload detachment",
    },
    {
        "id": "05_duplicate_detach2",
        "report": "p2_duplicate_detach2_report.json",
        "historical_sha256": "98e909d6eef48e0fa91d3c1bdb042c1820cf69d9d631e443d3311e143737ffcd",
        "cumulative_targets": 66,
        "summary": "second duplicate-payload detachment",
    },
    {
        "id": "06_duplicate_batch",
        "report": "p2_duplicate_batch_report.json",
        "historical_sha256": "5e4208265d145ccb3706f71f57aa1f3a9d6e592ce23dbe5ebc59050a8b2eeef1",
        "cumulative_targets": 72,
        "summary": "zero-nested duplicate batch, including 02FE to 0313 detachment",
    },
    {
        "id": "07_nested_duplicate",
        "report": "p2_nested_duplicate_batch_report.json",
        "historical_sha256": "6b28ff72a70ce7bb9739f081f55cecfc9612ef5d207701e24093f947f7fed7d9",
        "cumulative_targets": 78,
        "summary": "nested duplicate batch",
    },
    {
        "id": "08_local_ext3",
        "report": "p2_local_ext3_expansion_report.json",
        "historical_sha256": "9cc8727e1582c028353d936126a22cccf2511328c1def4fe06bc119fde6e620f",
        "cumulative_targets": 105,
        "summary": "27 one-NUL-gap local ext3 records",
    },
    {
        "id": "09_retired_slots",
        "report": "p2_retired_slot_reclaim_report.json",
        "historical_sha256": "38d8b150971de7a097cb755fd50073e02b02e4ada04840b61572e5e08d39771c",
        "cumulative_targets": 205,
        "summary": "83 retired stock slots reused for the final 100 targets",
    },
    {
        "id": "10_fix0208",
        "report": "p2_retired_slot_reclaim_fix0208_report.json",
        "historical_sha256": BAD_SHA,
        "cumulative_targets": 205,
        "summary": "slot 0208 stage-name repair and promoted first-bad ROM",
    },
]

# Test only the most informative historical cuts.  Each file is byte-identical
# to the historical candidate named in its output metadata.
OUTPUT_CUTS = [
    "01_exact_reuse",
    "02_true_free",
    "03_stock_spill",
    "06_duplicate_batch",
    "08_local_ext3_fix0208",
]

LOCAL_FIX_SHA = "9ff3a791a89ba5447826d2ea0a060a7c3d699df2bad8daaf4fad54fa4f50b33d"


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_extents(report_name: str, stock_file_base: int) -> list[tuple[int, int, str, str]]:
    report_path = PATCH / report_name
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows: list[tuple[int, int, str, str]] = []
    for extent in report.get("approved_change_extents", []):
        if "file_start" in extent:
            lo = int(extent["file_start"], 16)
            hi = int(extent["file_end_exclusive"], 16)
        else:
            lo = stock_file_base + int(extent["start"], 16)
            hi = stock_file_base + int(extent["end_exclusive"], 16)
        # Every historical stage report includes the checksum as an allowed
        # extent.  We always recompute it after reconstruction instead.
        if lo >= ROM_SIZE - 2:
            continue
        rows.append((lo, hi, str(extent.get("kind", "unknown")), str(extent.get("owner_id", ""))))
    return rows


def reconstruct_cut(
    *,
    good: bytes,
    bad: bytes,
    extents_by_stage: list[list[tuple[int, int, str, str]]],
    keep_stage_index: int,
) -> bytes:
    candidate = bytearray(bad)
    # Roll back every stage after the requested historical cut.
    for stage_index in range(keep_stage_index + 1, len(STAGES)):
        for lo, hi, _kind, _owner in extents_by_stage[stage_index]:
            candidate[lo:hi] = good[lo:hi]
    update_ws_checksum(candidate)
    return bytes(candidate)


def changed_bytes(left: bytes, right: bytes) -> int:
    return sum(a != b for a, b in zip(left, right))


def diff_runs(left: bytes, right: bytes) -> int:
    count = 0
    active = False
    for a, b in zip(left, right):
        if a != b and not active:
            count += 1
            active = True
        elif a == b:
            active = False
    return count


def publish_candidate(name: str, rom: bytes, save: bytes) -> tuple[Path, Path]:
    rom_path = PATCH / f"sig_id_p2_stage_{name}_candidate.wsc"
    save_path = ROOT / f"sram/sig_id_p2_stage_{name}_candidate.sav"
    atomic_bytes(rom_path, rom)
    atomic_bytes(save_path, save)
    return rom_path, save_path


def main() -> int:
    good = GOOD.read_bytes()
    bad = BAD.read_bytes()
    main_before = MAIN.read_bytes()
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(good) != ROM_SIZE or sha(good) != GOOD_SHA:
        raise BuildError("last-good backup identity drifted")
    if len(bad) != ROM_SIZE or sha(bad) != BAD_SHA:
        raise BuildError("first-bad backup identity drifted")
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("current main TIP identity drifted")
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("current main SaveRAM missing or wrong size")
    if stock_base(good) != stock_base(bad):
        raise BuildError("backup stock bases differ")

    file_base = stock_base(bad)
    extents_by_stage = [load_extents(stage["report"], file_base) for stage in STAGES]
    outputs: list[dict[str, Any]] = []

    # Four exact historical cuts reconstructed by rolling back all later stages.
    for cut in OUTPUT_CUTS[:-1]:
        index = next(i for i, stage in enumerate(STAGES) if stage["id"] == cut)
        candidate = reconstruct_cut(
            good=good,
            bad=bad,
            extents_by_stage=extents_by_stage,
            keep_stage_index=index,
        )
        expected = STAGES[index]["historical_sha256"]
        if sha(candidate) != expected:
            raise BuildError(f"historical reconstruction mismatch for {cut}")
        rom_path, save_path = publish_candidate(cut, candidate, save_snapshot)
        outputs.append(
            {
                "test_order": len(outputs) + 1,
                "cut": cut,
                "historical_stage": STAGES[index],
                "rom": identity(rom_path, candidate),
                "save": identity(save_path, save_snapshot),
                "exact_historical_sha_match": True,
                "changed_bytes_vs_last_good": changed_bytes(good, candidate),
                "diff_runs_vs_last_good": diff_runs(good, candidate),
                "checksum": f"{int(ws_header(candidate)['checksum']):04X}",
            }
        )

    # The most recent useful cut is the documented local-ext3 candidate after
    # applying the independent slot0208 repair.  It is reconstructed exactly by
    # rolling back only the later retired-slot stage from first-bad.
    retired_index = next(i for i, stage in enumerate(STAGES) if stage["id"] == "09_retired_slots")
    local_fix = bytearray(bad)
    for lo, hi, _kind, _owner in extents_by_stage[retired_index]:
        local_fix[lo:hi] = good[lo:hi]
    update_ws_checksum(local_fix)
    local_fix_bytes = bytes(local_fix)
    if sha(local_fix_bytes) != LOCAL_FIX_SHA:
        raise BuildError("local_ext3_fix0208 reconstruction mismatch")
    rom_path, save_path = publish_candidate("08_local_ext3_fix0208", local_fix_bytes, save_snapshot)
    outputs.append(
        {
            "test_order": 1,
            "cut": "08_local_ext3_fix0208",
            "historical_stage": {
                "id": "08_local_ext3_fix0208",
                "historical_sha256": LOCAL_FIX_SHA,
                "cumulative_targets": 105,
                "summary": "local ext3 cumulative candidate with slot0208 repair, before retired-slot 100-record stage",
            },
            "rom": identity(rom_path, local_fix_bytes),
            "save": identity(save_path, save_snapshot),
            "exact_historical_sha_match": True,
            "changed_bytes_vs_last_good": changed_bytes(good, local_fix_bytes),
            "diff_runs_vs_last_good": diff_runs(good, local_fix_bytes),
            "checksum": f"{int(ws_header(local_fix_bytes)['checksum']):04X}",
        }
    )

    # Reorder into the recommended reverse-bisection sequence.
    recommended = [
        "08_local_ext3_fix0208",
        "06_duplicate_batch",
        "03_stock_spill",
        "02_true_free",
        "01_exact_reuse",
    ]
    order = {name: i + 1 for i, name in enumerate(recommended)}
    outputs.sort(key=lambda row: order[row["cut"]])
    for row in outputs:
        row["test_order"] = order[row["cut"]]

    checks = {
        "last_good_and_first_bad_differ": good != bad,
        "first_bad_is_documented_promoted_p2_tip": sha(bad) == STAGES[-1]["historical_sha256"],
        "all_outputs_historical_exact": all(row["exact_historical_sha_match"] for row in outputs),
        "all_output_checksums_valid": all(
            int(ws_header((PATCH / Path(row["rom"]["path"]).name).read_bytes())["checksum"])
            == (sum((PATCH / Path(row["rom"]["path"]).name).read_bytes()[:-2]) & 0xFFFF)
            for row in outputs
        ),
        "main_tip_unchanged": MAIN.read_bytes() == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_snapshot,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps(checks, ensure_ascii=False))

    result = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_id_p2_stage_bisect_candidates.py",
        "ok": True,
        "published": False,
        "status": "historical_stage_candidates_ready_for_runtime_bisection",
        "regression_window": {
            "last_good": identity(GOOD, good),
            "first_bad": identity(BAD, bad),
            "first_bad_matches_promoted_p2_fix0208": True,
        },
        "main": {
            "tip": identity(MAIN, main_before),
            "save": identity(MAIN_SAVE, save_snapshot),
            "modified": False,
        },
        "reconstruction": {
            "method": "start from first-bad and restore later approved stage extents from last-good, then recompute checksum",
            "stage_reports": [
                {
                    "id": stage["id"],
                    "report": f"out/patch/{stage['report']}",
                    "historical_sha256": stage["historical_sha256"],
                    "approved_extents": len(extents_by_stage[index]),
                }
                for index, stage in enumerate(STAGES)
            ],
            "proof": "every emitted ROM SHA-256 exactly matches its archived historical candidate identity",
        },
        "candidates": outputs,
        "test_protocol": {
            "order": recommended,
            "instruction": "Test in order and stop at the first ROM where the result changes relative to the previous ROM.",
            "observe": [
                "ID command activation",
                "unrelated dictionary text auto-advance",
                "Event Error occurrence",
                "both decimal error values",
            ],
            "interpretation": [
                "08 works: the final retired-slot 100-record/83-slot stage caused the regression",
                "08 fails and 06 works: nested duplicate or local-ext3 stage caused it",
                "06 fails and 03 works: one of the duplicate-detachment stages caused it",
                "03 fails and 02 works: stock bank-5F true-free spill stage caused it",
                "02 fails and 01 works: expansion-bank true-free dictionary stage caused it",
                "01 fails: exact-reuse record writes caused it; dictionary storage changes are not required",
            ],
        },
        "checks": checks,
        "promotion": "blocked_pending_runtime_bisection",
    }
    atomic_json(REPORT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
