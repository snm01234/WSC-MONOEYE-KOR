#!/usr/bin/env python3
"""Independent read-only audit of the vanilla-font terrain HUD candidate."""
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
from build_terrain_space_abaoaqu_vanilla_font_candidate import (  # noqa: E402
    ABAOA_BEFORE,
    ABAOA_SITES,
    ABAOA_TEXT,
    AUX_U_BEFORE,
    AUX_U_SITE,
    EMPTY_STOCK,
    EXPECTED_MAIN_SHA256,
    EXPECTED_SAVE_SHA256,
    FAILED_ONEBYTE,
    HANGUL_SOURCE,
    PINNED_PARENT_FALLBACK,
    SEKI_SITES,
    SLOT_008F_AFTER,
    SLOT_008F_BEFORE,
    SLOT_SPACE,
    SPACE_BEFORE,
    SPACE_SITES,
    SPACE_TEXT,
    VANILLA_JU,
    VANILLA_U,
    compact_glyph_offset,
    decode_mapped,
    find_f58ce5,
    hangul_glyph_offset,
    payload_at,
    read_glyph,
    u16,
)
from mixed_residual_reference_union import build_reference_union  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    load_rom,
    set_stock_base,
    stock_base,
    token_from_dict_index,
)

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/terrain_space_abaoaqu_vanilla_font_candidate.wsc"
SAVE = ROOT / "sram/terrain_space_abaoaqu_vanilla_font_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BUILD_REPORT = ROOT / "out/patch/terrain_space_abaoaqu_vanilla_font_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/terrain_space_abaoaqu_vanilla_font_audit.json"
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

    parent_path = args.parent
    if sha256(parent_path.read_bytes()) != EXPECTED_MAIN_SHA256 and PINNED_PARENT_FALLBACK.is_file():
        parent_path = PINNED_PARENT_FALLBACK
    parent = bytes(load_rom(parent_path))
    candidate = bytes(load_rom(args.candidate))
    original = bytes(load_rom(ORIGINAL))
    set_stock_base(stock_base(parent))
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
        "live_save_pin",
        MAIN_SAVE.is_file() and sha256(MAIN_SAVE.read_bytes()) == EXPECTED_SAVE_SHA256,
    )
    check(
        failures,
        "pair_save_pin",
        SAVE.is_file() and sha256(SAVE.read_bytes()) == EXPECTED_SAVE_SHA256,
    )
    check(
        failures,
        "slot_008F_parent",
        bytes(d_parent.raw_entry(SLOT_SPACE)) == SLOT_008F_BEFORE,
    )
    check(
        failures,
        "slot_008F_restored",
        bytes(d_candidate.raw_entry(SLOT_SPACE)) == SLOT_008F_AFTER,
    )
    check(
        failures,
        "slot_008F_no_marker",
        b"\xEC\x8D" not in bytes(d_candidate.raw_entry(SLOT_SPACE)),
    )
    check(
        failures,
        "slot_008F_pointer_moved",
        d_candidate.ptrs[SLOT_SPACE] != d_parent.ptrs[SLOT_SPACE],
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
    for logical in SEKI_SITES:
        check(
            failures,
            "색적우주_site",
            payload_at(candidate, logical)[0] == payload_at(parent, logical)[0],
            abs=f"{logical:06X}",
        )
    check(
        failures,
        "F58CE5_pointers",
        find_f58ce5(parent) == find_f58ce5(candidate) and bool(find_f58ce5(parent)),
    )
    check(
        failures,
        "parent_compact_base",
        compact_glyph_offset(parent, VANILLA_U) >= 0x800000,
    )
    check(
        failures,
        "original_compact_base",
        compact_glyph_offset(original, VANILLA_U) < 0x800000,
    )

    glyph_rows = list(build.get("glyphs") or [])
    vanilla_count = 0
    one_count = 0
    preserve_count = 0
    code_to_char: dict[int, str] = {}
    one_by_char: dict[str, int] = {}
    for row in glyph_rows:
        stolen = int(str(row["stolen_code"]), 16)
        char = str(row["hangul"])
        code_to_char[stolen] = char
        kind = str(row.get("kind") or "")
        if kind == "vanilla_e0":
            vanilla_count += 1
            check(
                failures,
                "vanilla_code",
                stolen in {VANILLA_U, VANILLA_JU},
                code=f"{stolen:04X}",
            )
        elif kind == "e0_preserve_u":
            preserve_count += 1
            check(failures, "preserve_not_e5", (stolen >> 8) != 0xE5, code=f"{stolen:04X}")
            check(
                failures,
                "preserve_not_vanilla",
                stolen not in {VANILLA_U, VANILLA_JU, 0xE519},
                code=f"{stolen:04X}",
            )
        elif kind == "onebyte":
            one_count += 1
            one_by_char[char] = stolen
            check(failures, "onebyte_range", 0x02 <= stolen <= 0xDF, code=f"{stolen:02X}")
            check(
                failures,
                "onebyte_not_failed",
                stolen not in FAILED_ONEBYTE,
                code=f"{stolen:02X}",
            )
        if char in HANGUL_SOURCE:
            source = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        elif char == "宇":
            source = read_glyph(original, compact_glyph_offset(original, VANILLA_U))
        else:
            source = b""
        offset = compact_glyph_offset(candidate, stolen)
        got = bytes(candidate[offset : offset + 16])
        old = bytes(parent[offset : offset + 16])
        check(failures, "glyph_copied", got == source, hangul=char, code=f"{stolen:04X}")
        check(failures, "glyph_changed", got != old, hangul=char, code=f"{stolen:04X}")
    check(failures, "two_vanilla_glyphs", vanilla_count == 2)
    check(failures, "four_onebyte_glyphs", one_count == 4)
    check(failures, "one_preserve_glyph", preserve_count == 1)

    u_off = compact_glyph_offset(candidate, VANILLA_U)
    ju_off = compact_glyph_offset(candidate, VANILLA_JU)
    check(
        failures,
        "E078_is_우",
        bytes(candidate[u_off : u_off + 16])
        == read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE["우"])),
    )
    check(
        failures,
        "E046_is_주",
        bytes(candidate[ju_off : ju_off + 16])
        == read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE["주"])),
    )

    preserve_meta = build.get("preserve_u") or {}
    preserve_code = int(str(preserve_meta.get("preserve_code") or "0"), 16)
    aux_before, aux_before_term = payload_at(parent, AUX_U_SITE)
    aux_after, aux_after_term = payload_at(candidate, AUX_U_SITE)
    check(failures, "aux_before", aux_before == AUX_U_BEFORE)
    check(failures, "aux_no_e078", aux_after[0:2] != u16(VANILLA_U))
    check(failures, "aux_len", len(aux_after) == 4)
    check(
        failures,
        "aux_preserve_code",
        aux_after[0:2] == u16(preserve_code) and aux_after[2:] == AUX_U_BEFORE[2:],
    )
    check(failures, "aux_term", aux_before_term == aux_after_term == AUX_U_SITE + 4)
    check(
        failures,
        "aux_render",
        decode_mapped(aux_after, code_to_char, d_candidate, tbl) == "宇人質",
        actual=decode_mapped(aux_after, code_to_char, d_candidate, tbl),
    )

    path = str((build.get("dictionary") or {}).get("abaoa_path") or "")
    check(failures, "abaoa_path_known", path in {"f0_skeleton", "inline_fallback"})
    proof = list((build.get("dictionary") or {}).get("proof") or [])
    if path == "f0_skeleton":
        check(failures, "two_retired_slots", len(proof) == 2)
        expected_abaoa = b""
        if len(proof) == 2 and "아" in one_by_char:
            expected_abaoa = (
                bytes([one_by_char["아"], 0x01])
                + token_from_dict_index(int(str(proof[0]["index"]), 16))
                + bytes([0x01])
                + token_from_dict_index(int(str(proof[1]["index"]), 16))
            )
            union = build_reference_union(
                original,
                candidate,
                regions=("script", "name75", "aux"),
                ext_meta=ext_meta,
                ext3_meta=ext3_meta,
            )
            keepers = set(ABAOA_SITES)
            for row in proof:
                index = int(str(row["index"]), 16)
                unexpected_working = [
                    f"{c.abs:06X}/{c.region}"
                    for c in union.consumers_for(index)
                    if "working" in c.seen_in and c.abs not in keepers
                ]
                check(
                    failures,
                    "retired_keepers_only",
                    not unexpected_working,
                    slot=f"{index:04X}",
                    extra=unexpected_working[:8],
                )
                raw = bytes(d_candidate.raw_entry(index))
                check(failures, "retired_no_e519", b"\xE5\x19" not in raw)
                check(failures, "retired_no_marker", b"\xEC\x8D" not in raw)
    else:
        check(failures, "inline_has_no_retired", len(proof) == 0)
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
        check(failures, "space_after_F08F", after == SPACE_BEFORE, abs=f"{logical:06X}")
        check(failures, "space_not_F050", after != bytes.fromhex("F050"), abs=f"{logical:06X}")
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
            actual=decode_mapped(after, code_to_char, d_candidate, tbl),
        )
    for logical in ABAOA_SITES:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        check(failures, "abaoa_before", before == ABAOA_BEFORE[logical], abs=f"{logical:06X}")
        check(failures, "abaoa_after", after == expected_abaoa, abs=f"{logical:06X}")
        check(failures, "abaoa_len7", len(after) == 7, abs=f"{logical:06X}")
        expected_term = {0x75E58C: 0x75E593, 0x75BD77: 0x75BD7E}[logical]
        check(
            failures,
            "abaoa_term",
            before_term == after_term == expected_term,
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
        check(
            failures,
            "abaoa_no_failed_onebyte",
            all(code not in after for code in FAILED_ONEBYTE),
            abs=f"{logical:06X}",
        )

    runs = diff_runs(parent, candidate)
    declared: list[tuple[int, int]] = []
    for row in glyph_rows:
        offset = int(str(row["compact_offset"]), 16)
        declared.append((offset, offset + 16))
    slot_meta = (build.get("dictionary") or {}).get("slot_008F") or {}
    if slot_meta:
        declared.append(
            (
                int(str(slot_meta["phrase_file_start"]), 16),
                int(str(slot_meta["phrase_file_end"]), 16),
            )
        )
        declared.append(
            (
                int(str(slot_meta["pointer_file"]), 16),
                int(str(slot_meta["pointer_file"]), 16) + 2,
            )
        )
    for row in proof:
        declared.append(
            (
                int(str(row["phrase_file_start"]), 16),
                int(str(row["phrase_file_end"]), 16),
            )
        )
    sb = stock_base(candidate)
    declared.append((sb + AUX_U_SITE, sb + AUX_U_SITE + 2))
    for logical in ABAOA_SITES:
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
        "generated_by": "tools/audit_terrain_space_abaoaqu_vanilla_font_candidate.py",
        "ok": not failures,
        "failures": failures,
        "candidate_sha256": sha256(candidate),
        "parent_sha256": sha256(parent),
        "save_sha256": sha256(SAVE.read_bytes()) if SAVE.is_file() else None,
        "diff_runs": len(runs),
        "diff_bytes": sum(hi - lo for lo, hi in runs),
        "failure_count": len(failures),
        "abaoa_path": path,
        "onebyte_codes": {char: f"{code:02X}" for char, code in one_by_char.items()},
        "preserve_u": f"{preserve_code:04X}" if preserve_code else None,
        "slot_008F": bytes(d_candidate.raw_entry(SLOT_SPACE)).hex().upper(),
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
