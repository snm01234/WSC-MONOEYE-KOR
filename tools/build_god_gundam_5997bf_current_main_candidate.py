#!/usr/bin/env python3
"""Build a focused current-main God Gundam 5997BF repair candidate.

This patch intentionally changes only the runtime-proven God Gundam second-line
record at logical 0x5997BF plus the WonderSwan checksum.  The current Garrod
scenario range 0x61E234..0x61E25C is pinned byte-exact and must not change.

The 5997BF replacement is the exact body previously runtime-verified in
``god_garrod_runtime_followup_candidate.wsc``:
  17 1C 18 E5 18 08 92 + 0x01 padding
which renders ``내 이 손이 새빨갛게 타오른다！！`` while preserving the record
extent, terminator 0x5997D4, separator 0x5997D5, and following 08 4B control.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT = ROOT / "out/patch/god_gundam_5997bf_current_main_candidate.wsc"
OUT_SAVE = ROOT / "sram/god_gundam_5997bf_current_main_candidate.sav"
REPORT = ROOT / "out/patch/god_gundam_5997bf_current_main_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_MAIN_SHA = "35c56e0f8d1aaec9b4687490ddc7b9e999f100ce2987666612931178d0ca44c2"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
GOD2 = 0x5997BF
GOD2_TERM = 0x5997D4
GOD2_SEPARATOR = 0x5997D5
GOD2_NEXT = 0x5997D6
GARROD_START = 0x61E234
GARROD_END = 0x61E25D

EXPECTED_BEFORE = bytes.fromhex("171C18F430F5E67217E08409E0800CE2D5F0BBF044")
EXPECTED_AFTER = bytes.fromhex("171C18E51808920101010101010101010101010101")
EXPECTED_GARROD = bytes.fromhex(
    "173418F184F191000018F2B80101010101010101010100"
    "F2C50101010101010101010101010000082B"
)
EXPECTED_TEXT = "내　이　손이　새빨갛게　타오른다！！"


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise RuntimeError(f"main TIP identity drifted: {sha(parent)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise RuntimeError("live SaveRAM missing/wrong size")

    sb = stock_base(parent)
    before = parent[sb + GOD2 : sb + GOD2_TERM]
    if before != EXPECTED_BEFORE:
        raise RuntimeError(f"5997BF parent bytes drifted: {before.hex().upper()}")
    if parent[sb + GOD2_TERM] != 0 or parent[sb + GOD2_SEPARATOR] != 0:
        raise RuntimeError("5997BF terminator/separator drifted")
    if parent[sb + GOD2_NEXT : sb + GOD2_NEXT + 2] != bytes.fromhex("084B"):
        raise RuntimeError("5997BF following 08 4B control drifted")

    garrod_before = parent[sb + GARROD_START : sb + GARROD_END]
    if garrod_before != EXPECTED_GARROD:
        raise RuntimeError(f"Garrod protected range drifted: {garrod_before.hex().upper()}")

    out = bytearray(parent)
    out[sb + GOD2 : sb + GOD2_TERM] = EXPECTED_AFTER
    checksum = update_ws_checksum(out)
    candidate = bytes(out)

    # Hard scope proof: only 5997BF payload and final checksum may differ.
    allowed = set(range(sb + GOD2, sb + GOD2_TERM)) | {len(candidate) - 2, len(candidate) - 1}
    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    outside = [i for i in changed if i not in allowed]
    if outside:
        raise RuntimeError(f"diff outside 5997BF/checksum: {outside[:8]}")

    if candidate[sb + GOD2_TERM] != 0 or candidate[sb + GOD2_SEPARATOR] != 0:
        raise RuntimeError("5997BF NUL boundary changed")
    if candidate[sb + GOD2_NEXT : sb + GOD2_NEXT + 2] != bytes.fromhex("084B"):
        raise RuntimeError("5997BF following control changed")
    garrod_after = candidate[sb + GARROD_START : sb + GARROD_END]
    if garrod_after != garrod_before:
        raise RuntimeError("Garrod protected range changed")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    got = read_encoded_z_safe(candidate, sb + GOD2, max_len=64)
    if got is None:
        raise RuntimeError("5997BF became unreadable")
    payload, term_file = got
    term = int(term_file - sb)
    if bytes(payload) != EXPECTED_AFTER or term != GOD2_TERM:
        raise RuntimeError("5997BF payload/terminator verification failed")
    rendered = dictionary.expand(bytes(payload)[3:], tbl).rstrip("　 \t")
    if rendered != EXPECTED_TEXT:
        raise RuntimeError(f"5997BF render mismatch: {rendered!r}")

    OUT.write_bytes(candidate)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_god_gundam_5997bf_current_main_candidate.py",
        "status": "focused_runtime_proven_fix_main_unchanged",
        "parent": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": sha(parent)},
        "candidate": {
            "path": "out/patch/god_gundam_5997bf_current_main_candidate.wsc",
            "sha256": sha(candidate),
            "size": len(candidate),
            "checksum": f"{checksum:04X}",
        },
        "patch": {
            "abs": "5997BF",
            "before_hex": EXPECTED_BEFORE.hex().upper(),
            "after_hex": EXPECTED_AFTER.hex().upper(),
            "rendered": rendered,
            "terminator": "5997D4",
            "separator": "5997D5",
            "following_control": "084B",
            "runtime_proven_source_candidate": "god_garrod_runtime_followup_candidate.wsc",
        },
        "protected_garrod": {
            "range": "61E234-61E25C",
            "byte_exact": True,
            "hex": garrod_after.hex().upper(),
        },
        "diff": {
            "changed_bytes_total": len(changed),
            "outside_target_and_checksum": len(outside),
        },
        "saveram": {
            "path": "sram/god_gundam_5997bf_current_main_candidate.sav",
            "sha256": sha(MAIN_SAVE.read_bytes()),
            "size": MAIN_SAVE.stat().st_size,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    print(json.dumps(report["patch"], ensure_ascii=False, indent=2))
    print(json.dumps(report["protected_garrod"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
