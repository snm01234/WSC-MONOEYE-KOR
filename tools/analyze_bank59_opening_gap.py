#!/usr/bin/env python3
"""Audit the bank-59 opening block omitted by the vetted aux population.

Read-only ROM analysis.  The existing current-tip aux population starts at
59:0244, so this checks 59:0000-59:0243 using canonical zstring boundaries and
the script prefix grammar.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from mixed_residual_classification import core_character_count, japanese_character_count
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/bank59_opening_gap_audit.json"
EXPECTED_TIP_SHA256 = "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825"
START = 0x590000
END_EXCLUSIVE = 0x590244


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    rom = bytes(load_rom(TIP))
    if sha256(rom) != EXPECTED_TIP_SHA256:
        raise RuntimeError("main TIP identity drifted")

    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META_PATH),
        load_ext_meta(EXT3_META_PATH),
    )

    records: list[dict[str, Any]] = []
    meaningful: list[dict[str, Any]] = []
    cursor = START
    while cursor < END_EXCLUSIVE:
        got = read_encoded_z_safe(rom, sb + cursor, max_len=END_EXCLUSIVE - cursor)
        if got is None:
            cursor += 1
            continue
        payload = bytes(got[0])
        terminator = int(got[1]) - sb
        prefix, body, kind = split_prefix_body(payload)
        text = dictionary.expand(body, tbl).rstrip("\u3000 \t")
        row = {
            "abs": f"{cursor:06X}",
            "terminator": f"{terminator:06X}",
            "payload_capacity": len(payload),
            "prefix_hex": prefix.hex().upper(),
            "body_capacity": len(body),
            "kind": kind,
            "body_hex": body.hex().upper(),
            "text": text,
            "japanese_chars": japanese_character_count(text),
            "core_chars": core_character_count(text),
        }
        records.append(row)
        if row["japanese_chars"] and row["core_chars"] >= 2:
            meaningful.append(row)
        cursor = terminator + 1

    first = next((row for row in meaningful if row["abs"] == "590005"), None)
    checks = {
        "tip_identity": sha256(rom) == EXPECTED_TIP_SHA256,
        "range_exact": START == 0x590000 and END_EXCLUSIVE == 0x590244,
        "first_user_line_found": first is not None,
        "first_user_line_prefix_exact": bool(first and first["prefix_hex"] == "173418"),
        "first_user_line_text_exact": bool(
            first and first["text"] == "……思ったより연방の兵力が少ないな。"
        ),
        "first_user_line_body_capacity_17": bool(first and first["body_capacity"] == 17),
        "first_user_line_terminator_exact": bool(first and first["terminator"] == "590019"),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_bank59_opening_gap.py",
        "read_only": True,
        "ok": all(checks.values()),
        "tip": {
            "path": str(TIP.relative_to(ROOT)),
            "sha256": sha256(rom),
        },
        "range": {
            "start": f"{START:06X}",
            "end_exclusive": f"{END_EXCLUSIVE:06X}",
            "reason": "current_tip_aux_sentence_rate_prefix_evidence population starts at 590244",
        },
        "counts": {
            "zstring_records": len(records),
            "meaningful_japanese_records": len(meaningful),
        },
        "user_captured_first_line": first,
        "meaningful_records": meaningful,
        "checks": checks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "counts": report["counts"],
        "user_captured_first_line": first,
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    if not report["ok"]:
        raise RuntimeError("bank59 opening gap audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
