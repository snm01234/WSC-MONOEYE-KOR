#!/usr/bin/env python3
"""Build v2 candidate removing E5 18 from the full Sig Wedna(Z) ID-command quote.

The first diagnostic replaced the two visible headline variants but left the
automatic second line at 5D:0D6F on E5 18.  Runtime feedback was unchanged, so
v2 replaces all three records: the two-line first line, its unlisted automatic
continuation, and the one-line variant.  Structural prefixes, record extents,
terminators, runtime hooks, and every non-target byte remain intact.  All three
Korean phrases move to union-proven free normal expansion-dictionary slots in
bank 0x10 and use ordinary two-byte dictionary tokens.

The main TIP and live SaveRAM are never modified.
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import encode_phrase  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_reference_union,
    guard_slot_writes,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_exp_dictionary import write_exp_dictionary_slots  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/sig_wedna_z_id_command_safe_text_v2_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/sig_wedna_z_id_command_safe_text_v2_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_wedna_z_id_command_safe_text_v2_candidate.sav"
REPORT = ROOT / "out/patch/sig_wedna_z_id_command_safe_text_v2_report.json"

EXPECTED_PARENT_SHA256 = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
NORMAL_EXT_SEG = 0x10
FORBIDDEN_TARGET_SEQUENCES = (bytes.fromhex("E518"), bytes.fromhex("E62F"))


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": digest(payload),
    }


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_bytes(path, encoded)


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise BuildError("ROM size changed")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def covered(run: tuple[int, int], allowed: Iterable[tuple[int, int]]) -> bool:
    left, right = run
    return any(begin <= left and right <= end for begin, end in allowed)


def ext_phrase_cursor(rom: bytes | bytearray, meta: Mapping[str, Any]) -> int:
    ext_ptr_off = int(str(meta["ext_ptr_off"]), 16)
    stock_count = int(meta["stock_count"])
    slot_count = int(meta["slot_count"])
    bank = rom[NORMAL_EXT_SEG * BANK_SIZE : (NORMAL_EXT_SEG + 1) * BANK_SIZE]
    cursor = ext_ptr_off + slot_count * 2
    for local in range(slot_count):
        at = ext_ptr_off + local * 2
        pointer = bank[at] | (bank[at + 1] << 8)
        if pointer < cursor or pointer >= BANK_SIZE:
            continue
        end = pointer
        while end < BANK_SIZE and bank[end] != 0:
            end += 1
        if end >= BANK_SIZE:
            raise BuildError(f"unterminated normal ext phrase at {pointer:04X}")
        cursor = max(cursor, end + 1)
    if stock_count + slot_count != 0x1000:
        raise BuildError("normal expansion dictionary no longer ends at index 0FFF")
    return cursor


def load_targets(parent: bytes, dictionary: Any, tbl: Tbl) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_rom_sha256") or "").lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("spec parent identity drifted")
    rows: list[dict[str, Any]] = []
    sb = stock_base(parent)
    seen_records: set[int] = set()
    seen_slots: set[int] = set()
    for source in spec.get("records") or []:
        logical = int(str(source["record_start"]), 16)
        prefix = bytes.fromhex(str(source["prefix_hex"]))
        expected_payload = bytes.fromhex(str(source["expected_payload_hex"]))
        body_capacity = int(source["body_capacity"])
        index = int(str(source["dictionary_index"]), 16)
        if logical in seen_records or index in seen_slots:
            raise BuildError("duplicate target record or dictionary slot")
        seen_records.add(logical)
        seen_slots.add(index)
        if not expected_payload.startswith(prefix):
            raise BuildError(f"prefix is not part of expected payload at {logical:06X}")
        if len(expected_payload) != len(prefix) + body_capacity:
            raise BuildError(f"capacity drift in spec at {logical:06X}")
        got = read_encoded_z_safe(parent, sb + logical, max_len=128)
        if got is None:
            raise BuildError(f"unreadable parent record at {logical:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        if payload != expected_payload:
            raise BuildError(
                f"parent payload drift at {logical:06X}: expected {expected_payload.hex().upper()}, got {payload.hex().upper()}"
            )
        if parent[terminator] != 0 or terminator != sb + logical + len(payload):
            raise BuildError(f"terminator drift at {logical:06X}")
        before = dictionary.expand(payload[len(prefix):], tbl).rstrip("\u3000 \t")
        phrase = str(source["ko"])
        encoded = encode_phrase(phrase, tbl)
        if not phrase or any(is_japanese_character(character) for character in phrase):
            raise BuildError(f"invalid Korean phrase at {logical:06X}")
        rows.append(
            {
                "logical": logical,
                "prefix": prefix,
                "expected_payload": expected_payload,
                "body_capacity": body_capacity,
                "index": index,
                "phrase": phrase,
                "encoded": encoded,
                "before": before,
                "terminator": terminator,
                "category": str(source.get("category") or ""),
            }
        )
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != 3:
        raise BuildError(f"expected exactly three affected records, got {len(rows)}")
    return spec, rows


def main() -> int:
    parent = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    spec, rows = load_targets(parent, parent_dictionary, tbl)

    slot_payload = {int(row["index"]): bytes(row["encoded"]) for row in rows}
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    guard = guard_slot_writes(parent, slot_payload, union=union, require_free=True)
    if not guard.ok:
        raise BuildError(f"normal ext slot guard refused write: {guard.outcome}")

    candidate = bytearray(parent)
    phrase_start = ext_phrase_cursor(candidate, ext_meta)
    write_info = write_exp_dictionary_slots(
        candidate,
        slot_payload,
        ext_ptr_off=int(str(ext_meta["ext_ptr_off"]), 16),
        stock_count=int(ext_meta["stock_count"]),
        slot_count=int(ext_meta["slot_count"]),
        allow_aux_consumers=False,
        locs=union.as_locs(),
    )
    if int(write_info.get("written") or 0) != len(rows):
        raise BuildError("normal expansion dictionary writer did not write all three phrases")
    phrase_end = int(write_info["phrase_end"])

    sb = stock_base(candidate)
    record_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        prefix = bytes(row["prefix"])
        token = token_from_dict_index(int(row["index"]))
        capacity = int(row["body_capacity"])
        if len(token) != 2 or token[0] != 0xFF:
            raise BuildError(f"selected index is not a normal two-byte FF-page token: {int(row['index']):04X}")
        replacement = token + b"\x01" * (capacity - len(token))
        if len(replacement) != capacity:
            raise BuildError(f"target capacity too short at {int(row['logical']):06X}")
        if any(sequence in replacement for sequence in FORBIDDEN_TARGET_SEQUENCES):
            raise BuildError("forbidden portal remained in replacement")
        body_file = sb + int(row["logical"]) + len(prefix)
        candidate[body_file : body_file + capacity] = replacement
        record_extents.append((body_file, body_file + capacity))
        applied.append(
            {
                "record_start": f"{int(row['logical']):06X}",
                "category": row["category"],
                "before": row["before"],
                "after": row["phrase"],
                "prefix_hex": prefix.hex().upper(),
                "body_capacity": capacity,
                "normal_ext_index": f"{int(row['index']):04X}",
                "token_hex": token.hex().upper(),
                "before_payload_hex": bytes(row["expected_payload"]).hex().upper(),
                "after_payload_hex": (prefix + replacement).hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"])
        prefix = bytes(row["prefix"])
        got = read_encoded_z_safe(candidate_bytes, sb + logical, max_len=128)
        if got is None:
            target_failures.append({"record_start": f"{logical:06X}", "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        body = payload[len(prefix):]
        rendered = candidate_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        expected = str(row["phrase"]).rstrip("\u3000 \t")
        ok = (
            payload.startswith(prefix)
            and len(body) == int(row["body_capacity"])
            and terminator == int(row["terminator"])
            and candidate_bytes[terminator] == 0
            and rendered == expected
            and not any(is_japanese_character(character) for character in rendered)
            and not any(sequence in body for sequence in FORBIDDEN_TARGET_SEQUENCES)
            and candidate_dictionary.raw_entry(int(row["index"])) == bytes(row["encoded"])
        )
        if not ok:
            target_failures.append(
                {
                    "record_start": f"{logical:06X}",
                    "expected": expected,
                    "actual": rendered,
                    "payload_hex": payload.hex().upper(),
                    "terminator_ok": terminator == int(row["terminator"]),
                }
            )

    ext_pointer_extents: list[tuple[int, int]] = []
    stock_count = int(ext_meta["stock_count"])
    ext_ptr_off = int(str(ext_meta["ext_ptr_off"]), 16)
    ext_bank_file = NORMAL_EXT_SEG * BANK_SIZE
    for row in rows:
        local = int(row["index"]) - stock_count
        start = ext_bank_file + ext_ptr_off + local * 2
        ext_pointer_extents.append((start, start + 2))
    allowed = (
        record_extents
        + ext_pointer_extents
        + [(ext_bank_file + phrase_start, ext_bank_file + phrase_end)]
        + [(len(parent) - 2, len(parent))]
    )
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]
    target_record_union = set()
    for left, right in record_extents:
        target_record_union.update(range(left, right))
    non_target_stock_changes = sum(
        1
        for file_index in range(stock_base(parent), len(parent) - 2)
        if parent[file_index] != candidate_bytes[file_index] and file_index not in target_record_union
    )

    ok = (
        not target_failures
        and not unaccounted
        and runtime_unchanged
        and non_target_stock_changes == 0
        and digest(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256
        and MAIN_SAVE.read_bytes() == main_save
    )
    if not ok:
        raise BuildError("candidate static verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 2,
        "generated_by": "tools/build_sig_wedna_z_id_command_safe_text_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_user_runtime_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "spec": identity(SPEC),
        "symptom": spec.get("symptom") or {},
        "cause_assessment": {
            "record_and_ext3_payload_structurally_valid": True,
            "v1_runtime_result": "unchanged because automatic continuation 5D0D6F still used E5 18",
            "special_two_line_runtime_path_ext3_incompatibility": "candidate_under_test",
            "repair": "replace E5 18 in both lines and the one-line variant with ordinary two-byte normal expansion-dictionary tokens",
        },
        "counts": {
            "target_records": len(rows),
            "normal_ext_slots_written": len(slot_payload),
            "target_failures": len(target_failures),
            "unaccounted_diff_runs": len(unaccounted),
            "non_target_stock_changes": non_target_stock_changes,
        },
        "allocation": {
            "segment": f"{NORMAL_EXT_SEG:02X}",
            "phrase_start": f"{phrase_start:04X}",
            "phrase_end": f"{phrase_end:04X}",
            "bytes_added": phrase_end - phrase_start,
            "bytes_free_after": int(write_info.get("bytes_free") or 0),
            "slots": {row["phrase"]: f"{int(row['index']):04X}" for row in rows},
            "guard": guard.as_dict(),
        },
        "records": applied,
        "verification": {
            "target_render_exact": not target_failures,
            "prefix_capacity_terminator_preserved": not target_failures,
            "target_ext3_and_compact3_absent": not target_failures,
            "diffs_bounded": not unaccounted,
            "runtime_hook_unchanged": runtime_unchanged,
            "all_non_target_stock_bytes_unchanged": non_target_stock_changes == 0,
            "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save,
            "candidate_saveram_matches_live_at_build": OUT_SAVE.read_bytes() == main_save,
        },
        "allowed_file_extents": [
            {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
            for left, right in allowed
        ],
        "diff": {
            "changed_bytes_from_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "runs_detail": [
                {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
                for left, right in runs
            ],
            "checksum": f"{checksum:04X}",
        },
        "promotion": "blocked_pending_user_runtime_validation_on_sig_wedna_z_spirit_id_command",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "candidate_save": report["candidate_save"],
                "counts": report["counts"],
                "allocation": report["allocation"],
                "diff": report["diff"],
                "report": str(REPORT.relative_to(ROOT)).replace("\\", "/"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
