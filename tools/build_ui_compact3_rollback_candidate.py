#!/usr/bin/env python3
"""Replace the unsupported E5 19 UI/dialogue records with guarded stock tokens.

The accepted runtime has compact3 disabled.  The 2026-08-02 follow-up mistakenly
wrote ten E5 19 records, which the game renders as raw glyphs.  This candidate:

* allocates five candidate-bound retired stock slots for the five phrases;
* rewrites all ten E5 19 bodies as ordinary 2-byte stock tokens plus the original
  one-byte padding;
* installs two otherwise-identical E5 18 walkers with one narrow exception: the
  dedicated 범용 token consumes a following 0x01 padding byte without rendering
  it.  Record length, terminator, and every following string start stay fixed;
* preserves the existing ext3 leaf, font hooks and expansion dictionary;
* copies the current live ``monoeye_ko_expanded.sav`` to the candidate stem
  without pinning or validating its hash.  That file is mutable test progress,
  so whatever exists at build time is the latest source of truth.

Candidate only.  It never overwrites the main TIP or main SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_p2_stock_spill_candidate import _stock_phrase_cursor
from expand_dictionary import write_dictionary_slots_spill
from extract_script import split_prefix_body
from hangul_marker import marker_code
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import (
    CAVE3,
    CAVE3_MAX,
    CODE_SEG_7A,
    EXT_CAVE_SEG,
    HANGUL_FAR_STUB,
    HOOK_LEN,
    INDEX_BASE,
    LEAF,
    MAGIC,
    SITE1,
    SITE1_MOVES,
    SITE1_RETURN,
    SITE2_MOVES,
    far_jmp,
    find_site2,
    sab,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SOURCE_REPORT = ROOT / "out/patch/ui_menu_dialogue_followup_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ui_compact3_rollback_candidate.wsc"
OUT_SAVE = ROOT / "sram/ui_compact3_rollback_candidate.sav"
OUT_REPORT = ROOT / "out/patch/ui_compact3_rollback_report.json"

EXPECTED_MAIN_SHA256 = "ec295935607b4843bc654c2709995262bade543d6c0be64556a45b6b240d4833"
ROM_SIZE = 16_777_216
APPEND_OFFSET = 232

# Exactly the unsupported records introduced by the previous follow-up.
TARGETS = {
    0x75B3CA: ("범용", "terrain_fit", False),
    0x75B321: ("불러오기", "menu", False),
    0x75B325: ("저장", "menu", False),
    0x5F2787: ("저장", "menu", False),
    0x5F2793: ("불러오기", "menu", False),
    0x5F2DD1: ("저장", "menu", False),
    0x5F2DE4: ("불러오기", "menu", False),
    0x600E66: ("하하하핫！", "dialogue_second_line", True),
    0x600EA6: ("하하하핫！", "dialogue_second_line", True),
    0x6028A4: ("감사히　받아두겠습니다。", "dialogue_second_line", True),
}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(text: str, tbl: Tbl) -> tuple[str, bytes]:
    normalized = normalize_ko_text(text)
    payload = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or not payload or b"\x00" in payload:
        raise BuildError(f"cannot encode phrase: {text!r}")
    return normalized, payload


def patch_rel8(buf: bytearray, at: int, target: int) -> None:
    disp = target - (at + 2)
    if not -128 <= disp <= 127:
        raise BuildError(f"rel8 out of range: {disp}")
    buf[at + 1] = disp & 0xFF


def build_pad_skip_walker(moves: bytes, return_ip: int, skip_word: int) -> bytes:
    """Legacy E5 18 walker plus one token-specific trailing-01 skip."""
    out = bytearray()
    out += b"\x81\xFA" + struct.pack("<H", MAGIC)
    not_ext3_at = len(out)
    out += b"\x75\x00"
    out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x27"
    out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x07"
    out += b"\x89\xC3"
    out += b"\x81\xC3" + struct.pack("<H", INDEX_BASE)
    out += b"\x89\x1E\xF8\x19"
    out += b"\xC6\x06\xFA\x19\x01"
    out += b"\xBA\x00\xF0"
    common = len(out)
    patch_rel8(out, not_ext3_at, common)

    # Only the dedicated 범용 token takes this branch.  [bp-8] already points
    # one byte past the 2-byte token at both hook sites.  Verify that byte is
    # the expected 0x01 padding before consuming it, then continue through the
    # unchanged stock register moves and ext3 leaf call path.
    out += b"\x81\xFA" + struct.pack("<H", skip_word)
    not_special_at = len(out)
    out += b"\x75\x00"
    out += b"\xC4\x5E\xF8"           # les bx,[bp-8]
    out += b"\x26\x80\x3F\x01"     # cmp byte ptr es:[bx],1
    not_padding_at = len(out)
    out += b"\x75\x00"
    out += b"\xFF\x46\xF8"           # inc word ptr [bp-8]
    normal = len(out)
    patch_rel8(out, not_special_at, normal)
    patch_rel8(out, not_padding_at, normal)

    out += moves
    out += b"\x9A" + struct.pack("<HH", HANGUL_FAR_STUB & 0xFFFF, CODE_SEG_7A)
    out += far_jmp(return_ip & 0xFFFF, CODE_SEG_7A)
    return bytes(out)


def diff_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(before):
        if before[cursor] == after[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < len(before) and before[cursor] != after[cursor]:
            cursor += 1
        rows.append(
            {
                "start": f"{start:06X}",
                "end": f"{cursor:06X}",
                "length": cursor - start,
                "before_hex": before[start : min(cursor, start + 24)].hex().upper(),
                "after_hex": after[start : min(cursor, start + 24)].hex().upper(),
            }
        )
    return rows


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return [(start, end) for start, end in result]


def historical_abs(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("record_abs", value.get("abs"))
    if isinstance(value, str):
        return int(value, 16)
    return int(value)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file():
        raise BuildError("live main SaveRAM is missing; cannot create the test-ROM pair")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    if ext3_meta.get("compact3") is not False:
        raise BuildError("accepted runtime metadata no longer says compact3=false")
    d_original = Dictionary(original)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    source_report = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    report_compact = {
        int(str(row["abs"]), 16): row
        for row in source_report.get("records") or []
        if row.get("strategy") == "compact3"
    }
    if set(report_compact) != set(TARGETS):
        raise BuildError(
            f"compact3 target set drifted: {sorted(report_compact)} != {sorted(TARGETS)}"
        )

    target_rows: list[dict[str, Any]] = []
    for logical, (text, category, dialogue) in TARGETS.items():
        got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        if got is None:
            raise BuildError(f"unreadable target {logical:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        if dialogue:
            prefix, body, kind = split_prefix_body(payload)
            if kind != "dialogue":
                prefix, body = b"", payload
        else:
            prefix, body, kind = b"", payload, "direct"
        if len(body) != 3 or body[:2] != b"\xE5\x19":
            raise BuildError(
                f"unsupported record drifted at {logical:06X}: {body.hex().upper()}"
            )
        normalized, encoded = encode(text, tbl)
        target_rows.append(
            {
                "logical": logical,
                "abs": f"{logical:06X}",
                "category": category,
                "dialogue_kind": kind,
                "prefix": prefix,
                "prefix_hex": prefix.hex().upper(),
                "payload": payload,
                "payload_hex": payload.hex().upper(),
                "body": body,
                "body_hex": body.hex().upper(),
                "terminator": terminator,
                "ko": normalized,
                "encoded": encoded,
            }
        )

    phrases: dict[str, bytes] = {}
    for row in target_rows:
        phrases.setdefault(row["ko"], row["encoded"])
    if len(phrases) != 5:
        raise BuildError(f"expected five unique phrases, found {len(phrases)}")

    # Candidate-bound retired slot proof.  A selected slot must be absent from
    # every current external/nested/raw reference, retain its vanilla pointer
    # and payload, and have at least one historical Original consumer.
    wanted = {
        index
        for index in range(min(d_original.stock_count, 0xF00))
        if dict_token_safe_in_zstring(index)
    }
    original_external = external_occurrence_map(original, ext3_aware=False, wanted=wanted)
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    original_nested = nested_occurrence_map(d_original, wanted=wanted, ext3_aware=False)
    current_nested = nested_occurrence_map(d_parent, wanted=wanted, ext3_aware=True)
    preliminary: list[dict[str, Any]] = []
    for index in sorted(wanted):
        if current_external.get(index) or current_nested.get(index) or original_nested.get(index):
            continue
        historical = list(original_external.get(index) or [])
        if not historical:
            continue
        try:
            vanilla_payload = bytes(d_original.raw_entry(index))
            current_payload = bytes(d_parent.raw_entry(index))
        except Exception:
            continue
        if d_original.ptrs[index] != d_parent.ptrs[index]:
            continue
        if vanilla_payload != current_payload:
            continue
        preliminary.append(
            {
                "index": index,
                "old_pointer": d_parent.ptrs[index],
                "old_payload": current_payload,
                "historical": historical,
            }
        )
    raw_hits = _raw_pair_hits(parent, [row["index"] for row in preliminary])
    strong = [row for row in preliminary if not raw_hits.get(row["index"])]
    strong.sort(key=lambda row: (len(row["historical"]), row["index"]))
    if len(strong) < len(phrases):
        raise BuildError(f"need {len(phrases)} retired slots, found {len(strong)}")

    phrase_order = ["범용", "저장", "불러오기", "하하하핫！", "감사히　받아두겠습니다。"]
    slot_payload: dict[int, bytes] = {}
    phrase_to_slot: dict[str, int] = {}
    retired_proof: list[dict[str, Any]] = []
    historical_by_slot: dict[int, set[int]] = {}
    for evidence, phrase in zip(strong, phrase_order):
        index = int(evidence["index"])
        phrase_to_slot[phrase] = index
        slot_payload[index] = phrases[phrase]
        historical = {historical_abs(value) for value in evidence["historical"]}
        historical_by_slot[index] = historical
        retired_proof.append(
            {
                "index": f"{index:04X}",
                "token_hex": token_from_dict_index(index).hex().upper(),
                "phrase": phrase,
                "old_pointer": f"{int(evidence['old_pointer']):04X}",
                "old_payload_hex": bytes(evidence["old_payload"]).hex().upper(),
                "historical_original_consumers": [
                    f"{value:06X}" for value in sorted(historical)
                ],
                "current_external_consumers": 0,
                "current_nested_consumers": 0,
                "current_raw_pair_hits": 0,
            }
        )

    general_slot = phrase_to_slot["범용"]
    general_token = token_from_dict_index(general_slot)
    general_word = int.from_bytes(general_token, "big")

    site2, site2_return = find_site2(parent)
    walker1 = build_pad_skip_walker(SITE1_MOVES, SITE1_RETURN, general_word)
    walker2 = build_pad_skip_walker(SITE2_MOVES, site2_return, general_word)
    walker_blob = walker1 + walker2
    walker1_offset = APPEND_OFFSET
    walker2_offset = APPEND_OFFSET + len(walker1)
    append_end = APPEND_OFFSET + len(walker_blob)
    if append_end > CAVE3_MAX:
        raise BuildError(f"walker tail exceeds cave: {append_end} > {CAVE3_MAX}")

    cave_file = sab(parent, CAVE3)
    old_cave = parent[cave_file : cave_file + CAVE3_MAX]
    if not all(byte == 0xFF for byte in old_cave[APPEND_OFFSET:append_end]):
        raise BuildError("walker append range is not all FF")
    legacy_prefix = bytes(old_cave[:APPEND_OFFSET])
    leaf_hook_before = bytes(parent[sab(parent, LEAF) : sab(parent, LEAF) + 6])

    candidate = bytearray(parent)
    stock_cursor_before = _stock_phrase_cursor(parent)
    _pointers, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        slot_payload,
        allow_aux_consumers=True,
    )

    applied: list[dict[str, Any]] = []
    expected_current_consumers: dict[int, set[int]] = defaultdict(set)
    for row in target_rows:
        index = phrase_to_slot[row["ko"]]
        token = token_from_dict_index(index)
        replacement_body = token + b"\x01"
        start = sb + row["logical"] + len(row["prefix"])
        candidate[start : start + 3] = replacement_body
        if candidate[row["terminator"]] != 0:
            raise BuildError(f"terminator changed at {row['abs']}")
        expected_current_consumers[index].add(row["logical"])
        applied.append(
            {
                "abs": row["abs"],
                "category": row["category"],
                "ko": row["ko"],
                "prefix_hex": row["prefix_hex"],
                "before_payload_hex": row["payload_hex"],
                "before_body_hex": row["body_hex"],
                "slot": f"{index:04X}",
                "token_hex": token.hex().upper(),
                "after_body_hex": replacement_body.hex().upper(),
                "padding_behavior": (
                    "runtime_skip_only_for_general"
                    if row["logical"] == 0x75B3CA
                    else "rendered_fullwidth_space"
                ),
            }
        )

    candidate[cave_file + APPEND_OFFSET : cave_file + append_end] = walker_blob
    candidate[sab(candidate, SITE1) : sab(candidate, SITE1) + HOOK_LEN] = far_jmp(
        (CAVE3 + walker1_offset) & 0xFFFF, EXT_CAVE_SEG
    )
    candidate[sab(candidate, site2) : sab(candidate, site2) + HOOK_LEN] = far_jmp(
        (CAVE3 + walker2_offset) & 0xFFFF, EXT_CAVE_SEG
    )
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    if candidate_bytes[cave_file : cave_file + APPEND_OFFSET] != legacy_prefix:
        raise BuildError("legacy ext3 cave changed")
    leaf_hook_after = bytes(
        candidate_bytes[sab(candidate_bytes, LEAF) : sab(candidate_bytes, LEAF) + 6]
    )
    if leaf_hook_after != leaf_hook_before:
        raise BuildError("ext3 leaf hook changed")

    d_candidate = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    decode_failures: list[dict[str, Any]] = []
    for source, result in zip(target_rows, applied):
        got = read_encoded_z_safe(candidate_bytes, sb + source["logical"], max_len=256)
        if got is None:
            decode_failures.append({"abs": source["abs"], "reason": "unreadable"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        if terminator != source["terminator"] or len(payload) != len(source["payload"]):
            decode_failures.append({"abs": source["abs"], "reason": "boundary"})
            continue
        if payload[: len(source["prefix"])] != source["prefix"]:
            decode_failures.append({"abs": source["abs"], "reason": "prefix"})
            continue
        body = payload[len(source["prefix"]) :]
        rendered = d_candidate.expand(body, tbl)
        result["rendered_static"] = rendered
        expected_static = source["ko"] + "　"
        result["static_ok"] = rendered == expected_static
        if not result["static_ok"]:
            decode_failures.append(
                {
                    "abs": source["abs"],
                    "reason": "render",
                    "expected": expected_static,
                    "actual": rendered,
                }
            )
    if decode_failures:
        raise BuildError(f"decode failures: {decode_failures[:10]}")

    # The unsupported portal must be gone from all ten record bodies.  Raw E519
    # pairs elsewhere can be vanilla data/glyph bytes and are not interpreted as
    # compact3 because the accepted runtime remains compact3=false.
    residual_e519: list[str] = []
    for row in target_rows:
        got = read_encoded_z_safe(candidate_bytes, sb + row["logical"], max_len=256)
        payload = bytes(got[0]) if got else b""
        if b"\xE5\x19" in payload:
            residual_e519.append(row["abs"])
    if residual_e519:
        raise BuildError(f"target E519 residuals: {residual_e519}")

    candidate_union = build_reference_union(
        original,
        candidate_bytes,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    consumer_checks: list[dict[str, Any]] = []
    for index, expected in sorted(expected_current_consumers.items()):
        actual_rows = candidate_union.consumers_for(index)
        actual = sorted({consumer.abs for consumer in actual_rows})
        historical = historical_by_slot[index]

        def accounted(value: int) -> bool:
            if value in historical or value in expected:
                return True
            return any(0 <= target - value <= 8 for target in expected)

        unexpected = [value for value in actual if not accounted(value)]
        current_visible = [
            value
            for value in actual
            if value not in historical
        ]
        missing = [
            value
            for value in sorted(expected)
            if value not in actual
            and not any(0 <= value - start <= 8 for start in actual)
        ]
        check = {
            "index": f"{index:04X}",
            "expected_current": [f"{value:06X}" for value in sorted(expected)],
            "historical_original": [f"{value:06X}" for value in sorted(historical)],
            "union_actual": [f"{value:06X}" for value in actual],
            "current_visible": [f"{value:06X}" for value in current_visible],
            "unexpected": [f"{value:06X}" for value in unexpected],
            "missing_from_union_scope": [f"{value:06X}" for value in missing],
            "ok": not unexpected,
        }
        consumer_checks.append(check)
    if any(not row["ok"] for row in consumer_checks):
        raise BuildError("dictionary consumer proof failed")

    # Changed-byte accounting: target bodies, selected stock pointers/append
    # phrase extent, two hook sites, appended walkers and checksum only.
    dict_bank = sb + SEG_DICT * BANK_SIZE
    intervals: list[tuple[int, int]] = []
    for row in target_rows:
        start = sb + row["logical"] + len(row["prefix"])
        intervals.append((start, start + 3))
    for index in slot_payload:
        pointer = dict_bank + DICT_PTR_START + index * 2
        intervals.append((pointer, pointer + 2))
    intervals.append((dict_bank + stock_cursor_before, dict_bank + stock_cursor_after))
    intervals.extend(
        [
            (cave_file + APPEND_OFFSET, cave_file + append_end),
            (sab(parent, SITE1), sab(parent, SITE1) + HOOK_LEN),
            (sab(parent, site2), sab(parent, site2) + HOOK_LEN),
            (len(parent) - 2, len(parent)),
        ]
    )
    intervals = merged(intervals)
    runs = diff_runs(parent, candidate_bytes)
    unaccounted: list[int] = []
    for run in runs:
        start, end = int(run["start"], 16), int(run["end"], 16)
        for offset in range(start, end):
            if not any(lo <= offset < hi for lo, hi in intervals):
                unaccounted.append(offset)
                if len(unaccounted) >= 30:
                    break
        if unaccounted:
            break
    if unaccounted:
        raise BuildError(
            "unaccounted changes: " + ", ".join(f"{value:06X}" for value in unaccounted)
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ui_compact3_rollback_candidate.py",
        "status": "candidate_static_verified_needs_target_screen_check",
        "ok": True,
        "main_tip_modified": False,
        "parent": {
            "path": str(MAIN.relative_to(ROOT)).replace("\\", "/"),
            "size": len(parent),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "size": len(candidate_bytes),
            "sha256": sha256(candidate_bytes),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "source": str(MAIN_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "policy": "copy_live_main_sav_at_build_time",
            "copied_latest": True,
            "hash_verification_skipped": True,
        },
        "runtime": {
            "compact3_enabled": False,
            "unsupported_e519_targets_removed": len(target_rows),
            "general_skip_token_index": f"{general_slot:04X}",
            "general_skip_token_hex": general_token.hex().upper(),
            "skip_contract": "consume one following byte only when it is 0x01",
            "site1": f"{SITE1:06X}",
            "site2": f"{site2:06X}",
            "walker1": f"{CAVE3 + walker1_offset:06X}",
            "walker2": f"{CAVE3 + walker2_offset:06X}",
            "walker1_bytes": len(walker1),
            "walker2_bytes": len(walker2),
            "append_offset": APPEND_OFFSET,
            "append_end": append_end,
            "legacy_cave_prefix_preserved": True,
            "legacy_ext3_leaf_preserved": True,
            "legacy_leaf_hook_hex": leaf_hook_before.hex().upper(),
        },
        "dictionary": {
            "selected_retired_slots": len(slot_payload),
            "stock_phrase_cursor_before": f"{stock_cursor_before:04X}",
            "stock_phrase_cursor_after": f"{stock_cursor_after:04X}",
            "proof": retired_proof,
            "consumer_checks": consumer_checks,
        },
        "records": applied,
        "verification": {
            "decode_failures": decode_failures,
            "target_e519_residuals": residual_e519,
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "dialogue_prefixes_preserved": True,
            "unaccounted_changed_bytes": len(unaccounted),
            "diff_runs": len(runs),
            "diff_bytes": sum(int(row["length"]) for row in runs),
        },
        "visual_verification": {
            "required": True,
            "reason": "available BizHawk state does not reach the reported battle status/save-load screens",
            "expected": [
                "범용 renders as two Hangul glyphs and the right blue border remains intact",
                "저장 and 불러오기 render as Korean without raw E5 19 glyphs",
            ],
        },
        "diff_runs": runs,
    }

    OUT_ROM.write_bytes(candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "candidate": report["candidate"],
                "save": report["save"],
                "runtime": report["runtime"],
                "verification": report["verification"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
