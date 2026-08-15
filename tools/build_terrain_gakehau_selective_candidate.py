#!/usr/bin/env python3
"""Build a current-main test ROM containing only two requested static fixes.

1. Restore the 37x13-byte terrain descriptor table at 75:E720 from Original.
   The table is structured data, not zstrings; its far pointers must resolve to
   the already-Korean name records at 75:E58C (A Baoa Qu) and 75:E59A (Space).
2. Restore 62663E's original F8A6 nested-token grammar while translating the
   shared F8A6 wrapper to the already-live Korean stock token F0FD (오우).

The unrelated 67:E9F7 heat-weapon/type change from
build_user_reported_static_fixes_candidate.py is deliberately NOT included.
The main TIP and live SaveRAM are never overwritten.
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
from build_scenario_page_boundary_guard_candidate import original_unit_kinds  # noqa: E402
from expand_dictionary import NAME75_STRUCTURED_RANGES  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/terrain_gakehau_selective_candidate.wsc"
OUT_SAVE = ROOT / "sram/terrain_gakehau_selective_candidate.sav"
REPORT = ROOT / "out/patch/terrain_gakehau_selective_candidate_report.json"

EXPECTED_MAIN_SHA = "2ec5a8e57ff58afa9076ba68ed10f703c6a9dbf6caa8d58587d99cd9654ffbce"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

TERRAIN_START = 0x75E720
TERRAIN_END = 0x75E901
TERRAIN_STRIDE = 13
TERRAIN_COUNT = 37

DIALOGUE_ABS = 0x62663E
DIALOGUE_TERM = 0x626645
DIALOGUE_BEFORE = bytes.fromhex("173418F0FDF044")
DIALOGUE_AFTER = bytes.fromhex("173418F8A6F044")
OU_WRAPPER_SLOT = 0x08A6
OU_KOREAN_SLOT = 0x00FD
EXCLAMATION_SLOT = 0x0044
OU_SECOND_CONSUMER = 0x672555
NATIVE_TWO_TOKEN_NEIGHBORS = (0x6053BF, 0x61E234, 0x627FB5)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def token(index: int) -> bytes:
    raw = token_from_dict_index(index)
    if len(raw) != 2 or 0 in raw:
        raise BuildError(f"unsafe native dictionary token {index:04X}")
    return raw


def read_record(rom: bytes | bytearray, logical: int, max_len: int = 128) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def far_target(row: bytes) -> int:
    if len(row) < 4:
        raise BuildError("short terrain descriptor")
    off = row[0] | (row[1] << 8)
    seg = row[2] | (row[3] << 8)
    cpu = ((seg << 4) + off) & 0xFFFFF
    return 0x700000 + cpu


def record_text(rom: bytes, dictionary: Any, tbl: Tbl, logical: int) -> str:
    payload, _term = read_record(rom, logical)
    return dictionary.expand(payload, tbl).rstrip("\u3000 \t")


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < len(before) and before[i] != after[i]:
            i += 1
        out.append((start, i))
    return out


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    start, end = run
    cursor = start
    for lo, hi in sorted(allowed):
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end


def main() -> int:
    parent = MAIN.read_bytes()
    save = SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"current main TIP identity drifted: {sha256(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if sha256(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("Original ROM identity drifted")

    # Prevent recurrence in future name75/string walkers: this source-level guard
    # is one of the requested listed-tool changes and must already be present.
    if NAME75_STRUCTURED_RANGES != ((TERRAIN_START, TERRAIN_END),):
        raise BuildError(f"terrain structured-range guard missing: {NAME75_STRUCTURED_RANGES!r}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)
    osb = stock_base(original)

    terrain_before = parent[sb + TERRAIN_START : sb + TERRAIN_END]
    terrain_original = original[osb + TERRAIN_START : osb + TERRAIN_END]
    if len(terrain_original) != TERRAIN_COUNT * TERRAIN_STRIDE:
        raise BuildError("terrain table extent drifted")
    if terrain_before == terrain_original:
        raise BuildError("terrain table is unexpectedly already restored")
    corrupt_targets = {
        "abao": far_target(terrain_before[0:TERRAIN_STRIDE]),
        "space": far_target(terrain_before[3 * TERRAIN_STRIDE : 4 * TERRAIN_STRIDE]),
    }
    if corrupt_targets != {"abao": 0x7D6225, "space": 0x7D7225}:
        raise BuildError(f"unexpected current terrain corruption: {corrupt_targets}")
    for i in range(TERRAIN_COUNT):
        row = terrain_original[i * TERRAIN_STRIDE : (i + 1) * TERRAIN_STRIDE]
        target = far_target(row)
        if not 0x75E58C <= target <= 0x75E62D:
            raise BuildError(f"Original terrain row {i} escaped name pool: {target:06X}")

    # Name strings themselves are already Korean; only the descriptor pointers
    # are corrupt on current main.
    if record_text(parent, dictionary, tbl, 0x75E58C) != "아・바오아・쿠":
        raise BuildError("current A Baoa Qu name string drifted")
    if record_text(parent, dictionary, tbl, 0x75E59A) != "우주":
        raise BuildError("current Space name string drifted")

    # Current main is the historically user-validated direct native two-token
    # form.  The newer static diagnosis in build_user_reported_static_fixes_candidate
    # restores the pristine nested wrapper itself while keeping two outer tokens.
    if read_record(parent, DIALOGUE_ABS) != (DIALOGUE_BEFORE, DIALOGUE_TERM):
        raise BuildError("62663E current record drifted")
    if bytes(dictionary.raw_entry(OU_WRAPPER_SLOT)) != bytes.fromhex("F24409"):
        raise BuildError("08A6 wrapper is not pristine おうっ")
    if dictionary.expand_index(OU_KOREAN_SLOT, tbl) != "오우":
        raise BuildError("00FD no longer expands to 오우")
    if dictionary.expand_index(EXCLAMATION_SLOT, tbl) != "！！":
        raise BuildError("0044 no longer expands to ！！")
    if parent[sb + OU_SECOND_CONSUMER : sb + OU_SECOND_CONSUMER + 2] != token(OU_WRAPPER_SLOT):
        raise BuildError("08A6 secondary working consumer drifted")

    wrapper_ptr = dictionary.entry_offset(OU_WRAPPER_SLOT)
    pointer_aliases = [
        i for i, ptr in enumerate(dictionary.ptrs[: dictionary.stock_count]) if ptr == wrapper_ptr
    ]
    if pointer_aliases != [OU_WRAPPER_SLOT]:
        raise BuildError(f"08A6 physical pointer alias hazard: {pointer_aliases}")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    terrain_file_start = sb + TERRAIN_START
    terrain_file_end = sb + TERRAIN_END
    candidate[terrain_file_start:terrain_file_end] = terrain_original
    allowed.append((terrain_file_start, terrain_file_end))

    ou_entry = dictionary.entry_abs(OU_WRAPPER_SLOT)
    ou_old_len = len(dictionary.raw_entry(OU_WRAPPER_SLOT))
    ou_wrapper_raw = token(OU_KOREAN_SLOT)
    candidate[ou_entry : ou_entry + len(ou_wrapper_raw)] = ou_wrapper_raw
    candidate[ou_entry + len(ou_wrapper_raw)] = 0
    allowed.append((ou_entry, ou_entry + ou_old_len + 1))

    dialogue_file = sb + DIALOGUE_ABS
    candidate[dialogue_file : dialogue_file + len(DIALOGUE_AFTER)] = DIALOGUE_AFTER
    allowed.append((dialogue_file, dialogue_file + len(DIALOGUE_AFTER)))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    after_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    restored = result[terrain_file_start:terrain_file_end]
    if restored != terrain_original:
        raise BuildError("terrain table restoration failed")
    terrain_targets = {
        "abao": far_target(restored[0:TERRAIN_STRIDE]),
        "space": far_target(restored[3 * TERRAIN_STRIDE : 4 * TERRAIN_STRIDE]),
    }
    if terrain_targets != {"abao": 0x75E58C, "space": 0x75E59A}:
        raise BuildError(f"restored terrain pointers drifted: {terrain_targets}")
    terrain_render = {
        key: record_text(result, after_dictionary, tbl, logical)
        for key, logical in terrain_targets.items()
    }
    if terrain_render != {"abao": "아・바오아・쿠", "space": "우주"}:
        raise BuildError(f"terrain render drifted: {terrain_render}")

    dialogue_payload, dialogue_term = read_record(result, DIALOGUE_ABS)
    if dialogue_payload != DIALOGUE_AFTER or dialogue_term != DIALOGUE_TERM:
        raise BuildError("62663E selective rewrite failed")
    if original_unit_kinds(dialogue_payload[3:]) != ["dict", "dict"]:
        raise BuildError("62663E no longer has native two-token outer grammar")
    if bytes(after_dictionary.raw_entry(OU_WRAPPER_SLOT)) != ou_wrapper_raw:
        raise BuildError("08A6 nested wrapper was not rewritten to F0FD")
    dialogue_render = after_dictionary.expand(dialogue_payload[3:], tbl)
    if dialogue_render != "오우！！":
        raise BuildError(f"62663E static render drifted: {dialogue_render!r}")

    similar: dict[str, Any] = {}
    for logical in NATIVE_TWO_TOKEN_NEIGHBORS:
        before_payload, before_term = read_record(parent, logical)
        after_payload, after_term = read_record(result, logical)
        kinds = original_unit_kinds(after_payload[3:])
        if before_payload != after_payload or before_term != after_term or kinds != ["dict", "dict"]:
            raise BuildError(f"similar native-two-token contract changed at {logical:06X}")
        similar[f"{logical:06X}"] = {
            "payload_hex": after_payload.hex().upper(),
            "terminator": f"{after_term:06X}",
            "body_units": kinds,
        }
    similar[f"{DIALOGUE_ABS:06X}"] = {
        "payload_hex": dialogue_payload.hex().upper(),
        "terminator": f"{dialogue_term:06X}",
        "body_units": original_unit_kinds(dialogue_payload[3:]),
    }

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:8]}")
    if result[sb + 0x67E9F7 : sb + 0x67E9FF] != parent[sb + 0x67E9F7 : sb + 0x67E9FF]:
        raise BuildError("unrelated 67E9F7 heat-weapon/type change leaked into selective candidate")
    if MAIN.read_bytes() != parent or SAVE.read_bytes() != save:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT.write_bytes(result)
    shutil.copy2(SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM is not byte-exact live copy")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terrain_gakehau_selective_candidate.py",
        "ok": True,
        "status": "static_verified_runtime_test_required",
        "parent": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": sha256(parent)},
        "candidate": {
            "path": "out/patch/terrain_gakehau_selective_candidate.wsc",
            "sha256": sha256(result),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "path": "sram/terrain_gakehau_selective_candidate.sav",
            "sha256": sha256(save),
            "byte_exact_live": True,
        },
        "selected_changes_only": {
            "terrain_descriptor_restore": True,
            "dialogue_62663E_nested_wrapper_restore": True,
            "heat_weapon_type_67E9F7": False,
        },
        "terrain": {
            "source_files": [
                "tools/_diag_terrain_farptr_static.py",
                "tools/_diag_terrain_ui_calls_static.py",
                "tools/_diag_user_reported_static.py",
                "tools/expand_dictionary.py",
                "tools/build_bank59_enc5c_name75_candidate.py",
                "tools/build_user_reported_static_fixes_candidate.py",
                "tools/audit_user_reported_static_fixes_candidate.py",
                "tools/audit_broad_japanese_residuals.py",
            ],
            "root_cause": "75:E720-E900 is a 37x13 structured terrain descriptor table; an older name75 zstring walk overwrote its far pointers/stats",
            "range": f"{TERRAIN_START:06X}-{TERRAIN_END - 1:06X}",
            "before_targets": {k: f"{v:06X}" for k, v in corrupt_targets.items()},
            "after_targets": {k: f"{v:06X}" for k, v in terrain_targets.items()},
            "rendered": terrain_render,
            "future_guard": "expand_dictionary.NAME75_STRUCTURED_RANGES plus build_bank59_enc5c_name75_candidate refusal",
        },
        "gakehau": {
            "source_files": [
                "tools/_diag_user_reported_static.py",
                "tools/build_user_reported_static_fixes_candidate.py",
                "tools/audit_user_reported_static_fixes_candidate.py",
            ],
            "abs": f"{DIALOGUE_ABS:06X}",
            "before": DIALOGUE_BEFORE.hex().upper(),
            "after": DIALOGUE_AFTER.hex().upper(),
            "wrapper_slot": f"{OU_WRAPPER_SLOT:04X}",
            "wrapper_before": "F24409",
            "wrapper_after": ou_wrapper_raw.hex().upper(),
            "render": dialogue_render,
            "similar_native_two_token_contracts": similar,
            "note": "This is the newer nested-wrapper restoration from the listed static-fix tools. Runtime validation is required because the current main's direct F0FD+F044 form also statically renders 오우！！.",
        },
        "diff": {
            "changed_runs": len(runs),
            "changed_bytes": sum(end - start for start, end in runs),
            "unexpected_runs": len(unexpected),
            "runs": [
                {"start": f"{start:07X}", "end_exclusive": f"{end:07X}", "length": end - start}
                for start, end in runs
            ],
        },
        "checks": {
            "terrain_table_exact_original": restored == terrain_original,
            "terrain_far_targets_exact": terrain_targets == {"abao": 0x75E58C, "space": 0x75E59A},
            "terrain_text_exact": terrain_render == {"abao": "아・바오아・쿠", "space": "우주"},
            "terrain_source_guard_present": NAME75_STRUCTURED_RANGES == ((TERRAIN_START, TERRAIN_END),),
            "62663E_native_two_token": original_unit_kinds(dialogue_payload[3:]) == ["dict", "dict"],
            "62663E_nested_wrapper_exact": dialogue_payload == DIALOGUE_AFTER and bytes(after_dictionary.raw_entry(OU_WRAPPER_SLOT)) == ou_wrapper_raw,
            "62663E_render_exact": dialogue_render == "오우！！",
            "similar_native_contracts_unchanged": len(similar) == 4,
            "unrelated_67E9F7_unchanged": result[sb + 0x67E9F7 : sb + 0x67E9FF] == parent[sb + 0x67E9F7 : sb + 0x67E9FF],
            "diff_allowlist_clean": not unexpected,
            "main_unchanged": MAIN.read_bytes() == parent,
            "live_save_unchanged": SAVE.read_bytes() == save,
        },
        "runtime_test": [
            "Cold boot/reset with the candidate ROM and paired SaveRAM; do not validate terrain from an old savestate.",
            "Open terrain information on a Space tile and confirm 우주 is displayed normally.",
            "Open terrain information on A Baoa Qu and confirm 아・바오아・쿠 is displayed normally.",
            "Reach 62663E and confirm only 오우！！ is visible, with no がけはう or similar extra hiragana line, and the event continues.",
        ],
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "terrain": report["terrain"],
        "gakehau": report["gakehau"],
        "diff": report["diff"],
        "promotion": report["promotion"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
