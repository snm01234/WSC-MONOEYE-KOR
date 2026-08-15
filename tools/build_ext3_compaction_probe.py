#!/usr/bin/env python3
"""Build a read-only probe candidate that compacts selected ext3 banks.

The runtime addresses ext3 phrases only by dictionary index.  Union-proven free
slots can therefore be reset to the shared empty NUL, while live slots retain
byte-identical raw payloads.  Identical live payloads may safely share one
pointer.  This recovers tail room without changing any rendered consumer.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import diff_runs, verify_non_target_invariance
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union
from monoeye_rom import BANK_SIZE, Tbl, patch_expansion_bank, slice_expansion_bank, update_ws_checksum
from patch_3byte_dict_token import EXP3_SEG0, EXP3_SLOTS

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ext3_compaction_probe.wsc"
OUT_REPORT = ROOT / "out/patch/ext3_compaction_probe_report.json"
EXPECTED_MAIN_SHA = "853f42f0f3d0d82fbbe9ee713cc9964e12c6e9884d7d99c1d3dfed65bdbbd68c"
SELECTED_SEGMENTS = (0x19, 0x1C)


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def compact_bank(
    *,
    segment: int,
    dictionary: Any,
    free_indices: set[int],
) -> tuple[bytes, dict[str, Any], dict[int, bytes]]:
    bank_index = segment - EXP3_SEG0
    if bank_index < 0:
        raise BuildError(f"not an ext3 bank: {segment:02X}")
    empty_at = EXP3_SLOTS * 2
    bank = bytearray([0xFF] * BANK_SIZE)
    bank[empty_at] = 0
    for local in range(EXP3_SLOTS):
        struct.pack_into("<H", bank, local * 2, empty_at)

    kept: dict[int, bytes] = {}
    payload_to_offset: dict[bytes, int] = {b"": empty_at}
    cursor = empty_at + 1
    duplicate_slots = 0
    unsafe_nonempty = 0
    for local in range(EXP3_SLOTS):
        index = 0x1000 + (bank_index << 12) + local
        if index in free_indices:
            continue
        raw = bytes(dictionary.raw_entry(index))
        kept[index] = raw
        if not raw:
            continue
        if (index & 0xFF) == 0:
            unsafe_nonempty += 1
        offset = payload_to_offset.get(raw)
        if offset is None:
            need = len(raw) + 1
            if cursor + need > BANK_SIZE:
                raise BuildError(f"compacted bank {segment:02X} overflow at {cursor:04X}")
            offset = cursor
            bank[cursor : cursor + len(raw)] = raw
            bank[cursor + len(raw)] = 0
            payload_to_offset[raw] = offset
            cursor += need
        else:
            duplicate_slots += 1
        struct.pack_into("<H", bank, local * 2, offset)

    return bytes(bank), {
        "segment": f"{segment:02X}",
        "kept_slots": len(kept),
        "unique_payloads": len(payload_to_offset) - 1,
        "duplicate_slots": duplicate_slots,
        "unsafe_low_byte_nonempty": unsafe_nonempty,
        "cursor_after": f"{cursor:04X}",
        "free_room": BANK_SIZE - cursor,
    }, kept


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)

    candidate = bytearray(parent)
    bank_reports: list[dict[str, Any]] = []
    expected_raw: dict[int, bytes] = {}
    for segment in SELECTED_SEGMENTS:
        compacted, report, kept = compact_bank(
            segment=segment,
            dictionary=before_dictionary,
            free_indices=set(inventory.ext3_free),
        )
        patch_expansion_bank(candidate, segment, compacted)
        bank_reports.append(report)
        expected_raw.update(kept)
    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    after_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    raw_failures = []
    for index, before_raw in expected_raw.items():
        after_raw = bytes(after_dictionary.raw_entry(index))
        if after_raw != before_raw:
            raw_failures.append({"index": f"{index:05X}", "before": before_raw.hex().upper(), "after": after_raw.hex().upper()})
            if len(raw_failures) >= 20:
                break

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=before_dictionary,
        after_dictionary=after_dictionary,
        tbl=Tbl.load(TBL_PATH),
        excluded=set(),
    )
    runs = diff_runs(parent, candidate_bytes)
    allowed = [(segment * BANK_SIZE, (segment + 1) * BANK_SIZE) for segment in SELECTED_SEGMENTS]
    allowed.append((len(parent) - 2, len(parent)))
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not any(left >= lo and right <= hi for lo, hi in allowed)
    ]
    other_banks_unchanged = all(
        slice_expansion_bank(parent, segment) == slice_expansion_bank(candidate_bytes, segment)
        for segment in range(EXP3_SEG0, EXP3_SEG0 + int(ext3_meta["num_banks"]))
        if segment not in SELECTED_SEGMENTS
    )
    ok = not raw_failures and invariance.get("ok") is True and not unaccounted and other_banks_unchanged
    if not ok:
        raise BuildError("compaction probe verification failed")
    atomic_bytes(OUT_ROM, candidate_bytes)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ext3_compaction_probe.py",
        "ok": True,
        "published": False,
        "parent": {"path": str(MAIN.resolve()), "size": len(parent), "sha256": sha(parent)},
        "candidate": {"path": str(OUT_ROM.resolve()), "size": len(candidate_bytes), "sha256": sha(candidate_bytes)},
        "selected_segments": [f"{segment:02X}" for segment in SELECTED_SEGMENTS],
        "banks": bank_reports,
        "checks": {
            "all_kept_raw_payloads_exact": not raw_failures,
            "all_record_renderings_invariant": invariance.get("ok") is True,
            "other_ext3_banks_unchanged": other_banks_unchanged,
            "diffs_bounded": not unaccounted,
        },
        "counts": {
            "kept_slots": len(expected_raw),
            "raw_failures": len(raw_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "changed_bytes": sum(right - left for left, right in runs),
            "diff_runs": len(runs),
        },
        "raw_failures": raw_failures,
        "non_target": invariance,
        "unaccounted": unaccounted,
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["candidate"], "banks": bank_reports, "counts": report["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
