#!/usr/bin/env python3
"""Fail-closed audit for term_unify_militia_the_o_candidate.wsc."""
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
from tbl_code_prefs import ambiguous_chars, find_codes, flatten_codes

from build_term_unify_militia_the_o_candidate import (
    EXPECTED_MAIN,
    EXPECTED_PARENT,
    EXT3,
    FORBIDDEN_AFTER,
    STOCK,
    strip_pad,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/weapon_enc_width13_candidate.wsc"
CAND = ROOT / "out/patch/term_unify_militia_the_o_candidate.wsc"
CAND_SAVE = ROOT / "sram/term_unify_militia_the_o_candidate.sav"
REPORT = ROOT / "out/patch/term_unify_militia_the_o_candidate_report.json"
OUT = ROOT / "out/patch/term_unify_militia_the_o_candidate_audit.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

SCAN_RANGES = (
    (0x590000, 0x5A0000),
    (0x5C0000, 0x5C7900),
    (0x75C000, 0x75E800),
    (0x600000, 0x640000),
)
BANK59_SAMPLES = (0x59930A, 0x599354)
ICON_SLOT = 0x0FE77


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    parent = bytes(load_rom(PARENT))
    target = bytes(load_rom(CAND))
    original = bytes(load_rom(ORIGINAL))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    parent_d = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
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
    check("checksum_present", bool(report.get("checksum")), checksum=report.get("checksum"))
    check(
        "slots_written",
        report.get("slots_written") == len(STOCK) + len(EXT3),
        slots=report.get("slots_written"),
        expected=len(STOCK) + len(EXT3),
    )

    for index, (_before, after) in {**STOCK, **EXT3}.items():
        got = strip_pad(out_d.expand_index(index, tbl))
        check(f"slot_{index:05X}", got == after, got=got, expected=after)

    parent_flat = flatten_codes(parent_d.raw_entry(ICON_SLOT), parent_d)
    cand_flat = flatten_codes(out_d.raw_entry(ICON_SLOT), out_d)
    block_codes = set(ambiguous_chars(tbl).get("█") or ())
    parent_markers = find_codes(parent_flat, block_codes)
    cand_markers = find_codes(cand_flat, block_codes)
    check(
        "icon_marker_codes_preserved",
        parent_markers == cand_markers and "디・오" in strip_pad(out_d.expand_index(ICON_SLOT, tbl)),
        parent_markers=[f"{c:04X}" for c in parent_markers],
        cand_markers=[f"{c:04X}" for c in cand_markers],
    )

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
                        {
                            "abs": f"{logical:06X}",
                            "bad": bad,
                            "text": strip_pad(text)[:80],
                        }
                    )
    check("no_forbidden_leftover_in_scan", leftover == [], leftover=leftover[:20], n=len(leftover))

    for abs_addr in BANK59_SAMPLES:
        got = read_encoded_z_safe(target, sb + abs_addr, max_len=256)
        text = strip_pad(out_d.expand(got[0], tbl)) if got else ""
        check(
            f"bank59_{abs_addr:06X}_militia",
            "밀리샤" in text and "미리샤" not in text,
            text=text,
        )

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

    payload = {
        "ok": not failures,
        "candidate": report["candidate"],
        "checks": checks,
        "failures": failures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "failures": len(failures)}, ensure_ascii=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
