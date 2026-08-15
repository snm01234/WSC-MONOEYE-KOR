#!/usr/bin/env python3
"""Independent read-only audit of the terrain-info 우주 / 아 바오아 쿠 candidate."""
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
from build_terrain_space_abaoaqu_compact_glyph_candidate import (  # noqa: E402
    ABAOA_BEFORE,
    ABAOA_FRAGMENTS,
    ABAOA_SITES,
    ABAOA_TEXT,
    EMPTY_STOCK,
    EXPECTED_MAIN_SHA256,
    HANGUL_SOURCE,
    SPACE_BEFORE,
    SPACE_SITES,
    SPACE_TEXT,
    STEAL_ORDER,
    compact_glyph_offset,
    decode_stolen,
    hangul_glyph_offset,
    payload_at,
    read_glyph,
)
from monoeye_rom import Tbl, load_rom, stock_base, token_from_dict_index  # noqa: E402

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/terrain_space_abaoaqu_compact_glyph_candidate.wsc"
SAVE = ROOT / "sram/terrain_space_abaoaqu_compact_glyph_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD_REPORT = ROOT / "out/patch/terrain_space_abaoaqu_compact_glyph_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/terrain_space_abaoaqu_compact_glyph_audit.json"
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
    check(failures, "slot_008F_preserved", bytes(d_candidate.raw_entry(0x008F)) == bytes(d_parent.raw_entry(0x008F)))
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

    glyph_rows = list(build.get("glyphs") or [])
    check(failures, "glyph_count", len(glyph_rows) == len(STEAL_ORDER))
    code_to_char: dict[int, str] = {}
    for row, char in zip(glyph_rows, STEAL_ORDER):
        stolen = int(str(row["stolen_code"]), 16)
        code_to_char[stolen] = char
        source = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        offset = compact_glyph_offset(candidate, stolen)
        got = bytes(candidate[offset : offset + 16])
        old = bytes(parent[offset : offset + 16])
        check(failures, "glyph_copied", got == source, hangul=char, code=f"{stolen:04X}")
        check(failures, "glyph_changed", got != old, hangul=char, code=f"{stolen:04X}")

    proof = list((build.get("dictionary") or {}).get("proof") or [])
    slot_by_phrase = {str(row["phrase"]): int(str(row["index"]), 16) for row in proof}
    check(failures, "space_slot", SPACE_TEXT in slot_by_phrase)
    for fragment in ABAOA_FRAGMENTS:
        check(failures, f"{fragment}_slot", fragment in slot_by_phrase)
    if SPACE_TEXT in slot_by_phrase:
        space_token = token_from_dict_index(slot_by_phrase[SPACE_TEXT])
        space_raw = bytes(d_candidate.raw_entry(slot_by_phrase[SPACE_TEXT]))
        check(
            failures,
            "space_slot_render",
            decode_stolen(space_raw, code_to_char, d_candidate, tbl) == SPACE_TEXT,
            actual=decode_stolen(space_raw, code_to_char, d_candidate, tbl),
        )
    else:
        space_token = b""
    if all(fragment in slot_by_phrase for fragment in ABAOA_FRAGMENTS):
        abaoa_body = (
            token_from_dict_index(slot_by_phrase["아"])
            + b"\x01"
            + token_from_dict_index(slot_by_phrase["바오"])
            + token_from_dict_index(slot_by_phrase["아쿠"])
        )
        for fragment in ABAOA_FRAGMENTS:
            raw = bytes(d_candidate.raw_entry(slot_by_phrase[fragment]))
            check(
                failures,
                f"{fragment}_slot_render",
                decode_stolen(raw, code_to_char, d_candidate, tbl) == fragment,
                actual=decode_stolen(raw, code_to_char, d_candidate, tbl),
            )
    else:
        abaoa_body = b""

    for logical in SPACE_SITES:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        check(failures, "space_before", before == SPACE_BEFORE, abs=f"{logical:06X}")
        check(failures, "space_after", after == space_token, abs=f"{logical:06X}")
        check(failures, "space_term", before_term == after_term == logical + 2, abs=f"{logical:06X}")
        check(
            failures,
            "space_render",
            decode_stolen(after, code_to_char, d_candidate, tbl) == SPACE_TEXT,
            abs=f"{logical:06X}",
        )
    for logical in ABAOA_SITES:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        check(failures, "abaoa_before", before == ABAOA_BEFORE[logical], abs=f"{logical:06X}")
        check(failures, "abaoa_after", after == abaoa_body, abs=f"{logical:06X}")
        check(
            failures,
            "abaoa_term",
            before_term == after_term == logical + len(before),
            abs=f"{logical:06X}",
        )
        check(
            failures,
            "abaoa_render",
            decode_stolen(after, code_to_char, d_candidate, tbl) == ABAOA_TEXT,
            abs=f"{logical:06X}",
            actual=decode_stolen(after, code_to_char, d_candidate, tbl),
        )

    report_records = {int(row["abs"], 16): row for row in build.get("records") or []}
    check(failures, "record_count", len(report_records) == len(SPACE_SITES) + len(ABAOA_SITES))

    runs = diff_runs(parent, candidate)
    declared: list[tuple[int, int]] = []
    for row in glyph_rows:
        offset = int(str(row["compact_offset"]), 16)
        declared.append((offset, offset + 16))
    for row in proof:
        start = int(str(row["phrase_file_start"]), 16)
        end = int(str(row["phrase_file_end"]), 16)
        declared.append((start, end))
        index = int(str(row["index"]), 16)
        check(
            failures,
            "slot_pointer_unchanged",
            d_candidate.ptrs[index] == d_parent.ptrs[index],
            index=f"{index:04X}",
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
        "generated_by": "tools/audit_terrain_space_abaoaqu_compact_glyph_candidate.py",
        "ok": not failures,
        "failures": failures,
        "candidate_sha256": sha256(candidate),
        "parent_sha256": sha256(parent),
        "diff_runs": len(runs),
        "diff_bytes": sum(hi - lo for lo, hi in runs),
        "failure_count": len(failures),
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
