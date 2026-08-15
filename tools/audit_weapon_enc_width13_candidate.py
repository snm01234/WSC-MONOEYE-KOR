#!/usr/bin/env python3
"""Fail-closed audit for weapon_enc_width13_candidate.wsc."""
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
    HANGUL_BUN,
    HANGUL_GONG,
    LOCATION_LONG_PAD,
    MAX_VISIBLE_PAD,
    MINUTE_ABS,
    MINUTE_STEAL,
    hangul_glyph_offset,
    payload_at,
    read_glyph,
)
from build_weapon_enc_width13_candidate import (
    ENC_END,
    ENC_LIMIT,
    ENC_START,
    EXPECTED_MAIN,
    EXPECTED_PARENT,
    cells,
    strip_pad,
)
from expand_dictionary import _walk_zstring_range
from monoeye_rom import Tbl, compact_font_file_offset, load_rom, read_encoded_z_safe, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/ui_onebyte_and_map_padding_candidate.wsc"
CAND = ROOT / "out/patch/weapon_enc_width13_candidate.wsc"
CAND_SAVE = ROOT / "sram/weapon_enc_width13_candidate.sav"
REPORT = ROOT / "out/patch/weapon_enc_width13_candidate_report.json"
OUT = ROOT / "out/patch/weapon_enc_width13_candidate_audit.json"
CATALOG = ROOT / "data/encyclopedia_width13_weapon_name_ko.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    parent = bytes(load_rom(PARENT))
    target = bytes(load_rom(CAND))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    failures: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **detail: Any) -> None:
        row = {"name": name, "ok": bool(ok), **detail}
        checks.append(row)
        if not ok:
            failures.append(row)

    check("main_unchanged", sha(main_rom) == EXPECTED_MAIN, sha256=sha(main_rom))
    check("parent_unchanged", sha(parent) == EXPECTED_PARENT, sha256=sha(parent))
    check("candidate_matches_report", sha(target) == report["candidate"]["sha256"])
    check("saveram_byte_exact_with_main", CAND_SAVE.read_bytes() == MAIN_SAVE.read_bytes())
    check("report_ok", report.get("ok") is True)
    check("slots_written", report.get("slots_written") == 94, got=report.get("slots_written"))

    gong = read_glyph(main_rom, hangul_glyph_offset(main_rom, HANGUL_GONG))
    bun = read_glyph(main_rom, hangul_glyph_offset(main_rom, HANGUL_BUN))
    c6 = compact_font_file_offset(ATTACK_CODE)
    df = compact_font_file_offset(MINUTE_STEAL)
    check("C6_glyph_still_공", bytes(target[c6 : c6 + 16]) == gong)
    check("DF_glyph_still_분", bytes(target[df : df + 16]) == bun)
    check("C6_unchanged_from_parent", bytes(target[c6 : c6 + 16]) == bytes(parent[c6 : c6 + 16]))
    check("DF_unchanged_from_parent", bytes(target[df : df + 16]) == bytes(parent[df : df + 16]))
    check("75B3EF_still_C6", payload_at(target, ATTACK_ABS)[0] == bytes([ATTACK_CODE]))
    check("75B559_still_DF", payload_at(target, MINUTE_ABS)[0] == bytes([MINUTE_STEAL]))
    pad_ok = True
    for logical in LOCATION_LONG_PAD:
        payload, _term = payload_at(target, logical)
        pad = len(payload) - len(payload.rstrip(b"\x01"))
        if pad > MAX_VISIBLE_PAD:
            pad_ok = False
    check("map_padding_still_short", pad_ok)
    empty = d.expand_index(EMPTY_STOCK, tbl)
    check("00A9_still_empty", empty == "")

    weapon = strip_pad(d.expand_index(int(catalog["weapon"]["index"], 16), tbl))
    check("weapon_복부_빔_캐논", weapon == "복부　빔　캐논", text=weapon)
    check("weapon_배부_gone", "배부" not in weapon)

    names = []
    for row in catalog["names"]:
        text = strip_pad(d.expand_index(int(row["index"], 16), tbl))
        names.append(text)
        check(f"name_{row['abs']}", text == row["after"], text=text)
    check("갈바르디_gone_from_names", all("갈바르디" not in text for text in names))

    enc_mismatch = []
    for row in catalog["encyclopedia"]:
        got = strip_pad(d.expand_index(int(row["index"], 16), tbl))
        if got != row["after"]:
            enc_mismatch.append({"abs": row["abs"], "got": got, "want": row["after"]})
        if cells(got) > ENC_LIMIT:
            enc_mismatch.append({"abs": row["abs"], "over13": cells(got), "text": got})
    check("encyclopedia_catalog_exact", not enc_mismatch, n=len(enc_mismatch), sample=enc_mismatch[:5])

    original = bytes(load_rom(ORIGINAL))
    over13 = []
    sb = stock_base(target)
    for logical, _orig, _kind in _walk_zstring_range(
        original, ENC_START, ENC_END, region="enc", max_len=256
    ):
        got = read_encoded_z_safe(target, sb + logical, max_len=256)
        if not got:
            continue
        try:
            text = d.expand(got[0], tbl)
        except Exception:
            continue
        for line in text.split("<E62F>"):
            n = cells(line)
            if n > ENC_LIMIT:
                over13.append({"abs": f"{logical:06X}", "cells": n, "text": strip_pad(line)})
    check("encyclopedia_no_line_over_13", not over13, n=len(over13), sample=over13[:8])

    # Record bodies at named encyclopedia addresses must stay identical to parent.
    body_changed = []
    for row in catalog["encyclopedia"] + catalog["names"]:
        abs_hex = row.get("abs")
        if not abs_hex:
            continue
        logical = int(abs_hex, 16)
        parent_payload = payload_at(parent, logical)[0]
        target_payload = payload_at(target, logical)[0]
        if parent_payload != target_payload:
            body_changed.append(abs_hex)
    check("encyclopedia_record_bodies_unchanged", not body_changed, changed=body_changed[:8])

    audit = {
        "ok": not failures,
        "candidate": report["candidate"],
        "failures": failures,
        "checks": checks,
    }
    OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": audit["ok"], "failures": [r["name"] for r in failures]}, ensure_ascii=True))
    return 0 if audit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
