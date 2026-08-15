#!/usr/bin/env python3
"""Build a minimal Sig ID-command regression probe.

The first user-confirmed bad TIP is the P2 fix0208 promotion result.  That
promotion inherited two P2 duplicate-detachment writes in bank 5D which later
smoke reports classify as UNINTENDED battle/UI-bank changes:

    5D:5364  F2 FE -> F3 13
    5D:AB58  F2 FE -> F3 13

Both sites were treated as dictionary-token consumers of slot 02FE, but their
surrounding bytes form a repeated structured table rather than ordinary battle
dialogue.  This probe restores only those two bytes to the last user-confirmed
good TIP values while preserving every later translation/runtime change.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum  # noqa: E402

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
GOOD_REF = (
    ROOT
    / "out/patch/backup/20260802_183444_pre_p2_fix0208/monoeye_ko_expanded.wsc"
)
BAD_REF = (
    ROOT
    / "out/patch/backup/20260802_191414_pre_preopening_ext3/monoeye_ko_expanded.wsc"
)
OUT_ROM = ROOT / "out/patch/sig_id_p2_aux_detachment_restore_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_id_p2_aux_detachment_restore_candidate.sav"
REPORT = ROOT / "out/patch/sig_id_p2_aux_detachment_restore_report.json"

EXPECTED_PARENT_SHA256 = (
    "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"
)
EXPECTED_GOOD_SHA256 = (
    "ec37720a93cadd8cd91bb1ffcb490d4d89b05eb49363a38c05ed6be46d29a9cb"
)
EXPECTED_BAD_SHA256 = (
    "0c6fd5c71d7ebb1f27204ebd2cff9bf889406fc483b4bd4c5b2e9156e51b8a6b"
)

SITES = (
    {
        "logical": 0x5D5364,
        "good": bytes.fromhex("F2FE"),
        "bad": bytes.fromhex("F313"),
        "approval_owner": "detach:02FE->0313",
        "approval_record": "5D5362",
    },
    {
        "logical": 0x5DAB58,
        "good": bytes.fromhex("F2FE"),
        "bad": bytes.fromhex("F313"),
        "approval_owner": "detach:02FE->0313",
        "approval_record": "5DAB56",
    },
)


def sha256_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | bytearray | None = None) -> dict:
    if data is None:
        data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256_bytes(data),
    }


def diff_runs(a: bytes | bytearray, b: bytes | bytearray) -> list[dict]:
    if len(a) != len(b):
        raise RuntimeError("ROM sizes differ")
    indices = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if not indices:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = indices[0]
    for idx in indices[1:]:
        if idx != prev + 1:
            ranges.append((start, prev + 1))
            start = idx
        prev = idx
    ranges.append((start, prev + 1))
    return [
        {
            "file_start": f"{start:08X}",
            "file_end_exclusive": f"{end:08X}",
            "length": end - start,
            "before_hex": bytes(a[start:end]).hex().upper(),
            "after_hex": bytes(b[start:end]).hex().upper(),
        }
        for start, end in ranges
    ]


def require_hash(path: Path, expected: str) -> bytes:
    data = path.read_bytes()
    actual = sha256_bytes(data)
    if actual != expected:
        raise RuntimeError(
            f"Unexpected SHA-256 for {path}: expected {expected}, got {actual}"
        )
    return data


def main() -> int:
    parent_raw = require_hash(PARENT, EXPECTED_PARENT_SHA256)
    good_raw = require_hash(GOOD_REF, EXPECTED_GOOD_SHA256)
    bad_raw = require_hash(BAD_REF, EXPECTED_BAD_SHA256)
    parent = bytearray(load_rom(PARENT))
    good = bytes(load_rom(GOOD_REF))
    bad = bytes(load_rom(BAD_REF))

    if not (len(parent) == len(good) == len(bad) == 0x1000000):
        raise RuntimeError("All ROMs must be prepended 16 MiB images")

    base = stock_base(parent)
    changed_sites: list[dict] = []
    for spec in SITES:
        logical = int(spec["logical"])
        physical = base + logical
        before = bytes(parent[physical : physical + 2])
        good_value = bytes(good[physical : physical + 2])
        bad_value = bytes(bad[physical : physical + 2])
        if good_value != spec["good"]:
            raise RuntimeError(
                f"Good reference mismatch at {logical:06X}: {good_value.hex()}"
            )
        if bad_value != spec["bad"]:
            raise RuntimeError(
                f"Bad reference mismatch at {logical:06X}: {bad_value.hex()}"
            )
        if before != spec["bad"]:
            raise RuntimeError(
                f"Current parent mismatch at {logical:06X}: {before.hex()}"
            )
        parent[physical : physical + 2] = spec["good"]
        changed_sites.append(
            {
                "logical": f"{logical:06X}",
                "site": f"{logical >> 16:02X}:{logical & 0xFFFF:04X}",
                "file_offset": f"{physical:08X}",
                "before_hex": before.hex().upper(),
                "after_hex": spec["good"].hex().upper(),
                "good_reference_exact": True,
                "bad_reference_exact": True,
                "approval_owner": spec["approval_owner"],
                "approval_record": spec["approval_record"],
                "later_smoke_classification": "UNINTENDED",
                "later_smoke_category": "battle_ui_bank",
            }
        )

    checksum = update_ws_checksum(parent)
    candidate = bytes(parent)
    runs = diff_runs(parent_raw, candidate)

    allowed_offsets = {
        base + int(spec["logical"]) + delta
        for spec in SITES
        for delta in range(2)
    }
    allowed_offsets.update({len(candidate) - 2, len(candidate) - 1})
    changed_offsets = {
        i for i, (x, y) in enumerate(zip(parent_raw, candidate)) if x != y
    }
    unexpected = sorted(changed_offsets - allowed_offsets)
    missing_site_diffs = sorted(
        (base + int(spec["logical"]) + delta)
        for spec in SITES
        for delta in range(2)
        if parent_raw[base + int(spec["logical"]) + delta]
        == candidate[base + int(spec["logical"]) + delta]
    )
    if unexpected or missing_site_diffs:
        raise RuntimeError(
            f"Diff audit failed: unexpected={unexpected}, missing={missing_site_diffs}"
        )

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(candidate)
    if not MAIN_SAVE.exists():
        raise RuntimeError(f"Missing current main SaveRAM: {MAIN_SAVE}")
    shutil.copy2(MAIN_SAVE, OUT_SAVE)

    report = {
        "ok": True,
        "generated_by": "tools/build_sig_id_p2_aux_detachment_restore_candidate.py",
        "purpose": (
            "Test whether two P2 duplicate-detachment writes in structured bank-5D "
            "data caused the Sig ID-command event regression"
        ),
        "regression_window": {
            "last_good": identity(GOOD_REF, good_raw),
            "first_bad": identity(BAD_REF, bad_raw),
            "first_bad_matches_p2_fix0208_promoted_tip": True,
            "intervening_promoted_work": "p2_retired_slot_reclaim_candidate_fix0208",
            "specific_inherited_stage": "P2 zero-nested duplicate batch 02FE->0313 detachment",
        },
        "inputs": {
            "parent_tip": identity(PARENT, parent_raw),
            "main_save": identity(MAIN_SAVE),
        },
        "changes": changed_sites,
        "audit": {
            "changed_bytes_including_checksum": len(changed_offsets),
            "diff_runs": runs,
            "unexpected_changed_offsets": unexpected,
            "missing_expected_site_diffs": missing_site_diffs,
            "only_two_data_sites_plus_checksum": True,
            "candidate_checksum": f"{checksum:04X}",
            "stored_checksum": f"{int.from_bytes(candidate[-2:], 'little'):04X}",
            "checksum_valid": checksum == int.from_bytes(candidate[-2:], "little"),
            "good_reference_values_restored": all(
                candidate[base + int(spec["logical"]) : base + int(spec["logical"]) + 2]
                == spec["good"]
                for spec in SITES
            ),
            "all_other_parent_bytes_preserved": not unexpected,
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, candidate),
            "candidate_save": identity(OUT_SAVE),
        },
        "runtime_test": {
            "character": "Sig Wedna (Z)",
            "action": "Use the same spirit/ID command that reproduced the regression",
            "observe": [
                "whether the ID command activates",
                "whether unrelated dictionary text scrolls",
                "whether Event Error appears",
                "both numeric error values if it appears",
            ],
            "promotion_blocked": True,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
