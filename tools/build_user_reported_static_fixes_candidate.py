#!/usr/bin/env python3
"""Build the static-only candidate for the 2026-08-15 user report.

The candidate fixes three independent storage/decoder contract violations:

* restore the 37 x 13-byte terrain descriptor table at 75:E720;
* restore 62663E's original nested-token grammar and translate its shared
  F8A6 phrase through a nested Korean token;
* encode the fixed-stride ``ヒ－ト兵器ダメ－ジ`` label with stock dictionary
  tokens supported by that decoder.

The live main TIP and SaveRAM are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_scenario_page_boundary_guard_candidate import (  # noqa: E402
    encode_text,
    original_unit_kinds,
)
from expand_dictionary import NAME75_STRUCTURED_RANGES  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_3byte_dict_token import (  # noqa: E402
    EXP3_SEG0,
    bank_local_for_index,
    token_from_ext3_index,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/user_reported_static_fixes_candidate.wsc"
OUT_SAVE = ROOT / "sram/user_reported_static_fixes_candidate.sav"
REPORT = ROOT / "out/patch/user_reported_static_fixes_report.json"

EXPECTED_MAIN_SHA = "1886d04e697baba17b454089dbd07cc556d3e49c5808e23e321cf6863773bc3d"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

TERRAIN_START = 0x75E720
TERRAIN_END = 0x75E901
TERRAIN_STRIDE = 13
TERRAIN_COUNT = 37
TERRAIN_ABAO_INDEX = 0
TERRAIN_SPACE_INDEX = 3

DIALOGUE_ABS = 0x62663E
DIALOGUE_TERM = 0x626645
DIALOGUE_BEFORE = bytes.fromhex("173418F0FDF044")
DIALOGUE_AFTER = bytes.fromhex("173418F8A6F044")
OU_WRAPPER_SLOT = 0x08A6
OU_KOREAN_SLOT = 0x00FD
EXCLAMATION_SLOT = 0x0044
OU_SECOND_CONSUMER = 0x672555

HIT_TYPE_ABS = 0x67E9F7
HIT_TYPE_BEFORE = bytes.fromhex("E095103EF4BFFB13")
WEAPON_SLOT = 0x012F
DAMAGE_SLOT = 0x0B13

NATIVE_ONLY_FIRST = (0x6053BF, 0x61E234, 0x62663E, 0x627FB5)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def token(index: int) -> bytes:
    raw = token_from_dict_index(index)
    if len(raw) != 2 or 0 in raw:
        raise BuildError(f"unsafe stock token {index:04X}")
    return raw


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    base = stock_base(rom)
    got = read_encoded_z_safe(rom, base + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - base


def far_target(row: bytes) -> int:
    if len(row) < 4:
        raise BuildError("short far pointer row")
    offset = row[0] | (row[1] << 8)
    segment = row[2] | (row[3] << 8)
    cpu_address = ((segment << 4) + offset) & 0xFFFFF
    return 0x700000 + cpu_address


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for pos, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = pos
        elif left == right and start is not None:
            runs.append((start, pos))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    return any(lo <= run[0] and run[1] <= hi for lo, hi in allowed)


def record_text(rom: bytes, dictionary: Any, tbl: Tbl, logical: int) -> str:
    payload, _term = read_record(rom, logical)
    return dictionary.expand(payload, tbl).rstrip("\u3000 \t")


def main() -> int:
    parent = MAIN.read_bytes()
    save = SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if sha256(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("Original ROM identity drifted")
    if NAME75_STRUCTURED_RANGES != ((TERRAIN_START, TERRAIN_END),):
        raise BuildError("terrain structured-range guard drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    parent_base = stock_base(parent)
    original_base = stock_base(original)

    terrain_before = parent[
        parent_base + TERRAIN_START : parent_base + TERRAIN_END
    ]
    terrain_original = original[
        original_base + TERRAIN_START : original_base + TERRAIN_END
    ]
    if len(terrain_original) != TERRAIN_COUNT * TERRAIN_STRIDE:
        raise BuildError("terrain table extent drifted")
    if terrain_before == terrain_original:
        raise BuildError("terrain table is unexpectedly already restored")
    for index in range(TERRAIN_COUNT):
        row = terrain_original[index * TERRAIN_STRIDE : (index + 1) * TERRAIN_STRIDE]
        target = far_target(row)
        if not 0x75E58C <= target <= 0x75E62D:
            raise BuildError(f"terrain pointer {index} escaped name pool: {target:06X}")
    corrupt_targets = {
        "abao": far_target(terrain_before[0:13]),
        "space": far_target(terrain_before[3 * 13 : 4 * 13]),
    }
    if corrupt_targets != {"abao": 0x7D6225, "space": 0x7D7225}:
        raise BuildError(f"unexpected corrupt terrain pointers: {corrupt_targets}")

    if read_record(parent, DIALOGUE_ABS) != (DIALOGUE_BEFORE, DIALOGUE_TERM):
        raise BuildError("62663E drifted")
    if bytes(dictionary.raw_entry(OU_WRAPPER_SLOT)) != bytes.fromhex("F24409"):
        raise BuildError("F8A6 pristine wrapper payload drifted")
    if dictionary.expand_index(OU_KOREAN_SLOT, tbl) != "오우":
        raise BuildError("F0FD no longer expands to 오우")
    if dictionary.expand_index(EXCLAMATION_SLOT, tbl) != "！！":
        raise BuildError("F044 no longer expands to full-width exclamation marks")
    if parent[parent_base + OU_SECOND_CONSUMER : parent_base + OU_SECOND_CONSUMER + 2] != token(OU_WRAPPER_SLOT):
        raise BuildError("F8A6's second script consumer drifted")

    hit_before = parent[
        parent_base + HIT_TYPE_ABS : parent_base + HIT_TYPE_ABS + len(HIT_TYPE_BEFORE)
    ]
    if hit_before != HIT_TYPE_BEFORE:
        raise BuildError("heat-weapon type record drifted")
    if dictionary.expand_index(WEAPON_SLOT, tbl) != "무기":
        raise BuildError("F12F no longer expands to 무기")
    if dictionary.expand_index(DAMAGE_SLOT, tbl) != "데미지":
        raise BuildError("FB13 no longer expands to 데미지")

    hit_phrase = "히트　무기　데미지"
    hit_phrase_raw = encode_text(tbl, hit_phrase)
    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 metadata is not installed")
    hit_ext3_slot: int | None = None
    for index in sorted(inventory.ext3_free):
        segment, _local = bank_local_for_index(index)
        bank = segment - EXP3_SEG0
        if int(inventory.ext3_bank_room.get(bank, 0)) >= len(hit_phrase_raw) + 1:
            hit_ext3_slot = index
            break
    if hit_ext3_slot is None:
        raise BuildError("no true-free ext3 slot has room for the heat-weapon type")
    hit_after = token_from_ext3_index(hit_ext3_slot, num_banks=num_banks) + b"\x01" * 4
    if len(hit_after) != len(HIT_TYPE_BEFORE):
        raise BuildError("fixed-stride hit type extent changed")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    terrain_file_start = parent_base + TERRAIN_START
    candidate[terrain_file_start : parent_base + TERRAIN_END] = terrain_original
    allowed.append((terrain_file_start, parent_base + TERRAIN_END))

    ou_entry = dictionary.entry_abs(OU_WRAPPER_SLOT)
    ou_old_len = len(dictionary.raw_entry(OU_WRAPPER_SLOT))
    ou_wrapper_raw = token(OU_KOREAN_SLOT)
    candidate[ou_entry : ou_entry + len(ou_wrapper_raw)] = ou_wrapper_raw
    candidate[ou_entry + len(ou_wrapper_raw)] = 0
    allowed.append((ou_entry, ou_entry + ou_old_len + 1))
    candidate[
        parent_base + DIALOGUE_ABS : parent_base + DIALOGUE_ABS + len(DIALOGUE_AFTER)
    ] = DIALOGUE_AFTER
    allowed.append(
        (parent_base + DIALOGUE_ABS, parent_base + DIALOGUE_ABS + len(DIALOGUE_AFTER))
    )

    before_ext3_write = bytes(candidate)
    ext3_write, ext3_guard = write_ext3_slots_guarded(
        candidate,
        {hit_ext3_slot: hit_phrase_raw},
        union=union,
        num_banks=num_banks,
    )
    if int(ext3_write.get("written") or 0) != 1:
        raise BuildError("ext3 writer did not write the heat-weapon phrase")
    allowed.extend(diff_runs(before_ext3_write, bytes(candidate)))
    candidate[
        parent_base + HIT_TYPE_ABS : parent_base + HIT_TYPE_ABS + len(hit_after)
    ] = hit_after
    allowed.append(
        (parent_base + HIT_TYPE_ABS, parent_base + HIT_TYPE_ABS + len(hit_after))
    )

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    candidate_bytes = bytes(candidate)
    after_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    restored = candidate_bytes[
        parent_base + TERRAIN_START : parent_base + TERRAIN_END
    ]
    if restored != terrain_original:
        raise BuildError("terrain descriptor restoration failed")
    terrain_targets = {
        "abao": far_target(restored[0:13]),
        "space": far_target(restored[3 * 13 : 4 * 13]),
    }
    if terrain_targets != {"abao": 0x75E58C, "space": 0x75E59A}:
        raise BuildError(f"restored terrain pointers drifted: {terrain_targets}")
    terrain_text = {
        key: record_text(candidate_bytes, after_dictionary, tbl, logical)
        for key, logical in terrain_targets.items()
    }
    if terrain_text["space"] != "우주" or terrain_text["abao"] != "아・바오아・쿠":
        raise BuildError(f"terrain translation drifted: {terrain_text}")

    dialogue_payload, dialogue_term = read_record(candidate_bytes, DIALOGUE_ABS)
    if dialogue_payload != DIALOGUE_AFTER or dialogue_term != DIALOGUE_TERM:
        raise BuildError("62663E rewrite failed")
    if original_unit_kinds(dialogue_payload[3:]) != ["dict", "dict"]:
        raise BuildError("62663E lost native two-token grammar")
    if after_dictionary.raw_entry(OU_WRAPPER_SLOT) != ou_wrapper_raw:
        raise BuildError("F8A6 nested wrapper write failed")
    dialogue_text = after_dictionary.expand(dialogue_payload[3:], tbl)
    if dialogue_text != "오우！！":
        raise BuildError(f"62663E static render drifted: {dialogue_text!r}")

    native_contracts: dict[str, Any] = {}
    for logical in NATIVE_ONLY_FIRST:
        payload, term = read_record(candidate_bytes, logical)
        kinds = original_unit_kinds(payload[3:])
        if payload[:3] != bytes((0x17, payload[1], 0x18)) or kinds != ["dict", "dict"]:
            raise BuildError(f"native-only first-line contract failed at {logical:06X}")
        native_contracts[f"{logical:06X}"] = {
            "payload_hex": payload.hex().upper(),
            "terminator": f"{term:06X}",
            "body_units": kinds,
        }

    if candidate_bytes[
        parent_base + HIT_TYPE_ABS : parent_base + HIT_TYPE_ABS + len(hit_after)
    ] != hit_after:
        raise BuildError("heat-weapon type rewrite failed")
    hit_text = after_dictionary.expand(hit_after, tbl).rstrip("\u3000 \t")
    if hit_text != hit_phrase:
        raise BuildError(f"heat-weapon type static render drifted: {hit_text!r}")
    if bytes(after_dictionary.raw_entry(hit_ext3_slot)) != hit_phrase_raw:
        raise BuildError("heat-weapon ext3 phrase storage drifted")

    escaped = [run for run in diff_runs(parent, candidate_bytes) if not covered(run, allowed)]
    if escaped:
        raise BuildError(f"unexpected diff runs: {escaped[:8]}")

    OUT.write_bytes(candidate_bytes)
    shutil.copy2(SAVE, OUT_SAVE)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "tools/build_user_reported_static_fixes_candidate.py",
        "status": "static_analysis_complete_runtime_not_required",
        "parent": {
            "path": str(MAIN.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(OUT.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(candidate_bytes),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(OUT_SAVE.read_bytes()),
            "byte_exact_copy_of_live_save": OUT_SAVE.read_bytes() == save,
        },
        "main_unchanged": MAIN.read_bytes() == parent,
        "fixes": {
            "terrain_popup": {
                "root_cause": "75:E720 13-byte terrain descriptors were misclassified as zstrings and their far pointers/stats were overwritten",
                "table": f"{TERRAIN_START:06X}-{TERRAIN_END:06X}",
                "stride": TERRAIN_STRIDE,
                "records": TERRAIN_COUNT,
                "before_corrupt_targets": {key: f"{value:06X}" for key, value in corrupt_targets.items()},
                "after_name_targets": {key: f"{value:06X}" for key, value in terrain_targets.items()},
                "rendered": terrain_text,
                "source_guard": "expand_dictionary.NAME75_STRUCTURED_RANGES",
            },
            "dialogue_62663E": {
                "root_cause": "the outer record kept two tokens but bypassed the original F8A6 nested-token wrapper",
                "before": DIALOGUE_BEFORE.hex().upper(),
                "after": DIALOGUE_AFTER.hex().upper(),
                "wrapper_slot": f"{OU_WRAPPER_SLOT:04X}",
                "wrapper_raw": ou_wrapper_raw.hex().upper(),
                "wrapper_expands": dialogue_text[:-2],
                "second_consumer": f"{OU_SECOND_CONSUMER:06X}",
                "similar_native_contracts": native_contracts,
            },
            "heat_weapon_type": {
                "root_cause": "fixed-stride bank 67 record was excluded from generic localization; its first four kana bytes stayed literal Japanese",
                "abs": f"{HIT_TYPE_ABS:06X}",
                "before": HIT_TYPE_BEFORE.hex().upper(),
                "after": hit_after.hex().upper(),
                "rendered": hit_text,
                "phrase_slot": f"{hit_ext3_slot:05X}",
                "phrase_raw": hit_phrase_raw.hex().upper(),
                "slot_was_true_free": hit_ext3_slot in inventory.ext3_free,
                "slot_guard": ext3_guard.as_dict(),
                "fixed_extent": len(hit_after),
            },
        },
        "unexpected_diff_runs": 0,
        "verification_mode": "static_only_no_bizhawk",
    }
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
