#!/usr/bin/env python3
"""Split the failing P2 nested slot-0585 group into decisive component probes.

Runtime evidence:

* stage06 duplicate batch: good
* stage07 nested duplicate: Event Error 12288 / 29688
* isolated slot-0208 group: good
* isolated slot-0585 group: bad

The slot-0585 group consists of three independent write classes:

1. retarget ten known F585 consumers to keeper token F573,
2. repoint stock dictionary slot 0585 to a new ``윽！！`` payload,
3. write F585 into three newly translated target records.

The historical reference scan covered only ten F585 consumers, while a raw ROM
scan finds twenty-four F585 byte pairs in stage06.  Fourteen remain outside the
approved text scopes.  These probes determine whether repointing the dictionary
slot breaks one of those hidden consumers, or whether the known consumer/target
writes themselves are responsible.

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
STAGE07 = PATCH / "sig_id_p2_stage_07_nested_duplicate_candidate.wsc"
NESTED_REPORT = PATCH / "p2_nested_duplicate_batch_report.json"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_REPORT = PATCH / "sig_id_p2_0585_component_probe_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STAGE06_SHA = "5e4208265d145ccb3706f71f57aa1f3a9d6e592ce23dbe5ebc59050a8b2eeef1"
STAGE07_SHA = "6b28ff72a70ce7bb9739f081f55cecfc9612ef5d207701e24093f947f7fed7d9"
MAIN_SHA = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"

SLOT = 0x0585
OLD_TOKEN = "F585"
KEEPER_TOKEN = "F573"
POINTER_TABLE = 0x5F7BCC
NEW_POINTER = 0xE34C
PHRASE_LO = 0x5FE34C
PHRASE_HI = 0x5FE353


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


def write_rows(
    candidate: bytearray,
    *,
    base: int,
    rows: list[dict[str, Any]],
    address_key: str,
) -> None:
    for row in rows:
        logical = int(row[address_key], 16)
        pos = base + logical
        before = bytes.fromhex(row["before_hex"])
        after = bytes.fromhex(row["after_hex"])
        if bytes(candidate[pos : pos + len(before)]) != before:
            raise BuildError(f"parent mismatch at {row[address_key]}")
        candidate[pos : pos + len(after)] = after


def finalize(candidate: bytearray) -> bytes:
    update_ws_checksum(candidate)
    out = bytes(candidate)
    if not checksum_valid(out):
        raise BuildError("checksum invalid")
    return out


def describe_delta(parent: bytes, candidate: bytes) -> dict[str, Any]:
    runs = diff_runs(parent, candidate)
    return {
        "changed_bytes": sum(hi - lo for lo, hi in runs),
        "diff_runs": len(runs),
        "runs": [
            {
                "file_start": f"{lo:08X}",
                "file_end_exclusive": f"{hi:08X}",
                "length": hi - lo,
            }
            for lo, hi in runs
        ],
        "checksum": f"{int(ws_header(candidate)['checksum']):04X}",
        "checksum_valid": checksum_valid(candidate),
    }


def raw_token_hits(rom: bytes, base: int, token: bytes) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    pos = 0
    while True:
        pos = rom.find(token, pos)
        if pos < 0:
            break
        hits.append(
            {
                "file_offset": f"{pos:08X}",
                "logical": f"{pos - base:06X}" if pos >= base else None,
                "expansion": pos < base,
                "context_hex": rom[max(0, pos - 12) : min(len(rom), pos + 14)].hex().upper(),
            }
        )
        pos += 1
    return hits


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

    report = json.loads(NESTED_REPORT.read_text(encoding="utf-8"))
    detachment_rows = [
        row
        for row in report["apply_report"]["detachment_writes"]
        if row["before_hex"].upper() == OLD_TOKEN
    ]
    target_rows = [
        row
        for row in report["apply_report"]["applied"]
        if row["dictionary_index"].upper() == f"{SLOT:04X}"
    ]
    if len(detachment_rows) != 10 or len(target_rows) != 3:
        raise BuildError("unexpected slot-0585 report population")

    base = stock_base(stage06)
    pointer_pos = base + POINTER_TABLE + SLOT * 2
    old_pointer = int.from_bytes(stage06[pointer_pos : pointer_pos + 2], "little")
    if int.from_bytes(stage07[pointer_pos : pointer_pos + 2], "little") != NEW_POINTER:
        raise BuildError("stage07 slot-0585 pointer mismatch")

    all_hits = raw_token_hits(stage06, base, bytes.fromhex(OLD_TOKEN))
    known_file_offsets = {base + int(row["token_abs"], 16) for row in detachment_rows}
    hidden_hits = [row for row in all_hits if int(row["file_offset"], 16) not in known_file_offsets]

    # Probe A: only repoint dictionary slot 0585 and install its new payload.
    dictionary_only = bytearray(stage06)
    dictionary_only[pointer_pos : pointer_pos + 2] = NEW_POINTER.to_bytes(2, "little")
    phrase_lo = base + PHRASE_LO
    phrase_hi = base + PHRASE_HI
    dictionary_only[phrase_lo:phrase_hi] = stage07[phrase_lo:phrase_hi]
    dictionary_only_bytes = finalize(dictionary_only)

    # Probe B: perform every known consumer retarget and every new target write,
    # but preserve the original slot-0585 pointer/payload.  The three new target
    # records will display the old slot text; only event-flow behavior matters.
    slot_preserved = bytearray(stage06)
    write_rows(slot_preserved, base=base, rows=detachment_rows, address_key="token_abs")
    write_rows(slot_preserved, base=base, rows=target_rows, address_key="abs")
    if int.from_bytes(slot_preserved[pointer_pos : pointer_pos + 2], "little") != old_pointer:
        raise BuildError("slot-preserved probe changed slot-0585 pointer")
    slot_preserved_bytes = finalize(slot_preserved)

    outputs: list[dict[str, Any]] = []
    specs = [
        (
            "dictionary_only",
            dictionary_only_bytes,
            {
                "contains": ["slot 0585 pointer E34C", "new 윽！！ payload"],
                "omits": ["ten known consumer retargets", "three target record writes"],
                "bad_means": "an untracked/hidden slot-0585 consumer is reached by the ID-command path",
            },
        ),
        (
            "slot_preserved",
            slot_preserved_bytes,
            {
                "contains": ["ten known F585-to-F573 consumer retargets", "three F585 target record writes"],
                "omits": ["slot 0585 pointer reassignment", "new 윽！！ payload"],
                "bad_means": "one of the known detachment or target writes corrupts control data",
            },
        ),
    ]
    for order, (name, rom, purpose) in enumerate(specs, start=1):
        stem = f"sig_id_p2_0585_{name}_probe_candidate"
        rom_path = PATCH / f"{stem}.wsc"
        save_path = ROOT / f"sram/{stem}.sav"
        atomic_bytes(rom_path, rom)
        atomic_bytes(save_path, save_before)
        outputs.append(
            {
                "test_order": order,
                "name": name,
                "rom": identity(rom_path, rom),
                "save": identity(save_path, save_before),
                "purpose": purpose,
                "delta_vs_stage06": describe_delta(stage06, rom),
            }
        )

    checks = {
        "raw_f585_hits_total": len(all_hits) == 24,
        "approved_known_consumers": len(detachment_rows) == 10,
        "untracked_f585_hits": len(hidden_hits) == 14,
        "dictionary_only_pointer_changed": int.from_bytes(dictionary_only_bytes[pointer_pos:pointer_pos+2], "little") == NEW_POINTER,
        "slot_preserved_pointer_original": int.from_bytes(slot_preserved_bytes[pointer_pos:pointer_pos+2], "little") == old_pointer,
        "both_checksums_valid": all(row["delta_vs_stage06"]["checksum_valid"] for row in outputs),
        "main_tip_unchanged": MAIN.read_bytes() == main_before,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == save_before,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps(checks, ensure_ascii=False))

    result = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_id_p2_0585_component_probes.py",
        "ok": True,
        "published": False,
        "status": "slot_0585_component_probes_ready",
        "runtime_evidence": {
            "stage06": "good",
            "stage07": "Event Error 12288 / 29688",
            "isolated_0208": "good",
            "isolated_0585": "bad",
            "first_bad_group": "0585",
        },
        "slot_0585": {
            "original_pointer": f"{old_pointer:04X}",
            "new_pointer": f"{NEW_POINTER:04X}",
            "known_text_consumers": len(detachment_rows),
            "raw_f585_hits_total": len(all_hits),
            "untracked_raw_hits": hidden_hits,
            "inference": "the historical text reference union covered only 10 of 24 raw F585 occurrences",
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
            "order": ["dictionary_only", "slot_preserved"],
            "observe": [
                "ID command activation",
                "dictionary text auto-advance",
                "Event Error occurrence",
                "both decimal error values",
            ],
            "interpretation": [
                "dictionary_only bad and slot_preserved good: slot 0585 reassignment is the cause; preserve 0585 and relocate 윽！！ elsewhere",
                "dictionary_only good and slot_preserved bad: one of the ten detachment or three target writes is the cause",
                "both bad: slot reassignment and at least one record write are independently unsafe",
                "both good: the components interact only when combined; build pairwise probes",
            ],
        },
        "checks": checks,
        "promotion": "blocked_pending_runtime_component_isolation",
    }
    atomic_json(OUT_REPORT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
