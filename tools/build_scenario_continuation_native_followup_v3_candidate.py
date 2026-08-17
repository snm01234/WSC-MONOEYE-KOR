#!/usr/bin/env python3
"""Fix runtime-proven / structurally duplicated `うふふふ……` continuation leaks.

Parent is v2 (Doctor-J follow-up wrapper repair).  This stage fixes the
STAGE21t Katejina `우후후후……` row at 624305 and the byte-identical structural
copy at 6335A6.  Both are Original `18 + FAA5 + F191` continuations followed by
`17 28 ...` control rows; the current E5 18 portal leaks those control bytes as
Japanese glyphs at runtime.

A single private native wrapper stores `우후후후` as two already-live stock
phrases (`우` + `후후후`).  The wrapper is placed in the five bytes freed at the
end of the shortened 0EF3 Doctor-J wrapper extent, and one proven-zero-reference
stock id is repointed to it.  Records then use only native dictionary tokens.
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
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/scenario_continuation_native_followup_v2_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/scenario_continuation_native_followup_v3_candidate.wsc"
OUT_SAVE = ROOT / "sram/scenario_continuation_native_followup_v3_candidate.sav"
REPORT = ROOT / "out/patch/scenario_continuation_native_followup_v3_candidate_report.json"

EXPECTED_MAIN_SHA = "2bccff99a36cd453bf01225365a40ed4d744049e69d46e726ab427851afa1799"
EXPECTED_PARENT_SHA = "1bb53f440ecb4bb5466c634c278b2d0a629feac42449539e9c0b088635a13d78"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

HELPER_SLOT = 0x0662
HELPER_TOKEN = bytes.fromhex("F662")
HELPER_RAW = bytes.fromhex("F10EF28A")  # 우 + 후후후
# v2 shortened slot 0EF3 from a 13-byte physical extent to 8 bytes including
# NUL, leaving exactly five unreachable bytes at local 0x7B9A..0x7B9E.
HELPER_LOCAL = 0x7B9A
HELPER_STORAGE_BEFORE = bytes.fromhex("C5F4180A00")

RUNTIME_TARGET = 0x624305
STRUCTURAL_TARGET = 0x6335A6
TARGETS = {
    RUNTIME_TARGET: {
        "before": bytes.fromhex("18E518219C"),
        "after": bytes.fromhex("18F662F191"),
        "expected": "우후후후……",
        "next_control": bytes.fromhex("17280106"),
        "confidence": "user-runtime-proven",
    },
    STRUCTURAL_TARGET: {
        "before": bytes.fromhex("18E518927E"),
        "after": bytes.fromhex("18F662F08B"),
        "expected": "우후후후…",
        "next_control": bytes.fromhex("17280106"),
        "confidence": "exact-structural-duplicate",
    },
}
ORIGINAL_PAYLOAD = bytes.fromhex("18FAA5F191")


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def next_nonzero(rom: bytes | bytearray, term: int) -> int:
    sb = stock_base(rom)
    p = term + 1
    while p < term + 8 and rom[sb + p] == 0:
        p += 1
    return p


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    parent = bytes(load_rom(PARENT))
    original = bytes(load_rom(ORIGINAL))
    live_save = LIVE_SAVE.read_bytes()
    if len(main_rom) != ROM_SIZE or sha(main_rom) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(main_rom)}")
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"v2 parent identity drifted: {sha(parent)}")
    if sha(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("Original ROM identity drifted")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)
    stock_bank_file = sb + SEG_DICT * BANK_SIZE

    if token_from_dict_index(HELPER_SLOT) != HELPER_TOKEN or not dict_token_safe_in_zstring(HELPER_SLOT):
        raise BuildError("helper stock id/token contract drifted")
    external = external_occurrence_map(parent, ext3_aware=True, wanted={HELPER_SLOT}).get(HELPER_SLOT, [])
    nested = nested_occurrence_map(dictionary, wanted={HELPER_SLOT}, ext3_aware=True).get(HELPER_SLOT, [])
    raw_hits = _raw_pair_hits(parent, [HELPER_SLOT]).get(HELPER_SLOT, [])
    if external or nested or raw_hits:
        raise BuildError(f"helper slot is no longer zero-reference: external={external} nested={nested} raw={raw_hits}")

    helper_file = stock_bank_file + HELPER_LOCAL
    if parent[helper_file:helper_file + 5] != HELPER_STORAGE_BEFORE:
        raise BuildError(f"freed 0EF3 tail drifted: {parent[helper_file:helper_file + 5].hex().upper()}")
    if any(ptr == HELPER_LOCAL for ptr in dictionary.ptrs[:dictionary.stock_count]):
        raise BuildError("a dictionary pointer already targets helper storage")

    rows: list[dict[str, Any]] = []
    for logical, spec in TARGETS.items():
        before, term = read_record(parent, logical)
        pristine, pristine_term = read_record(original, logical)
        if before != spec["before"]:
            raise BuildError(f"parent target drifted {logical:06X}: {before.hex().upper()}")
        if pristine != ORIGINAL_PAYLOAD or pristine_term != term:
            raise BuildError(f"Original two-token grammar drifted {logical:06X}")
        nxt = next_nonzero(parent, term)
        if parent[sb + nxt:sb + nxt + 4] != spec["next_control"]:
            raise BuildError(f"following control boundary drifted {logical:06X}")
        rows.append({
            "abs": f"{logical:06X}",
            "before_hex": before.hex().upper(),
            "original_hex": pristine.hex().upper(),
            "next_control_abs": f"{nxt:06X}",
            "next_control_hex": parent[sb + nxt:sb + nxt + 4].hex().upper(),
            "confidence": spec["confidence"],
        })

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Private nested-stock-only helper: `우` + `후후후`.
    candidate[helper_file:helper_file + len(HELPER_RAW)] = HELPER_RAW
    candidate[helper_file + len(HELPER_RAW)] = 0
    allowed.append((helper_file, helper_file + len(HELPER_RAW) + 1))

    pointer_file = stock_bank_file + DICT_PTR_START + HELPER_SLOT * 2
    old_ptr = dictionary.ptrs[HELPER_SLOT]
    struct.pack_into("<H", candidate, pointer_file, HELPER_LOCAL)
    allowed.append((pointer_file, pointer_file + 2))

    for logical, spec in TARGETS.items():
        start = sb + logical
        candidate[start:start + len(spec["after"])] = spec["after"]
        allowed.append((start, start + len(spec["after"])))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    if result_dictionary.ptrs[HELPER_SLOT] != HELPER_LOCAL:
        raise BuildError("helper pointer rewrite failed")
    if bytes(result_dictionary.raw_entry(HELPER_SLOT)) != HELPER_RAW:
        raise BuildError("helper payload rewrite failed")
    if clean(result_dictionary.expand_index(HELPER_SLOT, tbl)) != "우후후후":
        raise BuildError("helper does not render 우후후후")

    for row in rows:
        logical = int(row["abs"], 16)
        spec = TARGETS[logical]
        payload, term = read_record(result, logical)
        if payload != spec["after"] or b"\xE5\x18" in payload[1:]:
            raise BuildError(f"target is not native-only {logical:06X}")
        rendered = clean(result_dictionary.expand(payload[1:], tbl))
        if rendered != spec["expected"]:
            raise BuildError(f"target render mismatch {logical:06X}: {rendered!r}")
        nxt = next_nonzero(result, term)
        if result[sb + nxt:sb + nxt + 4] != spec["next_control"]:
            raise BuildError(f"following control changed {logical:06X}")
        row.update({
            "after_hex": payload.hex().upper(),
            "rendered": rendered,
            "terminator": f"{term:06X}",
            "next_control_preserved": True,
        })

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"diff escaped v3 scope: {unexpected[:8]}")
    if MAIN.read_bytes() != main_rom or LIVE_SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT.write_bytes(result)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != live_save:
        raise BuildError("v3 SaveRAM is not byte-exact live copy")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_continuation_native_followup_v3_candidate.py",
        "status": "candidate_pending_user_runtime_validation",
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(parent)},
        "main_unchanged": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(main_rom)},
        "candidate": {"path": str(OUT.relative_to(ROOT)), "sha256": sha(result), "checksum": f"{checksum:04X}", "size": len(result)},
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(live_save), "byte_exact_to_live_main": True},
        "helper": {
            "slot": f"{HELPER_SLOT:04X}",
            "token_hex": HELPER_TOKEN.hex().upper(),
            "old_pointer": f"{old_ptr:04X}",
            "new_pointer": f"{HELPER_LOCAL:04X}",
            "payload_hex": HELPER_RAW.hex().upper(),
            "rendered": "우후후후",
            "storage_source": "freed_tail_of_shortened_0EF3_wrapper",
            "top_level_direct_hangul_marker": False,
        },
        "records": rows,
        "guards": {
            "helper_slot_parent_external_zero": True,
            "helper_slot_parent_nested_zero": True,
            "helper_slot_parent_raw_pair_zero": True,
            "original_two_token_grammar_exact": True,
            "following_17280106_boundary_preserved": True,
            "unexpected_diff_runs": 0,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
        },
        "runtime_test": [
            "STAGE21t Katejina: confirm 우후후후…… is followed by the intended event, with no がけはう/히라가나 control-row leak.",
            "If the structurally duplicated 6335A6 branch is encountered, confirm 우후후후… also proceeds without a leaked control row.",
            "Recheck Doctor J follow-up: 그건 아니지만。 should use the v2 native wrapper repair.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "saveram": report["saveram"],
        "helper": report["helper"],
        "records": rows,
        "unexpected_diff_runs": 0,
        "report": str(REPORT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
