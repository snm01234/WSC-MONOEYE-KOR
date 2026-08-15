#!/usr/bin/env python3
"""Build the first broad Japanese-residual cleanup candidate.

Parent: the 15-slot shared-dictionary cleanup candidate.
This cumulative candidate then localizes every still-Japanese tier-A record from
``broad_japanese_residual_after_shared_audit.json``:

* body >= 4: private ext3 portal, deduplicated by Korean phrase;
* body 2-3: an existing exact stock phrase or a newly written strong-retired slot.

The main TIP and live SaveRAM are never modified.  Promotion remains blocked
until visual verification and explicit user approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, phrase_cursor, verify_non_target_invariance
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import (
    _working_two_byte_external_refs,
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    read_encoded_z_safe,
    slice_bank,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/shared_dictionary_cleanup_candidate.wsc"
PARENT_REPORT = ROOT / "out/patch/shared_dictionary_cleanup_report.json"
AUDIT = ROOT / "out/patch/broad_japanese_residual_after_shared_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/broad_residual_stage1_candidate.wsc"
OUT_SAVE = ROOT / "sram/broad_residual_stage1_candidate.sav"
REPORT = ROOT / "out/patch/broad_residual_stage1_report.json"

EXPECTED_MAIN_SHA = "279ce819fa63bea6ee52a307dc920714bb530c2f32e344e2543134dfbccbf7f9"
EXPECTED_PARENT_SHA = "322b38733b707a8593459d7a6435627fea985de76fac55cfbf2a5dbc2031e000"
ALLOC_SEG = 0x1C
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": digest(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def load_rows(parent: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit.get("ok") is not True:
        raise BuildError("broad residual audit is not successful")
    bound = (((audit.get("inputs") or {}).get("tip") or {}).get("sha256"))
    if bound != digest(parent):
        raise BuildError("audit is not bound to the shared-dictionary parent")
    rows: list[dict[str, Any]] = []
    for source in ((audit.get("records") or {}).get("tier_a") or []):
        row = dict(source)
        row["logical"] = int(str(row["abs"]), 16)
        row["ko"] = normalize_ko_text(str((row.get("translation") or {}).get("ko") or ""))
        if not row["ko"] or any(is_japanese_character(ch) for ch in row["ko"]):
            raise BuildError(f"invalid reviewed target: {row.get('record_id')}")
        if int(row["body_capacity"]) < 2:
            raise BuildError(f"one-byte tier-A target requires a separate method: {row.get('record_id')}")
        rows.append(row)
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != 34:
        raise BuildError(f"tier-A population drifted: expected 34, got {len(rows)}")
    return audit, rows


def bind_rows(parent: bytes, rows: list[dict[str, Any]]) -> None:
    base = stock_base(parent)
    for row in rows:
        logical = int(row["logical"])
        payload, term = payload_at(parent, logical)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        body = bytes.fromhex(str(row.get("body_hex") or ""))
        if payload != prefix + body:
            raise BuildError(f"payload or prefix drift: {row['record_id']}")
        if len(body) != int(row["body_capacity"]):
            raise BuildError(f"body capacity drift: {row['record_id']}")
        if term != base + logical + len(payload) or parent[term] != 0:
            raise BuildError(f"terminator drift: {row['record_id']}")


def exact_slots(dictionary: Any, tbl: Tbl, phrases: set[str]) -> dict[str, list[int]]:
    result = {phrase: [] for phrase in phrases}
    for index in range(min(int(dictionary.count), 0x1000)):
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            text = dictionary.expand(bytes(dictionary.raw_entry(index)), tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        if text in result:
            result[text].append(index)
    return result


def verify_targets(rom: bytes, rows: list[dict[str, Any]], dictionary: Any, tbl: Tbl) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        payload, term = payload_at(rom, int(row["logical"]))
        prefix_len = int(row["prefix_bytes"])
        rendered = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            failures.append({"record_id": row["record_id"], "expected": expected, "actual": rendered})
        elif any(is_japanese_character(ch) for ch in rendered):
            failures.append({"record_id": row["record_id"], "reason": "japanese_residual"})
        elif rom[term] != 0:
            failures.append({"record_id": row["record_id"], "reason": "terminator_changed"})
    return failures


def main() -> int:
    main_bytes = MAIN.read_bytes()
    parent = PARENT.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(main_bytes) != ROM_SIZE or digest(main_bytes) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("shared-dictionary parent identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing")
    parent_report = json.loads(PARENT_REPORT.read_text(encoding="utf-8"))
    if parent_report.get("ok") is not True or ((parent_report.get("candidate") or {}).get("sha256")) != EXPECTED_PARENT_SHA:
        raise BuildError("shared-dictionary parent report mismatch")

    audit, rows = load_rows(parent)
    bind_rows(parent, rows)
    direct = [row for row in rows if int(row["body_capacity"]) >= 4]
    short = [row for row in rows if 2 <= int(row["body_capacity"]) < 4]
    if len(direct) != 14 or len(short) != 20:
        raise BuildError(f"strategy population drifted: ext3={len(direct)} short={len(short)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)

    # Allocate one private ext3 phrase per unique Korean direct target.
    direct_phrases = sorted({str(row["ko"]) for row in direct})
    free_ext3 = sorted(index for index in inventory.ext3_free if bank_local_for_index(index)[0] == ALLOC_SEG)
    if len(free_ext3) < len(direct_phrases):
        raise BuildError("not enough ext3 slots in allocation bank")
    ext3_assignment = {phrase: index for phrase, index in zip(direct_phrases, free_ext3)}
    ext3_payloads = {index: encode_phrase(phrase, tbl) for phrase, index in ext3_assignment.items()}
    ext3_need = sum(len(payload) + 1 for payload in ext3_payloads.values())
    bank_index = ALLOC_SEG - EXP3_SEG0
    if ext3_need > int(inventory.ext3_bank_room.get(bank_index, 0)):
        raise BuildError("not enough ext3 phrase bytes")

    # Short rows reuse exact Korean stock phrases, otherwise strong-retired slots.
    short_phrases = {str(row["ko"]) for row in short}
    exact = exact_slots(parent_dictionary, tbl, short_phrases)
    reuse = {phrase: slots for phrase, slots in exact.items() if slots}
    new_phrases = sorted(short_phrases - set(reuse))
    retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected_retired = retired[: len(new_phrases)]
    if len(selected_retired) != len(new_phrases):
        raise BuildError("not enough strong-retired stock slots")
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=set(selected_retired))
    current_nested = nested_occurrence_map(parent_dictionary, wanted=set(selected_retired), ext3_aware=True)
    current_raw = _raw_pair_hits(parent, selected_retired)
    if any(current_external.get(index) or current_nested.get(index) or current_raw.get(index) for index in selected_retired):
        raise BuildError("a selected retired stock slot is still reachable")
    stock_assignment = {phrase: min(slots) for phrase, slots in reuse.items()}
    stock_payloads: dict[int, bytes] = {}
    for phrase, index in zip(new_phrases, selected_retired):
        stock_assignment[phrase] = index
        stock_payloads[index] = encode_phrase(phrase, tbl)

    candidate = bytearray(parent)
    ext3_bank_before = bytes(slice_expansion_bank(parent, ALLOC_SEG))
    ext3_cursor_before = phrase_cursor(ext3_bank_before)
    ext3_info, ext3_guard = write_ext3_slots_guarded(candidate, ext3_payloads, union=union, num_banks=num_banks)
    if ext3_info.get("written") != len(ext3_payloads):
        raise BuildError("ext3 writer did not write every selected phrase")

    stock_before = Dictionary(candidate)
    pointers_before = list(stock_before.ptrs)
    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_written, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        stock_payloads,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=_working_two_byte_external_refs(bytes(candidate)),
    )
    stock_after_write = Dictionary(candidate)
    pointers_after = list(stock_after_write.ptrs)
    if pointers_after != pointers_written:
        raise BuildError("stock writer pointer result mismatch")
    changed_slots = {index for index, (before, after) in enumerate(zip(pointers_before, pointers_after)) if before != after}
    if changed_slots != set(selected_retired):
        raise BuildError("stock pointer change set differs from selected retired slots")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        prefix_len = int(row["prefix_bytes"])
        capacity = int(row["body_capacity"])
        phrase = str(row["ko"])
        if capacity >= 4:
            index = ext3_assignment[phrase]
            token = token_from_ext3_index(index, num_banks=num_banks)
            strategy = "private_ext3"
            slot_key = "ext3_index"
            slot_value = f"{index:05X}"
        else:
            index = stock_assignment[phrase]
            token = token_from_dict_index(index)
            strategy = "existing_exact_stock" if phrase in reuse else "strong_retired_stock"
            slot_key = "stock_index"
            slot_value = f"{index:04X}"
        replacement = token + b"\x01" * (capacity - len(token))
        if len(replacement) != capacity:
            raise BuildError(f"replacement length mismatch: {row['record_id']}")
        file_start = base + logical + prefix_len
        candidate[file_start:file_start + capacity] = replacement
        target_extents.append((file_start, file_start + capacity))
        applied.append({
            "record_id": row["record_id"], "abs": row["abs"], "region": row["region"],
            "before": row["current_text"], "after": phrase, "body_capacity": capacity,
            "strategy": strategy, slot_key: slot_value, "token_hex": token.hex().upper(),
        })

    checksum = update_ws_checksum(candidate)
    final = bytes(candidate)
    final_dictionary = make_dictionary_ext3(final, ext_meta, ext3_meta)
    target_failures = verify_targets(final, rows, final_dictionary, tbl)
    invariance = verify_non_target_invariance(
        parent, final,
        before_dictionary=parent_dictionary,
        after_dictionary=final_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )

    ext3_cursor_after = phrase_cursor(bytes(slice_expansion_bank(final, ALLOC_SEG)))
    ext3_bank_file = ALLOC_SEG * BANK_SIZE
    ext3_pointer_extents = []
    for index in ext3_payloads:
        _segment, local = bank_local_for_index(index)
        ext3_pointer_extents.append((ext3_bank_file + local * 2, ext3_bank_file + local * 2 + 2))
    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (stock_bank_file + DICT_PTR_START + index * 2, stock_bank_file + DICT_PTR_START + index * 2 + 2)
        for index in selected_retired
    ]
    allowed = (
        target_extents
        + ext3_pointer_extents
        + [(ext3_bank_file + ext3_cursor_before, ext3_bank_file + ext3_cursor_after)]
        + stock_pointer_extents
        + [(stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)]
        + [(len(parent) - 2, len(parent))]
    )
    runs = diff_runs(parent, final)
    unaccounted = [
        {"start": f"{lo:08X}", "end_exclusive": f"{hi:08X}"}
        for lo, hi in runs if not covered((lo, hi), allowed)
    ]

    other_ext3_unchanged = all(
        bytes(slice_expansion_bank(parent, seg)) == bytes(slice_expansion_bank(final, seg))
        for seg in range(EXP3_SEG0, EXP3_SEG0 + num_banks) if seg != ALLOC_SEG
    )
    runtime_lo = stock_base(parent) + 0x7A0600
    runtime_hi = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_lo:runtime_hi] == final[runtime_lo:runtime_hi]
    main_unchanged = digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA
    ok = not target_failures and invariance["ok"] and not unaccounted and other_ext3_unchanged and runtime_unchanged and main_unchanged
    if not ok:
        raise BuildError("stage-1 candidate static verification failed")

    atomic_bytes(OUT_ROM, final)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_broad_residual_stage1_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_ready_for_visual_verification",
        "main_tip": identity(MAIN, main_bytes),
        "parent_shared_dictionary_candidate": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, final),
        "candidate_save": identity(OUT_SAVE),
        "source_reports": {"shared_dictionary": identity(PARENT_REPORT), "after_shared_audit": identity(AUDIT)},
        "counts": {
            "shared_dictionary_slots_in_parent": 15,
            "record_targets": len(rows),
            "ext3_records": len(direct),
            "short_stock_records": len(short),
            "ext3_unique_phrases": len(ext3_payloads),
            "short_unique_phrases": len(short_phrases),
            "existing_exact_stock_phrases": len(reuse),
            "new_retired_stock_phrases": len(stock_payloads),
            "target_failures": len(target_failures),
            "non_target_failures": int(invariance["failure_count"]),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segment": f"{ALLOC_SEG:02X}",
            "ext3_cursor_before": f"{ext3_cursor_before:04X}",
            "ext3_cursor_after": f"{ext3_cursor_after:04X}",
            "ext3_phrase_bytes": ext3_cursor_after - ext3_cursor_before,
            "ext3_indices": [f"{index:05X}" for index in sorted(ext3_payloads)],
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
            "stock_phrase_bytes": stock_cursor_after - stock_cursor_before,
            "selected_retired_slots": [f"{index:04X}" for index in selected_retired],
            "existing_exact_slots": {phrase: [f"{index:04X}" for index in slots] for phrase, slots in sorted(reuse.items())},
        },
        "guards": {"ext3": ext3_guard.as_dict(), "retired_slots_current_reachable_zero": True},
        "verification": {
            "all_record_targets_render_exact": True,
            "target_japanese_residuals_zero": True,
            "non_target_invariance": invariance,
            "diffs_bounded": True,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": main_unchanged,
            "main_saveram_untouched": True,
            "prefix_length_terminator_preserved": True,
        },
        "diff": {"changed_bytes_from_shared_parent": sum(hi - lo for lo, hi in runs), "runs": len(runs), "checksum": f"{checksum:04X}"},
        "records": applied,
        "promotion": "blocked_pending_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["candidate"], "counts": report["counts"], "allocation": report["allocation"], "report": str(REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
