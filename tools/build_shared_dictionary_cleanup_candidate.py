#!/usr/bin/env python3
"""Build a candidate that completes reviewed shared dictionary translations.

The parent TIP contains live stock dictionary phrases such as ``전투不能`` and
``未확인``.  The audit selected only slots with a unique reviewed Korean target
and a fully enumerated Original+Working consumer union.  This builder retargets
those exact stock pointers to new Korean payloads in the verified bank-5F tail.
All records consuming a selected shared term change together; record bytes,
runtime code, ext3 banks, and the main SaveRAM remain untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import nested_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_p2_exact_reuse_candidate import diff_runs
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from expand_dictionary import write_dictionary_slots_spill
from hangul_marker import marker_code
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import build_reference_union, guard_slot_writes
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    read_encoded_z_safe,
    slice_bank,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import try_encode_ko_text

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
AUDIT = ROOT / "out/patch/shared_dictionary_japanese_residual_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/shared_dictionary_cleanup_candidate.wsc"
OUT_SAVE = ROOT / "sram/shared_dictionary_cleanup_candidate.sav"
REPORT = ROOT / "out/patch/shared_dictionary_cleanup_report.json"

EXPECTED_PARENT_SHA256 = "279ce819fa63bea6ee52a307dc920714bb530c2f32e344e2543134dfbccbf7f9"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256_bytes(payload)}


def write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def covered(run: tuple[int, int], extents: Iterable[tuple[int, int]]) -> bool:
    lo, hi = run
    cursor = lo
    for start, end in sorted(extents):
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= hi:
            return True
    return cursor >= hi


def build() -> dict[str, Any]:
    parent = PARENT.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256_bytes(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if not PARENT_SAVE.is_file() or PARENT_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("main 32 KiB SaveRAM is missing")

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit.get("ok") is not True:
        raise BuildError("shared dictionary audit is not successful")
    if ((audit.get("inputs") or {}).get("tip") or {}).get("sha256") != EXPECTED_PARENT_SHA256:
        raise BuildError("audit is not bound to the current main TIP")
    rows = list(((audit.get("records") or {}).get("tier_a") or []))
    if len(rows) != 15:
        raise BuildError(f"tier-A selection drifted: expected 15, got {len(rows)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stock_before = Dictionary(parent)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    slot_payload: dict[int, bytes] = {}
    target_by_slot: dict[int, str] = {}
    for row in rows:
        index = int(str(row["index"]), 16)
        text = str((row.get("translation") or {}).get("ko") or "")
        if not text or any(is_japanese_character(ch) for ch in text):
            raise BuildError(f"invalid Korean target for slot {index:04X}")
        encoded = try_encode_ko_text(
            text,
            tbl,
            hangul_marker_code=marker_code(),
            hangul_marker_mode="run",
        )
        if encoded is None or b"\x00" in encoded:
            raise BuildError(f"target is not encodable for slot {index:04X}")
        if index in slot_payload:
            raise BuildError(f"duplicate selected slot {index:04X}")
        current = parent_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        if current != str(row["current_text"]):
            raise BuildError(f"audit current-text drift for slot {index:04X}")
        slot_payload[index] = bytes(encoded)
        target_by_slot[index] = text

    guard = guard_slot_writes(
        parent,
        slot_payload,
        union=union,
        allow_aux_consumers=True,
        justification=(
            "reviewed whole-term semantic replacement; every Original+Working external "
            "consumer and nested parent of each selected shared slot is accounted for"
        ),
    )
    if not guard.ok:
        raise BuildError("Original+Working slot guard refused the selected batch")

    bank_before = bytes(slice_bank(parent, SEG_DICT))
    pointers_before = list(stock_before.ptrs)
    payloads_before = [bytes(stock_before.raw_entry(index)) for index in range(stock_before.count)]
    phrase_start = _stock_phrase_cursor(parent)

    candidate = bytearray(parent)
    locations = union.as_locs()
    pointers_written, phrase_end = write_dictionary_slots_spill(
        candidate,
        slot_payload,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=True,
        locs=locations,
    )
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    stock_after = Dictionary(candidate_bytes)
    pointers_after = list(stock_after.ptrs)

    selected = set(slot_payload)
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != selected:
        raise BuildError("changed stock pointer set differs from the selected slots")
    if pointers_written != pointers_after:
        raise BuildError("writer pointer result differs from the candidate pointer table")

    current_nested = nested_occurrence_map(
        parent_dictionary, wanted=selected, ext3_aware=True
    )
    selected_checks: list[dict[str, Any]] = []
    cursor = phrase_start
    for index in sorted(selected):
        payload = slot_payload[index]
        if pointers_after[index] != cursor:
            raise BuildError(f"stock spill cursor drift for slot {index:04X}")
        if bytes(stock_after.raw_entry(index)) != payload:
            raise BuildError(f"stock payload verification failed for slot {index:04X}")
        rendered = candidate_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        if rendered != target_by_slot[index]:
            raise BuildError(f"render mismatch for slot {index:04X}: {rendered!r}")
        selected_checks.append(
            {
                "index": f"{index:04X}",
                "before": parent_dictionary.expand_index(index, tbl).rstrip("\u3000 \t"),
                "after": rendered,
                "new_pointer": f"{cursor:04X}",
                "encoded_payload_hex": payload.hex().upper(),
                "external_consumers": len(union.consumers_for(index)),
                "nested_parents": len(current_nested.get(index) or []),
            }
        )
        cursor += len(payload) + 1
    if cursor != phrase_end:
        raise BuildError("stock spill end drifted")

    for index, before_payload in enumerate(payloads_before):
        if index in selected:
            continue
        if pointers_after[index] != pointers_before[index]:
            raise BuildError(f"nonselected pointer changed: {index:04X}")
        if bytes(stock_after.raw_entry(index)) != before_payload:
            raise BuildError(f"nonselected dictionary payload changed: {index:04X}")

    # Verify every current external consumer and nested parent observes the new
    # target without changing record bytes.
    consumer_checks: list[dict[str, Any]] = []
    consumer_failures: list[dict[str, Any]] = []
    sb = stock_base(parent)
    seen_consumers: set[tuple[int, int]] = set()
    for index in sorted(selected):
        target = target_by_slot[index]
        for consumer in union.consumers_for(index):
            if "working" not in consumer.seen_in:
                continue
            key = (index, consumer.abs)
            if key in seen_consumers:
                continue
            seen_consumers.add(key)
            got = read_encoded_z_safe(parent, sb + consumer.abs, max_len=256)
            if got is None:
                consumer_failures.append({"index": f"{index:04X}", "abs": f"{consumer.abs:06X}", "reason": "unreadable_record"})
                continue
            payload = bytes(got[0])
            before_render = parent_dictionary.expand(payload, tbl)
            after_render = candidate_dictionary.expand(payload, tbl)
            ok = target in after_render and before_render != after_render
            check = {
                "index": f"{index:04X}",
                "abs": f"{consumer.abs:06X}",
                "region": consumer.region,
                "kind": consumer.kind,
                "before": before_render,
                "after": after_render,
                "target_present": target in after_render,
                "record_payload_unchanged": True,
                "ok": ok,
            }
            consumer_checks.append(check)
            if not ok:
                consumer_failures.append(check)
        nested_rows = list(current_nested.get(index) or [])
        parent_indices = sorted(
            {
                int(str(row.get("parent")), 16)
                for row in nested_rows
                if isinstance(row, Mapping) and row.get("parent") is not None
            }
        )
        for parent_index in parent_indices:
            before_render = parent_dictionary.expand_index(parent_index, tbl)
            after_render = candidate_dictionary.expand_index(parent_index, tbl)
            ok = target in after_render and before_render != after_render
            check = {
                "index": f"{index:04X}",
                "nested_parent": f"{parent_index:04X}",
                "before": before_render,
                "after": after_render,
                "target_present": target in after_render,
                "ok": ok,
            }
            consumer_checks.append(check)
            if not ok:
                consumer_failures.append(check)
    if consumer_failures:
        raise BuildError(
            "consumer render verification failed: "
            + json.dumps(consumer_failures[:3], ensure_ascii=False)
        )

    bank_after = bytes(slice_bank(candidate_bytes, SEG_DICT))
    pointer_extents = [
        (DICT_PTR_START + index * 2, DICT_PTR_START + index * 2 + 2)
        for index in sorted(selected)
    ]
    phrase_extent = (phrase_start, phrase_end)
    bad_bank_runs = [
        run
        for run in diff_runs(bank_before, bank_after)
        if not covered(run, pointer_extents + [phrase_extent])
    ]
    if bad_bank_runs:
        raise BuildError(f"bank-5F diff escaped approved extents: {bad_bank_runs[:8]}")

    bank_file_start = stock_base(parent) + SEG_DICT * BANK_SIZE
    allowed_file_extents = [
        (bank_file_start + lo, bank_file_start + hi)
        for lo, hi in pointer_extents + [phrase_extent]
    ] + [(len(parent) - 2, len(parent))]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"file_start": f"{lo:08X}", "file_end_exclusive": f"{hi:08X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed_file_extents)
    ]
    if unaccounted:
        raise BuildError("candidate has changes outside stock pointers, spill, and checksum")

    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    if parent[runtime_start:runtime_end] != candidate_bytes[runtime_start:runtime_end]:
        raise BuildError("runtime hook changed")
    if parent[:0x800000] != candidate_bytes[:0x800000]:
        raise BuildError("prepended expansion half changed")

    OUT_ROM.write_bytes(candidate_bytes)
    shutil.copy2(PARENT_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_shared_dictionary_cleanup_candidate.py",
        "ok": True,
        "accepted": True,
        "published": False,
        "status": "candidate_ready_for_visual_verification",
        "parent": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "source_audit": identity(AUDIT),
        "counts": {
            "selected_slots": len(selected),
            "external_and_nested_checks": len(consumer_checks),
            "consumer_failures": len(consumer_failures),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "guard": guard.as_dict(),
        "stock_allocation": {
            "spill_floor": f"{SPILL_FLOOR:04X}",
            "cursor_before": f"{phrase_start:04X}",
            "cursor_after": f"{phrase_end:04X}",
            "phrase_bytes": phrase_end - phrase_start,
            "selected_slots": [f"{index:04X}" for index in sorted(selected)],
        },
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
            "unaccounted": unaccounted,
        },
        "proof": {
            "selected_pointer_set_exact": True,
            "selected_payloads_exact": True,
            "all_current_consumers_render_target": True,
            "all_nested_parents_render_target": True,
            "nonselected_pointers_and_payloads_preserved": True,
            "record_bytes_unchanged": True,
            "expansion_half_unchanged": True,
            "runtime_hook_unchanged": True,
            "main_tip_unchanged": sha256_bytes(PARENT.read_bytes()) == EXPECTED_PARENT_SHA256,
            "main_saveram_untouched": True,
        },
        "slots": selected_checks,
        "consumer_checks": consumer_checks,
        "promotion": "blocked_pending_visual_verification",
    }
    write_json(REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["candidate"], "counts": report["counts"], "stock_allocation": report["stock_allocation"], "report": str(REPORT)}, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
