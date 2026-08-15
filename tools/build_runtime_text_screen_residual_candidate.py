#!/usr/bin/env python3
"""Build the screen-proven residual text candidate on the promoted main TIP.

Eight records across three runtime families are rewritten with six private ext3
phrases.  Prefix bytes, payload capacities, terminators, runtime code, shared
stock dictionary slots, protected structured tables, main TIP, and live SaveRAM
are preserved.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import (  # noqa: E402
    covered,
    diff_runs,
    encode_phrase,
    phrase_cursor,
    verify_non_target_invariance,
)
from mixed_residual_classification import is_japanese_character  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text  # noqa: E402
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index  # noqa: E402
from structured_token_write_guard import (  # noqa: E402
    PROTECTED_TABLES,
    classify_structured_token_site,
    validate_protected_table,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/runtime_text_screen_residual_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/runtime_text_screen_residual_candidate.wsc"
OUT_SAVE = ROOT / "sram/runtime_text_screen_residual_candidate.sav"
REPORT = ROOT / "out/patch/runtime_text_screen_residual_candidate_report.json"

EXPECTED_PARENT = "29d096e6462194e226b0895a43016d30b38056c1088bffe925571ac8e466b9ea"
EXPECTED_TARGETS = 8
EXPECTED_PHRASES = 6
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
PREFERRED_SEGMENTS = (0x1F, 0x20, 0x1E, 0x1D, 0x1C)


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": digest(data),
    }


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_rows(parent: bytes, dictionary: Any, tbl: Tbl) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_rom_sha256") or "").lower() != EXPECTED_PARENT:
        raise BuildError("spec parent binding drifted")
    rows: list[dict[str, Any]] = []
    starts: set[int] = set()
    intervals: list[tuple[int, int]] = []
    base = stock_base(parent)
    for raw in spec.get("records") or []:
        start = int(str(raw.get("record_start") or "0"), 16)
        prefix = bytes.fromhex(str(raw.get("prefix_hex") or ""))
        expected = bytes.fromhex(str(raw.get("expected_payload_hex") or ""))
        capacity = int(raw.get("body_capacity") or 0)
        ko = normalize_ko_text(str(raw.get("ko") or ""))
        jp = str(raw.get("jp") or "")
        if start in starts:
            raise BuildError(f"duplicate target {start:06X}")
        starts.add(start)
        if not expected.startswith(prefix) or len(expected) != len(prefix) + capacity:
            raise BuildError(f"spec shape drift at {start:06X}")
        if capacity < 4:
            raise BuildError(f"target cannot hold ext3 token at {start:06X}")
        if not ko or any(is_japanese_character(character) for character in ko):
            raise BuildError(f"invalid Korean translation at {start:06X}: {ko!r}")
        got = read_encoded_z_safe(parent, base + start, max_len=128)
        if got is None:
            raise BuildError(f"unreadable target {start:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        if payload != expected:
            raise BuildError(
                f"parent payload drift at {start:06X}: expected {expected.hex().upper()}, got {payload.hex().upper()}"
            )
        try:
            before = dictionary.expand(payload[len(prefix):], tbl).rstrip("\u3000 \t")
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"target decode failed at {start:06X}: {type(exc).__name__}") from exc
        extent = (base + start + len(prefix), base + start + len(prefix) + capacity)
        if any(not (extent[1] <= left or right <= extent[0]) for left, right in intervals):
            raise BuildError(f"overlapping target extent at {start:06X}")
        intervals.append(extent)
        structure = classify_structured_token_site(parent, start + len(prefix), length=capacity)
        if structure is not None:
            raise BuildError(f"target overlaps structured data at {start + len(prefix):06X}: {structure}")
        rows.append(
            {
                "record_start": start,
                "prefix": prefix,
                "expected": expected,
                "capacity": capacity,
                "terminator": terminator,
                "jp": jp,
                "before": before,
                "ko": ko,
                "family": str(raw.get("family") or ""),
                "bundle_start": str(raw.get("bundle_start") or ""),
                "line_role": str(raw.get("line_role") or ""),
                "translation_source": str(raw.get("translation_source") or ""),
                "review_status": str(raw.get("review_status") or ""),
            }
        )
    rows.sort(key=lambda row: int(row["record_start"]))
    if len(rows) != EXPECTED_TARGETS:
        raise BuildError(f"target count drift: {len(rows)}")
    if len({str(row["ko"]) for row in rows}) != EXPECTED_PHRASES:
        raise BuildError("unique phrase count drift")
    if any(row["review_status"] != "approved" for row in rows):
        raise BuildError("unapproved translation in spec")
    return spec, rows


def choose_segment(
    inventory: Any,
    phrases: list[str],
    payloads_by_phrase: dict[str, bytes],
) -> tuple[int, list[int], int]:
    required = sum(len(payloads_by_phrase[phrase]) + 1 for phrase in phrases)
    for segment in PREFERRED_SEGMENTS:
        free = sorted(
            index
            for index in inventory.ext3_free
            if bank_local_for_index(index)[0] == segment
        )
        room = int(inventory.ext3_bank_room.get(segment - EXP3_SEG0, 0))
        if len(free) >= len(phrases) and room >= required:
            return segment, free[: len(phrases)], room
    raise BuildError(f"no ext3 bank has room for {required} bytes / {len(phrases)} phrases")


def main() -> int:
    parent = MAIN.read_bytes()
    live_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT:
        raise BuildError("promoted main TIP identity drifted")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    spec, rows = load_rows(parent, parent_dictionary, tbl)

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    phrases = sorted({str(row["ko"]) for row in rows})
    encoded_by_phrase = {phrase: encode_phrase(phrase, tbl) for phrase in phrases}
    segment, selected_indices, room = choose_segment(inventory, phrases, encoded_by_phrase)
    assignment = dict(zip(phrases, selected_indices))
    payloads = {assignment[phrase]: encoded_by_phrase[phrase] for phrase in phrases}

    candidate = bytearray(parent)
    cursor_before = phrase_cursor(bytes(slice_expansion_bank(parent, segment)))
    write_info, guard = write_ext3_slots_guarded(
        candidate,
        payloads,
        union=union,
        num_banks=num_banks,
    )
    if int(write_info.get("written") or 0) != len(payloads):
        raise BuildError("ext3 writer did not write every phrase")

    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        phrase = str(row["ko"])
        index = assignment[phrase]
        token = token_from_ext3_index(index, num_banks=num_banks)
        capacity = int(row["capacity"])
        replacement = token + b"\x01" * (capacity - len(token))
        body_start = base + int(row["record_start"]) + len(bytes(row["prefix"]))
        candidate[body_start : body_start + capacity] = replacement
        target_extents.append((body_start, body_start + capacity))
        applied.append(
            {
                "record_start": f"{int(row['record_start']):06X}",
                "family": row["family"],
                "bundle_start": row["bundle_start"],
                "line_role": row["line_role"],
                "prefix_hex": bytes(row["prefix"]).hex().upper(),
                "body_capacity": capacity,
                "before": row["before"],
                "jp": row["jp"],
                "ko": phrase,
                "translation_source": row["translation_source"],
                "ext3_index": f"{index:05X}",
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []
    for row in rows:
        start = int(row["record_start"])
        got = read_encoded_z_safe(candidate_bytes, base + start, max_len=128)
        if got is None:
            failures.append({"record_start": f"{start:06X}", "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        prefix = bytes(row["prefix"])
        actual = candidate_dictionary.expand(payload[len(prefix):], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        ok = (
            payload[: len(prefix)] == prefix
            and len(payload) == len(bytes(row["expected"]))
            and terminator == int(row["terminator"])
            and candidate_bytes[terminator] == 0
            and actual == expected
            and not any(is_japanese_character(character) for character in actual)
        )
        if not ok:
            failures.append(
                {
                    "record_start": f"{start:06X}",
                    "expected": expected,
                    "actual": actual,
                    "payload_hex": payload.hex().upper(),
                    "prefix_ok": payload[: len(prefix)] == prefix,
                    "terminator_ok": terminator == int(row["terminator"]),
                }
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["record_start"]) for row in rows},
    )
    protected = [validate_protected_table(candidate_bytes, table) for table in PROTECTED_TABLES]

    cursor_after = phrase_cursor(bytes(slice_expansion_bank(candidate_bytes, segment)))
    bank_file = segment * BANK_SIZE
    pointer_extents: list[tuple[int, int]] = []
    for index in payloads:
        _seg, local = bank_local_for_index(index)
        pointer_extents.append((bank_file + local * 2, bank_file + local * 2 + 2))
    allowed = target_extents + pointer_extents + [
        (bank_file + cursor_before, bank_file + cursor_after),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"file_start": f"{left:08X}", "file_end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    other_banks_unchanged = all(
        bytes(slice_expansion_bank(parent, candidate_segment))
        == bytes(slice_expansion_bank(candidate_bytes, candidate_segment))
        for candidate_segment in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if candidate_segment != segment
    )
    runtime_start = base + 0x7A0600
    runtime_end = base + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate_bytes[runtime_start:runtime_end]

    checks = {
        "all_targets_render_exact": not failures,
        "prefix_capacity_terminator_preserved": not failures,
        "non_target_invariance": invariance.get("ok") is True,
        "diffs_bounded": not unaccounted,
        "protected_structured_tables_exact": all(row.get("ok") is True for row in protected),
        "other_ext3_banks_unchanged": other_banks_unchanged,
        "runtime_hooks_unchanged": runtime_unchanged,
        "main_tip_unchanged": MAIN.read_bytes() == parent,
        "live_saveram_unchanged": MAIN_SAVE.read_bytes() == live_save,
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "failures": failures,
                    "unaccounted": unaccounted,
                    "invariance": invariance,
                },
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_runtime_text_screen_residual_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_user_runtime_test",
        "inputs": {
            "parent": identity(MAIN, parent),
            "original": identity(ORIGINAL, original),
            "live_saveram": identity(MAIN_SAVE, live_save),
            "spec": identity(SPEC),
        },
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "counts": {
            "targets": len(rows),
            "unique_phrases": len(phrases),
            "id_bundle_lines": sum(row["family"] == "id_command_bundle" for row in rows),
            "prefixed_dialogue": sum(row["family"] == "prefixed_dialogue" for row in rows),
            "voice_tagged": sum(row["family"] == "voice_tagged_run" for row in rows),
            "target_failures": len(failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "segment": f"{segment:02X}",
            "room_before": room,
            "cursor_before": f"{cursor_before:04X}",
            "cursor_after": f"{cursor_after:04X}",
            "phrase_bytes": cursor_after - cursor_before,
            "assignments": {phrase: f"{assignment[phrase]:05X}" for phrase in phrases},
        },
        "guard": guard.as_dict(),
        "checks": checks,
        "verification": {
            "target_failures": failures,
            "non_target_invariance": invariance,
            "protected_tables": protected,
            "unaccounted_diff_runs": unaccounted,
        },
        "diff": {
            "changed_bytes_vs_parent": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "records": applied,
        "promotion": "blocked_pending_independent_audit_and_user_runtime_confirmation",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "counts": report["counts"],
                "allocation": report["allocation"],
                "diff": report["diff"],
                "report": str(REPORT.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
