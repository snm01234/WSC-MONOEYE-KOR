#!/usr/bin/env python3
"""Terrain-info popup via 7A:0610 one-byte compact codes only.

Runtime: the top line uses 7A:0610 (cmp AX,E000 / sub DF20 / 40:0440) and
does not honour EC8D or 7A:0521 hangul pads.  Codes >= E0 are one 2-byte
glyph, so F08F / E518 / F0xx never become 우주 / 아 바오아 쿠.

Vanilla-font candidate kept F08F and wrote F632/F050.  After reset: 우주
still ぉ, 아바오아쿠 became で人メあ ぇ쿠何 — only global C5→쿠 paint
landed.  This candidate writes *only* 02–DF bytes at the known sites.

Parent is live main (name+spirit combined).  Main TIP and live SaveRAM
are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_terrain_space_abaoaqu_e0_onebyte_candidate import (  # noqa: E402
    HUD_RANGES,
    KANA_CHARS,
    compact_glyph_offset,
    hangul_glyph_offset,
    payload_at,
    read_glyph,
    walk_units,
)
from expand_dictionary import _walk_zstring_range  # noqa: E402
from mixed_residual_reference_union import _reference_scopes  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    decode_compact_font_record,
    is_kanji_lead,
    load_rom,
    stock_base,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/terrain_popup_0610_onebyte_candidate.wsc"
OUT_SAVE = ROOT / "sram/terrain_popup_0610_onebyte_candidate.sav"
OUT_REPORT = ROOT / "out/patch/terrain_popup_0610_onebyte_report.json"

EXPECTED_MAIN_SHA256 = (
    "528f28e1050257e9f3698f27cf9aa577b217c67cd8951d6030cc5592fc6e0e85"
)
EXPECTED_SAVE_SHA256 = (
    "c0056b393cc669032ae19b88c33d8cffa861b49ef7de69d402569b72f11326dd"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

HANGUL_SOURCE = {
    "우": 0xE761,
    "주": 0xE764,
    "아": 0xE79E,
    "바": 0xE7C3,
    "오": 0xE754,
    "쿠": 0xE7F1,
}
SPACE_TEXT = "우주"
ABAOA_TEXT = "아　바오아　쿠"
SPACE_SITES = (0x75B3CE, 0x75E59A)
ABAOA_SITES = (0x75E58C, 0x75BD77)
SPACE_BEFORE = bytes.fromhex("F08F")
ABAOA_BEFORE = {
    0x75E58C: bytes.fromhex("E518EC41010101"),
    0x75BD77: bytes.fromhex("E518B445F0A901"),
}
TAKEN_ONEBYTE = {
    0x00,
    0x01,
    0x18,
    0x19,
    0x1F,
    0x26,
    0x2A,
    0xC6,
    0xDF,
    0x59,
    0xBC,
    0xB2,
    0xC2,
    0x90,
    0x41,
    0xD7,
    0xC5,
}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def decode_onebyte(payload: bytes, code_to_char: dict[int, str], tbl: Tbl) -> str:
    parts: list[str] = []
    for lead in payload:
        if lead >= 0xE0:
            raise BuildError(f"0610 payload still has E0+ lead {lead:02X}")
        if lead == 0x01:
            parts.append("　")
            continue
        parts.append(code_to_char.get(lead, tbl.decode_char(lead)))
    return "".join(parts)


def glyph_usable(rom: bytes, code: int) -> tuple[bool, int, int]:
    offset = compact_glyph_offset(rom, code)
    record = bytes(rom[offset : offset + 16])
    if len(record) != 16 or record == b"\xFF" * 16:
        return False, 0, offset
    ink = sum(sum(row) for row in decode_compact_font_record(record))
    return ink > 0, ink, offset


def select_onebyte_codes(
    parent: bytes,
    d_parent: Dictionary,
    tbl: Tbl,
    needed: int,
) -> list[dict[str, Any]]:
    """HUD-unused 1-byte compact codes. dict_hits<=1 is allowed (오/쿠 on vanilla)."""
    dict_one: Counter[int] = Counter()
    hud_one: Counter[int] = Counter()
    script_one: Counter[int] = Counter()
    for index in range(d_parent.count):
        try:
            raw = bytes(d_parent.raw_entry(index))
        except Exception:
            continue
        for kind, value in walk_units(raw, ext3_aware=True):
            if kind == "one":
                dict_one[value] += 1
    for lo, hi in HUD_RANGES:
        for _logical, payload, _kind in _walk_zstring_range(
            parent, lo, hi, region="name75", max_len=64
        ):
            for kind, value in walk_units(payload, ext3_aware=True):
                if kind == "one":
                    hud_one[value] += 1
    for region, lo, hi, max_len in _reference_scopes():
        if region != "script":
            continue
        for _logical, payload, _kind in _walk_zstring_range(
            parent, lo, hi, region=region, max_len=max_len
        ):
            for kind, value in walk_units(payload, ext3_aware=True):
                if kind == "one":
                    script_one[value] += 1
    ranked: list[dict[str, Any]] = []
    for code in range(0x02, 0xE0):
        if code in TAKEN_ONEBYTE:
            continue
        if hud_one[code]:
            continue
        if dict_one[code] > 8:
            continue
        char = tbl.decode_char(code)
        if char in KANA_CHARS:
            continue
        ok, ink, offset = glyph_usable(parent, code)
        if not ok:
            continue
        ranked.append(
            {
                "code": code,
                "code_hex": f"{code:02X}",
                "old_char": char,
                "old_glyph_hex": bytes(parent[offset : offset + 16]).hex().upper(),
                "compact_offset": f"{offset:06X}",
                "ink": ink,
                "kind": "onebyte",
                "script_collateral": script_one[code],
                "dict_hits": dict_one[code],
                "hud_hits": hud_one[code],
            }
        )
    ranked.sort(key=lambda row: (int(row["script_collateral"]), int(row["dict_hits"]), int(row["code"])))
    if len(ranked) < needed:
        raise BuildError(
            f"need {needed} HUD-unused 1-byte codes, found {len(ranked)}"
        )
    return ranked[:needed]


def extra_pattern_sites(rom: bytes, sb: int, patterns: dict[int, bytes]) -> list[int]:
    """Find extra copies of the known 7-byte / 2-byte payloads in bank 75."""
    found: list[int] = []
    bank75 = sb + 0x750000
    view = bytes(rom[bank75 : bank75 + 0x10000])
    wanted = set(patterns.values())
    for logical, needle in patterns.items():
        start = 0
        while True:
            hit = view.find(needle, start)
            if hit < 0:
                break
            abs_ = 0x750000 + hit
            if abs_ not in found:
                found.append(abs_)
            start = hit + 1
    return found


def main() -> int:
    parent = bytes(load_rom(MAIN))
    live_save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file() or len(live_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM is missing or not 32 KiB")
    if sha256(live_save) != EXPECTED_SAVE_SHA256:
        raise BuildError("live SaveRAM identity drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)
    if sb != 0x800000:
        raise BuildError("parent is not 16MB stock_base")
    if compact_glyph_offset(parent, 0xC5) < 0x800000:
        raise BuildError("compact_glyph_offset used 8MB base")
    if bytes(d_parent.raw_entry(0x008F)) != bytes.fromhex("EC8DE761E764"):
        raise BuildError("slot 008F 우주 payload drifted")

    for logical, expected in (
        *((site, SPACE_BEFORE) for site in SPACE_SITES),
        *ABAOA_BEFORE.items(),
    ):
        payload, terminator = payload_at(parent, logical)
        if payload != expected:
            raise BuildError(
                f"{logical:06X} drifted: {payload.hex().upper()} != {expected.hex().upper()}"
            )
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator drifted")

    space_neighbors = {
        0x75B3CA: payload_at(parent, 0x75B3CA)[0],
        0x75B3D1: payload_at(parent, 0x75B3D1)[0],
        0x75B457: payload_at(parent, 0x75B457)[0],
        0x75B75E: payload_at(parent, 0x75B75E)[0],
    }

    extra = extra_pattern_sites(parent, sb, dict(ABAOA_BEFORE))
    abaoa_sites = list(ABAOA_SITES)
    space_sites = list(SPACE_SITES)
    for abs_ in extra:
        if abs_ in abaoa_sites:
            continue
        payload, _term = payload_at(parent, abs_)
        if payload in ABAOA_BEFORE.values():
            abaoa_sites.append(abs_)

    one_rows = select_onebyte_codes(parent, d_parent, tbl, 6)
    letters = ("우", "주", "아", "바", "오", "쿠")
    for row, char in zip(one_rows, letters):
        row["hangul"] = char
        row["hangul_source_hex"] = f"{HANGUL_SOURCE[char]:04X}"
        if int(row["code"]) >= 0xE0:
            raise BuildError("1-byte steal escaped 02-DF")
        if int(row["code"]) in {0xE5, 0xF0, 0x00, 0x18, 0x19}:
            raise BuildError("refusing control/ext lead as 1-byte steal")

    char_to_code = {str(row["hangul"]): int(row["code"]) for row in one_rows}
    code_to_char = {code: char for char, code in char_to_code.items()}
    space_body = bytes([char_to_code["우"], char_to_code["주"]])
    abaoa_body = bytes(
        [
            char_to_code["아"],
            0x01,
            char_to_code["바"],
            char_to_code["오"],
            char_to_code["아"],
            0x01,
            char_to_code["쿠"],
        ]
    )
    for body, name in ((space_body, "우주"), (abaoa_body, "아바오아쿠")):
        if any(b >= 0xE0 or b in {0x00, 0x18, 0x19} for b in body):
            raise BuildError(f"{name} body has E0+/NUL/18/19")
        if 0xE5 in body or 0xF0 in body:
            raise BuildError(f"{name} body contains E5 or F0")
    if len(space_body) != 2 or len(abaoa_body) != 7:
        raise BuildError("payload length drifted")

    hangul_glyphs = {
        char: read_glyph(parent, hangul_glyph_offset(parent, HANGUL_SOURCE[char]))
        for char in letters
    }

    candidate = bytearray(parent)
    allow: list[tuple[int, int]] = []
    glyph_writes: list[dict[str, Any]] = []
    for row in one_rows:
        char = str(row["hangul"])
        offset = compact_glyph_offset(candidate, int(row["code"]))
        glyph = hangul_glyphs[char]
        if bytes(candidate[offset : offset + 16]) == glyph:
            raise BuildError(f"{row['code_hex']} already holds {char}")
        candidate[offset : offset + 16] = glyph
        allow.append((offset, offset + 16))
        glyph_writes.append(
            {
                "hangul": char,
                "stolen_code": row["code_hex"],
                "old_char": row["old_char"],
                "kind": "onebyte_0610",
                "compact_offset": f"{offset:06X}",
                "source_hangul_code": row["hangul_source_hex"],
                "glyph_hex": glyph.hex().upper(),
                "script_collateral": row.get("script_collateral"),
            }
        )

    applied: list[dict[str, Any]] = []
    for logical in space_sites:
        old, terminator = payload_at(parent, logical)
        if len(space_body) != len(old):
            raise BuildError(f"{logical:06X} 우주 length changed")
        start = sb + logical
        candidate[start : start + len(space_body)] = space_body
        allow.append((start, start + len(space_body)))
        if candidate[sb + terminator] != 0:
            raise BuildError(f"{logical:06X} terminator changed")
        applied.append(
            {
                "abs": f"{logical:06X}",
                "phrase": SPACE_TEXT,
                "before_hex": old.hex().upper(),
                "after_hex": space_body.hex().upper(),
            }
        )
    for logical in abaoa_sites:
        old, terminator = payload_at(parent, logical)
        if len(abaoa_body) != len(old):
            raise BuildError(f"{logical:06X} 아 바오아 쿠 length changed")
        start = sb + logical
        candidate[start : start + len(abaoa_body)] = abaoa_body
        allow.append((start, start + len(abaoa_body)))
        if candidate[sb + terminator] != 0:
            raise BuildError(f"{logical:06X} terminator changed")
        applied.append(
            {
                "abs": f"{logical:06X}",
                "phrase": ABAOA_TEXT,
                "before_hex": old.hex().upper(),
                "after_hex": abaoa_body.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    allow.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allow)]
    if unexpected:
        raise BuildError(
            "diff outside allowlist: "
            + ", ".join(f"{lo:08X}-{hi:08X}" for lo, hi in unexpected)
        )

    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)
    if bytes(d_result.raw_entry(0x008F)) != bytes(d_parent.raw_entry(0x008F)):
        raise BuildError("slot 008F changed")
    for logical, old in space_neighbors.items():
        if payload_at(result, logical)[0] != old:
            raise BuildError(f"neighbor {logical:06X} changed")

    for logical in space_sites:
        payload, terminator = payload_at(result, logical)
        rendered = decode_onebyte(payload, code_to_char, tbl)
        if payload != space_body or rendered != SPACE_TEXT:
            raise BuildError(f"{logical:06X} 우주 render {payload.hex()} {rendered!r}")
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator moved")
        if is_kanji_lead(payload[0]) or any(b >= 0xE0 for b in payload):
            raise BuildError(f"{logical:06X} 우주 still has E0+ bytes")
    for logical in abaoa_sites:
        payload, terminator = payload_at(result, logical)
        rendered = decode_onebyte(payload, code_to_char, tbl)
        if payload != abaoa_body or rendered != ABAOA_TEXT:
            raise BuildError(
                f"{logical:06X} 아 바오아 쿠 render {payload.hex()} {rendered!r}"
            )
        if terminator != logical + len(payload):
            raise BuildError(f"{logical:06X} terminator moved")
        if any(b >= 0xE0 for b in payload):
            raise BuildError(f"{logical:06X} still has E0+ bytes")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if MAIN.read_bytes() != parent:
        raise BuildError("main TIP mutated")
    if MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("live SaveRAM mutated")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terrain_popup_0610_onebyte_candidate.py",
        "status": "candidate_static_verified_needs_target_screen_check",
        "ok": True,
        "main_tip_modified": False,
        "parent": identity(MAIN, parent),
        "candidate": {**identity(OUT_ROM, result), "checksum": f"{checksum:04X}"},
        "save": {
            "source": str(MAIN_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "policy": "copy_live_main_sav_at_build_time",
            "copied_latest": True,
            "sha256": sha256(OUT_SAVE.read_bytes()),
            "matches_live_pin": sha256(OUT_SAVE.read_bytes()) == EXPECTED_SAVE_SHA256,
        },
        "failed_previous": [
            {
                "path": "out/patch/terrain_space_abaoaqu_vanilla_font_candidate.wsc",
                "why": (
                    "kept F08F and wrote F632/F050; 7A:0610 treats >=E0 as one "
                    "kanji. After reset 우주 still ぉ, 아바오아쿠 で人メあ ぇ쿠何 "
                    "(only global C5 paint landed)"
                ),
            }
        ],
        "cause": {
            "hud": "7A:0610 compact 40:0440; codes >= E000 are 2-byte, no hangul pad",
            "space": "replace F08F with two 02-DF bytes at 75B3CE/75E59A",
            "abaoa": "replace E518… with seven 02-DF bytes, no F0/E5",
            "slot_008F": "unchanged (색적우주 still F506 F08F)",
        },
        "glyphs": glyph_writes,
        "payloads": {
            "space_hex": space_body.hex().upper(),
            "abaoa_hex": abaoa_body.hex().upper(),
            "space_sites": [f"{s:06X}" for s in space_sites],
            "abaoa_sites": [f"{s:06X}" for s in abaoa_sites],
            "extra_bank75_pattern_hits": [f"{s:06X}" for s in extra],
        },
        "records": applied,
        "verification": {
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "범용_preserved": True,
            "명중_보정_preserved": True,
            "slot_008F_preserved": True,
            "no_e0_f0_e5_in_payloads": True,
            "diff_runs": len(runs),
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "allowlist_clean": True,
        },
        "promote": "pending_user_screen_check",
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({"ok": True, "rom": report["candidate"], "save": report["save"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
