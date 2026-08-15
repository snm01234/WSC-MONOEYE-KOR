#!/usr/bin/env python3
"""Fail-closed audit for ui_onebyte_and_map_padding_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_ui_onebyte_and_map_padding_candidate import (
    ATTACK_ABS,
    ATTACK_CODE,
    EMPTY_STOCK,
    EXPECTED_MAIN,
    GOBUN_INDEX,
    HANGUL_BUN,
    HANGUL_GONG,
    KIBUN_INDEX,
    LOCATION_LONG_PAD,
    MAX_VISIBLE_PAD,
    MINUTE_ABS,
    MINUTE_STEAL,
    hangul_glyph_offset,
    payload_at,
    read_glyph,
)
from monoeye_rom import (
    Tbl,
    compact_font_file_offset,
    load_rom,
    stock_base,
    token_from_dict_index,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = ROOT / "out/patch/ui_onebyte_and_map_padding_candidate.wsc"
CAND_SAVE = ROOT / "sram/ui_onebyte_and_map_padding_candidate.sav"
REPORT = ROOT / "out/patch/ui_onebyte_and_map_padding_candidate_report.json"
OUT = ROOT / "out/patch/ui_onebyte_and_map_padding_candidate_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parent = bytes(load_rom(MAIN))
    target = CAND.read_bytes()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    failures: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **detail: Any) -> None:
        row = {"name": name, "ok": bool(ok), **detail}
        checks.append(row)
        if not ok:
            failures.append(row)

    check("parent_unchanged", sha(parent) == EXPECTED_MAIN, sha256=sha(parent))
    check("candidate_matches_report", sha(target) == report["candidate"]["sha256"])
    check("saveram_byte_exact", CAND_SAVE.read_bytes() == MAIN_SAVE.read_bytes())
    check("report_ok", report.get("ok") is True)

    gong = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_GONG))
    bun = read_glyph(parent, hangul_glyph_offset(parent, HANGUL_BUN))
    check(
        "C6_glyph_is_공",
        bytes(target[compact_font_file_offset(ATTACK_CODE) : compact_font_file_offset(ATTACK_CODE) + 16])
        == gong,
    )
    check(
        "DF_glyph_is_분",
        bytes(target[compact_font_file_offset(MINUTE_STEAL) : compact_font_file_offset(MINUTE_STEAL) + 16])
        == bun,
    )
    check("75B3EF_still_C6", payload_at(target, ATTACK_ABS)[0] == bytes([ATTACK_CODE]))
    check("75B559_is_DF", payload_at(target, MINUTE_ABS)[0] == bytes([MINUTE_STEAL]))
    check("気分_preserved", dictionary.expand_index(KIBUN_INDEX, tbl) == "気分")
    check("五分五分_preserved", dictionary.expand_index(GOBUN_INDEX, tbl) == "五分五分")
    check("00A9_zero_width", dictionary.expand_index(EMPTY_STOCK, tbl) == "")
    check("F0A9_token", token_from_dict_index(EMPTY_STOCK) == bytes.fromhex("F0A9"))

    earth, _term = payload_at(target, 0x75BDD0)
    check(
        "earth_orbit_route",
        dictionary.expand(earth, tbl).rstrip("　 \t") == "지구　궤도　항로",
        text=dictionary.expand(earth, tbl),
        pad=len(earth) - len(earth.rstrip(b"\x01")),
    )
    for logical in LOCATION_LONG_PAD:
        payload, _term = payload_at(target, logical)
        pad = len(payload) - len(payload.rstrip(b"\x01"))
        check(
            f"{logical:06X}_short_pad",
            pad <= MAX_VISIBLE_PAD,
            pad=pad,
            text=dictionary.expand(payload, tbl),
        )

    overlay = Tbl(
        {**tbl.code_to_char, ATTACK_CODE: "공", MINUTE_STEAL: "분"},
        {**tbl.char_to_code, "공": ATTACK_CODE, "분": MINUTE_STEAL},
    )
    check(
        "overlay_75B3EF_공",
        dictionary.expand(payload_at(target, ATTACK_ABS)[0], overlay) == "공",
    )
    check(
        "overlay_75B559_분",
        dictionary.expand(payload_at(target, MINUTE_ABS)[0], overlay) == "분",
    )

    document = {
        "ok": not failures,
        "candidate_sha256": sha(target),
        "checks": checks,
        "failures": failures,
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"ok": document["ok"], "failure_names": [row["name"] for row in failures]},
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
