#!/usr/bin/env python3
"""REJECTED builder: 도감 gojuon-index ext3 rewrite.

Runtime regression proven on 2026-08-15: replacing the nine UI75 chart records
at 75B889–75B8BF with E5 18 ext3 portals corrupts the appreciation-mode BGM
list when scrolling from 함대전 B.  The records are dual-use/raw-index data,
not safe generic zstrings.  Keep their stock bytes and localize display only
through a dedicated chart-aware renderer if this UI is revisited.

This file is retained only for provenance and intentionally fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from apply_safe_unit import padded_token_payload
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    phrase_cursor,
    verify_non_target_invariance,
)
from hangul_marker import marker_code
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    Tbl,
    dict_token_safe_in_zstring,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CATALOG = ROOT / "data/encyclopedia_kana_index_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_DIR = ROOT / "out/patch/encyclopedia_kana_index_candidate"
OUT_ROM = OUT_DIR / "monoeye_ko_expanded_encyclopedia_kana_index_test.wsc"
OUT_SAVE = OUT_DIR / "monoeye_ko_expanded_encyclopedia_kana_index_test.sav"
REPORT = OUT_DIR / "encyclopedia_kana_index_report.json"

EXPECTED_MAIN_SHA = "0ff2bc7398c5b677d02bc1d81df21d12dc7731d2d16d62c3cc7cd25b1c74ca11"
EXPECTED_ROWS = 9
KEEP_LATIN = 0x75B8C6
KEEP_LATIN_HEX = "E1C0E0F5E1C907E132"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def visual_cells(text: str) -> int:
    if "<" in text or ">" in text:
        raise BuildError(f"control markup is not allowed: {text!r}")
    return len(text)


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    result = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if result is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(result[0]), int(result[1])


def load_rows(parent: bytes, dictionary: Any, tbl: Tbl) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if str(catalog.get("parent_tip_sha256") or "").lower() != EXPECTED_MAIN_SHA:
        raise BuildError("catalog parent identity drifted")
    sb = stock_base(parent)
    latin = parent[sb + KEEP_LATIN : sb + KEEP_LATIN + len(bytes.fromhex(KEEP_LATIN_HEX))]
    if latin != bytes.fromhex(KEEP_LATIN_HEX):
        raise BuildError("latin index row 75B8C6 drifted")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in catalog.get("records") or []:
        address = str(source.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate catalog address {address}")
        seen.add(address)
        logical = int(address, 16)
        if not (0x75B889 <= logical <= 0x75B8BF):
            raise BuildError(f"catalog address outside kana-index range: {address}")
        payload, terminator = payload_at(parent, logical)
        expected_hex = str(source.get("current_payload_hex") or "").upper()
        if payload.hex().upper() != expected_hex:
            raise BuildError(f"parent payload drifted at {address}")
        if len(payload) != int(source.get("payload_len") or -1):
            raise BuildError(f"payload length drifted at {address}")
        if len(payload) < 4:
            raise BuildError(f"body too small for private ext3 at {address}")
        if terminator != sb + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {address}")
        before = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        if before != str(source.get("jp") or ""):
            raise BuildError(f"parent render drifted at {address}: {before!r}")
        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or any(is_japanese_character(character) for character in ko):
            raise BuildError(f"invalid Korean at {address}: {ko!r}")
        max_cells = int(source.get("max_visual_cells") or 0)
        if visual_cells(ko) > max_cells:
            raise BuildError(f"visual width exceeds limit at {address}: {ko!r}")
        rows.append(
            {
                "abs": address,
                "logical": logical,
                "jp": str(source.get("jp") or ""),
                "ko": ko,
                "encoded": encode_phrase(ko, tbl),
                "payload": payload,
                "payload_len": len(payload),
                "max_visual_cells": max_cells,
            }
        )
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_ROWS:
        raise BuildError(f"catalog population drifted: expected {EXPECTED_ROWS}, got {len(rows)}")
    return catalog, rows


def main() -> int:
    raise BuildError(
        "REJECTED: 75:B889-B8BF is a dual-use kana/index chart; E518 portal rewrite "
        "causes appreciation-mode BGM corruption (runtime-confirmed 2026-08-15)."
    )
    parent = bytes(MAIN.read_bytes())
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")
    if not ORIGINAL.is_file():
        raise BuildError("original ROM missing")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 metadata is not installed")
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    _catalog, rows = load_rows(parent, parent_dictionary, tbl)

    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    unique: dict[bytes, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["encoded"], row)
    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in inventory.ext3_free:
        if not dict_token_safe_in_zstring(index):
            continue
        segment, _local = bank_local_for_index(index)
        free_by_bank[segment - EXP3_SEG0].append(index)
    for values in free_by_bank.values():
        values.sort()
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}
    encoded_to_index: dict[bytes, int] = {}
    slot_payload: dict[int, bytes] = {}
    used_segments: set[int] = set()
    for encoded, sample in sorted(unique.items(), key=lambda item: item[1]["logical"]):
        need = len(encoded) + 1
        chosen_bank = next(
            (
                bank
                for bank in sorted(room, key=lambda value: (-room[value], value))
                if room.get(bank, 0) >= need and free_by_bank.get(bank)
            ),
            None,
        )
        if chosen_bank is None:
            raise BuildError(f"no ext3 room for {sample['ko']!r}")
        index = free_by_bank[chosen_bank].pop(0)
        room[chosen_bank] -= need
        encoded_to_index[encoded] = index
        slot_payload[index] = encoded
        used_segments.add(chosen_bank + EXP3_SEG0)
    if not slot_payload:
        raise BuildError("no ext3 phrases assigned")
    for row in rows:
        row["slot"] = encoded_to_index[row["encoded"]]

    scratch = bytearray(parent)
    cursor_before = {
        segment: phrase_cursor(bytes(slice_expansion_bank(parent, segment)))
        for segment in used_segments
    }
    ext3_write, guard = write_ext3_slots_guarded(
        scratch,
        slot_payload,
        union=union,
        num_banks=num_banks,
        justification="encyclopedia gojuon index Hangul readings on UI75 labels",
    )
    if int(ext3_write.get("written") or 0) != len(slot_payload):
        raise BuildError("ext3 writer count mismatch")

    sb = stock_base(scratch)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        token = token_from_ext3_index(int(row["slot"]), num_banks=num_banks)
        replacement = padded_token_payload(b"", token, row["payload"])
        if len(replacement) != row["payload_len"]:
            raise BuildError(f"padded payload length drifted at {row['abs']}")
        at = sb + int(row["logical"])
        scratch[at : at + row["payload_len"]] = replacement
        scratch[at + row["payload_len"]] = 0
        target_extents.append((at, at + row["payload_len"]))
        applied.append(
            {
                "abs": row["abs"],
                "jp": row["jp"],
                "ko": row["ko"],
                "visual_cells": visual_cells(row["ko"]),
                "max_visual_cells": row["max_visual_cells"],
                "payload_len": row["payload_len"],
                "strategy": "private_ext3",
                "ext3_index": f"{int(row['slot']):05X}",
                "token_hex": token.hex().upper(),
                "new_payload_hex": replacement.hex().upper(),
            }
        )

    checksum = update_ws_checksum(scratch)
    candidate = bytes(scratch)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []
    for row in rows:
        payload, terminator = payload_at(candidate, int(row["logical"]))
        rendered = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        if rendered != expected:
            failures.append(
                {
                    "abs": row["abs"],
                    "reason": "render_mismatch",
                    "expected": expected,
                    "actual": rendered,
                }
            )
        elif visual_cells(rendered) > int(row["max_visual_cells"]):
            failures.append({"abs": row["abs"], "reason": "visual_width_exceeded"})
        elif any(is_japanese_character(character) for character in rendered):
            failures.append(
                {"abs": row["abs"], "reason": "japanese_residual", "actual": rendered}
            )
        elif terminator != sb + int(row["logical"]) + row["payload_len"] or candidate[terminator] != 0:
            failures.append({"abs": row["abs"], "reason": "terminator_changed"})

    latin_after = candidate[sb + KEEP_LATIN : sb + KEEP_LATIN + len(bytes.fromhex(KEEP_LATIN_HEX))]
    if latin_after != bytes.fromhex(KEEP_LATIN_HEX):
        failures.append({"abs": "75B8C6", "reason": "latin_index_row_changed"})

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )
    cursor_after = {
        segment: phrase_cursor(bytes(slice_expansion_bank(candidate, segment)))
        for segment in used_segments
    }
    pointer_extents = []
    phrase_extents = []
    for index in slot_payload:
        segment, local = bank_local_for_index(index)
        bank_file = segment * BANK_SIZE
        pointer_extents.append((bank_file + local * 2, bank_file + local * 2 + 2))
    for segment in used_segments:
        bank_file = segment * BANK_SIZE
        phrase_extents.append(
            (bank_file + cursor_before[segment], bank_file + cursor_after[segment])
        )
    allowed = target_extents + pointer_extents + phrase_extents + [
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    other_ext3_unchanged = all(
        bytes(slice_expansion_bank(parent, segment))
        == bytes(slice_expansion_bank(candidate, segment))
        for segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if segment not in used_segments
    )
    runtime_start = sb + 0x7A0600
    runtime_end = sb + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate[runtime_start:runtime_end]
    main_unchanged = sha256(MAIN.read_bytes()) == EXPECTED_MAIN_SHA
    save_unchanged = MAIN_SAVE.read_bytes() == main_save

    ok = (
        not failures
        and invariance.get("ok") is True
        and not unaccounted
        and other_ext3_unchanged
        and runtime_unchanged
        and main_unchanged
        and save_unchanged
        and checksum is not None
    )
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_encyclopedia_kana_index_candidate.py",
        "ok": ok,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "main_tip": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate) if ok else None,
        "counts": {
            "targets": len(rows),
            "ext3_unique_phrases": len(slot_payload),
            "target_failures": len(failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "ext3_segments": [f"{segment:02X}" for segment in sorted(used_segments)],
            "ext3_cursor_before": {
                f"{segment:02X}": f"{cursor_before[segment]:04X}"
                for segment in sorted(used_segments)
            },
            "ext3_cursor_after": {
                f"{segment:02X}": f"{cursor_after[segment]:04X}"
                for segment in sorted(used_segments)
            },
            "ext3_phrase_bytes": sum(
                cursor_after[segment] - cursor_before[segment]
                for segment in used_segments
            ),
        },
        "guards": {"ext3": guard.as_dict()},
        "verification": {
            "all_targets_render_exact": not failures,
            "latin_index_row_unchanged": latin_after.hex().upper() == KEEP_LATIN_HEX,
            "non_target_invariance": invariance,
            "diffs_bounded": not unaccounted,
            "other_ext3_banks_unchanged": other_ext3_unchanged,
            "runtime_hook_unchanged": runtime_unchanged,
            "main_tip_unchanged": main_unchanged,
            "main_saveram_untouched": save_unchanged,
            "record_lengths_and_terminators_preserved": True,
        },
        "diff": {
            "changed_bytes_from_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "failures": failures,
        "unaccounted_diff_runs": unaccounted,
        "records": applied,
        "kept": [{"abs": "75B8C6", "jp": "Ａ～Ｚ、∀"}],
        "marker_code": f"{marker_code():04X}",
        "promotion": "blocked_pending_user_visual_verification",
        "ext3_write": ext3_write,
    }
    if not ok:
        atomic_json(REPORT, report)
        raise BuildError(
            "encyclopedia kana-index candidate failed gates: "
            + json.dumps(
                {
                    "failures": failures,
                    "unaccounted": unaccounted,
                    "invariance_ok": invariance.get("ok"),
                    "other_ext3_unchanged": other_ext3_unchanged,
                    "runtime_unchanged": runtime_unchanged,
                },
                ensure_ascii=True,
            )
        )

    atomic_bytes(OUT_ROM, candidate)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report["candidate"] = identity(OUT_ROM)
    report["save"] = identity(OUT_SAVE)
    report["save_matches_main"] = sha256(OUT_SAVE.read_bytes()) == sha256(main_save)
    if not report["save_matches_main"]:
        raise BuildError("candidate SaveRAM is not byte-exact with live SaveRAM")
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "rom": rel(OUT_ROM),
                "sha256": report["candidate"]["sha256"],
                "checksum": report["diff"]["checksum"],
                "applied": len(applied),
                "diff_bytes": report["diff"]["changed_bytes_from_parent"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
