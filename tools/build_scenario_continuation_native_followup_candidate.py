#!/usr/bin/env python3
"""Build a focused scenario-continuation native-grammar follow-up candidate.

Runtime-proven fixes:
- 63449B: Four/Zero Murasame radio scene.  The continuation is currently
  ``18 + E5 18`` and leaks a bogus hiragana follow row (gakehau-like symptom).
- 635855 / 635BFB: duplicated Doctor J STAGE21t line.  The same direct ext3
  continuation corrupts the following ``그건 아니지만。`` row.

Proactive scope:
- Restore 21 additional 5-byte continuations whose pristine grammar is exactly
  ``18 + native stock token + native stock token`` and whose current Korean
  text can be represented by two already-live stock tokens without allocating
  any new phrase.

Only ten current-zero-reference stock ids are repurposed for small native
helper chains needed by the three runtime-proven records.  Their phrase storage
is packed exclusively into zero-reference stock phrase extents proven not to
overlap any live stock entry.  Main TIP and live SaveRAM are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_battle_dialogue_runtime_integrated_cleanup_candidate import clean  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_scenario_page_boundary_guard_candidate import original_unit_kinds  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Tbl,
    dict_token_safe_in_zstring,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "scenario_continuation_native_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/scenario_continuation_native_followup_candidate.sav"
REPORT = PATCH / "scenario_continuation_native_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "2bccff99a36cd453bf01225365a40ed4d744049e69d46e726ab427851afa1799"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
HANGUL_MARKER = 0xEC8D

FOUR_ZERO = 0x63449B
DOCTOR_J = (0x635855, 0x635BFB)
EXPECTED_RUNTIME_CURRENT = {
    0x63449B: bytes.fromhex("18E51841F1"),
    0x635855: bytes.fromhex("18E5184BB80101010101010101010101"),
    0x635BFB: bytes.fromhex("18E5181BB40101010101010101010101"),
}
EXPECTED_RUNTIME_TEXT = {
    0x63449B: "어째서……",
    0x635855: "……뭐、　승산　좋은　도박？",
    0x635BFB: "……뭐、　승산　좋은　도박？",
}

# Same 5-byte risk shape, but no helper/new dictionary phrase is required.
# 624305 is deliberately excluded because its only two-token split currently
# depends on a zero-reference stock id that is part of the reclaim pool.
PROACTIVE_TWO_TOKEN = (
    0x603048,
    0x604E06,
    0x605880,
    0x6102D5,
    0x612722,
    0x61B667,
    0x61D8C9,
    0x620925,
    0x6211F2,
    0x626CD5,
    0x62726A,
    0x62ADC8,
    0x62B041,
    0x62CDF2,
    0x63451F,
    0x63E9C0,
    0x63EE5E,
    0x63EE72,
    0x63EE79,
    0x63EE8C,
    0x63EE93,
)

# Ten 4-byte native payloads.  Nested chains deliberately mirror already-live
# stock entries such as F017 E7E9 (nested token + direct Hangul glyph).
HELPER_ORDER = (
    "어",
    "어째",
    "어째서",
    "뭐",
    "승",
    "승산",
    "좋",
    "좋은",
    "도",
    "도박",
)
HELPER_PARENT_TAIL: dict[str, tuple[str, str] | None] = {
    "어": None,
    "어째": ("어", "째"),
    "어째서": ("어째", "서"),
    "뭐": None,
    "승": None,
    "승산": ("승", "산"),
    "좋": None,
    "좋은": ("좋", "은"),
    "도": None,
    "도박": ("도", "박"),
}


class BuildError(RuntimeError):
    pass


def sha(payload: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(payload)).hexdigest()


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def encode_run(tbl: Tbl, text: str) -> bytes:
    raw = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=HANGUL_MARKER,
        hangul_marker_mode="run",
    )
    if raw is None or not raw or b"\x00" in raw:
        raise BuildError(f"cannot encode helper {text!r}")
    return bytes(raw)


def current_zero_reference_pool(parent: bytes, dictionary: Any) -> tuple[list[int], list[tuple[int, int]]]:
    safe_indices = [i for i in range(dictionary.stock_count) if dict_token_safe_in_zstring(i)]
    wanted = set(safe_indices)
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(dictionary, wanted=wanted, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, safe_indices)
    free = [
        i
        for i in safe_indices
        if not external.get(i) and not nested.get(i) and not raw_hits.get(i)
    ]
    extents = {
        i: (dictionary.ptrs[i], dictionary.ptrs[i] + len(bytes(dictionary.raw_entry(i))) + 1)
        for i in safe_indices
    }
    free_set = set(free)
    clean_free: list[int] = []
    for i in free:
        left, right = extents[i]
        overlap_live = False
        for j, (other_left, other_right) in extents.items():
            if j == i or j in free_set:
                continue
            if other_left < right and left < other_right:
                overlap_live = True
                break
        if not overlap_live:
            clean_free.append(i)
    raw_regions = sorted(extents[i] for i in clean_free if extents[i][1] > extents[i][0])
    merged: list[list[int]] = []
    for left, right in raw_regions:
        if not merged or left > merged[-1][1]:
            merged.append([left, right])
        else:
            merged[-1][1] = max(merged[-1][1], right)
    return sorted(clean_free), [(left, right) for left, right in merged]


def live_exact_text_map(dictionary: Any, tbl: Tbl, *, excluded: set[int]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for index in range(dictionary.stock_count):
        if index in excluded or not dict_token_safe_in_zstring(index):
            continue
        try:
            text = clean(dictionary.expand_index(index, tbl))
            token = token_from_dict_index(index)
        except Exception:
            continue
        if not text or 0 in token:
            continue
        out.setdefault(text, []).append(index)
    for text in out:
        out[text].sort()
    return out


def choose_two_token_split(text: str, exact: dict[str, list[int]]) -> tuple[str, int, str, int]:
    options: list[tuple[int, int, int, str, str]] = []
    for split in range(1, len(text)):
        left = text[:split]
        right = text[split:]
        if left not in exact or right not in exact:
            continue
        options.append((exact[left][0], exact[right][0], split, left, right))
    if not options:
        raise BuildError(f"no two-live-token split for {text!r}")
    i1, i2, _split, left, right = min(options)
    return left, i1, right, i2


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if len(original) != 8_388_608 or sha(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("pristine Japanese ROM identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")

    tbl = Tbl.load(TBL_PATH)
    if (
        tbl.code_to_char.get(0x01) != "　"
        or tbl.code_to_char.get(0x07) != "、"
        or tbl.code_to_char.get(0x18) != "こ"
        or tbl.code_to_char.get(0x1D) != "？"
        or tbl.code_to_char.get(HANGUL_MARKER) != ""
    ):
        raise BuildError("TBL structural mapping drifted")

    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    jp_dictionary = make_dictionary_ext3(original, {}, None)
    sb = stock_base(parent)
    osb = stock_base(original)

    clean_free, merged_regions = current_zero_reference_pool(parent, dictionary)
    if len(clean_free) < len(HELPER_ORDER):
        raise BuildError(f"insufficient current-zero-reference stock ids: {len(clean_free)}")
    if sum(right - left for left, right in merged_regions) < 50:
        raise BuildError("insufficient zero-reference phrase storage")
    helper_ids = dict(zip(HELPER_ORDER, clean_free[: len(HELPER_ORDER)]))
    free_set = set(clean_free)
    exact = live_exact_text_map(dictionary, tbl, excluded=free_set)

    # Build ten 4-byte helper payloads.  For nested phrases, a child stock token
    # supplies the Hangul marker and the tail is a direct 2-byte Hangul glyph.
    helper_payloads: dict[str, bytes] = {}
    for text in HELPER_ORDER:
        parent_tail = HELPER_PARENT_TAIL[text]
        if parent_tail is None:
            raw = encode_run(tbl, text)
            if len(text) != 1 or len(raw) != 4 or raw[:2] != HANGUL_MARKER.to_bytes(2, "big"):
                raise BuildError(f"single helper encoding drift {text!r}: {raw.hex().upper()}")
            payload = raw
        else:
            parent_text, tail = parent_tail
            raw_tail = encode_run(tbl, tail)
            if len(raw_tail) != 4 or raw_tail[:2] != HANGUL_MARKER.to_bytes(2, "big"):
                raise BuildError(f"tail helper encoding drift {text!r}")
            payload = token_from_dict_index(helper_ids[parent_text]) + raw_tail[2:]
        if len(payload) != 4 or b"\x00" in payload or b"\xE5\x18" in payload or b"\xE5\x19" in payload:
            raise BuildError(f"invalid native helper payload {text!r}: {payload.hex().upper()}")
        helper_payloads[text] = payload

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    stock_bank_file = sb + SEG_DICT * BANK_SIZE

    # Pack 10 x (4-byte payload + NUL) into the proven zero-reference regions.
    bins = [{"cursor": left, "end": right} for left, right in merged_regions]
    helper_storage: dict[str, int] = {}
    storage_rows: list[dict[str, Any]] = []
    for text in HELPER_ORDER:
        payload = helper_payloads[text]
        need = len(payload) + 1
        eligible = [
            (info["end"] - info["cursor"], bin_id)
            for bin_id, info in enumerate(bins)
            if info["end"] - info["cursor"] >= need
        ]
        if not eligible:
            raise BuildError(f"fragmented zero-reference pool cannot place {text!r}")
        _remaining, bin_id = min(eligible)
        info = bins[bin_id]
        local = info["cursor"]
        info["cursor"] += need
        helper_storage[text] = local
        file_at = stock_bank_file + local
        candidate[file_at : file_at + len(payload)] = payload
        candidate[file_at + len(payload)] = 0
        allowed.append((file_at, file_at + need))
        storage_rows.append(
            {
                "text": text,
                "stock_index": f"{helper_ids[text]:04X}",
                "pointer": f"{local:04X}",
                "payload_hex": payload.hex().upper(),
                "extent": [f"{file_at:07X}", f"{file_at + need:07X}"],
            }
        )

    pointer_rows: list[dict[str, Any]] = []
    for text in HELPER_ORDER:
        index = helper_ids[text]
        pointer = helper_storage[text]
        pointer_at = stock_bank_file + DICT_PTR_START + index * 2
        before_ptr = dictionary.ptrs[index]
        struct.pack_into("<H", candidate, pointer_at, pointer)
        allowed.append((pointer_at, pointer_at + 2))
        pointer_rows.append(
            {
                "text": text,
                "stock_index": f"{index:04X}",
                "before_pointer": f"{before_ptr:04X}",
                "after_pointer": f"{pointer:04X}",
                "pointer_abs": f"{pointer_at:07X}",
            }
        )

    # Existing-token-only proactive repairs.
    proactive_rows: list[dict[str, Any]] = []
    for logical in PROACTIVE_TWO_TOKEN:
        current, term = read_record(parent, logical)
        pristine, pristine_term = read_record(original, logical)
        if term != pristine_term or len(current) != 5 or len(pristine) != 5:
            raise BuildError(f"5-byte extent/terminator drift at {logical:06X}")
        if current[:3] != b"\x18\xE5\x18":
            raise BuildError(f"current risk shape drift at {logical:06X}: {current.hex().upper()}")
        if pristine[0] != 0x18 or original_unit_kinds(pristine) != ["char1", "dict", "dict"]:
            raise BuildError(f"pristine native pair drift at {logical:06X}")
        wanted = clean(dictionary.expand(current[1:], tbl))
        left, i1, right, i2 = choose_two_token_split(wanted, exact)
        after = b"\x18" + token_from_dict_index(i1) + token_from_dict_index(i2)
        if len(after) != 5 or original_unit_kinds(after) != ["char1", "dict", "dict"]:
            raise BuildError(f"native pair build failed at {logical:06X}")
        start = sb + logical
        candidate[start : start + 5] = after
        allowed.append((start, start + 5))
        proactive_rows.append(
            {
                "abs": f"{logical:06X}",
                "before_hex": current.hex().upper(),
                "after_hex": after.hex().upper(),
                "wanted": wanted,
                "parts": [
                    {"text": left, "stock_index": f"{i1:04X}"},
                    {"text": right, "stock_index": f"{i2:04X}"},
                ],
                "terminator": f"{term:06X}",
            }
        )

    # Four/Zero Murasame: preserve control 18 and pristine two-native-token shape.
    current, term = read_record(parent, FOUR_ZERO)
    pristine, pristine_term = read_record(original, FOUR_ZERO)
    if current != EXPECTED_RUNTIME_CURRENT[FOUR_ZERO] or term != pristine_term:
        raise BuildError("63449B parent/terminator drift")
    wanted = clean(dictionary.expand(current[1:], tbl))
    if wanted != EXPECTED_RUNTIME_TEXT[FOUR_ZERO]:
        raise BuildError(f"63449B semantic drift: {wanted!r}")
    ellipsis_index = min(exact.get("……") or [])
    after_four = (
        b"\x18"
        + token_from_dict_index(helper_ids["어째서"])
        + token_from_dict_index(ellipsis_index)
    )
    if len(after_four) != len(current) or original_unit_kinds(after_four) != original_unit_kinds(pristine):
        raise BuildError("63449B native grammar restore failed")
    start = sb + FOUR_ZERO
    candidate[start : start + len(current)] = after_four
    allowed.append((start, start + len(current)))

    # Doctor J duplicated line: retain leading control 18, remove direct ext3,
    # and fill the 15-byte body with native tokens + original punctuation/space codes.
    drj_rows: list[dict[str, Any]] = []
    drj_after = (
        b"\x18"
        + token_from_dict_index(ellipsis_index)
        + token_from_dict_index(helper_ids["뭐"])
        + b"\x07\x01"
        + token_from_dict_index(helper_ids["승산"])
        + b"\x01"
        + token_from_dict_index(helper_ids["좋은"])
        + b"\x01"
        + token_from_dict_index(helper_ids["도박"])
        + b"\x1D"
    )
    if len(drj_after) != 16 or b"\xE5\x18" in drj_after:
        raise BuildError(f"Doctor J native plan drift: {drj_after.hex().upper()}")
    for logical in DOCTOR_J:
        current, term = read_record(parent, logical)
        pristine, pristine_term = read_record(original, logical)
        if current != EXPECTED_RUNTIME_CURRENT[logical] or term != pristine_term:
            raise BuildError(f"Doctor J parent/terminator drift {logical:06X}")
        wanted = clean(dictionary.expand(current[1:], tbl))
        if wanted != EXPECTED_RUNTIME_TEXT[logical]:
            raise BuildError(f"Doctor J semantic drift {logical:06X}: {wanted!r}")
        if pristine[0] != 0x18 or current[0] != 0x18:
            raise BuildError(f"Doctor J control-18 drift {logical:06X}")
        start = sb + logical
        candidate[start : start + len(current)] = drj_after
        allowed.append((start, start + len(current)))
        drj_rows.append(
            {
                "abs": f"{logical:06X}",
                "before_hex": current.hex().upper(),
                "after_hex": drj_after.hex().upper(),
                "wanted": wanted,
                "terminator": f"{term:06X}",
            }
        )

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    final_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # Validate helper closure and runtime target renders without treating control 18 as text.
    helper_verify: list[dict[str, Any]] = []
    for text in HELPER_ORDER:
        index = helper_ids[text]
        rendered = clean(final_dictionary.expand_index(index, tbl))
        if rendered != text:
            raise BuildError(f"helper render mismatch {text!r}: {rendered!r}")
        helper_verify.append(
            {"text": text, "stock_index": f"{index:04X}", "rendered": rendered}
        )

    for row in proactive_rows:
        logical = int(row["abs"], 16)
        payload, _term = read_record(result, logical)
        if payload[0] != 0x18 or b"\xE5\x18" in payload[1:]:
            raise BuildError(f"proactive record still special at {logical:06X}")
        rendered = clean(final_dictionary.expand(payload[1:], tbl))
        if rendered != row["wanted"]:
            raise BuildError(f"proactive render mismatch {logical:06X}: {rendered!r}")

    payload, _ = read_record(result, FOUR_ZERO)
    four_render = clean(final_dictionary.expand(payload[1:], tbl))
    if payload != after_four or four_render != EXPECTED_RUNTIME_TEXT[FOUR_ZERO] or b"\xE5\x18" in payload[1:]:
        raise BuildError("63449B final validation failed")

    for logical in DOCTOR_J:
        payload, _ = read_record(result, logical)
        rendered = clean(final_dictionary.expand(payload[1:], tbl))
        if payload != drj_after or rendered != EXPECTED_RUNTIME_TEXT[logical] or b"\xE5\x18" in payload[1:]:
            raise BuildError(f"Doctor J final validation failed {logical:06X}: {rendered!r}")

    # Following Doctor J row must remain byte-exact and render normally.
    next_rows: list[dict[str, Any]] = []
    for logical in (0x635866, 0x635C0C):
        before, before_term = read_record(parent, logical)
        after, after_term = read_record(result, logical)
        if before != after or before_term != after_term:
            raise BuildError(f"Doctor J following row changed {logical:06X}")
        rendered = clean(final_dictionary.expand(after, tbl))
        if rendered != "그건　아니지만。":
            raise BuildError(f"Doctor J following row render drift {logical:06X}: {rendered!r}")
        next_rows.append(
            {
                "abs": f"{logical:06X}",
                "payload_hex": after.hex().upper(),
                "rendered": rendered,
                "byte_exact_parent": True,
            }
        )

    # Helper ids must now be referenced only through the intended records/helper chain.
    selected = set(helper_ids.values())
    external_after = external_occurrence_map(result, ext3_aware=True, wanted=selected)
    nested_after = nested_occurrence_map(final_dictionary, wanted=selected, ext3_aware=True)
    reference_rows: list[dict[str, Any]] = []
    for text in HELPER_ORDER:
        index = helper_ids[text]
        reference_rows.append(
            {
                "text": text,
                "stock_index": f"{index:04X}",
                "external_refs": external_after.get(index, []),
                "nested_refs": nested_after.get(index, []),
            }
        )
        if not external_after.get(index) and not nested_after.get(index):
            raise BuildError(f"helper became unreachable after build {text!r}/{index:04X}")

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:8]}")
    if MAIN.read_bytes() != parent or SAVE.read_bytes() != save:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(result)
    shutil.copy2(SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM copy mismatch")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_continuation_native_followup_candidate.py",
        "ok": True,
        "status": "candidate_pending_user_runtime_validation",
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "sha256": sha(result),
            "size": len(result),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "path": str(OUT_SAVE.relative_to(ROOT)),
            "sha256": sha(save),
            "size": len(save),
            "byte_exact_live_copy": True,
        },
        "root_cause": (
            "scenario continuation records with a runtime control 0x18 were converted from native token grammar "
            "to a direct E5 18 portal; the continuation consumer can leak/corrupt the following row"
        ),
        "runtime_proven": {
            "63449B": {
                "before": EXPECTED_RUNTIME_CURRENT[FOUR_ZERO].hex().upper(),
                "after": after_four.hex().upper(),
                "render": four_render,
                "symptom": "어째서…… 뒤 bogus hiragana/gakehau-like follow row",
            },
            "doctor_j": drj_rows,
            "doctor_j_following_rows": next_rows,
        },
        "proactive_existing_token_repairs": proactive_rows,
        "helpers": {
            "selected_ids": {text: f"{helper_ids[text]:04X}" for text in HELPER_ORDER},
            "storage": storage_rows,
            "pointer_changes": pointer_rows,
            "render_verify": helper_verify,
            "reference_verify": reference_rows,
            "current_zero_reference_ids_available": len(clean_free),
            "current_zero_reference_storage_capacity": sum(right - left for left, right in merged_regions),
        },
        "counts": {
            "runtime_proven_records": 3,
            "proactive_existing_token_records": len(proactive_rows),
            "total_record_repairs": 3 + len(proactive_rows),
            "helper_entries": len(HELPER_ORDER),
            "unexpected_diff_runs": len(unexpected),
        },
        "guards": {
            "leading_control_18_preserved": True,
            "runtime_proven_direct_ext3_removed": True,
            "proactive_pristine_two_token_shape_restored": True,
            "doctor_j_following_rows_byte_exact": True,
            "helper_ids_parent_zero_reference": True,
            "helper_storage_no_live_overlap": True,
            "diff_allowlist_clean": not unexpected,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
        },
        "runtime_test": [
            "포우-제로 무라사메 교신에서 어째서…… 뒤 가케하우/히라가나 가짜 줄이 사라지고 다음 정상 대사로 진행되는지 확인",
            "STAGE21t 닥터 J의 ……뭐、 승산 좋은 도박？ 다음에 그건 아니지만。이 깨지지 않고 정상 출력되는지 확인",
            "동일 구조 선제 복구 레코드들은 기존 문구/이벤트 진행이 유지되는지 일반 진행 중 확인",
        ],
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "counts": report["counts"],
        "helper_ids": report["helpers"]["selected_ids"],
        "report": str(REPORT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
