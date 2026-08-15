#!/usr/bin/env python3
"""Build isolated probes for the two P2 nested-duplicate reclaim groups.

Runtime bisection proved:

* stage06 duplicate batch: Sig Wedna(Z) ID command works
* stage07 nested duplicate: Event Error 12288 / 29688

Stage07 contains only two independent nested-duplicate reclaim groups:

* slot 0208 detached to keeper 0564, then reused for three "오오！" records
* slot 0585 detached to keeper 0573, then reused for three "윽！！" records

This builder applies each group independently to the exact stage06-good ROM.
The probes identify which slot reclaim first breaks the ID-command path.  The
main TIP and live SaveRAM are never modified.
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
STAGE07 = PATCH / "sig_id_p2_stage_07_nested_duplicate_candidate.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
NESTED_REPORT = PATCH / "p2_nested_duplicate_batch_report.json"
OUT_REPORT = PATCH / "sig_id_p2_nested_group_probe_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STAGE06_SHA = "5e4208265d145ccb3706f71f57aa1f3a9d6e592ce23dbe5ebc59050a8b2eeef1"
STAGE07_SHA = "6b28ff72a70ce7bb9739f081f55cecfc9612ef5d207701e24093f947f7fed7d9"
MAIN_SHA = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"

GROUPS = {
    "0208": {
        "reclaim_slot": 0x0208,
        "old_token": "F208",
        "keeper_slot": "0564",
        "new_pointer": 0xE344,
        "phrase_lo": 0x5FE344,
        "phrase_hi": 0x5FE34C,
        "target_text": "오오！",
    },
    "0585": {
        "reclaim_slot": 0x0585,
        "old_token": "F585",
        "keeper_slot": "0573",
        "new_pointer": 0xE34C,
        "phrase_lo": 0x5FE34C,
        "phrase_hi": 0x5FE353,
        "target_text": "윽！！",
    },
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


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def build_group(
    *,
    group_id: str,
    stage06: bytes,
    stage07: bytes,
    report: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    spec = GROUPS[group_id]
    candidate = bytearray(stage06)
    base = stock_base(candidate)
    old_token = spec["old_token"]

    detachment_rows = [
        row
        for row in report["apply_report"]["detachment_writes"]
        if row["before_hex"].upper() == old_token
    ]
    target_rows = [
        row
        for row in report["apply_report"]["applied"]
        if row["dictionary_index"].upper() == group_id
    ]
    if not detachment_rows or len(target_rows) != 3:
        raise BuildError(f"unexpected report population for group {group_id}")

    for row in detachment_rows:
        pos = base + int(row["token_abs"], 16)
        before = bytes.fromhex(row["before_hex"])
        after = bytes.fromhex(row["after_hex"])
        if bytes(candidate[pos : pos + len(before)]) != before:
            raise BuildError(f"detachment parent mismatch at {row['token_abs']}")
        candidate[pos : pos + len(after)] = after

    for row in target_rows:
        pos = base + int(row["abs"], 16)
        before = bytes.fromhex(row["before_hex"])
        after = bytes.fromhex(row["after_hex"])
        if bytes(candidate[pos : pos + len(before)]) != before:
            raise BuildError(f"target parent mismatch at {row['abs']}")
        candidate[pos : pos + len(after)] = after

    pointer_pos = base + 0x5F7BCC + int(spec["reclaim_slot"]) * 2
    candidate[pointer_pos : pointer_pos + 2] = int(spec["new_pointer"]).to_bytes(2, "little")

    phrase_lo = base + int(spec["phrase_lo"])
    phrase_hi = base + int(spec["phrase_hi"])
    candidate[phrase_lo:phrase_hi] = stage07[phrase_lo:phrase_hi]

    update_ws_checksum(candidate)
    out = bytes(candidate)
    runs = diff_runs(stage06, out)
    details = {
        "group": group_id,
        "reclaim_slot": f"{spec['reclaim_slot']:04X}",
        "keeper_slot": spec["keeper_slot"],
        "target_text": spec["target_text"],
        "detachment_writes": len(detachment_rows),
        "detachment_sites": [row["token_abs"] for row in detachment_rows],
        "target_records": [row["abs"] for row in target_rows],
        "pointer_value": f"{spec['new_pointer']:04X}",
        "phrase_range": [f"{spec['phrase_lo']:06X}", f"{spec['phrase_hi']:06X}"],
        "changed_bytes_vs_stage06": sum(hi - lo for lo, hi in runs),
        "diff_runs_vs_stage06": len(runs),
        "checksum": f"{int(ws_header(out)['checksum']):04X}",
        "checksum_valid": checksum_valid(out),
    }
    return out, details


def main() -> int:
    stage06 = STAGE06.read_bytes()
    stage07 = STAGE07.read_bytes()
    main_before = MAIN.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    if len(stage06) != ROM_SIZE or sha(stage06) != STAGE06_SHA:
        raise BuildError("stage06 identity drifted")
    if len(stage07) != ROM_SIZE or sha(stage07) != STAGE07_SHA:
        raise BuildError("stage07 identity drifted")
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    nested = json.loads(NESTED_REPORT.read_text(encoding="utf-8"))
    outputs: list[dict[str, Any]] = []
    for order, group_id in enumerate(("0208", "0585"), start=1):
        rom, details = build_group(
            group_id=group_id,
            stage06=stage06,
            stage07=stage07,
            report=nested,
        )
        stem = f"sig_id_p2_nested_group_{group_id}_probe_candidate"
        rom_path = PATCH / f"{stem}.wsc"
        save_path = ROOT / f"sram/{stem}.sav"
        atomic_bytes(rom_path, rom)
        atomic_bytes(save_path, save_before)
        outputs.append(
            {
                "test_order": order,
                "rom": identity(rom_path, rom),
                "save": identity(save_path, save_before),
                "details": details,
            }
        )

    checks = {
        "both_probes_created": len(outputs) == 2,
        "both_checksums_valid": all(row["details"]["checksum_valid"] for row in outputs),
        "paired_saves_match_live_snapshot": all((PATCH / Path(row["save"]["path"]).name).read_bytes() == save_before for row in outputs),
        "main_tip_unchanged": MAIN.read_bytes() == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps(checks, ensure_ascii=False))

    result = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_id_p2_nested_group_probe_candidates.py",
        "ok": True,
        "published": False,
        "status": "nested_group_runtime_probes_ready",
        "runtime_evidence": {
            "stage06_duplicate_batch": "good",
            "stage07_nested_duplicate": "Event Error 12288 / 29688",
            "first_bad_stage": "nested duplicate batch",
        },
        "inputs": {
            "stage06_good": identity(STAGE06, stage06),
            "stage07_bad": identity(STAGE07, stage07),
            "nested_report": identity(NESTED_REPORT),
            "main_tip": identity(MAIN, main_before),
            "main_save": identity(MAIN_SAVE, save_before),
        },
        "candidates": outputs,
        "test_protocol": {
            "order": ["0208", "0585"],
            "instruction": "Test both isolated groups with the same Sig Wedna(Z) ID-command action.",
            "observe": [
                "ID command activation",
                "dictionary text auto-advance",
                "Event Error occurrence",
                "both decimal error values",
            ],
            "interpretation": [
                "Only 0208 fails: reclaiming stock slot 0208 or one of its hidden/nested consumers causes the regression.",
                "Only 0585 fails: reclaiming stock slot 0585 or one of its hidden/nested consumers causes the regression.",
                "Both fail: both reclaimed slots have hidden ID-command consumers or the shared nested-reclaim method is unsafe.",
                "Neither fails: the two groups interact; build a combined write-category split next.",
            ],
        },
        "checks": checks,
        "promotion": "blocked_pending_runtime_group_isolation",
    }
    atomic_json(OUT_REPORT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
