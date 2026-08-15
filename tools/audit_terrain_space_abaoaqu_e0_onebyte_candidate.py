#!/usr/bin/env python3
"""Independent read-only audit of the E0xx + 1-byte terrain HUD candidate."""
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
from build_terrain_space_abaoaqu_e0_onebyte_candidate import (  # noqa: E402
    ABAOA_BEFORE,
    ABAOA_SITES,
    ABAOA_TEXT,
    EMPTY_STOCK,
    EXPECTED_MAIN_SHA256,
    HANGUL_SOURCE,
    SPACE_BEFORE,
    SPACE_SITES,
    SPACE_TEXT,
    compact_glyph_offset,
    decode_mapped,
    hangul_glyph_offset,
    payload_at,
    read_glyph,
)
from mixed_residual_reference_union import build_reference_union  # noqa: E402
from monoeye_rom import Tbl, load_rom, stock_base, token_from_dict_index  # noqa: E402

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/terrain_space_abaoaqu_e0_onebyte_candidate.wsc"
SAVE = ROOT / "sram/terrain_space_abaoaqu_e0_onebyte_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BUILD_REPORT = ROOT / "out/patch/terrain_space_abaoaqu_e0_onebyte_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/terrain_space_abaoaqu_e0_onebyte_audit.json"
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
    original = bytes(load_rom(ORIGINAL))
    build = json.loads(args.build_report.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_candidate = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    check(failures, "parent_size", len(parent) == ROM_SIZE)
    check(failures, "candidate_size", len(candidate) == ROM_SIZE)
    check(failures, "parent_sha", sha256(parent) == EXPECTED_MAIN_SHA256)
    check(
        failures,
        "candidate_report_binding",
        sha256(candidate) == ((build.get("candidate") or {}).get("sha256")),
    )
    check(failures, "main_not_overwritten", sha256(parent) != sha256(candidate))
    check(failures, "save_exists", SAVE.is_file() and SAVE.stat().st_size == SAVE_SIZE)
    check(
        failures,
        "save_copied_from_live_main",
        MAIN_SAVE.is_file() and SAVE.read_bytes() == MAIN_SAVE.read_bytes(),
    )
    check(
        failures,
        "slot_008F_preserved",
        bytes(d_candidate.raw_entry(0x008F)) == bytes(d_parent.raw_entry(0x008F)),
    )
    check(failures, "empty_00A9", d_candidate.expand_index(EMPTY_STOCK, tbl) == "")
    check(
        failures,
        "범용_preserved",
        payload_at(candidate, 0x75B3CA)[0] == payload_at(parent, 0x75B3CA)[0],
    )
    check(
        failures,
        "명중_보정_preserved",
        payload_at(candidate, 0x75B457)[0] == payload_at(parent, 0x75B457)[0],
    )
    check(
        failures,
        "색적우주_75B75E",
        payload_at(candidate, 0x75B75E)[0] == payload_at(parent, 0x75B75E)[0],
    )
    check(
        failures,
        "색적우주_75BE2B",
        payload_at(candidate, 0x75BE2B)[0] == payload_at(parent, 0x75BE2B)[0],
    )

    glyph_rows = list(build.get("glyphs") or [])
    check(failures, "glyph_count", len(glyph_rows) == 6)
    code_to_char: dict[int, str] = {}
    e0_count = 0
    one_count = 0
    for row in glyph_rows:
        stolen = int(str(row["stolen_code"]), 16)
        char = str(row["hangul"])
        code_to_char[stolen] = char
        kind = str(row.get("kind") or "")
        if kind == "e0_kanji":
            e0_count += 1
            check(failures, "e0_not_e5", (stolen >> 8) != 0xE5, code=f"{stolen:04X}")
            check(failures, "e0_not_e519", stolen != 0xE519, code=f"{stolen:04X}")
            check(failures, "e0_not_e078", stolen != 0xE078, code=f"{stolen:04X}")
        elif kind == "onebyte":
            one_count += 1
            check(failures, "onebyte_range", 0x02 <= stolen <= 0xDF, code=f"{stolen:02X}")
        source = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        offset = compact_glyph_offset(candidate, stolen)
        got = bytes(candidate[offset : offset + 16])
        old = bytes(parent[offset : offset + 16])
        check(failures, "glyph_copied", got == source, hangul=char, code=f"{stolen:04X}")
        check(failures, "glyph_changed", got != old, hangul=char, code=f"{stolen:04X}")
    check(failures, "two_e0_glyphs", e0_count == 2)
    check(failures, "four_onebyte_glyphs", one_count == 4)

    proof = list((build.get("dictionary") or {}).get("proof") or [])
    check(failures, "one_retired_slot", len(proof) == 1)
    slot_index = int(str(proof[0]["index"]), 16) if proof else -1
    space_token = token_from_dict_index(slot_index) if slot_index >= 0 else b""
    space_raw = bytes(d_candidate.raw_entry(slot_index)) if slot_index >= 0 else b""
    check(failures, "space_no_e5", space_raw[:1] != b"\xE5" and space_raw[2:3] != b"\xE5")
    check(failures, "space_no_e519", b"\xE5\x19" not in space_raw)
    check(
        failures,
        "space_slot_render",
        decode_mapped(space_raw, code_to_char, d_candidate, tbl) == SPACE_TEXT,
        actual=decode_mapped(space_raw, code_to_char, d_candidate, tbl) if space_raw else "",
    )

    e0_by_char = {
        str(row["hangul"]): int(str(row["stolen_code"]), 16)
        for row in glyph_rows
        if row.get("kind") == "e0_kanji"
    }
    one_by_char = {
        str(row["hangul"]): int(str(row["stolen_code"]), 16)
        for row in glyph_rows
        if row.get("kind") == "onebyte"
    }
    expected_abaoa = b""
    if all(char in one_by_char for char in ("아", "바", "오", "쿠")):
        expected_abaoa = bytes(
            [
                one_by_char["아"],
                0x01,
                one_by_char["바"],
                one_by_char["오"],
                one_by_char["아"],
                0x01,
                one_by_char["쿠"],
            ]
        )

    for logical in SPACE_SITES:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        check(failures, "space_before", before == SPACE_BEFORE, abs=f"{logical:06X}")
        check(failures, "space_after", after == space_token, abs=f"{logical:06X}")
        check(
            failures,
            "space_term",
            before_term == after_term == logical + 2,
            abs=f"{logical:06X}",
        )
        check(
            failures,
            "space_render",
            decode_mapped(after, code_to_char, d_candidate, tbl) == SPACE_TEXT,
            abs=f"{logical:06X}",
        )
    for logical in ABAOA_SITES:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        check(failures, "abaoa_before", before == ABAOA_BEFORE[logical], abs=f"{logical:06X}")
        check(failures, "abaoa_after", after == expected_abaoa, abs=f"{logical:06X}")
        check(
            failures,
            "abaoa_term",
            before_term == after_term == logical + len(before),
            abs=f"{logical:06X}",
        )
        check(
            failures,
            "abaoa_render",
            decode_mapped(after, code_to_char, d_candidate, tbl) == ABAOA_TEXT,
            abs=f"{logical:06X}",
            actual=decode_mapped(after, code_to_char, d_candidate, tbl),
        )
        check(failures, "abaoa_no_e5", 0xE5 not in after, abs=f"{logical:06X}")

    union = build_reference_union(
        original,
        candidate,
        regions=("script", "name75", "aux"),
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    keepers = set(SPACE_SITES)
    unexpected_working = [
        f"{c.abs:06X}/{c.region}"
        for c in union.consumers_for(slot_index)
        if "working" in c.seen_in and c.abs not in keepers
    ]
    check(
        failures,
        "working_keepers_only",
        not unexpected_working,
        extra=unexpected_working[:8],
    )

    runs = diff_runs(parent, candidate)
    declared: list[tuple[int, int]] = []
    for row in glyph_rows:
        offset = int(str(row["compact_offset"]), 16)
        declared.append((offset, offset + 16))
    if proof:
        start = int(str(proof[0]["phrase_file_start"]), 16)
        end = int(str(proof[0]["phrase_file_end"]), 16)
        declared.append((start, end))
        check(
            failures,
            "slot_pointer_unchanged",
            d_candidate.ptrs[slot_index] == d_parent.ptrs[slot_index],
        )
    sb = stock_base(candidate)
    for logical in list(SPACE_SITES) + list(ABAOA_SITES):
        payload = payload_at(candidate, logical)[0]
        start = sb + logical
        declared.append((start, start + len(payload)))
    declared.append((len(candidate) - 2, len(candidate)))
    unexpected = [run for run in runs if not covered(run, declared)]
    check(
        failures,
        "unexpected_diff",
        not unexpected,
        runs=[f"{lo:08X}-{hi:08X}" for lo, hi in unexpected[:8]],
    )

    document = {
        "schema_version": 1,
        "generated_by": "tools/audit_terrain_space_abaoaqu_e0_onebyte_candidate.py",
        "ok": not failures,
        "failures": failures,
        "candidate_sha256": sha256(candidate),
        "parent_sha256": sha256(parent),
        "diff_runs": len(runs),
        "diff_bytes": sum(hi - lo for lo, hi in runs),
        "failure_count": len(failures),
        "e0_codes": {char: f"{code:04X}" for char, code in e0_by_char.items()},
        "onebyte_codes": {char: f"{code:02X}" for char, code in one_by_char.items()},
        "space_slot": f"{slot_index:04X}" if slot_index >= 0 else None,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"ok": document["ok"], "failures": len(failures)}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
