#!/usr/bin/env python3
"""Read-only audit of the 7A:0610 one-byte terrain popup candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_terrain_popup_0610_onebyte_candidate import (  # noqa: E402
    ABAOA_SITES,
    ABAOA_TEXT,
    EXPECTED_MAIN_SHA256,
    EXPECTED_SAVE_SHA256,
    HANGUL_SOURCE,
    SPACE_SITES,
    SPACE_TEXT,
    compact_glyph_offset,
    decode_onebyte,
    hangul_glyph_offset,
    payload_at,
    read_glyph,
)
from monoeye_rom import Tbl, load_rom, stock_base  # noqa: E402

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/terrain_popup_0610_onebyte_candidate.wsc"
SAVE = ROOT / "sram/terrain_popup_0610_onebyte_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD_REPORT = ROOT / "out/patch/terrain_popup_0610_onebyte_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/terrain_popup_0610_onebyte_audit.json"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(failures: list[dict[str, Any]], kind: str, ok: bool, **extra: Any) -> None:
    if not ok:
        failures.append({"kind": kind, **extra})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--candidate", type=Path, default=CANDIDATE)
    parser.add_argument("--build-report", type=Path, default=BUILD_REPORT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    parent = bytes(load_rom(args.parent))
    candidate = bytes(load_rom(args.candidate))
    build = json.loads(args.build_report.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_candidate = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)

    failures: list[dict[str, Any]] = []
    check(failures, "parent_size", len(parent) == ROM_SIZE)
    check(failures, "candidate_size", len(candidate) == ROM_SIZE)
    check(failures, "parent_sha", sha256(parent) == EXPECTED_MAIN_SHA256)
    check(failures, "candidate_sha", sha256(candidate) == build["candidate"]["sha256"])
    check(failures, "main_unmodified", args.parent.read_bytes() == parent)
    check(failures, "live_save_unmodified", MAIN_SAVE.read_bytes() == SAVE.read_bytes())
    check(failures, "save_size", SAVE.stat().st_size == SAVE_SIZE)
    check(failures, "save_sha", sha256(SAVE.read_bytes()) == EXPECTED_SAVE_SHA256)
    check(
        failures,
        "slot_008F",
        bytes(d_candidate.raw_entry(0x008F)) == bytes(d_parent.raw_entry(0x008F)),
    )

    code_to_char: dict[int, str] = {}
    for row in build["glyphs"]:
        stolen = int(str(row["stolen_code"]), 16)
        char = str(row["hangul"])
        code_to_char[stolen] = char
        check(failures, f"code_lt_e0_{stolen:02X}", stolen < 0xE0)
        painted = bytes(
            candidate[
                compact_glyph_offset(candidate, stolen) : compact_glyph_offset(
                    candidate, stolen
                )
                + 16
            ]
        )
        source = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        check(failures, f"glyph_{char}", painted == source)

    space_hex = str(build["payloads"]["space_hex"])
    abaoa_hex = str(build["payloads"]["abaoa_hex"])
    space_body = bytes.fromhex(space_hex)
    abaoa_body = bytes.fromhex(abaoa_hex)
    check(failures, "space_len", len(space_body) == 2)
    check(failures, "abaoa_len", len(abaoa_body) == 7)
    check(failures, "space_no_e0", all(b < 0xE0 for b in space_body))
    check(failures, "abaoa_no_e0", all(b < 0xE0 for b in abaoa_body))
    check(failures, "no_f0", 0xF0 not in space_body + abaoa_body)
    check(failures, "no_e5", 0xE5 not in space_body + abaoa_body)

    for logical in SPACE_SITES:
        payload, term = payload_at(candidate, logical)
        check(failures, f"space_{logical:06X}", payload == space_body)
        check(
            failures,
            f"space_text_{logical:06X}",
            decode_onebyte(payload, code_to_char, tbl) == SPACE_TEXT,
        )
        check(failures, f"space_term_{logical:06X}", term == logical + 2)
        check(failures, f"space_nul_{logical:06X}", candidate[sb + term] == 0)
    for logical in ABAOA_SITES:
        payload, term = payload_at(candidate, logical)
        check(failures, f"abaoa_{logical:06X}", payload == abaoa_body)
        check(
            failures,
            f"abaoa_text_{logical:06X}",
            decode_onebyte(payload, code_to_char, tbl) == ABAOA_TEXT,
        )
        check(failures, f"abaoa_term_{logical:06X}", term == logical + 7)
        check(failures, f"abaoa_nul_{logical:06X}", candidate[sb + term] == 0)

    check(
        failures,
        "범용",
        payload_at(candidate, 0x75B3CA)[0] == payload_at(parent, 0x75B3CA)[0],
    )
    check(
        failures,
        "명중",
        payload_at(candidate, 0x75B457)[0] == payload_at(parent, 0x75B457)[0],
    )

    runs = diff_runs(parent, candidate)
    allow: list[tuple[int, int]] = []
    for row in build["glyphs"]:
        off = int(str(row["compact_offset"]), 16)
        allow.append((off, off + 16))
    for rec in build["records"]:
        abs_ = int(str(rec["abs"]), 16)
        n = len(bytes.fromhex(str(rec["after_hex"])))
        allow.append((sb + abs_, sb + abs_ + n))
    allow.append((len(candidate) - 2, len(candidate)))
    unexpected = [run for run in runs if not covered(run, allow)]
    check(failures, "allowlist", not unexpected, runs=unexpected)

    report = {
        "ok": not failures,
        "failures": failures,
        "candidate_sha256": sha256(candidate),
        "diff_runs": len(runs),
        "changed_bytes": sum(hi - lo for lo, hi in runs),
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "failures": len(failures)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
