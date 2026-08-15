#!/usr/bin/env python3
"""Fail-closed audit for term_unify_round2_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base

from build_term_unify_round2_candidate import (
    EXPECTED_MAIN,
    EXPECTED_PARENT,
    FORBIDDEN_AFTER,
    SCAN_RANGES,
    strip_pad,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/term_unify_militia_the_o_candidate.wsc"
CAND = ROOT / "out/patch/term_unify_round2_candidate.wsc"
CAND_SAVE = ROOT / "sram/term_unify_round2_candidate.sav"
REPORT = ROOT / "out/patch/term_unify_round2_candidate_report.json"
OUT = ROOT / "out/patch/term_unify_round2_candidate_audit.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
NESTED = 0x0081F


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    parent = bytes(load_rom(PARENT))
    target = bytes(load_rom(CAND))
    original = bytes(load_rom(ORIGINAL))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    out_d = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
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
    check("slots_written", report.get("slots_written") == 57, slots=report.get("slots_written"))

    nested = strip_pad(out_d.expand_index(NESTED, tbl))
    check("nested_0081F_follows_leila", "레이라" in nested and "레일라" not in nested, text=nested)

    leftover: list[dict[str, str]] = []
    sb = stock_base(target)
    for start, end in SCAN_RANGES:
        for logical, _orig, _kind in _walk_zstring_range(
            original, start, end, region="scan", max_len=256
        ):
            got = read_encoded_z_safe(target, sb + logical, max_len=256)
            if not got:
                continue
            try:
                text = out_d.expand(got[0], tbl)
            except Exception:
                continue
            for bad in FORBIDDEN_AFTER:
                if bad in text:
                    leftover.append(
                        {"abs": f"{logical:06X}", "bad": bad, "text": strip_pad(text)[:80]}
                    )
    check("no_forbidden_leftover_in_scan", leftover == [], leftover=leftover[:20], n=len(leftover))

    dict_bad: list[dict[str, str]] = []
    for idx in list(range(out_d.count)) + list(range(0x1000, 0x1000 + out_d.ext3_count)):
        try:
            text = strip_pad(out_d.expand_index(idx, tbl))
        except Exception:
            continue
        for bad in FORBIDDEN_AFTER:
            if bad in text:
                dict_bad.append({"index": f"{idx:05X}", "bad": bad, "text": text[:80]})
    check("no_forbidden_in_dictionary", dict_bad == [], leftover=dict_bad[:20], n=len(dict_bad))

    enc_leila = strip_pad(
        out_d.expand(read_encoded_z_safe(target, sb + 0x5C2B65, max_len=256)[0], tbl)
    )
    check(
        "encyclopedia_leila",
        enc_leila == "레이라・레이몬드",
        text=enc_leila,
    )
    name75 = strip_pad(
        out_d.expand(read_encoded_z_safe(target, sb + 0x75E440, max_len=256)[0], tbl)
    )
    check("name75_leila", name75 == "레이라・레이몬드", text=name75)
    suono_name = strip_pad(
        out_d.expand(read_encoded_z_safe(target, sb + 0x5C6357, max_len=256)[0], tbl)
    )
    suono_icon = strip_pad(
        out_d.expand(read_encoded_z_safe(target, sb + 0x5C63A1, max_len=256)[0], tbl)
    )
    check("enc_suono_name", suono_name == "테라・스오노", text=suono_name)
    check("enc_suono_icon", "테라・스오노" in suono_icon and "수오노" not in suono_icon, text=suono_icon)

    payload = {
        "ok": not failures,
        "candidate": report["candidate"],
        "skipped": report.get("skipped"),
        "checks": checks,
        "failures": failures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "failures": len(failures)}, ensure_ascii=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
