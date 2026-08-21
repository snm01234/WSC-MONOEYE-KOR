#!/usr/bin/env python3
"""Stemspace14 with all historical compact-Hangul UI aliases routed to 8x16 Hangul.

Historical UI fixes bypass the normal Hangul marker/tag path:
* C6  -> standalone 공 (old 攻 glyph steal)
* DF  -> standalone 분 (old 了 glyph steal)
* E511/E51B in stock slot 0B68 -> 근전
* E51C/E51B in stock slot 0C47 -> 사전

Those compact glyphs were copied from the then-active Galmuri7 8x8 records and
therefore still pass through the stock vertical doubler.  Normal Korean in the
Galmuri11Bitmap Condensed stemspace14 POC is a precomputed native 8x16 glyph,
so the historical aliases look visibly thicker/different.

This candidate preserves every text record, dictionary token, compact glyph and
palette/LUT byte.  The primary cave becomes a tiny dispatcher into the existing
helper.  The helper maps only the five historical compact aliases to the same
ordinary Hangul glyph indices used by stemspace14.  Tagged Hangul follows the
existing path; every other untagged JP/UI glyph executes the original stock
shl/add -> 7A:052B path byte-for-byte.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_dialogue_galmuri11bitmap_condensed_8x16_exactlut_poc as base
import build_dialogue_galmuri11bitmap_condensed_stemspace14_poc as stem
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import read_encoded_z_safe, stock_base
from patch_font_hangul_hook import (
    CODE_SEG_7A,
    HANGUL_PRIMARY_BUDGET,
    PRIMARY_RETURN,
    far_jmp,
    patch_rel8,
)

OUT = ROOT / "out/patch/dialogue_galmuri11bitmap_condensed_stemspace14_legacy_ui_sync_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_galmuri11bitmap_condensed_stemspace14_legacy_ui_sync_candidate.sav"
REPORT = ROOT / "out/patch/dialogue_galmuri11bitmap_condensed_stemspace14_legacy_ui_sync_report.json"
PREVIEW = ROOT / "out/patch/dialogue_galmuri11bitmap_condensed_stemspace14_legacy_ui_sync_preview"

# Runtime glyph indices before the stock *16 address calculation.
ALIASES = (
    # source glyph index, target Hangul code, label, historical role
    (0x00C6, 0xE746, "공", "standalone 攻 -> 공"),
    (0x00DF, 0xE77C, "분", "standalone 分 via stolen 了 -> 분"),
    (0xE511 - 0xDF20, 0xE8B0, "근", "근전 compact first glyph"),
    (0xE51B - 0xDF20, 0xE745, "전", "근전/사전 shared compact second glyph"),
    (0xE51C - 0xDF20, 0xE751, "사", "사전 compact first glyph"),
)

EXPECTED_SPECIAL_STORAGE = {
    0x0B68: bytes.fromhex("E511E51B"),
    0x0C47: bytes.fromhex("E51CE51B"),
}
EXPECTED_UI_RECORDS = {
    0x75B3FD: bytes.fromhex("FB68"),
    0x75B401: bytes.fromhex("FC47"),
}

ORIGINAL_BASE_HELPER = base.build_helper


def target_index(hangul_code: int) -> int:
    return hangul_code - 0xDF20


def build_primary_dispatch(helper_off: int, helper_seg: int | None = None) -> bytes:
    """Restore current bank then let helper classify tagged/special/stock glyphs."""
    target_seg = base.HELPER_SEG if helper_seg is None else helper_seg
    out = bytearray()
    out += b"\x9A" + struct.pack("<HH", base.legacy.RESTORE_OFF, base.legacy.EXT_CAVE_SEG)
    out += far_jmp(helper_off & 0xFFFF, target_seg)
    if len(out) > HANGUL_PRIMARY_BUDGET:
        raise base.BuildError(f"primary dispatcher {len(out)} > {HANGUL_PRIMARY_BUDGET}")
    return bytes(out)


def build_helper_with_aliases() -> bytes:
    """Classify historical aliases, then fall into the proven stemspace helper."""
    original = ORIGINAL_BASE_HELPER()
    prefix = bytearray()

    # Provenance-tagged Hangul goes straight to the existing helper body.
    prefix += b"\xF7\xC3\x00\x80"  # test bx,8000
    tagged_at = len(prefix)
    prefix += b"\x75\x00"  # jnz common

    alias_jumps: list[tuple[int, int]] = []
    for source_index, hangul_code, _ko, _role in ALIASES:
        prefix += b"\x81\xFB" + struct.pack("<H", source_index)
        je_at = len(prefix)
        prefix += b"\x74\x00"
        alias_jumps.append((je_at, target_index(hangul_code)))

    # Every other untagged glyph stays on the exact stock address-calculation path.
    prefix += b"\xC1\xE3\x04"  # shl bx,4
    prefix += b"\x03\xD3"       # add dx,bx
    prefix += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)

    route_offsets: list[tuple[int, int]] = []
    for je_at, mapped_index in alias_jumps:
        route = len(prefix)
        patch_rel8(prefix, je_at, route)
        prefix += b"\xBB" + struct.pack("<H", mapped_index)  # mov bx,target glyph index
        jmp_common = len(prefix)
        prefix += b"\xEB\x00"
        route_offsets.append((jmp_common, mapped_index))

    common = len(prefix)
    patch_rel8(prefix, tagged_at, common)
    for jmp_common, _mapped_index in route_offsets:
        patch_rel8(prefix, jmp_common, common)

    result = bytes(prefix) + original
    if len(result) > base.HELPER_MAX:
        raise base.BuildError(f"alias helper {len(result)} > {base.HELPER_MAX}")
    return result


def verify_historical_storage(parent: bytes) -> dict[str, object]:
    ext = load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json")
    ext3 = load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json")
    d = make_dictionary_ext3(parent, ext, ext3)
    sb = stock_base(parent)
    slots = {}
    for idx, expected in EXPECTED_SPECIAL_STORAGE.items():
        raw = bytes(d.raw_entry(idx))
        if raw != expected:
            raise base.BuildError(f"historical compact slot {idx:04X} drifted: {raw.hex().upper()}")
        slots[f"{idx:04X}"] = raw.hex().upper()
    records = {}
    for logical, expected in EXPECTED_UI_RECORDS.items():
        got = read_encoded_z_safe(parent, sb + logical, max_len=16)
        if got is None:
            raise base.BuildError(f"UI record {logical:06X} unreadable")
        payload = bytes(got[0])
        if payload != expected:
            raise base.BuildError(f"UI record {logical:06X} drifted: {payload.hex().upper()}")
        records[f"{logical:06X}"] = payload.hex().upper()
    return {"dictionary_slots": slots, "ui_records": records}


def main() -> int:
    # Build always starts from the unchanged current main, while retaining the
    # already user-validated 공/분 routing as part of this cumulative candidate.
    parent = base.MAIN.read_bytes()
    historical = verify_historical_storage(parent)

    base.OUT = OUT
    base.OUT_SAVE = OUT_SAVE
    base.REPORT = REPORT
    base.PREVIEW = PREVIEW
    base.render_condensed = stem.render_stemspace14

    old_primary = base.legacy.build_primary_cave
    old_helper = base.build_helper
    base.legacy.build_primary_cave = build_primary_dispatch
    base.build_helper = build_helper_with_aliases
    try:
        rc = base.main()
    finally:
        base.legacy.build_primary_cave = old_primary
        base.build_helper = old_helper

    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    rep["weight_strategy"] = {
        "name": "adaptive_stem_or_space_repeat14",
        "source_metric_window": "fixed rows 2..12 (11 rows)",
        "output_content_height": 14,
        "insert_count": 3,
        "insert_rule": "duplicate short vertical-stem rows; use interior blank rows when needed; never duplicate long horizontal bars",
        "new_pixel_policy": "forbidden: every output row is an exact original Condensed row",
    }
    rep["legacy_compact_ui_sync"] = {
        "root_cause": (
            "공/분/근/전/사 were historically baked into untagged compact 8x8 glyph codes; "
            "they therefore stayed on the stock vertical doubler instead of native stemspace14"
        ),
        "historical_storage": historical,
        "policy": (
            "preserve all UI record bytes, dictionary payloads and compact glyph records; "
            "runtime-map only the five legacy compact glyph indices to their ordinary Hangul stemspace14 indices"
        ),
        "routes": [
            {
                "source_glyph_index": f"{source:04X}",
                "target_hangul_code": f"{hangul:04X}",
                "target_glyph_index": f"{target_index(hangul):04X}",
                "target_slot": target_index(hangul) - base.BASE_INDEX,
                "ko": ko,
                "role": role,
            }
            for source, hangul, ko, role in ALIASES
        ],
        "records_modified": False,
        "dictionary_slots_modified": False,
        "compact_glyph_records_modified": False,
        "other_untagged_ui_stock_path_preserved": True,
    }
    rep["candidate"]["path"] = str(OUT.relative_to(ROOT)).replace("\\", "/")
    rep["save"]["path"] = str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/")
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
